from decimal import Decimal
from io import StringIO
from unittest.mock import patch
import urllib.error

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import Borough, Facility, Park, comma_token_q

SAMPLE_PARKS = [
    {
        'gispropnum': 'M110',
        'signname': 'William McCray Playground',
        'name311': 'William McCray Playground',
        'borough': 'M',
        'address': '53-41 WEST 138 STREET',
        'location': 'W. 138 St., bet. Lenox Ave. and 5 Ave.',
        'zipcode': '10037',
        'acres': '0.456',
        'typecategory': 'Playground',
        'subcategory': 'Neighborhood Plgd',
        'waterfront': False,
        'retired': False,
        'multipolygon': {
            'type': 'MultiPolygon',
            'coordinates': [[[[-73.937329, 40.815534], [-73.938141, 40.815562]]]],
        },
    },
    {
        'gispropnum': 'B237',
        'signname': 'Stockton Playground',
        'borough': 'B',
        'address': 'PARK AVENUE',
        'location': 'Park Ave. & Marcy Ave.',
        'zipcode': '11206',
        'acres': '1.200',
        'typecategory': 'Playground',
        'subcategory': 'JOP',
        'waterfront': False,
        'retired': False,
        'multipolygon': 'MULTIPOLYGON (((-73.953982 40.682178, -73.953830 40.682195)))',
    },
]

SAMPLE_FACILITIES = [
    {
        'system': 'B237-HANDBALL-2',
        'gispropnum': 'B237',
        'primary_sport': 'HDB',
        'handball': True,
        'basketball': False,
        'pickleball': True,
        'surface_type': 'Asphalt',
        'dimensions': 'Handball',
        'field_number': '1',
        'featurestatus': 'Active',
        'field_lighted': True,
        'accessible': False,
    },
    {
        'system': 'ORPHAN-FACILITY-1',
        'gispropnum': 'UNKNOWN999',
        'primary_sport': 'BKB',
        'basketball': True,
        'featurestatus': 'Active',
    },
]


class SyncParksCommandTests(TestCase):
    @patch('parks.management.commands.sync_parks.fetch_json')
    def test_sync_parks_creates_parks_and_linked_facilities(self, mock_fetch):
        mock_fetch.side_effect = [SAMPLE_PARKS, SAMPLE_FACILITIES]
        out = StringIO()
        call_command('sync_parks', stdout=out)

        self.assertEqual(Park.objects.count(), 2)
        self.assertEqual(Facility.objects.count(), 1)

        m110 = Park.objects.get(gispropnum='M110')
        self.assertEqual(m110.name, 'William McCray Playground')
        self.assertEqual(m110.borough, Borough.MANHATTAN)
        self.assertEqual(m110.acres, Decimal('0.456'))
        self.assertAlmostEqual(float(m110.latitude), 40.815548, places=5)
        self.assertAlmostEqual(float(m110.longitude), -73.937735, places=5)
        self.assertEqual(str(m110), 'William McCray Playground (Manhattan)')

        b237 = Park.objects.get(gispropnum='B237')
        self.assertAlmostEqual(float(b237.latitude), 40.682187, places=5)

        facility = Facility.objects.get(system_id='B237-HANDBALL-2')
        self.assertEqual(facility.park, b237)
        self.assertEqual(facility.primary_sport, 'HDB')
        self.assertEqual(facility.sport_list(), ['pickleball', 'handball'])
        self.assertTrue(facility.is_lighted)
        self.assertFalse(facility.is_accessible)

    @patch('parks.management.commands.sync_parks.fetch_json')
    def test_sync_parks_preserves_admin_modified_records(self, mock_fetch):
        park = Park.objects.create(
            gispropnum='M110',
            name='Admin Custom Park Name',
            borough=Borough.MANHATTAN,
            admin_modified=True,
        )
        facility = Facility.objects.create(
            system_id='B237-HANDBALL-2',
            park=park,
            primary_sport='CUSTOM',
            admin_modified=True,
        )
        mock_fetch.side_effect = [SAMPLE_PARKS, SAMPLE_FACILITIES]
        call_command('sync_parks', stdout=StringIO())

        park.refresh_from_db()
        facility.refresh_from_db()
        self.assertEqual(park.name, 'Admin Custom Park Name')
        self.assertEqual(facility.primary_sport, 'CUSTOM')
        b237 = Park.objects.get(gispropnum='B237')
        self.assertEqual(b237.name, 'Stockton Playground')

        # A subsequent weekly sync updates unmodified records in place without duplicating rows
        updated_parks = [
            SAMPLE_PARKS[0],
            {**SAMPLE_PARKS[1], 'signname': 'Stockton Playground Updated'},
        ]
        mock_fetch.side_effect = [updated_parks, SAMPLE_FACILITIES]
        call_command('sync_parks', stdout=StringIO())
        self.assertEqual(Park.objects.count(), 2)
        b237.refresh_from_db()
        self.assertEqual(b237.name, 'Stockton Playground Updated')

    @patch('parks.management.commands.sync_parks.fetch_json')
    def test_if_empty_skips_api_call_when_parks_exist(self, mock_fetch):
        Park.objects.create(gispropnum='M110', name='Existing Park', borough=Borough.MANHATTAN)
        out = StringIO()
        call_command('sync_parks', '--if-empty', stdout=out)
        mock_fetch.assert_not_called()
        self.assertIn('already populated', out.getvalue())

    @patch('parks.management.commands.sync_parks.fetch_json')
    def test_ignore_errors_handles_network_failure_gracefully(self, mock_fetch):
        mock_fetch.side_effect = urllib.error.URLError('timeout')
        err = StringIO()
        call_command('sync_parks', '--ignore-errors', stderr=err)
        self.assertIn('Unable to fetch NYC Open Data APIs', err.getvalue())

        with self.assertRaises(CommandError):
            call_command('sync_parks')


class CommaTokenQTests(TestCase):
    def setUp(self):
        self.single = Park.objects.create(gispropnum='M1', name='Single', borough=Borough.MANHATTAN,
                                          zipcode='10029')
        self.pair = Park.objects.create(gispropnum='M2', name='Pair', borough=Borough.MANHATTAN,
                                        zipcode='10029, 10128')
        self.triple = Park.objects.create(gispropnum='M3', name='Triple', borough=Borough.MANHATTAN,
                                          zipcode='10035, 10128, 10029')
        self.blank = Park.objects.create(gispropnum='M4', name='Blank', borough=Borough.MANHATTAN)

    def names(self, token):
        return set(Park.objects.filter(comma_token_q('zipcode', token)).values_list('name', flat=True))

    def test_matches_whole_zip_codes_in_any_position(self):
        self.assertEqual(self.names('10029'), {'Single', 'Pair', 'Triple'})
        self.assertEqual(self.names('10128'), {'Pair', 'Triple'})
        self.assertEqual(self.names('10035'), {'Triple'})

    def test_partial_zip_codes_do_not_match(self):
        self.assertEqual(self.names('1002'), set())
        self.assertEqual(self.names('0029'), set())

    def test_matches_facility_sports_without_spaces_and_isolates_similar_keys(self):
        Facility.objects.create(system_id='F1', park=self.single, sports='adult_softball,ll_softball')
        Facility.objects.create(system_id='F2', park=self.pair, sports='nonregulation_soccer')

        def parks_with(key):
            return set(Facility.objects.filter(comma_token_q('sports', key)).values_list('park__name', flat=True))

        self.assertEqual(parks_with('ll_softball'), {'Single'})
        self.assertEqual(parks_with('adult_softball'), {'Single'})
        self.assertEqual(parks_with('softball'), set())
        self.assertEqual(parks_with('regulation_soccer'), set())
        self.assertEqual(parks_with('nonregulation_soccer'), {'Pair'})

