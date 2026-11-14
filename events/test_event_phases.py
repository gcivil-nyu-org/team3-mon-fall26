"""US-4.8 (#117): upcoming, live and ended games, the ?phase= filter, the 30-day window and what's refused."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from parks.models import Borough, Park

from .models import NYC_TZ, RSVP, Activity, Event, EventPhase, EventStatus, RSVPStatus

User = get_user_model()
NOW = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=NYC_TZ)
TODAY = NOW.date()
STARTED = 'This event has already started.'


class PhaseTestCase(TestCase):
    now = NOW

    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_user('phase-host', 'phase-host@example.test')
        cls.player = User.objects.create_user('phase-player', 'phase-player@example.test')
        cls.park = Park.objects.create(
            gispropnum='B058', name='McCarren Park', borough=Borough.BROOKLYN, latitude=40.72, longitude=-73.95
        )

    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def event(self, title, days=0, start='18:00', end='20:00', **changes):
        data = dict(
            organizer=self.host,
            park=self.park,
            title=title,
            activity=Activity.BASKETBALL,
            date=self.now.astimezone(NYC_TZ).date() + datetime.timedelta(days=days),
            time=datetime.time.fromisoformat(start),
            end_time=datetime.time.fromisoformat(end),
            description='Pickup run',
        )
        data.update(changes)
        return Event.objects.create(**data)

    def titles(self, **params):
        response = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'events', **params})
        self.assertEqual(response.status_code, 200)
        return [event.title for event in response.context['events']]


class PhaseRulesTests(PhaseTestCase):
    def test_each_phase_boundary(self):
        cases = {
            'starts right now': ('12:00', '13:00', EventPhase.UPCOMING),
            'starts tomorrow': ('09:00', '10:00', EventPhase.UPCOMING),
            'started a minute ago': ('11:59', '12:30', EventPhase.LIVE),
            'ends in a minute': ('10:00', '12:01', EventPhase.LIVE),
            'ended right now': ('10:00', '12:00', EventPhase.ENDED),
            'ended this morning': ('08:00', '09:00', EventPhase.ENDED),
            'ended yesterday': ('18:00', '20:00', EventPhase.ENDED),
        }
        days = {'starts tomorrow': 1, 'ended yesterday': -1}
        events = {
            name: self.event(name, days=days.get(name, 0), start=start, end=end)
            for name, (start, end, _) in cases.items()
        }
        querysets = {
            EventPhase.UPCOMING: Event.objects.upcoming(),
            EventPhase.LIVE: Event.objects.live(),
            EventPhase.ENDED: Event.objects.ended(),
        }
        for name, (_, _, phase) in cases.items():
            with self.subTest(name):
                event = events[name]
                self.assertEqual(event.phase, phase)
                self.assertEqual(event.has_started, phase != EventPhase.UPCOMING)
                self.assertEqual(event.has_ended, phase == EventPhase.ENDED)
                for other, queryset in querysets.items():
                    self.assertEqual(queryset.filter(pk=event.pk).exists(), other == phase, other)

    def test_cancelled_and_removed_games_are_in_no_phase(self):
        for status in (EventStatus.CANCELLED, EventStatus.REMOVED):
            with self.subTest(status):
                event = self.event(f'{status} live game', start='11:00', end='13:00', status=status)
                self.assertIsNone(event.phase)
                self.assertFalse(Event.objects.in_phases(list(EventPhase)).filter(pk=event.pk).exists())
        self.assertFalse(Event.objects.in_phases([]).exists())


class ExplorePhaseTests(PhaseTestCase):
    def test_default_shows_live_and_upcoming_but_not_ended(self):
        self.event('Tomorrow', days=1)
        self.event('On now', start='11:00', end='13:00')
        self.event('Over', start='08:00', end='09:00')

        self.assertEqual(self.titles(), ['On now', 'Tomorrow'])
        self.assertEqual(self.titles(phase=['bogus']), ['On now', 'Tomorrow'], 'Unknown phases are ignored')

    def test_phase_parameter_picks_phases(self):
        self.event('Tomorrow', days=1)
        self.event('On now', start='11:00', end='13:00')
        self.event('Over', start='08:00', end='09:00')

        self.assertEqual(self.titles(phase='ended'), ['Over'])
        self.assertEqual(self.titles(phase='live'), ['On now'])
        self.assertEqual(self.titles(phase=['upcoming', 'ended']), ['Tomorrow', 'Over'])

    def test_ended_games_stay_for_30_days(self):
        self.event('Ended 30 days ago', days=-30)
        self.event('Ended 31 days ago', days=-31)

        self.assertEqual(self.titles(phase='ended'), ['Ended 30 days ago'])
        map_data = self.client.get(reverse('event_map_api'), {'phase': 'ended'}).json()
        self.assertEqual(map_data['parks'][0]['event_count'], 1, 'Older games are left out of every count')
        panel = self.client.get(reverse('explore_park_preview', args=[self.park.pk]), {'phase': 'ended'})
        self.assertEqual(panel.context['panel_event_total'], 1)

    def test_sort_order_holds_across_pages(self):
        for day in range(1, 20):
            self.event(f'Upcoming {day:02}', days=day)
        self.event('Live late start', start='11:30', end='13:00')
        self.event('Live early start', start='10:00', end='13:00')
        for day in (3, 1, 2):
            self.event(f'Ended {day} days ago', days=-day)
        params = {'phase': ['upcoming', 'live', 'ended']}

        first, second = self.titles(**params), self.titles(page=2, **params)

        self.assertEqual((len(first), len(second)), (20, 4))
        expected = [
            'Live early start',
            'Live late start',
            *(f'Upcoming {day:02}' for day in range(1, 20)),
            'Ended 1 days ago',
            'Ended 2 days ago',
            'Ended 3 days ago',
        ]
        self.assertEqual(first + second, expected)

    def test_filter_form_keeps_the_phase(self):
        response = self.client.get(reverse('event_list'), {'phase': ['ended', 'live']})

        self.assertContains(response, '<input type="hidden" name="phase" value="ended">', html=True)
        self.assertContains(response, '<input type="hidden" name="phase" value="live">', html=True)

    def test_list_queries_dont_grow_with_more_games(self):
        def count_queries():
            with CaptureQueriesContext(connection) as queries:
                self.titles(phase=['upcoming', 'live', 'ended'])
            return len(queries)

        self.event('On now', start='11:00', end='13:00')
        self.event('Over', start='08:00', end='09:00')
        count_queries()  # The first visit also creates the session.
        few = count_queries()
        for day in range(1, 6):
            self.event(f'Upcoming {day}', days=day)
            self.event(f'Ended {day}', days=-day)

        self.assertEqual(count_queries(), few)


class NewYorkDateTests(PhaseTestCase):
    # 11:30 PM in New York is already the next day in UTC.
    now = datetime.datetime(2026, 11, 10, 23, 30, tzinfo=NYC_TZ)

    def test_phases_and_window_use_the_new_york_date(self):
        self.event('Tonight', start='20:00', end='22:00')
        self.event('Tomorrow morning', days=1, start='09:00', end='10:00')
        self.event('Ended 30 New York days ago', days=-30)
        self.event('Ended 31 New York days ago', days=-31)

        self.assertEqual(self.titles(), ['Tomorrow morning'])
        self.assertEqual(self.titles(phase='ended'), ['Tonight', 'Ended 30 New York days ago'])


class MyEventsPhaseTests(PhaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.player)

    def attend(self, event):
        RSVP.objects.create(event=event, user=self.player)
        return event

    def test_my_events_keep_old_ended_games(self):
        self.attend(self.event('Attended last month', days=-45))
        self.event('Hosted last month', days=-40, organizer=self.player)
        self.event('Someone else, last month', days=-45)

        self.assertEqual(self.titles(scope='mine'), [])
        self.assertEqual(self.titles(scope='mine', phase='ended'), ['Hosted last month', 'Attended last month'])
        old = Event.objects.get(title='Attended last month')
        self.assertContains(self.client.get(old.get_absolute_url()), 'Attended last month')

    def test_cancelled_games_show_with_upcoming_ones_only(self):
        self.attend(self.event('Cancelled game', days=2, status=EventStatus.CANCELLED))
        self.attend(self.event('Live game', start='11:00', end='13:00'))
        self.attend(self.event('Ended game', days=-1))

        self.assertEqual(self.titles(scope='attending'), ['Live game', 'Cancelled game'])
        self.assertEqual(self.titles(scope='attending', phase='live'), ['Live game'])
        self.assertEqual(self.titles(scope='attending', phase='ended'), ['Ended game'])


class FeedTests(PhaseTestCase):
    def test_json_feed_lists_upcoming_games_only(self):
        self.event('Tomorrow', days=1)
        self.event('On now', start='11:00', end='13:00')
        self.event('Over', start='08:00', end='09:00')

        for params in ({}, {'phase': ['live', 'ended']}):
            with self.subTest(params=params):
                results = self.client.get(reverse('event_list_api'), params).json()['results']
                self.assertEqual([(row['title'], row['end_time']) for row in results], [('Tomorrow', '20:00')])


class StartedGameTests(PhaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.player)
        self.live = self.event('On now', start='11:00', end='13:00')
        self.over = self.event('Over', start='08:00', end='09:00')

    def test_join_is_refused_once_a_game_starts(self):
        for event in (self.live, self.over):
            with self.subTest(event.title):
                response = self.client.post(reverse('event_rsvp', args=[event.pk]), follow=True)
                self.assertContains(response, STARTED)
                self.assertFalse(RSVP.objects.filter(event=event, user=self.player).exists())

    def test_cancel_rsvp_is_refused_once_a_game_starts(self):
        for event in (self.live, self.over):
            with self.subTest(event.title):
                RSVP.objects.create(event=event, user=self.player)
                response = self.client.post(reverse('event_rsvp_cancel', args=[event.pk]), follow=True)
                self.assertContains(response, STARTED)
                self.assertEqual(RSVP.objects.get(event=event, user=self.player).status, RSVPStatus.ACTIVE)


class DiscussionAfterTheEndTests(PhaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.player)

    def post(self, event):
        return self.client.post(
            reverse('event_message_post', args=[event.pk]),
            {'body': 'Good game!'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_posting_stays_open_while_live(self):
        live = self.event('On now', start='11:00', end='13:00')

        self.assertEqual(self.post(live).status_code, 200)
        self.assertContains(self.client.get(live.get_absolute_url()), 'name="body"')
        updates = self.client.get(reverse('event_discussion_updates', args=[live.pk])).json()
        self.assertTrue(updates['discussion_open'])

    def test_posting_closes_when_the_game_ends(self):
        over = self.event('Over', start='08:00', end='09:00')
        over.messages.create(author=self.host, body='Thanks for coming')

        response = self.post(over)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {'error': 'Discussion is closed for this event.'})
        self.assertEqual(over.messages.count(), 1)
        page = self.client.get(over.get_absolute_url())
        self.assertContains(page, 'Thanks for coming', msg_prefix='The thread stays readable')
        self.assertContains(page, 'Discussion is closed for this event.')
        self.assertNotContains(page, 'name="body"')
        updates = self.client.get(reverse('event_discussion_updates', args=[over.pk])).json()
        self.assertFalse(updates['discussion_open'])
