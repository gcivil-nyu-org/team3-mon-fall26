"""US-2.5 (#20): clear feedback when nothing matches or Explore data can't load.

Covers each server-rendered state: Events tab, Parks tab, map, events-only and no park data.
Map request/tile failures run client-side (events/js/explore.test.cjs) and were checked manually.
"""

import datetime
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from parks.models import Borough, Park

from .models import NYC_TZ, Activity, Event

NO_EVENTS = 'No upcoming events match your filters.'
NO_MATCHES = 'No parks or events match your filters. Try broadening your criteria.'
EVENTS_ONLY = "No events match. Turn off 'Only parks with events' to see parks where you could host one."
NO_PARK_DATA = 'Park facility details are temporarily unavailable. Please try again shortly.'
MAP_ERROR = "We couldn't load the map."


def empty_block(html, state):
    match = re.search(rf'<(div|li) class="explore-empty[^"]*" data-empty-state="{state}"[^>]*>(.*?)</\1>', html, re.S)
    return match and match.group(0)


def links(block):
    """{link text: parsed query} for every link in an empty-state block."""
    found = {}
    for href, text in re.findall(r'<a[^>]*href="([^"]*)"[^>]*>([^<]+)</a>', block):
        url = unescape(href)
        found[unescape(text).strip()] = (url, parse_qs(urlsplit(url).query))
    return found


class EmptyStateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('empty-host', 'empty@example.test', password='pw-12345!')
        cls.park = Park.objects.create(
            gispropnum='B1',
            name='McCarren Park',
            borough=Borough.BROOKLYN,
            zipcode='11211',
            latitude='40.721400',
            longitude='-73.951500',
        )
        cls.quiet = Park.objects.create(
            gispropnum='Q1',
            name='Quiet Park',
            borough=Borough.QUEENS,
            zipcode='11368',
            latitude='40.740000',
            longitude='-73.840000',
        )
        Event.objects.create(
            organizer=cls.user,
            park=cls.park,
            title='Saturday hoops',
            activity=Activity.BASKETBALL,
            description='Pickup',
            time=datetime.time(10),
            date=timezone.now().astimezone(NYC_TZ).date() + datetime.timedelta(days=2),
        )

    def page(self, **params):
        response = self.client.get(reverse('event_list'), params)
        self.assertEqual(response.status_code, 200)
        return response, response.content.decode()

    # Scenario: No matching events on the Events tab
    def test_events_tab_with_no_matching_events_offers_clear_parks_and_create(self):
        response, html = self.page(view='list', tab='events', borough=Borough.QUEENS, skill='beginner')
        self.assertEqual(response.context['empty_state'], 'no_events')
        block = empty_block(html, 'no_events')
        self.assertIn(NO_EVENTS, block)
        actions = links(block)
        self.assertEqual(list(actions), ['Clear filters', 'Show parks instead', 'Create Event'])

        _, clear = actions['Clear filters']
        self.assertEqual(clear, {'view': ['list'], 'tab': ['events'], 'scope': ['all']})
        _, parks = actions['Show parks instead']
        self.assertEqual(
            (parks['view'], parks['tab'], parks['borough'], parks['skill']),
            (['list'], ['parks'], ['Q'], ['beginner']),
            'Parks tab keeps the filters',
        )
        self.assertEqual(actions['Create Event'][0], reverse('event_create'))
        # The Parks tab it points to does have a park to show.
        self.assertEqual(self.client.get(reverse('event_list'), parks).context['park_total'], 1)

    def test_events_tab_with_results_shows_no_empty_state(self):
        response, html = self.page(view='list', tab='events')
        self.assertIsNone(response.context['empty_state'])
        self.assertNotIn('data-empty-state="no_events"', html)
        self.assertIn('Saturday hoops', html)

    # Scenario: No matching parks (Parks tab and map)
    def test_parks_tab_with_no_parks_and_no_events_offers_clear_filters(self):
        response, html = self.page(view='list', tab='parks', zip='10001')
        self.assertEqual((response.context['park_total'], response.context['event_total']), (0, 0))
        block = empty_block(html, 'no_matches')
        self.assertIn(NO_MATCHES, block)
        self.assertEqual(links(block)['Clear filters'][1], {'view': ['list'], 'tab': ['parks'], 'scope': ['all']})

    def test_map_with_no_parks_renders_the_overlay_the_map_shows_instead_of_pins(self):
        response, html = self.page(view='map', borough=Borough.STATEN_ISLAND)
        self.assertEqual(response.context['map_empty_state'], 'no_matches')
        shell = html.split('data-map-shell')[1].split('data-map-error')[0]
        self.assertIn('data-map-empty', shell)
        self.assertIn(NO_MATCHES, shell)
        self.assertEqual(self.client.get(reverse('event_map_api'), {'borough': 'R'}).json()['parks'], [])

    def test_map_with_parks_has_no_empty_overlay(self):
        response, html = self.page(view='map')
        self.assertIsNone(response.context['map_empty_state'])
        self.assertNotIn('data-map-empty', html)

    # Scenario: Only parks with events, but none
    def test_events_only_with_no_events_explains_how_to_see_hostable_parks(self):
        for view, tab in (('map', 'events'), ('list', 'parks')):
            with self.subTest(view=view, tab=tab):
                response, html = self.page(view=view, tab=tab, events_only='1', borough='Q', skill='beginner')
                block = empty_block(html, 'events_only_empty')
                self.assertIn(EVENTS_ONLY, block)
                url, query = links(block)["Turn off 'Only parks with events'"]
                self.assertNotIn('events_only', query)
                self.assertEqual((query['borough'], query['skill']), (['Q'], ['beginner']))
                after = self.client.get(reverse('event_list') + url)
                self.assertEqual(after.context['park_total'], 1, 'Quiet Park is there to host a game')
        self.assertIn(
            EVENTS_ONLY, empty_block(self.page(view='map', events_only='1', sport='soccer')[1], 'events_only_empty')
        )

    def test_events_only_with_matching_events_is_not_empty(self):
        response, _ = self.page(view='list', tab='parks', events_only='1')
        self.assertIsNone(response.context['empty_state'])
        self.assertEqual(response.context['park_total'], 1)

    def test_personal_scope_cannot_turn_off_events_only_so_it_gets_the_general_message(self):
        self.client.force_login(self.user)
        response, html = self.page(view='list', tab='parks', scope='hosting', sport='soccer')
        self.assertEqual(response.context['empty_state'], 'no_matches')
        self.assertNotIn("Turn off 'Only parks with events'", html)
        query = links(empty_block(html, 'no_matches'))['Clear filters'][1]
        self.assertEqual(query['scope'], ['hosting'], 'Clear filters keeps the Show choice')
        self.assertNotIn('sport', query)

    # Scenario: Park data unavailable
    def test_no_active_parks_shows_unavailable_message_instead_of_map_or_lists(self):
        Park.objects.update(is_retired=True)
        for view in ('map', 'list'):
            with self.subTest(view=view):
                response, html = self.page(view=view)
                self.assertFalse(response.context['has_park_data'])
                block = empty_block(html, 'no_park_data')
                self.assertIn(NO_PARK_DATA, block)
                self.assertIn('Try again', block)
                self.assertNotIn('data-map-shell', html)
                self.assertNotIn('explore-tabs', html)
                self.assertNotIn(NO_EVENTS, html)

    def test_no_park_data_wins_even_with_filters(self):
        Park.objects.update(is_retired=True)
        response, html = self.page(view='list', tab='parks', zip='11211', events_only='1')
        self.assertEqual(response.context['empty_state'], 'no_park_data')
        self.assertNotIn(EVENTS_ONLY, html)

    # Scenario: Map data fails to load (client-side; markup checked here)
    def test_map_view_ships_a_hidden_error_state_with_retry_and_switch_to_list(self):
        _, html = self.page(view='map', zip='11211')
        block = empty_block(html, 'map_error')
        self.assertRegex(block, r'data-map-error hidden')
        self.assertIn(MAP_ERROR, block)
        self.assertIn('data-map-retry', block)
        _, query = links(block)['Switch to List']
        self.assertEqual((query['view'], query['zip']), (['list'], ['11211']))
        # List view still works on its own.
        list_page = self.client.get(reverse('event_list'), query)
        self.assertEqual(list_page.status_code, 200)
        self.assertIn('Saturday hoops', list_page.content.decode())
