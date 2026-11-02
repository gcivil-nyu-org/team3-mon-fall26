"""US-3.2 (#23): upcoming events on the park page."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from events.models import NYC_TZ, RSVP, Event, EventStatus, RSVPStatus

from .models import Park

User = get_user_model()


class ParkUpcomingEventsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_user('court-host', 'host@example.test')
        cls.players = [User.objects.create_user(f'player{i}', f'player{i}@example.test') for i in range(2)]
        cls.park = Park.objects.create(gispropnum='B58', name='McCarren Park', borough='B')
        cls.other_park = Park.objects.create(gispropnum='B1', name='Prospect Park', borough='B')

    def setUp(self):
        self.now = datetime.datetime(2026, 10, 23, 12, 0, tzinfo=NYC_TZ)
        clock = patch('django.utils.timezone.now', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def event(self, **changes):
        data = dict(
            organizer=self.host,
            park=self.park,
            title='Saturday pickup',
            activity='basketball',
            date=self.now.date() + datetime.timedelta(days=1),
            time=datetime.time(15),
            description='Friendly game',
            capacity=10,
        )
        data.update(changes)
        return Event.objects.create(**data)

    def page(self):
        return self.client.get(reverse('park_detail', kwargs={'pk': self.park.pk}))

    def test_lists_upcoming_events_soonest_first_with_their_details(self):
        later = self.event(
            title='Sunday run',
            activity='running',
            skill_level='beginner',
            date=self.now.date() + datetime.timedelta(days=2),
            meeting_spot='Track entrance',
        )
        sooner = self.event(title='Tonight hoops', date=self.now.date(), time=datetime.time(18))
        response = self.page()
        self.assertEqual(response.context['park_events'], [sooner, later])
        for text in (
            'Sunday run',
            'Running',
            'Beginner / Casual',
            'Sun, Oct 25 · 3:00 PM',
            'Meet at Track entrance',
            'Hosted by @court-host',
            'Meeting spot not specified',
        ):
            self.assertContains(response, text)

    def test_hides_started_cancelled_removed_and_other_parks_events(self):
        kept = self.event()
        self.event(title='Started an hour ago', date=self.now.date(), time=datetime.time(11))
        self.event(title='Called off', status=EventStatus.CANCELLED)
        self.event(title='Taken down', status=EventStatus.REMOVED)
        self.event(title='Elsewhere', park=self.other_park)
        response = self.page()
        self.assertEqual(response.context['park_events'], [kept])
        for title in ('Started an hour ago', 'Called off', 'Taken down', 'Elsewhere'):
            self.assertNotContains(response, title)

    def test_availability_counts_active_rsvps_and_a_playing_host(self):
        open_game = self.event(title='Open game', capacity=4)
        RSVP.objects.create(event=open_game, user=self.players[0])
        RSVP.objects.create(event=open_game, user=self.players[1], status=RSVPStatus.CANCELLED)
        full_game = self.event(title='Full game', capacity=2, time=datetime.time(16))
        RSVP.objects.create(event=full_game, user=self.players[0])
        self.event(title='Open run', capacity=None, time=datetime.time(17))
        response = self.page()
        self.assertEqual([event.spots_remaining for event in response.context['park_events']], [2, 0, None])
        self.assertContains(response, '2 spots remaining')
        self.assertContains(response, 'Full')
        self.assertContains(response, 'Unlimited spots')

    def test_no_upcoming_events_invites_organizing_here(self):
        self.event(park=self.other_park)
        response = self.page()
        self.assertContains(response, 'No upcoming events here yet. Be the first to host one!')
        # The header button plus the empty-state button.
        self.assertContains(response, f'href="{reverse("event_create")}?park={self.park.pk}"', count=2)

    def test_query_count_does_not_grow_with_events(self):
        self.event()
        with CaptureQueriesContext(connection) as one_event:
            self.page()
        for index in range(3):
            host = User.objects.create_user(f'host{index}', f'host{index}@example.test')
            self.event(organizer=host, time=datetime.time(16 + index))
        with CaptureQueriesContext(connection) as four_events:
            self.page()
        self.assertEqual(len(four_events), len(one_event))
