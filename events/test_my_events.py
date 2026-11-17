"""US-4.7 (#76): My Events badges, players vs. limit, and the empty state."""

import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from parks.models import Park

from .models import NYC_TZ, RSVP, Event
from .test_empty_states import empty_block, links

User = get_user_model()
NO_MY_EVENTS = 'You have no upcoming events.'


class MyEventsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.me = User.objects.create_user('my-events-user', 'me@example.test')
        cls.other = User.objects.create_user('other-organizer', 'other@example.test')
        cls.players = [User.objects.create_user(f'my-events-player{i}', f'p{i}@example.test') for i in range(2)]
        cls.park = Park.objects.create(
            gispropnum='B1', name='McCarren Park', borough='B', latitude='40.721400', longitude='-73.951500'
        )

    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=datetime.datetime(2026, 10, 23, 12, 0, tzinfo=NYC_TZ))
        clock.start()
        self.addCleanup(clock.stop)

    def event(self, organizer=None, players=0, **changes):
        data = dict(
            organizer=organizer or self.other,
            park=self.park,
            title='Pickup game',
            activity='basketball',
            date=datetime.date(2026, 10, 24),
            time=datetime.time(15),
            capacity=10,
        )
        data.update(changes)
        event = Event.objects.create(**data)
        for player in self.players[:players]:
            RSVP.objects.create(event=event, user=player)
        return event

    def events_tab(self, **params):
        response = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'events', **params})
        self.assertEqual(response.status_code, 200)
        return response

    def test_hosted_events_show_host_badge_and_players_vs_limit(self):
        self.client.force_login(self.me)
        self.event(self.me, players=2, title='Playing host', time=datetime.time(9))
        self.event(self.me, players=1, title='Sitting out', capacity=None, host_is_playing=False)

        response = self.events_tab(scope='hosting')

        self.assertContains(response, '>Host · Playing</span>', count=1)
        self.assertContains(response, '>Host · Not playing</span>', count=1)
        self.assertContains(response, '3 / 10 players')
        self.assertContains(response, '7 spots left')
        self.assertContains(response, '1 player<')
        self.assertContains(response, 'Unlimited spots')

    def test_attending_events_show_attending_badge_not_host(self):
        self.client.force_login(self.me)
        game = self.event(players=1)
        RSVP.objects.create(event=game, user=self.me)

        response = self.events_tab(scope='attending')

        self.assertEqual([event.pk for event in response.context['events']], [game.pk])
        self.assertContains(response, "You're attending", count=1)
        self.assertNotContains(response, 'Host ·')
        self.assertContains(response, '7 spots left')

    def test_cancelled_and_removed_events_stay_in_my_events_with_badges(self):
        self.client.force_login(self.me)
        self.event(self.me, players=1, title='My cancelled game', status='cancelled')
        removed = self.event(title='Removed game I joined', status='removed')
        RSVP.objects.create(event=removed, user=self.me)

        mine = self.events_tab(scope='mine')
        self.assertContains(mine, '<span class="event-badge explore-status-badge">Cancelled</span>', html=True)
        self.assertContains(
            mine, '<span class="event-badge explore-status-badge">Removed by a moderator</span>', html=True
        )
        # Games that aren't happening show that in place of players and spots.
        self.assertContains(mine, '<span class="discover-spots discover-spots--muted">Cancelled</span>', html=True)
        self.assertContains(mine, '<span class="discover-spots discover-spots--muted">Removed</span>', html=True)
        self.assertNotContains(mine, 'players')
        self.assertNotContains(mine, 'spots left')

        everyone = self.events_tab()
        self.assertNotContains(everyone, 'My cancelled game')
        self.assertNotContains(everyone, 'Removed game I joined')

    def test_empty_my_events_offers_all_events_and_create(self):
        self.client.force_login(self.me)
        self.event(title='Someone else’s game')

        map_page = self.client.get(reverse('event_list'), {'scope': 'mine'})
        self.assertEqual(map_page.context['map_empty_state'], 'no_my_events')
        self.assertContains(map_page, NO_MY_EVENTS)

        list_page = self.events_tab(scope='hosting')
        self.assertEqual(list_page.context['empty_state'], 'no_my_events')
        block = empty_block(list_page.content.decode(), 'no_my_events')
        self.assertIn(NO_MY_EVENTS, block)
        found = links(block)
        self.assertEqual(set(found), {'Show all events', 'Create Event'})
        self.assertNotIn('scope', found['Show all events'][1])
        self.assertEqual(found['Create Event'][0], reverse('event_create'))

        all_events = self.client.get(reverse('event_list') + found['Show all events'][0])
        self.assertEqual(all_events.context['event_total'], 1)

    def test_empty_my_events_with_filters_can_clear_them(self):
        self.client.force_login(self.me)
        self.event(self.me, activity='basketball')

        response = self.events_tab(scope='mine', sport='soccer')

        block = empty_block(response.content.decode(), 'no_my_events')
        self.assertIn('None of your events match your filters.', block)
        self.assertNotIn(NO_MY_EVENTS, block)
        found = links(block)
        self.assertEqual(found['Clear filters'][1]['scope'], ['mine'])
        self.assertNotIn('sport', found['Clear filters'][1])

    def test_anonymous_my_events_goes_to_login_and_back(self):
        response = self.client.get('/events/?scope=mine')
        url = urlsplit(response.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(url.path, reverse('login'))
        self.assertEqual(parse_qs(url.query)['next'], ['/events/?scope=mine'])
