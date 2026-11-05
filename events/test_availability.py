"""US-4.1 (#29): spots-left labels on the Events tab and the event page."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Profile
from parks.models import Park

from .models import NYC_TZ, RSVP, Event, RSVPStatus

User = get_user_model()


class EventAvailabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_user('spots-host', 'host@example.test')
        cls.players = [User.objects.create_user(f'spots-player{i}', f'player{i}@example.test') for i in range(3)]
        cls.park = Park.objects.create(gispropnum='M9', name='Tompkins Square Park', borough='M')

    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=datetime.datetime(2026, 10, 23, 12, 0, tzinfo=NYC_TZ))
        clock.start()
        self.addCleanup(clock.stop)

    def event(self, players=0, **changes):
        data = dict(
            organizer=self.host,
            park=self.park,
            title='Pickup game',
            activity='basketball',
            date=datetime.date(2026, 10, 24),
            time=datetime.time(15),
            capacity=5,
        )
        data.update(changes)
        event = Event.objects.create(**data)
        for player in self.players[:players]:
            RSVP.objects.create(event=event, user=player)
        return event

    def events_tab(self):
        return self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'events'})

    def test_events_tab_shows_spots_left_unlimited_and_full(self):
        open_game = self.event(players=2, time=datetime.time(9))
        full_game = self.event(players=1, capacity=2, time=datetime.time(10))
        unlimited_game = self.event(players=3, capacity=None, time=datetime.time(11))

        response = self.events_tab()

        spots = {event.pk: event.spots_remaining for event in response.context['events']}
        self.assertEqual(spots, {open_game.pk: 2, full_game.pk: 0, unlimited_game.pk: None})
        self.assertContains(response, '2 spots left')
        self.assertContains(response, 'of 5')
        self.assertContains(response, 'Full')
        self.assertContains(response, 'Unlimited spots')
        self.assertNotContains(response, 'Up to')

    def test_host_takes_a_spot_only_when_playing(self):
        playing = self.event(players=1, capacity=3, time=datetime.time(9))
        sitting_out = self.event(players=1, capacity=3, time=datetime.time(10), host_is_playing=False)

        spots = {event.pk: event.spots_remaining for event in self.events_tab().context['events']}

        self.assertEqual(spots, {playing.pk: 1, sitting_out.pk: 2})
        self.assertContains(self.client.get(playing.get_absolute_url()), '1 spot left')
        self.assertContains(self.client.get(sitting_out.get_absolute_url()), '2 spots left')

    def test_cancelled_rsvp_frees_its_spot(self):
        game = self.event(players=1, capacity=2)
        self.assertContains(self.events_tab(), 'Full')

        RSVP.objects.filter(event=game).update(status=RSVPStatus.CANCELLED)

        self.assertContains(self.events_tab(), '1 spot left')

    def test_event_page_links_to_its_park(self):
        response = self.client.get(self.event().get_absolute_url())
        park_url = reverse('park_detail', args=[self.park.pk])
        self.assertContains(response, f'<a href="{park_url}">Tompkins Square Park</a>')

    def test_retired_park_name_is_not_linked(self):
        retired = Park.objects.create(gispropnum='M10', name='Old Playground', borough='M', is_retired=True)
        response = self.client.get(self.event(park=retired).get_absolute_url())
        self.assertContains(response, '<strong>Old Playground</strong>', html=True)
        self.assertNotContains(response, reverse('park_detail', args=[retired.pk]))

    def test_going_list_shows_display_names_and_photos(self):
        Profile.objects.create(user=self.host, display_username='CourtCaptain', avatar='avatars/captain.png')
        Profile.objects.create(user=self.players[0], display_username='Hooper')
        game = self.event(players=2)

        response = self.client.get(game.get_absolute_url())

        self.assertContains(response, '@CourtCaptain <span class="event-roster-tag">Host</span>')
        self.assertContains(response, 'avatars/captain.png')
        self.assertContains(response, '@Hooper</li>')
        self.assertContains(response, '@spots-player1</li>', msg_prefix='No profile falls back to the username')
        self.assertContains(response, 'class="profile-photo-placeholder"', count=2)
