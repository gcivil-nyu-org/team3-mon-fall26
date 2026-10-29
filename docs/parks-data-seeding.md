# NYC Parks & Athletic Facilities Data Seeding

NYC Active uses real park and athletic facility records published by NYC Parks on
the [NYC Open Data](https://opendata.cityofnewyork.us/) Socrata platform. The
`parks` Django app (`parks/models.py`) provides the shared `Park` and `Facility`
models used across Park Discovery (Epic 2), Event Creation (Epic 3), Park Reviews
(Epic 6), and Admin Data Maintenance (Epic 7), along with an idempotent
management command (`python manage.py sync_parks`) and automated AWS deployment
and background refresh hooks.

---

## 1. Upstream NYC Open Data Sources

`sync_parks` ingests two public Socrata JSON endpoints over HTTPS using only the
Python standard library (`urllib.request`):

| Dataset | Socrata Dataset ID | Endpoint URL | Approximate Volume | Target Model |
| --- | --- | --- | --- | --- |
| **Parks Properties** | `enfh-gkve` | `https://data.cityofnewyork.us/resource/enfh-gkve.json` | ~2,061 rows | `parks.Park` |
| **Athletic Facilities** | `qnem-b8re` *(tabular dataset backing map view `qpgi-ckmp`)* | `https://data.cityofnewyork.us/resource/qnem-b8re.json` | ~6,951 rows (~6,924 linked to parks) | `parks.Facility` |

> **Note on Athletic Facilities dataset ID:** The NYC Open Data portal URL
> `https://data.cityofnewyork.us/Recreation/Athletic-Facilities/qpgi-ckmp` is a
> Socrata Geospatial Map wrapper (`assetType: map`), which returns HTTP `400` when
> queried via the `/resource/qpgi-ckmp.json` SODA API. Its underlying tabular
> Socrata dataset UID is **`qnem-b8re`**, which supports full SODA JSON pagination
> (`$limit` and `$offset`) and links each court/field to its parent park via
> `gispropnum`.

### Optional Socrata App Token

Public anonymous requests succeed without authentication. If higher Socrata rate
limits are ever needed, set the `NYC_OPEN_DATA_APP_TOKEN` environment variable;
`sync_parks` automatically attaches it as the `X-App-Token` request header.

---

## 2. Database Models (`parks/models.py`)

### `Park` (`parks_park`)

Each row corresponds to an NYC Parks property keyed by `gispropnum`:

- **`gispropnum`** (`CharField`, `unique=True`, indexed): Primary NYC Parks
  identifier (e.g., `M010` for Central Park, `B057` for McCarren Park).
- **`name`** (`CharField`, indexed): Official `signname` (falls back to `name311`
  or `gispropnum` if blank).
- **`borough`** (`CharField`, indexed, `Borough` choices):
  - `M` — Manhattan
  - `B` — Brooklyn
  - `Q` — Queens
  - `X` — Bronx
  - `R` — Staten Island
  - Use `park.get_borough_display()` for the human-readable borough name.
- **`address`** / **`location`** / **`zipcode`**: Street address (`address`),
  cross-street description (`location`), and postal code (`zipcode`). The helper
  property `park.display_address` returns `address` if present, otherwise
  `location`.
- **`latitude`** / **`longitude`** (`FloatField`, nullable): Extracted during
  ingestion by computing the centroid of the outer ring of each park's
  `multipolygon` (supports both Socrata GeoJSON `MultiPolygon`/`Polygon` dicts
  and WKT `MULTIPOLYGON`/`POLYGON` strings) so map pins can be placed without
  requiring PostGIS/GDAL.
- **`acres`**, **`park_type`**, **`subcategory`**, **`department`**,
  **`jurisdiction`**, **`mapped`**, **`waterfront`**: Metadata from NYC Parks
  Properties.
- **`is_retired`** (`BooleanField`): Marks decommissioned properties (`retired=True`).
  Active UI dropdowns filter on `Park.objects.filter(is_retired=False)`.
- **`admin_modified`** (`BooleanField`, default `False`): Protects manual
  administrator edits from being overwritten during subsequent `sync_parks` runs.
- **`created_at`** / **`updated_at`**: Automatic creation and last-modified
  timestamps.

### `Facility` (`parks_facility`)

Each row corresponds to an individual court, field, track, or play area linked to
its parent `Park`:

- **`system_id`** (`CharField`, `unique=True`, indexed): Unique NYC Parks
  facility identifier (`system` column in `qnem-b8re`, e.g., `B237-HANDBALL-2`).
- **`park`** (`ForeignKey(Park, related_name="facilities", on_delete=CASCADE)`):
  Linked via matching `gispropnum`. Facilities whose `gispropnum` does not match
  a known `Park` are skipped and reported in the command summary.
- **`primary_sport`** (`CharField`, indexed): Normalized primary sport/activity
  code (e.g., `BASKETBALL`, `TENNIS`, `SOCCER`, `HANDBALL`, `PICKLEBALL`).
- **`sports`** (`JSONField`, default `dict`): Dictionary of all 26 boolean sport
  flags from NYC Open Data (`basketball`, `bocce`, `cricket`, `football`,
  `handball`, `icehockey`, `kickball`, `lacrosse`, `netball`, `pickleball`,
  `rugby`, `soccer`, `softball`, `tennis`, ` track and field`, ` ultimate`,
  `volleyball`, `wheelchairfootball`, `youthbaseball`, `adultbaseball`,
  `littleleague`, `tball`, `rollerhockey`, `fieldhockey`, `multiprogram`,
  `inlinehockey`). Use `facility.sport_list()` for a sorted list of human-readable
  sport names enabled on that facility.
- **`surface_type`**, **`dimensions`**, **`field_number`**, **`feature_status`**,
  **`is_accessible`**, **`is_lighted`**, **`department`**, **`is_retired`**:
  Facility attributes for filtering and park detail views.
- **`admin_modified`** (`BooleanField`, default `False`): Protects manual
  administrator edits from being overwritten during subsequent `sync_parks` runs.

---

## 3. Running `sync_parks` Locally

After running database migrations, seed your local `db.sqlite3` database with:

```bash
python manage.py migrate
python manage.py sync_parks
```

Typical output (~2 seconds):

```text
Fetching NYC Parks Properties from Socrata API...
Fetching NYC Athletic Facilities from Socrata API...
Sync complete. Parks: 2061 created, 0 updated, 0 skipped (admin/invalid). Facilities: 6924 created, 0 updated, 27 skipped (unmatched park/admin).
```

### Command-Line Options

| Flag | Description |
| --- | --- |
| *(no flags)* | Fetches and upserts all parks and athletic facilities. |
| `--if-empty` | Runs the sync only when `Park.objects.exists()` is `False`. Exits immediately with `Parks table already populated; skipping (--if-empty).` if records already exist. |
| `--parks-only` | Syncs only `parks.Park` (`enfh-gkve`), skipping `parks.Facility`. |
| `--facilities-only` | Syncs only `parks.Facility` (`qnem-b8re`) against existing `Park` records. |
| `--limit <N>` | Page size per Socrata HTTP request (default: `10000`). The command automatically paginates with `$offset` until all pages are retrieved. |
| `--timeout <seconds>` | Socket timeout per HTTP request in seconds (default: `45`). |
| `--ignore-errors` | Logs a warning and exits with status `0` if the NYC Open Data API is unreachable, rather than failing the calling script or deployment hook. |

---

## 4. Upsert Behavior & Admin Edit Protection (Issue #45)

1. **Idempotent Bulk Upsert:**
   - Existing `Park` (`gispropnum`) and `Facility` (`system_id`) records are
     loaded in memory at the start of each batch.
   - New records are inserted via `bulk_create(..., batch_size=500)`.
   - Existing records are updated in place via `bulk_update(..., batch_size=500)`
     inside `transaction.atomic()`, including refreshing `updated_at` to the
     current sync timestamp.
2. **Preserving Administrator Edits (`admin_modified=True`):**
   - Any `Park` or `Facility` row with `admin_modified=True` is skipped during
     `sync_parks`.
   - In `parks/admin.py`, editing an existing `Park` or `Facility` in Django
     Admin automatically sets `obj.admin_modified = True` in `save_model()`.
   - Future custom admin views (Epic 7, Issue #45) should likewise set
     `admin_modified = True` whenever an administrator saves manual overrides to
     a park or facility.

---

## 5. AWS Deployment Seeding & Weekly Automation

On the team's Elastic Beanstalk environment (`DJANGO_LOCAL_POSTGRES=True`), park
and facility data is seeded and refreshed automatically by
[`.platform/scripts/local_database.py`](../.platform/scripts/local_database.py):

### A. Initial / Deploy-Time Seeding (`local_database.py migrate`)

During every application or configuration deployment, `.platform/hooks/predeploy/02_migrate.sh`
(and `.platform/confighooks/predeploy/02_migrate.sh`) invokes `local_database.py migrate`,
which:

1. Takes an encrypted pre-migration PostgreSQL dump to S3 (`backup("pre-migrate")`).
2. Runs `python manage.py migrate --noinput` as the unprivileged `webapp` user.
3. Runs `python manage.py sync_parks --if-empty --ignore-errors` as `webapp`:
   - On the first deployment after adding the `parks` tables (when `parks_park`
     is empty), it populates all ~2,061 parks and ~6,924 facilities immediately.
   - On subsequent deployments, `--if-empty` detects that `parks_park` is already
     populated and completes instantaneously without re-downloading datasets.
   - `--ignore-errors` guarantees that a temporary upstream NYC Open Data outage
     never blocks an application release.
4. Collects static assets and takes an encrypted post-migration S3 backup
   (`backup("post-migrate")`, updating `postgres/latest.dump`).

### B. Weekly Background Sync Timer (`nyc-active-parks-sync.timer`)

During instance setup (`local_database.py setup`), two `systemd` units are
installed in `/etc/systemd/system/` alongside the hourly database backup timer:

- **`nyc-active-parks-sync.service`**:
  ```ini
  [Unit]
  Description=Sync NYC Parks Open Data into PostgreSQL
  After=postgresql.service network-online.target

  [Service]
  Type=oneshot
  ExecStart=/usr/bin/python3 /var/app/current/.platform/scripts/local_database.py sync_parks
  ```
- **`nyc-active-parks-sync.timer`**:
  ```ini
  [Unit]
  Description=Weekly NYC Parks Open Data sync

  [Timer]
  OnCalendar=weekly
  Persistent=true
  RandomizedDelaySec=30m

  [Install]
  WantedBy=timers.target
  ```

When the timer fires, `local_database.py sync_parks`:
1. Validates the local PostgreSQL environment configuration.
2. Runs `python manage.py sync_parks --ignore-errors` inside `/var/app/current`
   as the unprivileged `webapp` user (`DJANGO_SETTINGS_MODULE=config.production`),
   upserting any new or updated parks and facilities while skipping records with
   `admin_modified=True`.
3. Immediately triggers `backup("periodic")` so the refreshed park/facility rows
   are captured in S3 (`postgres/latest.dump` and `postgres/hourly/`).

### C. Operations & Troubleshooting on AWS

Using AWS Systems Manager Session Manager on the Elastic Beanstalk instance:

- **Check timer schedule and last run:**
  ```bash
  systemctl status nyc-active-parks-sync.timer
  ```
- **Inspect sync logs:**
  ```bash
  journalctl -u nyc-active-parks-sync.service -n 50 --no-pager
  ```
- **Trigger an immediate manual refresh on the server:**
  ```bash
  sudo systemctl start nyc-active-parks-sync.service
  ```

