"""Sync NYC Parks and Athletic Facilities from NYC Open Data Socrata JSON APIs."""

from decimal import Decimal, InvalidOperation
import json
import re
import urllib.error
import urllib.request

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from parks.models import Borough, Facility, Park

PARKS_API_URL = "https://data.cityofnewyork.us/resource/enfh-gkve.json?$limit=5000"
FACILITIES_API_URL = "https://data.cityofnewyork.us/resource/qnem-b8re.json?$limit=10000"
VALID_BOROUGHS = set(Borough.values)


def fetch_json(url, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "NYCActive/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "t"}
    return bool(value)


def to_decimal(value, places="0.001"):
    if value in (None, ""):
        return None
    try:
        cleaned = str(value).replace(",", "").strip()
        return Decimal(cleaned).quantize(Decimal(places))
    except (InvalidOperation, ValueError):
        return None


def extract_centroid(multipolygon):
    """Extract an approximate (latitude, longitude) centroid from GeoJSON or WKT multipolygon."""
    if not multipolygon:
        return None, None
    lons = []
    lats = []
    if isinstance(multipolygon, dict):

        def walk(coords):
            if isinstance(coords, (list, tuple)):
                if len(coords) >= 2 and isinstance(coords[0], (int, float)) and isinstance(coords[1], (int, float)):
                    lons.append(float(coords[0]))
                    lats.append(float(coords[1]))
                else:
                    for item in coords:
                        walk(item)

        walk(multipolygon.get("coordinates", []))
    elif isinstance(multipolygon, str):
        for lon_str, lat_str in re.findall(r"(-?\d+\.\d+)\s+(-?\d+\.\d+)", multipolygon):
            lons.append(float(lon_str))
            lats.append(float(lat_str))
    if not lons or not lats:
        return None, None
    lat = to_decimal(sum(lats) / len(lats), "0.000001")
    lon = to_decimal(sum(lons) / len(lons), "0.000001")
    return lat, lon


def extract_sports(row):
    active = []
    for field_key, _label in Facility.SPORT_FIELDS:
        if to_bool(row.get(field_key)):
            active.append(field_key)
    return ",".join(active)


class Command(BaseCommand):
    help = "Ingest and update NYC Parks and Athletic Facilities from NYC Open Data JSON APIs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--if-empty",
            action="store_true",
            help="Only fetch and seed if the Park table is currently empty.",
        )
        parser.add_argument(
            "--ignore-errors",
            action="store_true",
            help="Log network/API errors without exiting non-zero (safe for automated deploy hooks).",
        )

    def handle(self, *args, **options):
        if options["if_empty"] and Park.objects.exists():
            self.stdout.write("Parks table is already populated; skipping sync (--if-empty).")
            return

        try:
            parks_raw = fetch_json(PARKS_API_URL)
            facilities_raw = fetch_json(FACILITIES_API_URL)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            message = f"Unable to fetch NYC Open Data APIs: {exc}"
            if options["ignore_errors"]:
                self.stderr.write(self.style.WARNING(message))
                return
            raise CommandError(message) from exc

        protected_parks = set(Park.objects.filter(admin_modified=True).values_list("gispropnum", flat=True))
        protected_facilities = set(Facility.objects.filter(admin_modified=True).values_list("system_id", flat=True))

        park_objects = {}
        for row in parks_raw:
            gispropnum = (row.get("gispropnum") or "").strip()
            if not gispropnum or gispropnum in protected_parks:
                continue
            name = (row.get("signname") or row.get("name311") or gispropnum).strip()
            borough = (row.get("borough") or "").strip().upper()
            if borough not in VALID_BOROUGHS:
                continue
            lat, lon = extract_centroid(row.get("multipolygon"))
            park_objects[gispropnum] = Park(
                gispropnum=gispropnum,
                name=name[:255],
                borough=borough,
                address=(row.get("address") or "").strip()[:255],
                location=(row.get("location") or "").strip()[:255],
                zipcode=(row.get("zipcode") or "").strip()[:32],
                acres=to_decimal(row.get("acres"), "0.001"),
                park_type=(row.get("typecategory") or "").strip()[:100],
                subcategory=(row.get("subcategory") or "").strip()[:100],
                latitude=lat,
                longitude=lon,
                waterfront=to_bool(row.get("waterfront")),
                is_retired=to_bool(row.get("retired")),
            )

        with transaction.atomic():
            if park_objects:
                Park.objects.bulk_create(
                    list(park_objects.values()),
                    batch_size=500,
                    update_conflicts=True,
                    unique_fields=["gispropnum"],
                    update_fields=[
                        "name",
                        "borough",
                        "address",
                        "location",
                        "zipcode",
                        "acres",
                        "park_type",
                        "subcategory",
                        "latitude",
                        "longitude",
                        "waterfront",
                        "is_retired",
                        "updated_at",
                    ],
                )

            park_id_by_gis = dict(Park.objects.values_list("gispropnum", "id"))
            facility_objects = {}
            skipped_facilities = 0
            for row in facilities_raw:
                system_id = (row.get("system") or "").strip()
                gispropnum = (row.get("gispropnum") or "").strip()
                if not system_id or not gispropnum:
                    skipped_facilities += 1
                    continue
                if system_id in protected_facilities:
                    continue
                park_id = park_id_by_gis.get(gispropnum)
                if not park_id:
                    skipped_facilities += 1
                    continue
                facility_objects[system_id] = Facility(
                    system_id=system_id[:64],
                    park_id=park_id,
                    primary_sport=(row.get("primary_sport") or "").strip()[:64],
                    sports=extract_sports(row)[:500],
                    surface_type=(row.get("surface_type") or "").strip()[:100],
                    dimensions=(row.get("dimensions") or "").strip()[:100],
                    field_number=str(row.get("field_number") or "").strip()[:32],
                    feature_status=(row.get("featurestatus") or "Active").strip()[:32],
                    is_lighted=to_bool(row.get("field_lighted")),
                    is_accessible=to_bool(row.get("accessible")),
                )

            if facility_objects:
                Facility.objects.bulk_create(
                    list(facility_objects.values()),
                    batch_size=500,
                    update_conflicts=True,
                    unique_fields=["system_id"],
                    update_fields=[
                        "park_id",
                        "primary_sport",
                        "sports",
                        "surface_type",
                        "dimensions",
                        "field_number",
                        "feature_status",
                        "is_lighted",
                        "is_accessible",
                        "updated_at",
                    ],
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Synced {len(park_objects)} parks and {len(facility_objects)} facilities "
                f"(skipped {skipped_facilities} unlinked facility rows)."
            )
        )
