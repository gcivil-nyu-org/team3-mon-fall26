"""#45: administrators add missing facilities and correct park information in /admin/."""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .admin import next_admin_system_id
from .models import Borough, Facility, Park

User = get_user_model()
PASSWORD = 'Hudson-River-Run-77'

OPEN_DATA_PARKS = [
    {'gispropnum': 'B237', 'signname': 'Stockton Playground', 'borough': 'B', 'zipcode': '11206'},
]
OPEN_DATA_FACILITIES = [
    {
        'system': 'B237-HANDBALL-2',
        'gispropnum': 'B237',
        'primary_sport': 'HDB',
        'handball': True,
        'featurestatus': 'Active',
        'field_lighted': False,
    },
]


class AdminCorrectionsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('mod', 'mod@example.test', PASSWORD, is_staff=True)
        cls.member = User.objects.create_user('player', 'player@example.test', PASSWORD)
        cls.park = Park.objects.create(
            gispropnum='B237', name='Stockton Playground', borough=Borough.BROOKLYN, zipcode='11206'
        )
        cls.facility = Facility.objects.create(
            system_id='B237-HANDBALL-2', park=cls.park, primary_sport='HDB', sports='handball'
        )

    def setUp(self):
        self.client.force_login(self.admin)
        self.park_url = reverse('admin:parks_park_change', args=[self.park.pk])
        self.facility_url = reverse('admin:parks_facility_change', args=[self.facility.pk])

    def park_data(self, *new_facilities, **changes):
        """The park change form as an administrator would submit it, plus any new inline facilities."""
        data = {
            'name': self.park.name,
            'borough': self.park.borough,
            'address': '',
            'location': '',
            'zipcode': self.park.zipcode,
            'acres': '',
            'park_type': '',
            'subcategory': '',
            'latitude': '',
            'longitude': '',
            'facilities-TOTAL_FORMS': str(1 + len(new_facilities)),
            'facilities-INITIAL_FORMS': '1',
            'facilities-0-id': str(self.facility.pk),
            'facilities-0-park': str(self.park.pk),
            'facilities-0-primary_sport': 'HDB',
            'facilities-0-sports': ['handball'],
            'facilities-0-feature_status': 'Active',
        }
        for i, facility in enumerate(new_facilities, start=1):
            data.update({f'facilities-{i}-park': str(self.park.pk), f'facilities-{i}-feature_status': 'Active'})
            data.update({f'facilities-{i}-{key}': value for key, value in facility.items()})
        data.update(changes)
        return data

    def facility_data(self, **changes):
        data = {
            'park': str(self.park.pk),
            'primary_sport': 'HDB',
            'sports': ['handball'],
            'surface_type': '',
            'dimensions': '',
            'field_number': '',
            'feature_status': 'Active',
        }
        data.update(changes)
        return data

    def sync(self):
        with patch(
            'parks.management.commands.sync_parks.fetch_json', side_effect=[OPEN_DATA_PARKS, OPEN_DATA_FACILITIES]
        ):
            call_command('sync_parks', stdout=StringIO())

    def park_page(self):
        return self.client.get(reverse('park_detail', kwargs={'pk': self.park.pk}))


class AddMissingFacilityTests(AdminCorrectionsTestCase):
    # Scenario: Add a missing facility
    def test_facility_added_on_the_park_appears_on_its_page(self):
        new = {'primary_sport': 'BKB', 'sports': ['basketball'], 'is_lighted': 'on'}
        response = self.client.post(self.park_url, self.park_data(new))

        self.assertRedirects(response, reverse('admin:parks_park_changelist'))
        added = Facility.objects.get(primary_sport='BKB')
        self.assertEqual(added.park, self.park)
        self.assertEqual(added.system_id, 'ADMIN-B237-1')
        self.assertEqual(added.sports, 'basketball')
        self.assertTrue(added.admin_modified)
        page = self.park_page()
        self.assertContains(page, '<span class="park-facility-sport">Basketball</span>', html=True)
        self.assertContains(page, '1 lit · 0 accessible')

    def test_generated_system_ids_are_unique_per_park(self):
        Facility.objects.create(system_id='ADMIN-B237-1', park=self.park, primary_sport='BKB')
        Facility.objects.create(system_id='ADMIN-B237-3', park=self.park, primary_sport='BKB')
        self.assertEqual(next_admin_system_id(self.park), 'ADMIN-B237-2')

        self.client.post(
            self.park_url,
            self.park_data(
                {'primary_sport': 'TNS', 'sports': ['tennis']}, {'primary_sport': 'PKB', 'sports': ['pickleball']}
            ),
        )
        ids = set(Facility.objects.filter(primary_sport__in=['TNS', 'PKB']).values_list('system_id', flat=True))
        self.assertEqual(ids, {'ADMIN-B237-2', 'ADMIN-B237-4'})

    def test_facility_added_from_the_facility_admin_also_gets_an_id(self):
        response = self.client.post(
            reverse('admin:parks_facility_add'), self.facility_data(primary_sport='SCR', sports=['regulation_soccer'])
        )
        self.assertEqual(response.status_code, 302)
        added = Facility.objects.get(primary_sport='SCR')
        self.assertEqual((added.system_id, added.admin_modified), ('ADMIN-B237-1', True))

    def test_sports_are_saved_in_the_comma_format_in_field_order(self):
        self.client.post(self.facility_url, self.facility_data(sports=['pickleball', 'handball', 'tennis']))
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.sports, 'tennis,pickleball,handball')
        self.assertEqual(self.facility.sport_list(), ['tennis', 'pickleball', 'handball'])

    def test_added_facility_is_found_by_the_explore_sport_filter(self):
        self.client.post(self.park_url, self.park_data({'primary_sport': 'TNS', 'sports': ['tennis']}))
        response = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'parks', 'sport': 'tennis'})
        self.assertContains(response, 'Stockton Playground')


class CorrectExistingInformationTests(AdminCorrectionsTestCase):
    # Scenario: Correct existing information
    def test_park_correction_shows_and_survives_the_weekly_sync(self):
        response = self.client.post(self.park_url, self.park_data(name='Stockton Street Playground'))
        self.assertEqual(response.status_code, 302)
        self.park.refresh_from_db()
        self.assertTrue(self.park.admin_modified, 'set automatically, no checkbox to remember')

        self.sync()

        self.park.refresh_from_db()
        self.assertEqual(self.park.name, 'Stockton Street Playground')
        self.assertContains(self.park_page(), 'Stockton Street Playground')

    def test_facility_correction_shows_and_survives_the_weekly_sync(self):
        response = self.client.post(self.facility_url, self.facility_data(is_lighted='on'))
        self.assertEqual(response.status_code, 302)
        self.facility.refresh_from_db()
        self.assertTrue(self.facility.admin_modified)

        self.sync()

        self.facility.refresh_from_db()
        self.assertTrue(self.facility.is_lighted, 'open data says unlit; the correction stands')
        self.assertContains(self.park_page(), '1 lit · 0 accessible')

    def test_correcting_a_facility_inline_also_protects_it(self):
        data = self.park_data()
        data['facilities-0-is_accessible'] = 'on'
        self.client.post(self.park_url, data)
        self.facility.refresh_from_db()
        self.assertTrue(self.facility.is_accessible)
        self.assertTrue(self.facility.admin_modified)

    def test_unchanged_inline_facilities_stay_unprotected(self):
        self.client.post(self.park_url, self.park_data(name='Renamed'))
        self.facility.refresh_from_db()
        self.assertFalse(self.facility.admin_modified, 'only the rows an administrator changed are protected')

    def test_sync_still_updates_records_nobody_corrected(self):
        Park.objects.filter(pk=self.park.pk).update(name='Old name')
        self.sync()
        self.park.refresh_from_db()
        self.assertEqual(self.park.name, 'Stockton Playground')

    def test_sync_never_deletes_admin_added_facilities(self):
        self.client.post(self.park_url, self.park_data({'primary_sport': 'BKB', 'sports': ['basketball']}))
        self.sync()
        self.assertTrue(Facility.objects.filter(system_id='ADMIN-B237-1').exists())

    def test_protection_flag_and_ids_are_read_only(self):
        page = self.client.get(self.park_url)
        self.assertNotContains(page, 'name="admin_modified"')
        self.assertNotContains(page, 'name="gispropnum"')
        self.assertNotContains(page, 'name="facilities-0-system_id"')

    def test_unknown_open_data_sport_code_can_still_be_edited(self):
        Facility.objects.filter(pk=self.facility.pk).update(primary_sport='MPPA2')
        page = self.client.get(self.facility_url)
        self.assertContains(page, 'MPPA2 (from NYC Open Data)')
        response = self.client.post(self.facility_url, self.facility_data(primary_sport='MPPA2', field_number='4'))
        self.assertEqual(response.status_code, 302)
        self.facility.refresh_from_db()
        self.assertEqual((self.facility.primary_sport, self.facility.field_number), ('MPPA2', '4'))


class InvalidChangeTests(AdminCorrectionsTestCase):
    # Scenario: Invalid change
    def assertUnchanged(self):
        self.park.refresh_from_db()
        self.facility.refresh_from_db()
        self.assertEqual(self.park.name, 'Stockton Playground')
        self.assertFalse(self.park.admin_modified)
        self.assertEqual((self.facility.primary_sport, self.facility.sports), ('HDB', 'handball'))
        self.assertEqual(Facility.objects.count(), 1)

    def test_unknown_sport_is_rejected(self):
        response = self.client.post(self.facility_url, self.facility_data(sports=['quidditch']))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Select a valid choice', str(response.context['adminform'].form.errors['sports']))
        self.assertUnchanged()

    def test_unknown_primary_sport_is_rejected(self):
        response = self.client.post(self.facility_url, self.facility_data(primary_sport='QDT'))
        self.assertIn('primary_sport', response.context['adminform'].form.errors)
        self.assertUnchanged()

    def test_facility_needs_at_least_one_sport(self):
        response = self.client.post(self.facility_url, self.facility_data(sports=[]))
        self.assertIn('This field is required.', response.context['adminform'].form.errors['sports'])
        self.assertUnchanged()

    def test_invalid_new_inline_facility_saves_nothing(self):
        response = self.client.post(
            self.park_url, self.park_data({'primary_sport': 'BKB', 'sports': ['quidditch']}, name='Changed')
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select a valid choice')
        self.assertUnchanged()

    def test_park_without_a_name_is_rejected(self):
        response = self.client.post(self.park_url, self.park_data(name=''))
        self.assertIn('name', response.context['adminform'].form.errors)
        self.assertUnchanged()


class UnauthorizedTests(AdminCorrectionsTestCase):
    # Scenario: Unauthorized (#44)
    def test_members_cannot_reach_park_or_facility_admin(self):
        self.client.force_login(self.member)
        for url in (self.park_url, self.facility_url, reverse('admin:parks_facility_add')):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertRedirects(response, f"{reverse('admin:login')}?next={url}")
        response = self.client.post(self.park_url, self.park_data(name='Hijacked'))
        self.assertEqual(response.status_code, 302)
        self.park.refresh_from_db()
        self.assertEqual(self.park.name, 'Stockton Playground')

    def test_administrators_can_add_and_change_parks_and_facilities(self):
        for name in ('parks_park_change', 'parks_facility_change'):
            with self.subTest(view=name):
                obj = self.park if 'park_change' in name else self.facility
                self.assertEqual(self.client.get(reverse(f'admin:{name}', args=[obj.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:parks_facility_add')).status_code, 200)
