"""Membership, count and URL-state contracts shared by all Explore presentations."""

import datetime
from html import unescape
import re
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.db import connection
from django.http import QueryDict
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from parks.models import Facility, Park
from .explore import displayed_parks, filter_params, matching_events, parse_filters
from .models import Event, NYC_TZ, RSVP


class ExploreTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = get_user_model().objects.create_user('map-host', 'map@example.test')
        cls.other = get_user_model().objects.create_user('other-host', 'other@example.test')
        cls.central = Park.objects.create(
            gispropnum='M1',
            name='Central Park',
            borough='M',
            address='Central Park West',
            zipcode='10029, 10128',
            latitude='40.781200',
            longitude='-73.966500',
        )
        cls.mccarren = Park.objects.create(
            gispropnum='B1',
            name='McCarren Park',
            borough='B',
            zipcode='11211',
            latitude='40.721400',
            longitude='-73.951500',
        )
        cls.unmapped = Park.objects.create(gispropnum='Q1', name='Unmapped Park', borough='Q', zipcode='11355')
        cls.retired = Park.objects.create(
            gispropnum='R1', name='Retired Park', borough='R', latitude=40, longitude=-74, is_retired=True
        )
        for index, key in enumerate(('tennis', 'track_and_field', 'nonregulation_soccer')):
            Facility.objects.create(system_id=f'M-{index}', park=cls.central, sports=key)
        Facility.objects.create(system_id='B-1', park=cls.mccarren, sports='basketball', feature_status='Inactive')

    def setUp(self):
        self.now = datetime.datetime(2026, 10, 23, 12, 0, tzinfo=NYC_TZ)
        self.clock = patch('django.utils.timezone.now', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def event(self, park=None, **changes):
        data = dict(
            organizer=self.host,
            park=park or self.mccarren,
            title='Saturday pickup',
            activity='basketball',
            date=self.now.date() + datetime.timedelta(days=1),
            time=datetime.time(15),
            description='Friendly game',
            capacity=10,
        )
        data.update(changes)
        return Event.objects.create(**data)

    def filters(self, query=''):
        return parse_filters(QueryDict(query))[1]

    def test_defaults_invalid_parameters_and_multivalues(self):
        form, f = parse_filters(
            QueryDict(
                'view=nope&tab=nope&borough=ZZ&skill=expert&zip=11211&zip=x'
                '&sport=tennis&sport=unknown&sport=tennis&facility=tennis&facility=bad&park=-1'
            )
        )
        self.assertEqual((f['view'], f['tab'], f['scope']), ('map', 'events', 'all'))
        self.assertEqual((f['zip'], f['sport'], f['facility']), (['11211'], ['tennis'], ['tennis']))
        self.assertFalse(f['borough'])
        self.assertIsNone(f['park'])
        self.assertEqual(filter_params(f).getlist('sport'), ['tennis'])

    def test_map_excludes_retired_missing_lat_or_missing_lon(self):
        self.central.longitude = None
        self.central.save()
        self.event()
        self.event(self.unmapped)
        self.event(self.central)
        self.event(self.retired)
        data = self.client.get(reverse('event_map_api')).json()
        self.assertEqual([p['id'] for p in data['parks']], [self.mccarren.pk])
        self.assertEqual(data['unmapped_event_count'], 2)
        listing = self.client.get('/events/?view=list&tab=parks')
        self.assertContains(listing, 'Unmapped Park')
        self.assertNotContains(listing, 'Retired Park')

    def test_map_counts_and_events_only(self):
        for _ in range(3):
            self.event()
        self.event(self.unmapped)
        self.event(status='cancelled')
        self.event(status='removed')
        self.event(date=self.now.date(), time=datetime.time(10))
        data = self.client.get('/events/api/map/').json()
        counts = {p['id']: p['event_count'] for p in data['parks']}
        self.assertEqual(counts, {self.mccarren.pk: 3, self.central.pk: 0})
        self.assertEqual(sum(counts.values()) + data['unmapped_event_count'], 4)
        only = self.client.get('/events/api/map/?events_only=1').json()
        self.assertEqual([p['id'] for p in only['parks']], [self.mccarren.pk])

    def test_date_skill_change_counts_but_do_not_hide_parks(self):
        self.event(skill_level='all')
        self.event(skill_level='competitive')
        self.event(date=self.now.date() + datetime.timedelta(days=2))
        f = self.filters('date_from=2026-10-24&date_to=2026-10-24&skill=beginner')
        self.assertEqual(matching_events(f).count(), 1)
        self.assertEqual(
            dict(displayed_parks(f).values_list('pk', 'event_count')),
            {self.central.pk: 0, self.mccarren.pk: 1, self.unmapped.pk: 0},
        )

    def test_today_uses_new_york_date_at_utc_midnight(self):
        self.event(date=datetime.date(2026, 10, 23), time=datetime.time(22))
        self.event(date=datetime.date(2026, 10, 24), time=datetime.time(10))
        utc_midnight = datetime.datetime(2026, 10, 24, 1, tzinfo=datetime.timezone.utc)
        with patch('django.utils.timezone.now', return_value=utc_midnight):
            self.assertEqual(matching_events(self.filters('when=today')).count(), 1)

    def test_invalid_and_reversed_ranges_report_and_ignore_both_ends(self):
        self.event()
        for query in ('date_from=bad&date_to=2020-01-01', 'date_from=2027-01-01&date_to=2026-01-01'):
            with self.subTest(query=query):
                form, f = parse_filters(QueryDict(query))
                self.assertTrue(form.errors)
                self.assertIsNone(f['date_from'])
                self.assertIsNone(f['date_to'])
                self.assertEqual(matching_events(f).count(), 1)

    def test_event_keyword_hit_brings_its_park_and_no_duplicate_counts(self):
        self.event(self.central, title='Special hoops 5v5')
        f = self.filters('q=5v5&sport=basketball')
        self.assertEqual(list(displayed_parks(f).values_list('pk', 'event_count')), [(self.central.pk, 1)])

    def test_zip_or_and_facilities_and_whole_token_matching(self):
        self.event(self.central)
        self.event()
        f = self.filters('zip=10128&zip=11211&facility=tennis&facility=track_and_field')
        self.assertEqual(list(matching_events(f).values_list('park_id', flat=True)), [self.central.pk])
        self.assertEqual(displayed_parks(self.filters('facility=regulation_soccer')).count(), 0)

    def test_sport_facilities_active_and_sport_without_facilities(self):
        self.assertEqual(
            list(displayed_parks(self.filters('sport=tennis')).values_list('pk', flat=True)), [self.central.pk]
        )
        self.assertEqual(displayed_parks(self.filters('sport=basketball')).count(), 0)
        self.assertEqual(displayed_parks(self.filters('sport=running')).count(), 3)

    def test_scopes_keep_private_results_private_and_force_events_only(self):
        mine = self.event(status='cancelled')
        attended = self.event(self.central, organizer=self.other, status='removed')
        RSVP.objects.create(user=self.host, event=attended)
        self.event(organizer=self.other)
        for path in ('/events/', '/events/api/map/', '/events/api/upcoming/'):
            response = self.client.get(path + '?scope=mine')
            self.assertEqual(response.status_code, 302)
            self.assertIn('next=', response.url)
        self.client.force_login(self.host)
        f = self.filters('scope=mine')
        self.assertTrue(f['events_only'])
        self.assertEqual(set(matching_events(f, self.host).values_list('pk', flat=True)), {mine.pk, attended.pk})
        self.assertEqual(matching_events(self.filters('scope=attending'), self.host).get(), attended)
        self.assertEqual(matching_events(self.filters('scope=hosting'), self.host).get(), mine)
        mine_parks = self.client.get('/events/api/map/?scope=mine').json()['parks']
        self.assertEqual(sum(p['event_count'] for p in mine_parks), 2)
        personal_page = self.client.get('/events/?scope=mine')
        self.assertContains(
            personal_page, '<input type="checkbox" name="events_only" id="id_events_only" disabled checked>', html=True
        )

    def test_views_and_map_agree_for_same_filter_state(self):
        self.event()
        self.event(self.central)
        query = 'q=pickup&zip=11211&zip=10128&skill=beginner'
        data = self.client.get('/events/api/map/?' + query).json()
        for view, tab in (('map', 'events'), ('list', 'events'), ('list', 'parks')):
            response = self.client.get(f'/events/?{query}&view={view}&tab={tab}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['event_total'], 2)
            self.assertEqual(response.context['park_total'], len(data['parks']))
            self.assertEqual(response.context['explore_params'].getlist('zip'), ['11211', '10128'])

    def test_pagination_toggle_and_removable_pills_preserve_repeated_params(self):
        for i in range(23):
            self.event(title=f'Pickup {i}')
        page = self.client.get('/events/?view=list&zip=11211&zip=10029&sport=basketball&sport=tennis')
        self.assertEqual(len(page.context['events']), 20)
        links = [unescape(href) for href in re.findall(r'href="([^"]+)"', page.content.decode())]
        next_link = next(link for link in links if 'page=2' in link)
        params = parse_qs(urlsplit(next_link).query)
        self.assertEqual(params['zip'], ['11211', '10029'])
        self.assertEqual(params['sport'], ['basketball', 'tennis'])
        second = self.client.get('/events/' + next_link)
        self.assertEqual(len(second.context['events']), 3)
        remove = next(p['url'] for p in page.context['filter_pills'] if p['label'] == 'Zip codes: 11211')
        self.assertEqual(parse_qs(urlsplit(remove).query)['zip'], ['10029'])
        self.assertNotIn('page=', remove)

    def test_map_unpaginated_and_query_count_does_not_grow_with_parks(self):
        self.event()
        with CaptureQueriesContext(connection) as before:
            self.client.get('/events/api/map/')
        Park.objects.bulk_create(
            [Park(gispropnum=f'NEW{i}', name=f'New {i}', borough='M', latitude=40, longitude=-73) for i in range(25)]
        )
        with CaptureQueriesContext(connection) as after:
            data = self.client.get('/events/api/map/?page=2').json()
        self.assertEqual(len(before), len(after))
        self.assertLessEqual(len(after), 2)
        self.assertEqual(len(data['parks']), 27)

    def test_preview_shares_filters_and_escapes_park_and_event_names(self):
        self.central.name = '<script>bad()</script>'
        self.central.save()
        self.event(self.central, title='<img src=x onerror=alert(1)>')
        url = reverse('explore_park_preview', args=[self.central.pk])
        response = self.client.get(url)
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<img src=x')
        self.assertEqual(self.client.get(url + '?borough=B').status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_no_js_and_failed_map_have_server_rendered_list(self):
        self.event()
        response = self.client.get('/events/')
        self.assertContains(response, 'The interactive map needs JavaScript.')
        self.assertContains(response, 'Saturday pickup')
        self.assertContains(response, 'Switch to List')
        self.assertContains(response, 'data-list-fallback')

    def test_legacy_include_past_is_retired(self):
        self.event(date=self.now.date() - datetime.timedelta(days=1))
        self.assertEqual(self.client.get('/events/api/upcoming/?include_past=1').json()['count'], 0)
        self.assertNotContains(self.client.get('/events/'), 'Include past events')

    def test_parks_tab_orders_event_parks_first_and_paginates_with_repeated_filters(self):
        self.event()
        self.event()
        Park.objects.bulk_create(
            [Park(gispropnum=f'PAGE{i}', name=f'Nearby {i:02}', borough='B', zipcode='11222') for i in range(23)]
        )
        response = self.client.get('/events/?view=list&tab=parks&zip=11211&zip=11222')
        self.assertEqual(response.context['park_total'], 24)
        self.assertEqual(len(response.context['parks']), 20)
        self.assertEqual(response.context['parks'][0].pk, self.mccarren.pk)
        self.assertContains(response, '2 matching events')
        links = [unescape(href) for href in re.findall(r'href="([^"]+)"', response.content.decode())]
        next_url = next(link for link in links if 'page=2' in link)
        params = parse_qs(urlsplit(next_url).query)
        self.assertEqual(params['zip'], ['11211', '11222'])
        self.assertEqual(params['tab'], ['parks'])
        self.assertEqual(len(self.client.get('/events/' + next_url).context['parks']), 4)

    def test_empty_parks_clear_link_removes_date_and_skill_filters_too(self):
        self.client.force_login(self.host)
        response = self.client.get(
            '/events/?view=list&tab=parks&scope=mine&q=missing&skill=beginner&date_from=2030-01-01'
        )
        self.assertContains(response, 'No parks or events match your filters.')
        links = [unescape(href) for href in re.findall(r'href="([^"]+)"', response.content.decode())]
        clear_urls = [
            url
            for url in links
            if url.startswith('?')
            and 'q=' not in url
            and 'skill=' not in url
            and 'date_from=' not in url
            and 'scope=mine' in url
        ]
        self.assertTrue(clear_urls)
        empty = response.content.decode().split('No parks or events match your filters.')[1]
        clear = unescape(re.search(r'href="([^"]+)"', empty).group(1))
        self.assertEqual(parse_qs(urlsplit(clear).query), {'scope': ['mine'], 'view': ['list'], 'tab': ['parks']})
