"""US-4.9 (#118): the Phase checkboxes, their pills and URL state, the panel's hidden ended games and the date limit."""

import datetime
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import AnonymousUser
from django.http import QueryDict
from django.test import RequestFactory
from django.urls import reverse

from .explore import filter_params, parse_filters
from .hub import EXPLORE_SESSION_KEY, panel_param
from .models import Activity, SkillLevel
from .test_event_phases import TODAY, PhaseTestCase
from .views import explore_park_preview

LIMIT = TODAY - datetime.timedelta(days=30)


def days_ago(days):
    return (TODAY - datetime.timedelta(days=days)).isoformat()


def query(url):
    return parse_qs(urlsplit(unescape(url)).query)


class PhaseFilterTestCase(PhaseTestCase):
    def page(self, **params):
        response = self.client.get(reverse('event_list'), params)
        self.assertEqual(response.status_code, 200)
        return response

    def panel(self, **params):
        response = self.client.get(reverse('explore_park_preview', args=[self.park.pk]), params)
        self.assertEqual(response.status_code, 200)
        return response

    def panel_titles(self, response):
        return [event.title for event in response.context['park_events']]


class PhaseCheckboxTests(PhaseFilterTestCase):
    def setUp(self):
        super().setUp()
        self.event('Upcoming run', days=1)
        self.event('Live run', start='11:00', end='13:00')
        self.event('Ended run', days=-2)

    # Scenario: Default
    def test_upcoming_and_live_are_checked_by_default(self):
        response = self.page()

        self.assertEqual(response.context['filters']['phase'], [])
        self.assertEqual(response.context['filter_form']['phase'].value(), ['upcoming', 'live'])
        self.assertEqual(self.titles(), ['Live run', 'Upcoming run'])
        self.assertFalse(any(pill['label'].startswith('Phase') for pill in response.context['filter_pills']))

        self.client.force_login(self.host)
        mine = self.page(scope='mine')
        self.assertEqual(mine.context['filter_form']['phase'].value(), ['upcoming', 'live'])

    def test_checkboxes_replace_the_hidden_inputs_and_apply_right_away(self):
        html = self.page(phase='ended').content.decode()

        self.assertNotIn('type="hidden" name="phase"', html)
        boxes = re.findall(r'<input type="checkbox" name="phase"[^>]*>', html)
        self.assertEqual(len(boxes), 3)
        self.assertTrue(all('data-autosubmit' in box for box in boxes))
        self.assertEqual(['checked' in box for box in boxes], [False, False, True])
        self.assertIn('id="explore-phase-label"', html)

    def test_choosing_upcoming_and_live_is_stored_as_the_default(self):
        _, filters = parse_filters(QueryDict('phase=live&phase=upcoming'))
        self.assertEqual(filters['phase'], [])
        self.assertNotIn('phase=', filter_params(filters).urlencode())

        _, filters = parse_filters(QueryDict('phase=ended&phase=upcoming&phase=ended'))
        self.assertEqual(filters['phase'], ['upcoming', 'ended'])

    # Scenario: Choosing phases
    def test_chosen_phases_apply_to_the_map_both_tabs_and_the_panel(self):
        self.assertEqual(self.titles(phase='ended'), ['Ended run'])
        self.assertEqual(self.titles(phase=['ended', 'live']), ['Live run', 'Ended run'])

        parks = self.page(view='list', tab='parks', phase='ended')
        self.assertEqual([park.event_count for park in parks.context['parks']], [1])
        pins = self.client.get(reverse('event_map_api'), {'phase': ['ended', 'upcoming']}).json()['parks']
        self.assertEqual([pin['event_count'] for pin in pins], [2])
        self.assertEqual(self.panel_titles(self.panel(phase='live')), ['Live run'])

    # Scenario: Removing and invalid values
    def test_each_phase_gets_a_pill_and_clear_filters_returns_to_the_default(self):
        response = self.page(phase=['upcoming', 'ended'], sport=Activity.BASKETBALL)
        pills = {pill['label']: pill['url'] for pill in response.context['filter_pills']}

        self.assertEqual(query(pills['Phase: Ended'])['phase'], ['upcoming'])
        self.assertEqual(query(pills['Phase: Upcoming'])['phase'], ['ended'])
        self.assertNotIn('phase', query(response.context['clear_filters_url']))
        clear_link = re.search(r'class="explore-clear" href="([^"]+)"', response.content.decode()).group(1)
        self.assertNotIn('phase', query(clear_link))

        only_ended = self.page(phase='ended')
        pill = next(pill for pill in only_ended.context['filter_pills'] if pill['label'] == 'Phase: Ended')
        self.assertNotIn('phase', query(pill['url']))

    def test_invalid_phase_values_are_ignored(self):
        self.assertEqual(self.titles(phase=['bogus', 'ended']), ['Ended run'])
        self.assertEqual(self.titles(phase='bogus'), ['Live run', 'Upcoming run'])

    # Scenario: Shareable
    def test_the_selection_survives_the_map_see_all_and_back_to_results(self):
        response = self.page(phase='ended')

        self.assertEqual(query(response.context['map_url'])['phase'], ['ended'])
        self.assertEqual(parse_qs(self.client.session[EXPLORE_SESSION_KEY])['phase'], ['ended'])
        self.assertEqual(query(self.panel(phase='ended').context['events_url'])['phase'], ['ended'])


class HiddenEndedGamesTests(PhaseFilterTestCase):
    # Scenario: Hidden ended games
    def test_panel_says_how_many_ended_games_are_hidden_and_shows_them(self):
        self.event('Tonight')
        self.event('Ended yesterday', days=-1)
        self.event('Ended last week', days=-7)
        self.event('Ended long ago', days=-40)

        response = self.panel()
        self.assertEqual(self.panel_titles(response), ['Tonight'])
        self.assertContains(response, '1 matching event')
        self.assertContains(response, '2 ended games hidden')

        link = unescape(re.search(r'hidden – <a href="([^"]+)">show</a>', response.content.decode()).group(1))
        self.assertEqual(query(link)['phase'], ['upcoming', 'live', 'ended'])
        self.assertEqual(query(link)['panel'], [panel_param(self.park.pk)])
        shown = self.client.get(link)
        self.assertEqual(self.panel_titles(shown), ['Tonight', 'Ended yesterday', 'Ended last week'])
        self.assertNotContains(shown, 'hidden –')

    def test_one_hidden_game_and_none_when_ended_is_chosen(self):
        self.event('Ended yesterday', days=-1)

        self.assertContains(self.panel(), '1 ended game hidden')
        self.assertNotContains(self.panel(phase='ended'), 'hidden –')

    def test_hidden_count_follows_the_other_filters(self):
        self.event('Ended casual game', days=-1, skill_level=SkillLevel.BEGINNER)
        self.event('Ended competitive game', days=-1, skill_level=SkillLevel.COMPETITIVE)

        response = self.panel(skill=SkillLevel.COMPETITIVE)
        self.assertContains(response, '1 ended game hidden')
        link = re.search(r'hidden – <a href="([^"]+)">', response.content.decode()).group(1)
        self.assertEqual(query(link)['skill'], [SkillLevel.COMPETITIVE])

    def test_counting_hidden_games_adds_no_queries(self):
        self.event('Tonight')
        for days in range(1, 6):
            self.event(f'Ended {days} days ago', days=-days)
        request = RequestFactory().get(reverse('explore_park_preview', args=[self.park.pk]))
        request.user = AnonymousUser()

        with self.assertNumQueries(3):
            response = explore_park_preview(request, self.park.pk)
        self.assertContains(response, '5 ended games hidden')


class PhaseDateTests(PhaseFilterTestCase):
    # Scenario: Date limit
    def test_an_older_from_date_moves_up_to_the_limit(self):
        response = self.page(date_from=days_ago(45))

        self.assertEqual(response.context['filters']['date_from'], LIMIT)
        self.assertEqual(response.context['filter_form']['date_from'].value(), LIMIT.isoformat())
        self.assertIn(f'From: {LIMIT}', [pill['label'] for pill in response.context['filter_pills']])
        html = response.content.decode()
        for name in ('date_from', 'date_to'):
            picker = re.search(rf'<input type="date" name="{name}"[^>]*>', html).group(0)
            self.assertIn(f'min="{LIMIT.isoformat()}"', picker)

    def test_the_limit_is_exactly_30_days(self):
        _, filters = parse_filters(QueryDict(f'date_from={days_ago(30)}'))
        self.assertEqual(filters['date_from'], LIMIT)
        _, filters = parse_filters(QueryDict(f'date_from={days_ago(31)}'))
        self.assertEqual(filters['date_from'], LIMIT)
        _, filters = parse_filters(QueryDict(f'date_from={days_ago(29)}'))
        self.assertEqual(filters['date_from'], LIMIT + datetime.timedelta(days=1))

    # Scenario: My Events
    def test_my_events_reach_back_without_a_limit(self):
        self.event('Old game', days=-60)
        self.client.force_login(self.host)

        response = self.page(scope='mine', phase='ended', date_from=days_ago(90))
        self.assertEqual(response.context['filters']['date_from'], TODAY - datetime.timedelta(days=90))
        self.assertNotIn(
            'min=', re.search(r'<input type="date" name="date_from"[^>]*>', response.content.decode()).group(0)
        )
        self.assertEqual(self.titles(scope='mine', phase='ended', date_from=days_ago(90)), ['Old game'])

    # Scenario: Date range and phases
    def test_live_games_need_today_in_the_range_and_ended_games_the_window(self):
        self.event('Live run', start='11:00', end='13:00')
        self.event('Ended last week', days=-7)
        self.event('Ended long ago', days=-45)
        phases = ['live', 'ended']

        self.assertEqual(
            self.titles(phase=phases, date_from=days_ago(60), date_to=TODAY.isoformat()),
            ['Live run', 'Ended last week'],
        )
        self.assertEqual(self.titles(phase=phases, date_from=days_ago(10), date_to=days_ago(1)), ['Ended last week'])

    # Scenario: Today
    def test_today_shows_todays_games_in_the_chosen_phases_only(self):
        self.event('Morning game', start='08:00', end='09:00')
        self.event('Live run', start='11:00', end='13:00')
        self.event('Evening game')
        self.event('Tomorrow', days=1)

        self.assertEqual(self.titles(when='today'), ['Live run', 'Evening game'])
        self.assertEqual(self.titles(when='today', phase='ended'), ['Morning game'])
        self.assertEqual(self.titles(when='today', phase=['upcoming', 'ended']), ['Evening game', 'Morning game'])
