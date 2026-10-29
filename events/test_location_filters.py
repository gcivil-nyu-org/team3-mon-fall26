"""US-2.3 (#18): filter Explore Events by borough and by zip codes from a lookahead dropdown.

Every scenario is checked across all presentations that share the filter engine: the Events list
tab, the Parks list tab and the map endpoint (which the map fits its bounds to).
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

from .explore import zip_code_choices
from .models import NYC_TZ, Activity, Event, SkillLevel


class BoroughAndZipFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = get_user_model().objects.create_user('zip-host', 'zip@example.test')

        def park(code, name, borough, zipcode, **extra):
            return Park.objects.create(
                gispropnum=code,
                name=name,
                borough=borough,
                zipcode=zipcode,
                latitude='40.700000',
                longitude='-73.950000',
                **extra,
            )

        cls.heights = park('B1', 'Brooklyn Heights Promenade', Borough.BROOKLYN, '11201')
        cls.mccarren = park('B2', 'McCarren Park', Borough.BROOKLYN, '11211')
        # Parks on a zip boundary list several codes, with either separator used in NYC Open Data.
        cls.prospect = park('B3', 'Prospect Park', Borough.BROOKLYN, '11215, 11225')
        cls.bushwick = park('B4', 'Bushwick Inlet Park', Borough.BROOKLYN, '11222,11211')
        cls.central = park('M1', 'Central Park', Borough.MANHATTAN, '10029, 10128')
        cls.flushing = park('Q1', 'Flushing Meadows', Borough.QUEENS, '11368')
        cls.bad_zip = park('Q2', 'Unknown Zip Park', Borough.QUEENS, 'N/A, 1135')
        cls.retired = park('B9', 'Retired Brooklyn Park', Borough.BROOKLYN, '11211, 11299', is_retired=True)

        day = timezone.now().astimezone(NYC_TZ).date() + datetime.timedelta(days=2)
        for p in (cls.heights, cls.mccarren, cls.prospect, cls.bushwick, cls.central, cls.flushing, cls.retired):
            Event.objects.create(
                organizer=cls.host,
                park=p,
                title=f'Game at {p.name}',
                activity=Activity.BASKETBALL,
                date=day,
                time=datetime.time(18),
                skill_level=SkillLevel.ALL,
                description='Pickup run',
            )
        Event.objects.create(
            organizer=cls.host,
            park=cls.mccarren,
            title='Competitive tennis at McCarren',
            activity=Activity.TENNIS,
            date=day,
            time=datetime.time(9),
            skill_level=SkillLevel.COMPETITIVE,
            description='Ladder match',
        )

    def results(self, params):
        """(event titles, park names) from the Events tab, Parks tab and map, asserting all three agree."""
        events_tab = self.client.get(reverse('event_list'), {**params, 'view': 'list', 'tab': 'events'})
        parks_tab = self.client.get(reverse('event_list'), {**params, 'view': 'list', 'tab': 'parks'})
        map_data = self.client.get(reverse('event_map_api'), params).json()
        events = {event.title for event in events_tab.context['events']}
        parks = {park.name for park in parks_tab.context['parks']}
        self.assertEqual(
            {park['name'] for park in map_data['parks']}, parks, 'the map must show exactly the parks in the Parks tab'
        )
        self.assertEqual(events_tab.context['event_total'], len(events))
        # Events always sit at a displayed park.
        event_parks = {event.park.name for event in events_tab.context['events']}
        self.assertLessEqual(event_parks, parks)
        return events, parks

    # Scenario: Filter by borough
    def test_borough_shows_only_that_boroughs_parks_and_events_everywhere(self):
        events, parks = self.results({'borough': Borough.BROOKLYN})
        self.assertEqual(parks, {'Brooklyn Heights Promenade', 'McCarren Park', 'Prospect Park', 'Bushwick Inlet Park'})
        self.assertEqual(
            events,
            {
                'Game at Brooklyn Heights Promenade',
                'Game at McCarren Park',
                'Game at Prospect Park',
                'Game at Bushwick Inlet Park',
                'Competitive tennis at McCarren',
            },
        )

    def test_unknown_borough_is_ignored(self):
        _, parks = self.results({'borough': 'ZZ'})
        self.assertIn('Central Park', parks)
        self.assertIn('Flushing Meadows', parks)

    # Scenario: Zip code lookahead (options) + single zip
    def test_lookahead_options_are_distinct_five_digit_zips_of_active_parks(self):
        self.assertEqual(zip_code_choices(), ['10029', '10128', '11201', '11211', '11215', '11222', '11225', '11368'])
        page = self.client.get(reverse('event_list'), {'view': 'list'})
        widget_choices = [value for value, _ in page.context['filter_form'].fields['zip'].widget.choices]
        self.assertEqual(widget_choices, zip_code_choices())
        self.assertNotIn('11299', widget_choices, 'retired parks must not offer their zip codes')
        self.assertNotIn('1135', widget_choices)

    def test_single_zip_filters_parks_and_the_events_at_them(self):
        events, parks = self.results({'zip': '11211'})
        self.assertEqual(parks, {'McCarren Park', 'Bushwick Inlet Park'})
        self.assertEqual(
            events, {'Game at McCarren Park', 'Competitive tennis at McCarren', 'Game at Bushwick Inlet Park'}
        )

    # Scenario: Multiple zip codes (OR)
    def test_multiple_zips_match_parks_in_either(self):
        events, parks = self.results({'zip': ['11201', '11211']})
        self.assertEqual(parks, {'Brooklyn Heights Promenade', 'McCarren Park', 'Bushwick Inlet Park'})
        self.assertIn('Game at Brooklyn Heights Promenade', events)
        self.assertIn('Game at McCarren Park', events)

    def test_park_listing_several_zips_matches_each_of_them_but_not_partial_codes(self):
        for zipcode in ('11215', '11225'):
            with self.subTest(zip=zipcode):
                self.assertEqual(self.results({'zip': zipcode})[1], {'Prospect Park'})
        _, parks = self.results({'zip': '11222'})
        self.assertEqual(parks, {'Bushwick Inlet Park'}, 'no-space separator "11222,11211"')
        _, parks = self.results({'zip': ['10029', '10128']})
        self.assertEqual(parks, {'Central Park'}, 'a park in two selected zips appears once')

    def test_partial_or_malformed_zips_are_ignored_not_wildcards(self):
        _, unfiltered = self.results({})
        for value in ('112', '1121a', '112111', ' 11211x'):
            with self.subTest(zip=value):
                self.assertEqual(self.results({'zip': value})[1], unfiltered)
        # A bad value next to a good one keeps the good one.
        self.assertEqual(self.results({'zip': ['112', '11368']})[1], {'Flushing Meadows'})

    def test_retired_parks_and_their_events_never_match_a_zip(self):
        events, parks = self.results({'zip': ['11211', '11299']})
        self.assertNotIn('Retired Brooklyn Park', parks)
        self.assertNotIn('Game at Retired Brooklyn Park', events)

    # Borough + zip, and zip AND the other filters
    def test_borough_and_zip_combine_with_and(self):
        _, parks = self.results({'borough': Borough.BROOKLYN, 'zip': ['11211', '10029']})
        self.assertEqual(parks, {'McCarren Park', 'Bushwick Inlet Park'})
        events, parks = self.results({'borough': Borough.MANHATTAN, 'zip': '11211'})
        self.assertEqual((events, parks), (set(), set()))

    def test_zip_filters_events_through_their_park_and_ands_with_other_filters(self):
        events, _ = self.results({'zip': '11211', 'skill': SkillLevel.COMPETITIVE, 'sport': Activity.TENNIS})
        self.assertEqual(events, {'Competitive tennis at McCarren'})
        events, _ = self.results({'zip': '11211', 'q': 'ladder'})
        self.assertEqual(events, {'Competitive tennis at McCarren'})
        upcoming = self.client.get(reverse('event_list_api'), {'zip': '11201'}).json()
        self.assertEqual([e['title'] for e in upcoming['results']], ['Game at Brooklyn Heights Promenade'])

    # Scenario: Removing filters
    def test_pill_x_removes_only_that_filter_and_clear_removes_borough_and_zips(self):
        params = {'view': 'list', 'borough': Borough.BROOKLYN, 'zip': ['11201', '11211']}
        page = self.client.get(reverse('event_list'), params)
        pills = {pill['label']: pill['url'] for pill in page.context['filter_pills']}
        self.assertEqual(set(pills), {'Borough: Brooklyn', 'Zip codes: 11201', 'Zip codes: 11211'})

        query = parse_qs(urlsplit(pills['Zip codes: 11201']).query)
        self.assertEqual((query['zip'], query['borough']), (['11211'], ['B']))
        query = parse_qs(urlsplit(pills['Borough: Brooklyn']).query)
        self.assertNotIn('borough', query)
        self.assertEqual(query['zip'], ['11201', '11211'])
        self.assertEqual(self.results(query)[1], {'Brooklyn Heights Promenade', 'McCarren Park', 'Bushwick Inlet Park'})

        clear = unescape(re.search(r'class="explore-clear" href="([^"]+)"', page.content.decode()).group(1))
        query = parse_qs(urlsplit(clear).query)
        self.assertNotIn('borough', query)
        self.assertNotIn('zip', query)
        self.assertEqual(query.get('view'), ['list'], 'clearing filters keeps the current view')

    # Controls rendered for the lookahead widget and immediate apply
    def test_page_renders_borough_select_and_lookahead_zip_picker(self):
        page = self.client.get(reverse('event_list'), {'zip': '11211', 'borough': Borough.BROOKLYN})
        html = page.content.decode()
        zip_select = re.search(r'<select name="zip"[^>]*>', html).group(0)
        for attribute in ('multiple', 'data-lookahead', 'data-autosubmit', 'data-placeholder="Type a zip code"'):
            self.assertIn(attribute, zip_select)
        self.assertRegex(html, r'<option value="11211" selected>11211</option>')
        self.assertRegex(html, r'<select name="borough"[^>]*data-autosubmit')
        self.assertRegex(html, r'<option value="B" selected>Brooklyn</option>')
        self.assertIn('js/lookahead-select.js', html)
        self.assertIn('css/lookahead-select.css', html)
        # Zip sits in the main row next to borough, not hidden under "More filters".
        main_row = html.split('class="explore-filter-options"')[0]
        self.assertIn('name="zip"', main_row)

    def test_location_filters_sit_below_the_keyword_search_row(self):
        html = self.client.get(reverse('event_list')).content.decode()
        search_row, rest = html.split('class="explore-filter-row"', 1)
        self.assertRegex(search_row, r'<input type="search" name="q"')
        self.assertIn('name="borough"', rest)
        self.assertIn('name="zip"', rest)
