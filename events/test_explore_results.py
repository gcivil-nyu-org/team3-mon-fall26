"""#123: /events/results/ returns the Explore pieces that change with the filters, tab or page, as JSON."""

from urllib.parse import parse_qs, urlsplit

from django.urls import reverse

from parks.models import Park

from .hub import EXPLORE_SESSION_KEY, panel_param
from .models import Activity
from .test_event_phases import PhaseTestCase


class ExploreResultsTests(PhaseTestCase):
    def results(self, **params):
        response = self.client.get(reverse('explore_results'), params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        return response.json()

    def test_returns_the_fields_counts_list_and_map_address_for_the_filters(self):
        self.event('Tonight')
        self.event('Soccer later', days=1, activity=Activity.SOCCER)

        data = self.results(view='list', tab='events', sport=Activity.BASKETBALL)

        self.assertIn('Tonight', data['list_html'])
        self.assertNotIn('Soccer later', data['list_html'])
        self.assertIn('Sport: Basketball', data['filters_html'])
        self.assertIn('<strong>1</strong> event', data['counts_html'])
        self.assertTrue(data['map_url'].startswith(reverse('event_map_api') + '?'))
        self.assertEqual(parse_qs(urlsplit(data['map_url']).query)['sport'], [Activity.BASKETBALL])
        self.assertEqual((data['panel_id'], data['panel_html'], data['map_empty_html']), ('', '', ''))

    def test_url_is_the_canonical_explore_address_with_page_and_panel(self):
        for index in range(25):
            self.event(f'Game {index}', days=1 + index)

        data = self.results(view='list', tab='events', q='', phase=['live', 'upcoming'], page='2', panel=self.park.pk)
        url = urlsplit(data['url'])
        query = parse_qs(url.query)

        self.assertEqual(url.path, reverse('event_list'))
        self.assertEqual(query['page'], ['2'])
        self.assertEqual(query['panel'], [panel_param(self.park.pk)])
        self.assertEqual(query['view'], ['list'])
        self.assertNotIn('q', query)
        self.assertNotIn('phase', query, 'the default phases are stored as no phase filter')
        self.assertEqual(data['panel_id'], str(self.park.pk))
        self.assertIn('McCarren Park', data['panel_html'])
        self.assertIn('Page 2 of 2', data['list_html'])

    def test_each_update_saves_the_state_for_back_to_results(self):
        self.event('Tonight')

        self.results(view='list', tab='parks', sport=Activity.BASKETBALL)

        saved = parse_qs(self.client.session[EXPLORE_SESSION_KEY])
        self.assertEqual((saved['tab'], saved['sport']), (['parks'], [Activity.BASKETBALL]))

    def test_empty_results_include_the_map_and_list_empty_states(self):
        data = self.results(view='map', q='no park or event is called this')

        self.assertIn('data-map-empty', data['map_empty_html'])
        self.assertIn('data-empty-state', data['list_html'])

    def test_without_park_data_the_page_reloads_instead(self):
        Park.objects.update(is_retired=True)

        self.assertEqual(self.results(view='list'), {'reload': True})

    def test_personal_scopes_still_need_a_login(self):
        response = self.client.get(reverse('explore_results'), {'scope': 'mine'})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_a_page_past_the_end_is_not_found(self):
        self.assertEqual(self.client.get(reverse('explore_results'), {'page': '99'}).status_code, 404)

    def test_explore_page_marks_the_parts_that_update(self):
        response = self.client.get(reverse('event_list'))

        self.assertContains(response, f'data-results-url="{reverse("explore_results")}"')
        self.assertContains(response, '<p data-explore-counts aria-live="polite">')
        self.assertContains(response, '<div data-map-overlay>')
        self.assertContains(response, '<form class="explore-filters"')
