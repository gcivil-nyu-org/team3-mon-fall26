"""US-2.4 (#19): filter Explore Events by sport (OR) and by exact park facilities (AND).

Every scenario is checked across the Events list tab, the Parks list tab and the map endpoint.
"""

import datetime
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from parks.models import Borough, Facility, Park

from .models import ACTIVITY_FACILITY_KEYS, NYC_TZ, Activity, Event


class SportAndFacilityFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = get_user_model().objects.create_user('sport-host', 'sport@example.test')
        facility_ids = iter(range(1, 100))

        def park(code, name, *facilities):
            p = Park.objects.create(
                gispropnum=code,
                name=name,
                borough=Borough.BROOKLYN,
                zipcode='11211',
                latitude='40.700000',
                longitude='-73.950000',
            )
            for sports, status in facilities:
                Facility.objects.create(
                    system_id=f'{code}-{next(facility_ids)}', park=p, sports=sports, feature_status=status
                )
            return p

        active = 'Active'
        cls.hoops = park('B1', 'Hoops Park', ('basketball', active), ('basketball', active))  # two courts
        cls.wall = park('B2', 'Wall and Hoops Park', ('handball,basketball', active))  # comma list
        cls.closed = park('B3', 'Closed Courts Park', ('basketball', 'Inactive'))
        cls.regulation = park('B4', 'Regulation Pitch Park', ('regulation_soccer', active))
        cls.small_fields = park('B5', 'Small Fields Park', ('nonregulation_soccer', active))
        cls.tennis_track = park('B6', 'Tennis and Track Park', ('tennis', active), ('track_and_field', active))
        cls.tennis_only = park('B7', 'Tennis Only Park', ('tennis', active))
        cls.track_only = park('B8', 'Track Only Park', ('track_and_field', active), ('tennis', 'Inactive'))
        cls.lawn = park('B9', 'Open Lawn Park')  # no facilities at all

        day = timezone.now().astimezone(NYC_TZ).date() + datetime.timedelta(days=3)

        def event(title, p, activity):
            return Event.objects.create(
                organizer=cls.host,
                park=p,
                title=title,
                activity=activity,
                date=day,
                time=datetime.time(10),
                description='Pickup',
            )

        event('Hoops run', cls.hoops, Activity.BASKETBALL)
        event('Lawn hoops', cls.lawn, Activity.BASKETBALL)  # basketball at a park with no court
        event('Small-sided soccer', cls.small_fields, Activity.SOCCER)
        event('Lawn jog', cls.lawn, Activity.RUNNING)
        event('Doubles', cls.tennis_track, Activity.TENNIS)
        event('Singles', cls.tennis_only, Activity.TENNIS)
        event('Track yoga', cls.track_only, Activity.YOGA)
        event('Wall ball', cls.wall, Activity.HANDBALL)

        cls.all_parks = {
            p.name
            for p in (
                cls.hoops,
                cls.wall,
                cls.closed,
                cls.regulation,
                cls.small_fields,
                cls.tennis_track,
                cls.tennis_only,
                cls.track_only,
                cls.lawn,
            )
        }

    def results(self, params):
        """(event titles, park names) from the Events tab, Parks tab and map, asserting all three agree."""
        events_tab = self.client.get(reverse('event_list'), {**params, 'view': 'list', 'tab': 'events'})
        parks_tab = self.client.get(reverse('event_list'), {**params, 'view': 'list', 'tab': 'parks'})
        map_data = self.client.get(reverse('event_map_api'), params).json()
        events = [event.title for event in events_tab.context['events']]
        parks = [park.name for park in parks_tab.context['parks']]
        self.assertEqual(len(parks), len(set(parks)), 'a park must never be listed twice')
        self.assertEqual(len(events), len(set(events)), 'an event must never be listed twice')
        self.assertEqual({park['name'] for park in map_data['parks']}, set(parks))
        self.assertLessEqual({event.park.name for event in events_tab.context['events']}, set(parks))
        return set(events), set(parks)

    def test_activity_to_facility_mapping_used_by_the_filter(self):
        self.assertEqual(ACTIVITY_FACILITY_KEYS[Activity.SOCCER], ('regulation_soccer', 'nonregulation_soccer'))
        self.assertNotIn(Activity.RUNNING, ACTIVITY_FACILITY_KEYS)

    # Scenario: Sport lookahead (select "Basketball")
    def test_single_sport_matches_its_events_and_parks_with_an_active_facility_or_a_matching_event(self):
        events, parks = self.results({'sport': Activity.BASKETBALL})
        self.assertEqual(events, {'Hoops run', 'Lawn hoops'})
        self.assertEqual(parks, {'Hoops Park', 'Wall and Hoops Park', 'Open Lawn Park'})
        self.assertNotIn('Closed Courts Park', parks, 'inactive facilities do not count')

    # Scenario: Multiple sports (OR)
    def test_multiple_sports_are_ored_for_events_and_facilities(self):
        events, parks = self.results({'sport': [Activity.BASKETBALL, Activity.SOCCER]})
        self.assertEqual(events, {'Hoops run', 'Lawn hoops', 'Small-sided soccer'})
        self.assertEqual(
            parks, {'Hoops Park', 'Wall and Hoops Park', 'Open Lawn Park', 'Regulation Pitch Park', 'Small Fields Park'}
        )

    # Scenario: Sport without a facility type
    def test_sport_without_a_facility_type_counts_its_events_and_does_not_narrow_parks(self):
        events, parks = self.results({'sport': Activity.RUNNING})
        self.assertEqual(events, {'Lawn jog'})
        self.assertEqual(parks, self.all_parks)
        counts = {
            p.name: p.event_count
            for p in self.client.get(
                reverse('event_list'), {'sport': Activity.RUNNING, 'view': 'list', 'tab': 'parks'}
            ).context['parks']
        }
        self.assertEqual(counts['Open Lawn Park'], 1)
        self.assertEqual(counts['Hoops Park'], 0)

    def test_mixing_a_facility_sport_with_one_that_has_no_facility_keeps_all_parks(self):
        # OR semantics: Running can be played at any park, so the union of parks is every park.
        events, parks = self.results({'sport': [Activity.BASKETBALL, Activity.RUNNING]})
        self.assertEqual(events, {'Hoops run', 'Lawn hoops', 'Lawn jog'})
        self.assertEqual(parks, self.all_parks)

    def test_events_only_with_a_sport_keeps_just_parks_hosting_that_sport(self):
        _, parks = self.results({'sport': Activity.RUNNING, 'events_only': '1'})
        self.assertEqual(parks, {'Open Lawn Park'})

    # Scenario: Facilities filter (AND)
    def test_facilities_are_anded_and_events_are_filtered_through_their_park(self):
        events, parks = self.results({'facility': ['tennis', 'track_and_field']})
        self.assertEqual(parks, {'Tennis and Track Park'})
        self.assertEqual(events, {'Doubles'})
        self.assertNotIn('Track Only Park', parks, 'its tennis court is inactive')

        events, parks = self.results({'facility': 'tennis'})
        self.assertEqual(parks, {'Tennis and Track Park', 'Tennis Only Park'})
        self.assertEqual(events, {'Doubles', 'Singles'})

    def test_facility_filter_excludes_events_at_parks_without_that_facility(self):
        events, parks = self.results({'facility': 'basketball'})
        self.assertEqual(parks, {'Hoops Park', 'Wall and Hoops Park'})
        # Facilities filter parks; every event at those parks counts, whatever its sport.
        self.assertEqual(events, {'Hoops run', 'Wall ball'})
        self.assertNotIn('Lawn hoops', events, 'a basketball event at a park without a court is excluded')

    def test_facility_token_inside_a_comma_separated_list_matches(self):
        events, parks = self.results({'facility': ['handball', 'basketball']})
        self.assertEqual(parks, {'Wall and Hoops Park'})
        self.assertEqual(events, {'Wall ball'})

    def test_sport_and_facility_combine_with_and(self):
        events, parks = self.results({'sport': Activity.TENNIS, 'facility': 'track_and_field'})
        self.assertEqual(events, {'Doubles'})
        self.assertEqual(parks, {'Tennis and Track Park'})

    # Scenario: Similar facility names don't cross-match
    def test_regulation_and_non_regulation_soccer_do_not_cross_match(self):
        _, parks = self.results({'facility': 'regulation_soccer'})
        self.assertEqual(parks, {'Regulation Pitch Park'})
        _, parks = self.results({'facility': 'nonregulation_soccer'})
        self.assertEqual(parks, {'Small Fields Park'})

    def test_a_park_with_several_matching_facilities_appears_once_with_correct_counts(self):
        page = self.client.get(reverse('event_list'), {'facility': 'basketball', 'view': 'list', 'tab': 'parks'})
        counts = {p.name: p.event_count for p in page.context['parks']}
        self.assertEqual(counts, {'Hoops Park': 1, 'Wall and Hoops Park': 1})

    def test_unknown_sports_and_facilities_are_ignored(self):
        events, parks = self.results({'sport': ['quidditch', Activity.SOCCER], 'facility': ['moon_court']})
        self.assertEqual(events, {'Small-sided soccer'})
        self.assertEqual(parks, {'Regulation Pitch Park', 'Small Fields Park'})

    # Lookahead controls, pills and removal
    def test_page_renders_sport_and_facility_lookaheads_and_pills(self):
        page = self.client.get(
            reverse('event_list'),
            {'view': 'list', 'sport': [Activity.BASKETBALL, Activity.SOCCER], 'facility': 'tennis'},
        )
        html = page.content.decode()
        for name in ('sport', 'facility'):
            with self.subTest(field=name):
                select = re.search(rf'<select name="{name}"[^>]*>', html).group(0)
                for attribute in ('multiple', 'data-lookahead', 'data-autosubmit', 'data-placeholder='):
                    self.assertIn(attribute, select)
        self.assertRegex(html, r'<option value="basketball" selected>Basketball</option>')
        self.assertRegex(html, r'<option value="track_and_field">Track &amp; Field</option>')
        main_row = html.split('class="explore-filter-options"')[0]
        self.assertIn('name="sport"', main_row)
        self.assertNotIn('name="facility"', main_row)
        self.assertRegex(
            html, r'<details class="explore-more-filters" open>', 'More filters stays open while a facility is selected'
        )
        self.assertNotIn('Ctrl (Windows)', html)

        pills = {pill['label']: pill['url'] for pill in page.context['filter_pills']}
        self.assertLessEqual({'Sport: Basketball', 'Sport: Soccer', 'Facilities: Tennis'}, set(pills))
        events, _ = self.results(dict(re.findall(r'([^?&=]+)=([^&]*)', pills['Sport: Basketball'])))
        self.assertNotIn('Hoops run', events)
