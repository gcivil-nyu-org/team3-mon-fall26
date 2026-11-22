"""US-4.10: phase-independent panel totals and phase-selected map pins."""

import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from django.urls import reverse

from parks.models import Facility, Park
from .hub import EXPLORE_SESSION_KEY
from .models import Event, EventStatus, NYC_TZ, RSVP
from .views import event_map_api, explore_park_preview


class MapPhaseCountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = get_user_model().objects.create_user('phase-map-host', 'phase-map@example.test')
        cls.player = get_user_model().objects.create_user('phase-map-player', 'phase-player@example.test')
        cls.park = Park.objects.create(
            gispropnum='PHASEMAP', name='Phase Park', borough='B', zipcode='11222', latitude=40.72, longitude=-73.95
        )
        Facility.objects.create(system_id='PHASE-BKB', park=cls.park, primary_sport='BKB', sports='basketball')

    def setUp(self):
        self.now = datetime.datetime(2026, 11, 14, 12, tzinfo=NYC_TZ)
        clock = patch('django.utils.timezone.now', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def event(self, phase='upcoming', **changes):
        times = {'upcoming': (14, 16), 'live': (11, 13), 'ended': (9, 10)}
        start, end = times[phase]
        fields = dict(
            organizer=self.host,
            park=self.park,
            title='Phase game',
            activity='basketball',
            date=self.now.date(),
            time=datetime.time(start),
            end_time=datetime.time(end),
        )
        fields.update(changes)
        return Event.objects.create(**fields)

    def panel(self, **params):
        return self.client.get(reverse('explore_park_preview', args=[self.park.pk]), params)

    def map_park(self, **params):
        data = self.client.get(reverse('event_map_api'), params).json()
        return next(park for park in data['parks'] if park['id'] == self.park.pk)

    def test_panel_counts_ignore_phase_but_cards_and_pins_follow_selection(self):
        for phase, count in (('live', 2), ('upcoming', 3), ('ended', 5)):
            for _ in range(count):
                self.event(phase)
        cases = [
            (None, 5, 2),
            (['upcoming'], 3, 0),
            (['live'], 2, 2),
            (['ended'], 5, 0),
            (['live', 'ended'], 7, 2),
            (['upcoming', 'live', 'ended'], 10, 2),
        ]
        for phases, total, live in cases:
            with self.subTest(phases=phases):
                params = {'phase': phases} if phases else {}
                response = self.panel(**params)
                self.assertContains(response, '2 live · 3 upcoming · 5 ended')
                self.assertEqual(response.context['panel_event_total'], total)
                park = self.map_park(**params)
                self.assertEqual((park['event_count'], park['live_count']), (total, live))

    def test_hidden_ended_link_updates_selection_without_changing_phase_totals(self):
        for phase in ('live', 'upcoming', 'ended'):
            self.event(phase, title='Wanted game')
            self.event(phase, title='Other game')
        response = self.panel(q='Wanted', phase='live', borough='B')
        totals = {'live': 1, 'upcoming': 1, 'ended': 1}
        self.assertEqual(response.context['phase_counts'], totals)
        self.assertEqual(response.context['hidden_ended_total'], 1)
        self.assertEqual(response.context['panel_event_total'], 1)

        shown = self.client.get(response.context['show_ended_url'])
        self.assertEqual(shown.status_code, 200)
        self.assertEqual(shown.context['selected_park'].pk, self.park.pk)
        self.assertEqual(shown.context['phase_counts'], totals)
        self.assertEqual(shown.context['hidden_ended_total'], 0)
        self.assertEqual(shown.context['panel_event_total'], 2)
        self.assertEqual(shown.context['filter_form']['phase'].value(), ['live', 'ended'])
        self.assertEqual(shown.context['filters']['q'], 'Wanted')
        pins = self.client.get(shown.context['map_url']).json()['parks']
        pin = next(park for park in pins if park['id'] == self.park.pk)
        self.assertEqual((pin['event_count'], pin['live_count']), (2, 1))
        self.assertEqual(parse_qs(urlsplit(shown.context['events_url']).query)['phase'], ['live', 'ended'])
        saved = parse_qs(self.client.session[EXPLORE_SESSION_KEY])
        self.assertEqual(saved['phase'], ['live', 'ended'])
        self.assertEqual(saved['q'], ['Wanted'])

    def test_counts_share_sport_keyword_skill_and_date_filters(self):
        for phase in ('live', 'upcoming', 'ended'):
            self.event(phase, title='Wanted game', skill_level='beginner')
            self.event(phase, title='Other keyword', skill_level='beginner')
            self.event(phase, title='Wanted tennis', activity='tennis', skill_level='beginner')
            self.event(phase, title='Wanted advanced', skill_level='competitive')
            self.event(phase, title='Wanted yesterday', date=self.now.date() - datetime.timedelta(days=1))
            self.event(phase, title='Wanted tomorrow', date=self.now.date() + datetime.timedelta(days=1))
        params = dict(
            q='Wanted',
            sport='basketball',
            skill='beginner',
            date_from=self.now.date().isoformat(),
            date_to=self.now.date().isoformat(),
            borough='B',
            zip='11222',
            facility='basketball',
        )
        response = self.panel(**params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['phase_counts'], {'live': 1, 'upcoming': 1, 'ended': 1})
        park = self.map_park(**params)
        self.assertEqual((park['event_count'], park['live_count']), (2, 1))
        # Today uses the same membership as an explicit date range.
        params.pop('date_from')
        params.pop('date_to')
        self.assertEqual(self.panel(when='today', **params).context['phase_counts'], response.context['phase_counts'])

    def test_park_filters_cannot_leak_other_parks_counts(self):
        other = Park.objects.create(gispropnum='PHASEOTHER', name='Other', borough='M', zipcode='10023')
        self.event('live', park=other)
        self.event('live')
        for params in ({'borough': 'M'}, {'zip': '10023'}, {'facility': 'tennis'}):
            with self.subTest(params=params):
                self.assertEqual(self.panel(**params).status_code, 404)
        self.assertEqual(self.panel().context['phase_counts']['live'], 1)

    def test_cancelled_removed_and_old_public_events_do_not_count(self):
        self.event('ended', date=self.now.date() - datetime.timedelta(days=30))
        self.event('ended', date=self.now.date() - datetime.timedelta(days=31))
        for status in (EventStatus.CANCELLED, EventStatus.REMOVED):
            for phase in ('live', 'upcoming', 'ended'):
                self.event(phase, status=status)
        response = self.panel(phase='ended')
        self.assertContains(response, '0 live · 0 upcoming · 1 ended')
        park = self.map_park(phase=['live', 'upcoming', 'ended'])
        self.assertEqual((park['event_count'], park['live_count']), (1, 0))

    def test_personal_phase_breakdown_preserves_scope_and_historical_access(self):
        self.client.force_login(self.player)
        self.event('live', organizer=self.player)
        attending = self.event('upcoming')
        RSVP.objects.create(user=self.player, event=attending)
        self.event('ended', organizer=self.player, date=self.now.date() - datetime.timedelta(days=31))
        self.event('live')  # Someone else's game is not in My Events.
        self.event('upcoming', organizer=self.player, status=EventStatus.CANCELLED)
        self.event('upcoming', organizer=self.player, status=EventStatus.REMOVED)
        expected = {'hosting': (1, 0, 1), 'attending': (0, 1, 0), 'mine': (1, 1, 1)}
        for scope, (live, upcoming, ended) in expected.items():
            with self.subTest(scope=scope):
                response = self.panel(scope=scope)
                self.assertEqual(response.context['phase_counts'], dict(live=live, upcoming=upcoming, ended=ended))
                self.assertEqual(self.map_park(scope=scope)['live_count'], live)

    def test_exact_start_and_end_boundaries_match_shared_phase_rules(self):
        self.event(time=datetime.time(12), end_time=datetime.time(13))
        self.event(time=datetime.time(11), end_time=datetime.time(12))
        response = self.panel()
        self.assertContains(response, '0 live · 1 upcoming · 1 ended')
        self.assertEqual(self.map_park()['live_count'], 0)

    def test_zero_counts_and_server_rendered_panel(self):
        self.assertContains(self.panel(), '0 live · 0 upcoming · 0 ended')
        self.assertEqual(self.map_park()['live_count'], 0)
        self.event('live')
        response = self.client.get(reverse('event_list'), {'panel': str(self.park.pk).zfill(2), 'view': 'map'})
        self.assertContains(response, '1 live · 0 upcoming · 0 ended')
        self.assertContains(response, 'Live events')
        self.assertContains(response, 'Other matching events')

    def test_panel_and_map_query_budgets_stay_constant_as_data_grows(self):
        request = RequestFactory().get('/events/')
        request.user = AnonymousUser()
        for count in (1, 8):
            for index in range(count):
                host = get_user_model().objects.create_user(f'phase-host-{count}-{index}')
                for phase in ('live', 'upcoming', 'ended'):
                    self.event(phase, organizer=host)
                park = Park.objects.create(
                    gispropnum=f'COUNT-{count}-{index}', name='More parks', latitude=40.7, longitude=-73.9
                )
                self.event('live', park=park)
            with self.assertNumQueries(3):
                response = explore_park_preview(request, self.park.pk)
            with self.assertNumQueries(2):
                map_response = event_map_api(request)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(map_response.status_code, 200)
