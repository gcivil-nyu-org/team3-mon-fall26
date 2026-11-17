"""US-3.10 (#82): the organizer chooses whether they play in their own event."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from parks.models import Borough, Park

from .hosting import FULL_MESSAGE, STARTED_MESSAGE, HostPlayingError, set_host_playing
from .models import NYC_TZ, Activity, Event, EventStatus, RSVP

User = get_user_model()


class HostPlayingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_user('playing-host', 'playing-host@example.test')
        cls.other = User.objects.create_user('playing-other', 'playing-other@example.test')
        cls.admin = User.objects.create_user('playing-admin', 'playing-admin@example.test', is_staff=True)
        cls.players = [User.objects.create_user(f'playing-p{i}', f'p{i}@example.test') for i in range(3)]
        cls.park = Park.objects.create(
            gispropnum='PLAY1', name='Play Park', borough=Borough.BROOKLYN, latitude=40.72, longitude=-73.95
        )

    def setUp(self):
        self.now = datetime.datetime(2026, 11, 2, 12, tzinfo=NYC_TZ)
        clock = patch('django.utils.timezone.now', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.client.force_login(self.host)

    def make_event(self, **changes):
        data = dict(
            organizer=self.host,
            park=self.park,
            title='Saturday hoops',
            activity=Activity.BASKETBALL,
            date=datetime.date(2026, 11, 7),
            time=datetime.time(10),
            capacity=10,
            description='Pickup run',
        )
        data.update(changes)
        return Event.objects.create(**data)

    def join(self, event, count):
        for player in self.players[:count]:
            RSVP.objects.create(event=event, user=player)

    def create(self, **changes):
        data = {
            'title': 'Saturday hoops',
            'park': self.park.pk,
            'meeting_spot': 'Court 3',
            'activity': Activity.BASKETBALL,
            'date': '2026-11-07',
            'time': '10:00',
            'end_time': '12:00',
            'skill_level': 'all',
            'capacity': '10',
            'host_is_playing': 'on',
            'description': 'Pickup run',
        }
        data.update(changes)
        data = {key: value for key, value in data.items() if value is not None}
        return self.client.post(reverse('event_create'), data, follow=True)

    def toggle(self, event, playing):
        data = {'playing': '1'} if playing else {}
        return self.client.post(reverse('event_host_playing', kwargs={'pk': event.pk}), data, follow=True)

    def confirmed(self, event):
        return Event.objects.with_confirmed_players().get(pk=event.pk).confirmed_players

    def messages(self, response):
        return [str(message) for message in response.context['messages']]

    # Scenario: Hosting and playing (default)
    def test_create_defaults_to_playing_shows_one_of_ten_and_host_playing(self):
        form = self.client.get(reverse('event_create'))
        self.assertContains(form, 'I&#x27;m playing in this game')  # the label, HTML-escaped
        self.assertContains(form, 'name="host_is_playing"')
        self.assertTrue(form.context['form']['host_is_playing'].value())

        response = self.create()

        event = Event.objects.get()
        self.assertTrue(event.host_is_playing)
        self.assertFalse(event.rsvps.exists(), 'the host never gets an RSVP row')
        self.assertContains(response, '1 / 10 players')
        self.assertContains(response, '<span class="event-roster-tag">Host · Playing</span>', html=True)
        self.assertContains(response, '9 spots left')

    # Scenario: Hosting without playing
    def test_create_unchecked_shows_zero_of_ten_and_host_not_playing(self):
        response = self.create(host_is_playing=None)

        event = Event.objects.get()
        self.assertFalse(event.host_is_playing)
        self.assertContains(response, '0 / 10 players')
        self.assertContains(response, '<span class="event-roster-tag">Host · Not playing</span>', html=True)
        self.assertContains(response, '10 spots left')

    # Scenario: Switching to playing later
    def test_switching_to_playing_takes_a_spot(self):
        event = self.make_event(host_is_playing=False)
        self.join(event, 2)

        response = self.toggle(event, True)

        event.refresh_from_db()
        self.assertTrue(event.host_is_playing)
        self.assertEqual(self.confirmed(event), 3)
        self.assertIn("You're playing in this game.", self.messages(response))
        self.assertContains(response, '3 / 10 players')
        self.assertContains(response, '7 spots left')

    # Scenario: Switching to playing on a full event
    def test_switching_to_playing_on_a_full_event_is_rejected_and_nothing_changes(self):
        event = self.make_event(host_is_playing=False, capacity=3)
        self.join(event, 3)

        response = self.toggle(event, True)

        event.refresh_from_db()
        self.assertFalse(event.host_is_playing)
        self.assertEqual(self.confirmed(event), 3)
        self.assertIn(FULL_MESSAGE, self.messages(response))
        self.assertEqual(FULL_MESSAGE, 'The event is full. Raise the limit to add yourself.')

    # Scenario: Stepping out
    def test_stepping_out_frees_a_spot_and_reopens_a_full_event(self):
        event = self.make_event(capacity=3)
        self.join(event, 2)
        self.assertEqual(self.confirmed(event), 3)

        response = self.toggle(event, False)

        event.refresh_from_db()
        self.assertFalse(event.host_is_playing)
        self.assertEqual(self.confirmed(event), 2)
        self.assertIn("You're no longer playing in this game.", self.messages(response))
        self.assertContains(response, '1 spot left')
        self.assertContains(response, '>Host · Not playing</span>')
        # The reopened spot can be taken.
        self.client.force_login(self.players[2])
        joined = self.client.post(reverse('event_rsvp', kwargs={'pk': event.pk}), follow=True)
        self.assertIn('You are attending this event.', self.messages(joined))

    # Scenario: Unlimited events
    def test_unlimited_events_always_toggle(self):
        event = self.make_event(capacity=None)
        self.join(event, 3)
        for playing in (False, True, False, True):
            with self.subTest(playing=playing):
                self.toggle(event, playing)
                event.refresh_from_db()
                self.assertEqual(event.host_is_playing, playing)
        response = self.client.get(event.get_absolute_url())
        self.assertContains(response, '4 players')
        self.assertContains(response, 'Unlimited spots')

    # Scenario: After the game has started
    def test_change_after_start_is_refused_with_an_explanation(self):
        event = self.make_event(date=datetime.date(2026, 11, 2), time=datetime.time(11), host_is_playing=False)

        response = self.toggle(event, True)

        event.refresh_from_db()
        self.assertFalse(event.host_is_playing)
        self.assertIn(STARTED_MESSAGE, self.messages(response))
        self.assertContains(response, 'name="playing" value="1" disabled')
        self.assertNotContains(response, '>Save</button>')

    def test_cancelled_events_cannot_be_toggled(self):
        event = self.make_event(status=EventStatus.CANCELLED)

        response = self.toggle(event, False)

        event.refresh_from_db()
        self.assertTrue(event.host_is_playing)
        self.assertIn('This event has been cancelled.', self.messages(response))

    # Who can change it
    def test_only_the_organizer_can_change_whether_they_play(self):
        event = self.make_event()
        url = reverse('event_host_playing', kwargs={'pk': event.pk})
        for user in (self.other, self.admin):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.client.logout()
        response = self.client.post(url, {})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        event.refresh_from_db()
        self.assertTrue(event.host_is_playing)
        self.assertEqual(self.client.get(url).status_code, 302, 'GET is not a way to change it')

    def test_toggle_shows_only_for_the_organizer(self):
        event = self.make_event()
        self.assertContains(self.client.get(event.get_absolute_url()), "I'm playing in this game")
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(event.get_absolute_url()), "I'm playing in this game")

    def test_saving_without_a_change_says_nothing(self):
        event = self.make_event()
        response = self.toggle(event, True)
        self.assertEqual(self.messages(response), [])

    # Shared helper for Edit Event (#25)
    def test_helper_checks_capacity_with_the_submitted_limit(self):
        event = self.make_event(host_is_playing=False, capacity=3)
        self.join(event, 3)
        with self.assertRaisesMessage(HostPlayingError, FULL_MESSAGE):
            set_host_playing(event.pk, True)
        updated, changed = set_host_playing(event.pk, True, capacity=4)
        self.assertTrue(changed)
        self.assertTrue(updated.host_is_playing)
