"""US-3.3 (#24) set event capacity, and US-4.3 (#31) enforce it.

Deterministic checks that run on any database. The simultaneous-join cases need PostgreSQL row locks
and live in test_capacity_concurrency.py.
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from parks.models import Borough, Park

from .models import CAPACITY_ERROR_MESSAGE, NYC_TZ, Activity, Event, RSVP, RSVPStatus

User = get_user_model()


class CapacityTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_user('cap-host', 'cap-host@example.test')
        cls.players = [User.objects.create_user(f'cap-p{i}', f'cap-p{i}@example.test') for i in range(6)]
        cls.park = Park.objects.create(
            gispropnum='CAP1', name='Capacity Park', borough=Borough.QUEENS, latitude=40.74, longitude=-73.84
        )

    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=datetime.datetime(2026, 11, 2, 12, tzinfo=NYC_TZ))
        clock.start()
        self.addCleanup(clock.stop)

    def make_event(self, **changes):
        data = dict(
            organizer=self.host,
            park=self.park,
            title='Evening run',
            activity=Activity.SOCCER,
            date=datetime.date(2026, 11, 7),
            time=datetime.time(18),
            capacity=10,
            description='Pickup',
        )
        data.update(changes)
        return Event.objects.create(**data)

    def join(self, event, user):
        self.client.force_login(user)
        response = self.client.post(reverse('event_rsvp', kwargs={'pk': event.pk}), follow=True)
        return [str(message) for message in response.context['messages']], response

    def roster(self, event):
        return set(event.rsvps.filter(status=RSVPStatus.ACTIVE).values_list('user__username', flat=True))


class EnforceCapacityTests(CapacityTestCase):
    """US-4.3 (#31)."""

    # Scenario: Event fills (host counted when playing)
    def test_event_fills_when_rsvps_plus_playing_host_reach_the_limit(self):
        event = self.make_event(capacity=3)
        self.assertEqual(self.join(event, self.players[0])[0], ['You are attending this event.'])
        messages, page = self.join(event, self.players[1])
        self.assertEqual(messages, ['You are attending this event.'])
        self.assertContains(page, '3 / 3 players')
        self.assertContains(page, 'event-spots--full">Full</span>')

        # Full everywhere: the event page disables Join, and the cards say Full.
        self.client.force_login(self.players[2])
        detail = self.client.get(event.get_absolute_url())
        self.assertContains(
            detail, '<button class="btn btn--primary" type="button" disabled>Join event</button>', html=True
        )
        self.assertContains(detail, 'This event is full.')
        explore = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'events'})
        self.assertContains(explore, 'discover-spots--ended">Full</span>')
        park_page = self.client.get(reverse('park_detail', kwargs={'pk': self.park.pk}))
        self.assertContains(park_page, 'discover-spots--ended">Full</span>')

    def test_host_not_playing_leaves_their_spot_open(self):
        event = self.make_event(capacity=2, host_is_playing=False)
        for player in self.players[:2]:
            self.assertEqual(self.join(event, player)[0], ['You are attending this event.'])
        self.assertEqual(self.join(event, self.players[2])[0], ['This event is full.'])

    # Scenario: Join on a full event (UI and direct POST)
    def test_join_on_a_full_event_is_refused_and_the_roster_is_unchanged(self):
        event = self.make_event(capacity=2)
        RSVP.objects.create(event=event, user=self.players[0])
        messages, page = self.join(event, self.players[1])

        self.assertEqual(messages, ['This event is full.'])
        self.assertEqual(self.roster(event), {'cap-p0'})
        self.assertNotContains(page, '>Join event</button>\n      </form>')

    def test_rejoining_a_full_event_after_cancelling_is_refused(self):
        event = self.make_event(capacity=2)
        RSVP.objects.create(event=event, user=self.players[0])
        RSVP.objects.create(event=event, user=self.players[1], status=RSVPStatus.CANCELLED)

        self.assertEqual(self.join(event, self.players[1])[0], ['This event is full.'])
        self.assertEqual(event.rsvps.get(user=self.players[1]).status, RSVPStatus.CANCELLED)

    def test_cancelling_an_rsvp_reopens_a_full_event(self):
        event = self.make_event(capacity=2)
        RSVP.objects.create(event=event, user=self.players[0])
        self.client.force_login(self.players[0])
        self.client.post(reverse('event_rsvp_cancel', kwargs={'pk': event.pk}), follow=True)

        self.assertEqual(self.join(event, self.players[1])[0], ['You are attending this event.'])

    # Scenario: Unlimited
    def test_unlimited_events_accept_everyone_and_never_show_full(self):
        event = self.make_event(capacity=None)
        for player in self.players:
            with self.subTest(player=player.username):
                self.assertEqual(self.join(event, player)[0], ['You are attending this event.'])
        self.assertEqual(Event.objects.with_confirmed_players().get(pk=event.pk).confirmed_players, 7)
        page = self.client.get(event.get_absolute_url())
        self.assertContains(page, 'Unlimited spots')
        self.assertContains(page, '7 players')
        self.assertNotContains(page, '>Full<')

    def test_count_confirmed_players_matches_the_annotation(self):
        event = self.make_event()
        RSVP.objects.create(event=event, user=self.players[0])
        RSVP.objects.create(event=event, user=self.players[1], status=RSVPStatus.CANCELLED)
        RSVP.objects.create(event=event, user=self.players[2], status=RSVPStatus.REMOVED)
        annotated = Event.objects.with_confirmed_players().get(pk=event.pk).confirmed_players
        self.assertEqual(event.count_confirmed_players(), annotated)
        self.assertEqual(annotated, 2)


class SetCapacityTests(CapacityTestCase):
    """US-3.3 (#24)."""

    def create(self, capacity, **changes):
        data = {
            'title': 'Evening run',
            'park': self.park.pk,
            'meeting_spot': 'Track entrance',
            'activity': Activity.RUNNING,
            'date': '2026-11-07',
            'time': '18:00',
            'skill_level': 'all',
            'capacity': capacity,
            'host_is_playing': 'on',
            'description': 'Pickup',
        }
        data.update(changes)
        data = {key: value for key, value in data.items() if value is not None}
        self.client.force_login(self.host)
        return self.client.post(reverse('event_create'), data, follow=True)

    def test_label_and_help_text_say_the_host_counts(self):
        self.client.force_login(self.host)
        form = self.client.get(reverse('event_create'))
        self.assertContains(form, '<label for="id_capacity">Participant limit (optional)</label>', html=True)
        self.assertContains(form, "Total players, including you if you're playing. Leave blank for unlimited.")

    # Scenario: Valid limit
    def test_valid_limit_is_saved_and_shown(self):
        response = self.create('10')
        event = Event.objects.get()
        self.assertEqual(event.capacity, 10)
        self.assertContains(response, '10 participants')

    # Scenario: Invalid limit
    def test_zero_negative_or_too_large_limits_are_rejected(self):
        for value, message in (
            ('0', CAPACITY_ERROR_MESSAGE),
            ('-3', CAPACITY_ERROR_MESSAGE),
            ('2.5', CAPACITY_ERROR_MESSAGE),
            ('10001', 'Participant limit cannot exceed 10,000.'),
        ):
            with self.subTest(capacity=value):
                response = self.create(value)
                self.assertFalse(Event.objects.exists())
                self.assertIn(message, response.context['form'].errors['capacity'])

    # Scenario: Unlimited
    def test_blank_limit_is_unlimited(self):
        response = self.create(None)
        self.assertIsNone(Event.objects.get().capacity)
        self.assertContains(response, 'Unlimited')

    # Scenario: Host counted
    def test_playing_host_counts_toward_the_limit(self):
        response = self.create('10')
        self.assertContains(response, '1 / 10 players')
        self.assertContains(response, '9 spots left')

        response = self.create('10', host_is_playing=None, title='Not playing')
        self.assertContains(response, '0 / 10 players')
