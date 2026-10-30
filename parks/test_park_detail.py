"""US-2.6 (#21): the park detail page."""

from urllib.parse import quote

from django.test import TestCase
from django.urls import reverse

from .models import Facility, Park


class ParkDetailPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.park = Park.objects.create(
            gispropnum='B58',
            name='McCarren Park',
            borough='B',
            address='776 LORIMER STREET',
            location='Nassau Ave, Bayard, Leonard & N 12 Sts',
            zipcode='11211, 11222',
            acres='36.493',
            park_type='Community Park',
        )
        for index, (code, lighted, accessible) in enumerate(
            (('TNS', True, False), ('TNS', False, True), ('TNS', False, False), ('BKB', True, True))
        ):
            Facility.objects.create(
                system_id=f'B58-{index}',
                park=cls.park,
                primary_sport=code,
                is_lighted=lighted,
                is_accessible=accessible,
            )
        Facility.objects.create(system_id='B58-old', park=cls.park, primary_sport='HDB', feature_status='Removed')
        cls.bare = Park.objects.create(gispropnum='X1', name='Walton Avenue Garden', borough='X')
        cls.retired = Park.objects.create(gispropnum='R1', name='Retired Park', borough='R', is_retired=True)

    def page(self, park):
        return self.client.get(reverse('park_detail', kwargs={'pk': park.pk}))

    def test_shows_park_details_and_active_facilities_grouped_by_sport(self):
        response = self.page(self.park)
        self.assertEqual(response.status_code, 200)
        for text in (
            'McCarren Park',
            '776 LORIMER STREET',
            'Nassau Ave, Bayard, Leonard &amp; N 12 Sts',
            'Brooklyn',
            '11211, 11222',
            '36.49 acres',
            'Not yet rated',
        ):
            self.assertContains(response, text)
        self.assertEqual(
            response.context['facility_groups'],
            [
                {'sport': 'Tennis', 'count': 3, 'lighted': 1, 'accessible': 1},
                {'sport': 'Basketball', 'count': 1, 'lighted': 1, 'accessible': 1},
            ],
        )
        self.assertContains(response, '1 lit · 1 accessible', count=2)
        self.assertNotContains(response, 'Handball')

    def test_missing_address_facilities_and_size_show_fallbacks(self):
        response = self.page(self.bare)
        self.assertContains(response, 'Address not specified')
        self.assertContains(response, 'Facility details not specified')
        self.assertContains(response, '<dd>Not specified</dd>', html=True)

    def test_unknown_or_retired_park_is_404(self):
        self.assertEqual(self.page(self.retired).status_code, 404)
        self.assertEqual(self.client.get('/parks/999999/').status_code, 404)

    def test_sits_in_events_hub_with_back_to_results(self):
        response = self.page(self.park)
        self.assertContains(response, 'class="events-hub-toggle"')
        self.assertContains(response, 'Back to results')

    def test_organize_link_preselects_park_and_logged_out_users_log_in_first(self):
        create_url = f'{reverse("event_create")}?park={self.park.pk}'
        self.assertContains(self.page(self.park), f'href="{create_url}"')
        response = self.client.get(create_url)
        self.assertRedirects(response, f'{reverse("login")}?next={quote(create_url)}', fetch_redirect_response=False)

    def test_show_on_map_opens_explore_map_with_this_parks_panel(self):
        map_url = f'{reverse("event_list")}?view=map&park={self.park.pk}&panel=1'
        self.assertContains(self.page(self.park), f'href="{map_url.replace("&", "&amp;")}"')
        response = self.client.get(map_url)
        self.assertEqual(response.context['filters']['view'], 'map')
        self.assertEqual(response.context['selected_park'], self.park)

    def test_explore_parks_list_links_to_the_park_page(self):
        response = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'parks'})
        self.assertContains(response, f'href="{reverse("park_detail", kwargs={"pk": self.park.pk})}"')
