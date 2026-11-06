"""US-4.3 (#31): simultaneous joins never overfill an event (PostgreSQL only).

SQLite ignores row locks, so these tests skip there; run them against PostgreSQL, e.g. by pointing
DATABASES at a local Postgres. Each test makes the first request pause inside its write, so the
second request always overlaps it: without the event lock both see a free spot, with it the second
waits and then sees the event as it really is.
"""

import datetime
import threading
import time
import unittest
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from parks.models import Borough, Park

from .hosting import set_host_playing
from .models import NYC_TZ, Activity, Event, EventStatus, RSVP, RSVPStatus

User = get_user_model()
PAUSE = 0.4  # seconds the first writer holds its transaction open


KNOWN_MESSAGES = (
    'You are attending this event.',
    "You're already attending.",
    'This event is full.',
    'This event has been cancelled.',
)


def shown_messages(response):
    """Messages rendered on this response's own page.

    Read from the HTML, not `response.context`: the test client collects context through a global
    signal, so with two requests in parallel threads it can pick up the other thread's page.
    """
    html = response.content.decode().replace('&#x27;', "'")
    return [message for message in KNOWN_MESSAGES if message in html]


def slow(method):
    """Wrap a model method so the first call pauses before writing."""
    original = method
    calls = {'n': 0}
    guard = threading.Lock()

    def wrapper(self, *args, **kwargs):
        with guard:
            calls['n'] += 1
            first = calls['n'] == 1
        if first:
            time.sleep(PAUSE)
        return original(self, *args, **kwargs)

    return wrapper


@unittest.skipUnless(connection.vendor == 'postgresql', 'row locks need PostgreSQL; SQLite ignores them')
class SimultaneousJoinTests(TransactionTestCase):
    def setUp(self):
        self.host = User.objects.create_user('race-host', 'race-host@example.test')
        self.players = [User.objects.create_user(f'race-p{i}', f'race-p{i}@example.test') for i in range(4)]
        self.park = Park.objects.create(gispropnum='RACE1', name='Race Park', borough=Borough.BROOKLYN)
        day = timezone.now().astimezone(NYC_TZ).date() + datetime.timedelta(days=3)
        # Host playing + capacity 3 + one RSVP = exactly one spot left.
        self.event = Event.objects.create(
            organizer=self.host,
            park=self.park,
            title='Last spot',
            activity=Activity.BASKETBALL,
            date=day,
            time=datetime.time(10),
            capacity=3,
            description='x',
        )
        RSVP.objects.create(event=self.event, user=self.players[0])

    def confirmed(self):
        return Event.objects.with_confirmed_players().get(pk=self.event.pk).confirmed_players

    def run_together(self, *actions):
        """Start each action in its own thread (and DB connection), the second just after the first."""
        results, errors = [None] * len(actions), []

        def run(index, action):
            try:
                results[index] = action()
            except Exception as error:  # surfaced below
                errors.append(error)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(i, a)) for i, a in enumerate(actions)]
        for thread in threads:
            thread.start()
            time.sleep(0.1)
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        return results

    def joiner(self, user):
        def join():
            client = Client()
            client.force_login(user)
            response = client.post(reverse('event_rsvp', kwargs={'pk': self.event.pk}), follow=True)
            return shown_messages(response)

        return join

    def test_two_players_racing_for_the_last_spot_exactly_one_gets_it(self):
        with patch.object(RSVP, 'save', slow(RSVP.save)):
            first, second = self.run_together(self.joiner(self.players[1]), self.joiner(self.players[2]))

        self.assertEqual(self.confirmed(), 3, 'never more players than the limit')
        self.assertEqual(RSVP.objects.filter(event=self.event, status=RSVPStatus.ACTIVE).count(), 2)
        self.assertEqual(sorted([first, second]), [['This event is full.'], ['You are attending this event.']])

    def test_rejoining_after_cancelling_also_respects_the_last_spot(self):
        RSVP.objects.create(event=self.event, user=self.players[1], status=RSVPStatus.CANCELLED)
        with patch.object(RSVP, 'save', slow(RSVP.save)):
            results = self.run_together(self.joiner(self.players[1]), self.joiner(self.players[2]))

        self.assertEqual(self.confirmed(), 3)
        self.assertEqual(sorted(results), [['This event is full.'], ['You are attending this event.']])

    def test_double_submitted_join_creates_one_rsvp_without_an_error(self):
        with patch.object(RSVP, 'save', slow(RSVP.save)):
            results = self.run_together(self.joiner(self.players[1]), self.joiner(self.players[1]))

        self.assertEqual(RSVP.objects.filter(event=self.event, user=self.players[1]).count(), 1)
        self.assertEqual(sorted(results), [['You are attending this event.'], ["You're already attending."]])

    def test_host_stepping_back_in_races_a_last_spot_join(self):
        Event.objects.filter(pk=self.event.pk).update(host_is_playing=False, capacity=2)  # 1 RSVP, 1 spot left

        def host_in():
            try:
                set_host_playing(self.event.pk, True)
                return 'host in'
            except Exception as error:
                return str(error)

        with patch.object(Event, 'save', slow(Event.save)), patch.object(RSVP, 'save', slow(RSVP.save)):
            host, join = self.run_together(host_in, self.joiner(self.players[2]))

        self.assertEqual(self.confirmed(), 2, 'host + joiner can never both take the last spot')
        self.assertEqual(host, 'host in')
        self.assertEqual(join, ['This event is full.'])

    def test_join_in_progress_when_the_host_cancels_leaves_the_roster_unchanged(self):
        def cancel():
            client = Client()
            client.force_login(self.host)
            client.post(reverse('event_cancel', kwargs={'pk': self.event.pk}))
            return 'cancelled'

        with patch.object(Event, 'save', slow(Event.save)):
            _, join = self.run_together(cancel, self.joiner(self.players[1]))

        self.event.refresh_from_db()
        self.assertEqual(self.event.status, EventStatus.CANCELLED)
        self.assertEqual(join, ['This event has been cancelled.'])
        self.assertEqual(
            list(RSVP.objects.filter(event=self.event).values_list('user__username', flat=True)), ['race-p0']
        )
