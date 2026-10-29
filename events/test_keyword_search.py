"""US-2.7 (#70): one keyword search for parks (name, address, cross-streets) and events
(title, description, meeting spot), combined with the other Explore filters.

Every scenario is checked across the Events list tab, the Parks list tab and the map endpoint.
"""

import datetime
import re
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from parks.models import Borough, Park

from .explore import parse_filters
from .models import NYC_TZ, Activity, Event


class KeywordSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = get_user_model().objects.create_user('search-host', 'search@example.test')

        def park(code, name, borough, zipcode, address='', location=''):
            return Park.objects.create(
                gispropnum=code,
                name=name,
                borough=borough,
                zipcode=zipcode,
                address=address,
                location=location,
                latitude='40.700000',
                longitude='-73.950000',
            )

        cls.astoria = park(
            'Q1', 'Astoria Park', Borough.QUEENS, '11102', '19 St. & Shore Blvd', 'Shore Blvd, Ditmars Blvd, 19 St'
        )
        cls.hudson = park(
            'M1', 'Hudson River Park', Borough.MANHATTAN, '10014', 'Pier 40, 353 West St', 'West St & W Houston St'
        )
        cls.mccarren = park(
            'B1', 'McCarren Park', Borough.BROOKLYN, '11211', '776 Lorimer St', 'Bedford Ave, Lorimer St, N 12 St'
        )
        cls.mccarren_annex = park('B2', 'McCarren Annex', Borough.BROOKLYN, '11222', 'Nassau Ave')
        cls.mccarren_manhattan = park('M2', 'McCarren Playground', Borough.MANHATTAN, '10002', 'Essex St')
        cls.cooper = park(
            'B3', 'Cooper Park', Borough.BROOKLYN, '11211', 'Olive St & Maspeth Ave', 'Olive St, Sharon St'
        )

        day = timezone.now().astimezone(NYC_TZ).date() + datetime.timedelta(days=2)

        def event(title, p, description='Pickup game', meeting_spot=''):
            return Event.objects.create(
                organizer=cls.host,
                park=p,
                title=title,
                description=description,
                meeting_spot=meeting_spot,
                activity=Activity.OTHER,
                date=day,
                time=datetime.time(10),
            )

        event('Saturday Morning 5v5 Pickup', cls.cooper, description='Bring a light and a dark shirt.')
        event('Sunset jog', cls.mccarren, meeting_spot='Track entrance')
        event('Annex hoops', cls.mccarren_annex)
        event('Pickup in the LES', cls.mccarren_manhattan)
        event('Riverside picnic', cls.hudson, description='Frisbee golf near the skate park, then food.')
        event('Ditmars meetup', cls.astoria, meeting_spot='Steps under the Triborough Bridge')

    def results(self, params):
        """(event titles, {park name: event count}) from Events tab, Parks tab and map, asserting they agree."""
        events_tab = self.client.get(reverse('event_list'), {**params, 'view': 'list', 'tab': 'events'})
        parks_tab = self.client.get(reverse('event_list'), {**params, 'view': 'list', 'tab': 'parks'})
        map_data = self.client.get(reverse('event_map_api'), params).json()
        events = {event.title for event in events_tab.context['events']}
        parks = {park.name: park.event_count for park in parks_tab.context['parks']}
        self.assertEqual({p['name']: p['event_count'] for p in map_data['parks']}, parks)
        self.assertLessEqual({event.park.name for event in events_tab.context['events']}, set(parks))
        return events, parks

    # Scenario: Park name or address (each park field, case-insensitive)
    def test_park_name_is_case_insensitive_and_its_events_count(self):
        for query in ('astoria', 'ASTORIA', 'AsToRiA'):
            with self.subTest(q=query):
                events, parks = self.results({'q': query})
                self.assertEqual(parks, {'Astoria Park': 1})
                self.assertEqual(events, {'Ditmars meetup'}, "the park's event counts though its text doesn't match")

    def test_park_address_matches(self):
        events, parks = self.results({'q': 'Pier 40'})
        self.assertEqual(parks, {'Hudson River Park': 1})
        self.assertEqual(events, {'Riverside picnic'})

    def test_park_cross_streets_match(self):
        events, parks = self.results({'q': 'bedford ave'})
        self.assertEqual(parks, {'McCarren Park': 1})
        self.assertEqual(events, {'Sunset jog'})

    # Scenario: Event text (each event field)
    def test_event_title_brings_its_park_even_when_the_park_name_does_not_match(self):
        events, parks = self.results({'q': '5v5'})
        self.assertEqual(events, {'Saturday Morning 5v5 Pickup'})
        self.assertEqual(parks, {'Cooper Park': 1})

    def test_event_description_matches(self):
        events, parks = self.results({'q': 'FRISBEE GOLF'})
        self.assertEqual(events, {'Riverside picnic'})
        self.assertEqual(parks, {'Hudson River Park': 1})

    def test_event_meeting_spot_matches(self):
        events, parks = self.results({'q': 'triborough'})
        self.assertEqual(events, {'Ditmars meetup'})
        self.assertEqual(parks, {'Astoria Park': 1})

    def test_search_never_brings_in_unrelated_parks_or_events(self):
        events, parks = self.results({'q': 'mccarren'})
        self.assertEqual(set(parks), {'McCarren Park', 'McCarren Annex', 'McCarren Playground'})
        self.assertNotIn('Saturday Morning 5v5 Pickup', events)

    # Scenario: Combined with filters
    def test_keyword_ands_with_borough_and_zip_pills(self):
        events, parks = self.results({'borough': Borough.BROOKLYN, 'zip': '11211', 'q': 'McCarren'})
        self.assertEqual(parks, {'McCarren Park': 1})
        self.assertEqual(events, {'Sunset jog'})

        # The keyword also narrows events at a park the location filters keep.
        events, parks = self.results({'zip': '11211', 'q': '5v5'})
        self.assertEqual((events, parks), ({'Saturday Morning 5v5 Pickup'}, {'Cooper Park': 1}))

    def test_keyword_ands_with_pills_on_the_json_feed_too(self):
        data = self.client.get(reverse('event_list_api'), {'borough': 'B', 'zip': '11211', 'q': 'mccarren'}).json()
        self.assertEqual([e['title'] for e in data['results']], ['Sunset jog'])

    # Scenario: Clearing and no results
    def test_clearing_the_search_resets_to_the_other_filters(self):
        page = self.client.get(reverse('event_list'), {'view': 'list', 'borough': 'B', 'zip': '11211', 'q': 'McCarren'})
        pill = next(p['url'] for p in page.context['filter_pills'] if p['label'] == 'Search: McCarren')
        query = parse_qs(urlsplit(pill).query)
        self.assertNotIn('q', query)
        self.assertEqual((query['borough'], query['zip']), (['B'], ['11211']))
        events, parks = self.results(query)
        self.assertEqual(set(parks), {'McCarren Park', 'Cooper Park'})
        self.assertEqual(events, {'Sunset jog', 'Saturday Morning 5v5 Pickup'})

        # An emptied box submits q= and behaves exactly like no search.
        self.assertEqual(self.results({'borough': 'B', 'zip': '11211', 'q': '   '}), (events, parks))
        clear = page.context['clear_filters_url']
        self.assertNotIn('q', parse_qs(urlsplit(clear).query))

    def test_no_results_shows_the_us_2_5_empty_states(self):
        events_tab = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'events', 'q': 'curling'})
        self.assertEqual(events_tab.context['empty_state'], 'no_events')
        self.assertContains(events_tab, 'No upcoming events match your filters.')
        parks_tab = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'parks', 'q': 'curling'})
        self.assertEqual(parks_tab.context['empty_state'], 'no_matches')
        map_view = self.client.get(reverse('event_list'), {'view': 'map', 'q': 'curling'})
        self.assertEqual(map_view.context['map_empty_state'], 'no_matches')

    def test_park_with_matching_name_but_no_events_shows_parks_and_the_events_empty_state(self):
        Event.objects.filter(park=self.astoria).delete()
        events_tab = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'events', 'q': 'astoria'})
        self.assertEqual(events_tab.context['empty_state'], 'no_events')
        self.assertEqual(events_tab.context['park_total'], 1, '"Show parks instead" leads to Astoria Park')

    # Input handling and the rendered search bar
    def test_query_is_trimmed_and_capped_at_100_characters(self):
        self.assertEqual(parse_filters({'q': '  astoria  '})[1]['q'], 'astoria')
        self.assertEqual(self.results({'q': '  astoria  '})[1], {'Astoria Park': 1})
        self.assertEqual(len(parse_filters({'q': 'x' * 250})[1]['q']), 100)

    def test_search_bar_is_rendered_above_the_filters_and_keeps_its_value(self):
        html = self.client.get(reverse('event_list'), {'q': 'Pier 40', 'zip': '11211'}).content.decode()
        search_input = re.search(r'<input type="search" name="q"[^>]*>', html).group(0)
        self.assertIn('value="Pier 40"', search_input)
        self.assertIn('maxlength="100"', search_input)
        self.assertIn('Search parks and events', html)
        self.assertNotRegex(html, r'<input type="hidden" name="q"', 'only one q field is submitted')
        self.assertLess(html.index('name="q"'), html.index('class="explore-filter-row"'))
