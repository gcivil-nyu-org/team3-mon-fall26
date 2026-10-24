"""Shared query and URL-state contract; map/list presentation is a follow-up."""
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


class ExploreFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = get_user_model().objects.create_user('map-host', 'map@example.test')
        cls.other = get_user_model().objects.create_user('other-host', 'other@example.test')
        cls.central = Park.objects.create(gispropnum='M1', name='Central Park', borough='M',
            address='Central Park West', zipcode='10029, 10128', latitude='40.781200', longitude='-73.966500')
        cls.mccarren = Park.objects.create(gispropnum='B1', name='McCarren Park', borough='B',
            zipcode='11211', latitude='40.721400', longitude='-73.951500')
        cls.unmapped = Park.objects.create(gispropnum='Q1', name='Unmapped Park', borough='Q', zipcode='11355')
        cls.retired = Park.objects.create(gispropnum='R1', name='Retired Park', borough='R',
            latitude=40, longitude=-74, is_retired=True)
        for index, key in enumerate(('tennis', 'track_and_field', 'nonregulation_soccer')):
            Facility.objects.create(system_id=f'M-{index}', park=cls.central, sports=key)
        Facility.objects.create(system_id='B-1', park=cls.mccarren, sports='basketball', feature_status='Inactive')


    def setUp(self):
        self.now = datetime.datetime(2026, 10, 23, 12, 0, tzinfo=NYC_TZ)
        self.clock = patch('django.utils.timezone.now', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)


    def event(self, park=None, **changes):
        data = dict(organizer=self.host, park=park or self.mccarren, title='Saturday pickup',
            activity='basketball', date=self.now.date()+datetime.timedelta(days=1),
            time=datetime.time(15), description='Friendly game', capacity=10)
        data.update(changes)
        return Event.objects.create(**data)


    def filters(self, query=''):
        return parse_filters(QueryDict(query))[1]


    def test_defaults_invalid_parameters_and_multivalues(self):
        form, f = parse_filters(QueryDict('view=nope&tab=nope&borough=ZZ&skill=expert&zip=11211&zip=x'
            '&sport=tennis&sport=unknown&sport=tennis&facility=tennis&facility=bad&park=-1'))
        self.assertEqual((f['view'], f['tab'], f['scope']), ('map','events','all'))
        self.assertEqual((f['zip'], f['sport'], f['facility']), (['11211'],['tennis'],['tennis']))
        self.assertFalse(f['borough'])
        self.assertIsNone(f['park'])
        self.assertEqual(filter_params(f).getlist('sport'), ['tennis'])


    def test_date_skill_change_counts_but_do_not_hide_parks(self):
        self.event(skill_level='all')
        self.event(skill_level='competitive')
        self.event(date=self.now.date()+datetime.timedelta(days=2))
        f = self.filters('date_from=2026-10-24&date_to=2026-10-24&skill=beginner')
        self.assertEqual(matching_events(f).count(), 1)
        self.assertEqual(dict(displayed_parks(f).values_list('pk','event_count')),
            {self.central.pk:0, self.mccarren.pk:1, self.unmapped.pk:0})


    def test_today_uses_new_york_date_at_utc_midnight(self):
        self.event(date=datetime.date(2026,10,23),time=datetime.time(22))
        self.event(date=datetime.date(2026,10,24),time=datetime.time(10))
        with patch('django.utils.timezone.now',return_value=datetime.datetime(2026,10,24,1,tzinfo=datetime.timezone.utc)):
            self.assertEqual(matching_events(self.filters('when=today')).count(),1)


    def test_invalid_and_reversed_ranges_report_and_ignore_both_ends(self):
        self.event()
        for query in ('date_from=bad&date_to=2020-01-01', 'date_from=2027-01-01&date_to=2026-01-01'):
            with self.subTest(query=query):
                form, f = parse_filters(QueryDict(query))
                self.assertTrue(form.errors)
                self.assertIsNone(f['date_from'])
                self.assertIsNone(f['date_to'])
                self.assertEqual(matching_events(f).count(),1)


    def test_event_keyword_hit_brings_its_park_and_no_duplicate_counts(self):
        self.event(self.central, title='Special hoops 5v5')
        f = self.filters('q=5v5&sport=basketball')
        self.assertEqual(list(displayed_parks(f).values_list('pk','event_count')), [(self.central.pk,1)])


    def test_zip_or_and_facilities_and_whole_token_matching(self):
        self.event(self.central)
        self.event()
        f = self.filters('zip=10128&zip=11211&facility=tennis&facility=track_and_field')
        self.assertEqual(list(matching_events(f).values_list('park_id',flat=True)),[self.central.pk])
        self.assertEqual(displayed_parks(self.filters('facility=regulation_soccer')).count(),0)


    def test_sport_facilities_active_and_sport_without_facilities(self):
        self.assertEqual(list(displayed_parks(self.filters('sport=tennis')).values_list('pk',flat=True)),[self.central.pk])
        self.assertEqual(displayed_parks(self.filters('sport=basketball')).count(),0)
        self.assertEqual(displayed_parks(self.filters('sport=running')).count(),3)


    def test_pagination_toggle_and_removable_pills_preserve_repeated_params(self):
        for i in range(23): self.event(title=f'Pickup {i}')
        page=self.client.get('/events/?view=list&zip=11211&zip=10029&sport=basketball&sport=tennis')
        self.assertEqual(len(page.context['events']),20)
        links=[unescape(href) for href in re.findall(r'href="([^"]+)"',page.content.decode())]
        next_link=next(link for link in links if 'page=2' in link)
        params=parse_qs(urlsplit(next_link).query)
        self.assertEqual(params['zip'],['11211','10029'])
        self.assertEqual(params['sport'],['basketball','tennis'])
        second=self.client.get('/events/'+next_link)
        self.assertEqual(len(second.context['events']),3)
        remove=next(p['url'] for p in page.context['filter_pills'] if p['label']=='Zip codes: 11211')
        self.assertEqual(parse_qs(urlsplit(remove).query)['zip'],['10029'])
        self.assertNotIn('page=',remove)


    def test_legacy_include_past_is_retired(self):
        self.event(date=self.now.date()-datetime.timedelta(days=1))
        self.assertEqual(self.client.get('/events/api/upcoming/?include_past=1').json()['count'],0)
        self.assertNotContains(self.client.get('/events/'),'Include past events')

    def test_park_parameter_ignores_unknown_retired_or_invalid_ids(self):
        for value in ('invalid', '-1', '99999999', str(self.retired.pk)):
            with self.subTest(value=value):
                self.assertIsNone(self.filters('park='+value)['park'])
                self.assertEqual(displayed_parks(self.filters('park='+value)).count(),3)
        self.assertEqual(list(displayed_parks(self.filters(f'park={self.central.pk}')).values_list('pk',flat=True)),
                         [self.central.pk])

    def test_feed_json_and_future_park_map_input_share_membership(self):
        first = self.event()
        second = self.event(self.unmapped)
        self.event(status='cancelled')
        self.event(status='removed')
        self.event(date=self.now.date(),time=datetime.time(9))
        f = self.filters('q=pickup&skill=beginner')
        page = self.client.get('/events/?q=pickup&skill=beginner')
        api = self.client.get('/events/api/upcoming/?q=pickup&skill=beginner').json()
        self.assertEqual(set(e.pk for e in page.context['events']),{first.pk,second.pk})
        self.assertEqual({e['id'] for e in api['results']},{first.pk,second.pk})
        counts = {p.pk:p.event_count for p in displayed_parks(f)}
        self.assertEqual(counts,{self.mccarren.pk:1,self.unmapped.pk:1})
        self.assertEqual(sum(counts.values()),api['count'])

    def test_scope_privacy_and_force_events_only(self):
        hosted = self.event(status='cancelled')
        attending = self.event(self.central,organizer=self.other,status='removed')
        RSVP.objects.create(user=self.host,event=attending)
        self.event(organizer=self.other)
        for path in ('/events/','/events/api/upcoming/'):
            response = self.client.get(path+'?scope=mine')
            self.assertEqual(response.status_code,302)
            self.assertIn('next=',response.url)
        self.client.force_login(self.host)
        f = self.filters('scope=mine')
        self.assertTrue(f['events_only'])
        self.assertEqual(set(matching_events(f,self.host).values_list('pk',flat=True)),{hosted.pk,attending.pk})
        self.assertEqual(matching_events(self.filters('scope=attending'),self.host).get(),attending)
        self.assertEqual(matching_events(self.filters('scope=hosting'),self.host).get(),hosted)
        self.assertEqual(displayed_parks(f,self.host).count(),2)
        page = self.client.get('/events/?scope=mine')
        self.assertContains(page,'<input type="checkbox" name="events_only" id="id_events_only" disabled checked>',html=True)

    def test_query_count_stays_constant_and_facility_matches_do_not_multiply_counts(self):
        self.event(self.central,activity='tennis')
        self.event(self.central,activity='tennis')
        f = self.filters('sport=tennis')
        with CaptureQueriesContext(connection) as before:
            parks = list(displayed_parks(f))
        Facility.objects.create(system_id='M-more',park=self.central,sports='tennis')
        Park.objects.bulk_create([Park(gispropnum=f'P{i}',name=f'Park {i}',borough='M') for i in range(30)])
        with CaptureQueriesContext(connection) as after:
            updated = list(displayed_parks(f))
        self.assertEqual(len(before),1)
        self.assertEqual(len(after),1)
        self.assertEqual([(p.pk,p.event_count) for p in parks],[(self.central.pk,2)])
        self.assertEqual([(p.pk,p.event_count) for p in updated],[(self.central.pk,2)])

    def test_switch_urls_keep_multivalues_and_reset_page(self):
        from django.template import Context, Template
        params = filter_params(self.filters('view=list&tab=parks&zip=11211&zip=10029&sport=tennis&sport=basketball'))
        params['page'] = '4'
        url = Template('{% load explore_tags %}{% explore_url view="map" tab="events" %}').render(
            Context({'explore_params':params}))
        parsed = parse_qs(urlsplit(unescape(url)).query)
        self.assertEqual(parsed['zip'],['11211','10029'])
        self.assertEqual(parsed['sport'],['tennis','basketball'])
        self.assertEqual(parsed['view'],['map'])
        self.assertEqual(parsed['tab'],['events'])
        self.assertNotIn('page',parsed)
