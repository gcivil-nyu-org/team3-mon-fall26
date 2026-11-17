"""US-4.8 (#117): phase badges, greyed-out ended rows, and what an ended event page shows."""

import re
from io import StringIO

from django.core.management import call_command
from django.urls import reverse

from .models import RSVP, Event, EventPhase, EventStatus
from .test_event_phases import PhaseTestCase

UPCOMING_BADGE = '<span class="event-badge event-badge--upcoming">Upcoming</span>'
LIVE_BADGE = '<span class="event-badge event-badge--live">Live</span>'
ENDED_BADGE = '<span class="event-badge event-badge--ended">Ended</span>'


def card(html, title):
    """The <li> card that shows `title`."""
    for item in re.findall(r'<li class="discover-item.*?</li>', html, re.S):
        if title in item:
            return item
    raise AssertionError(f'No card for {title!r}')


class PhaseBadgeTestCase(PhaseTestCase):
    def setUp(self):
        super().setUp()
        self.upcoming = self.event('Tomorrow', days=1)
        self.live = self.event('On now', start='11:00', end='13:00')
        self.over = self.event('Over', start='08:00', end='09:00')

    def page(self, url, **params):
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class ExploreBadgeTests(PhaseBadgeTestCase):
    def test_cards_show_a_badge_for_each_phase(self):
        html = self.page(reverse('event_list'), view='list', tab='events', phase=list(EventPhase))

        for title, badge, phase in (
            ('Tomorrow', UPCOMING_BADGE, 'upcoming'),
            ('On now', LIVE_BADGE, 'live'),
            ('Over', ENDED_BADGE, 'ended'),
        ):
            with self.subTest(title):
                item = card(html, title)
                self.assertIn(badge, item)
                self.assertIn(f'discover-item--{phase}', item)

    def test_ended_rows_in_public_explore_are_greyed_out_and_not_links(self):
        html = self.page(reverse('event_list'), view='list', tab='events', phase=list(EventPhase))

        over = card(html, 'Over')
        self.assertIn('discover-item--static', over)
        self.assertNotIn('<a ', over)
        self.assertIn('1 played', over, 'Just the host played')
        self.assertNotIn('spots left', over)
        live = card(html, 'On now')
        self.assertIn(f'href="{self.live.get_absolute_url()}"', live)
        self.assertIn('1 playing', live, 'Joining closes at the start, so live cards show who is playing')
        self.assertIn('In progress', live)
        self.assertNotIn('spots left', live)

    def test_park_panel_greys_out_ended_rows_too(self):
        RSVP.objects.create(event=self.over, user=self.player)
        html = self.page(reverse('explore_park_preview', args=[self.park.pk]), phase=['live', 'ended'])

        over = card(html, 'Over')
        self.assertIn(ENDED_BADGE, over)
        self.assertNotIn('<a ', over)
        self.assertIn(LIVE_BADGE, card(html, 'On now'))
        self.assertNotIn('Tomorrow', html)

    def test_my_events_keep_ended_games_as_links(self):
        RSVP.objects.create(event=self.over, user=self.player)
        self.client.force_login(self.player)

        html = self.page(reverse('event_list'), view='list', tab='events', scope='attending', phase='ended')

        over = card(html, 'Over')
        self.assertIn(ENDED_BADGE, over)
        self.assertIn(f'href="{self.over.get_absolute_url()}"', over)
        self.assertNotIn('discover-item--static', over)

    def test_cancelled_games_show_their_status_and_no_phase(self):
        cancelled = self.event('Called off', days=2, status=EventStatus.CANCELLED)
        RSVP.objects.create(event=cancelled, user=self.player)
        self.client.force_login(self.player)

        item = card(self.page(reverse('event_list'), view='list', tab='events', scope='attending'), 'Called off')

        self.assertIn('explore-status-badge">Cancelled</span>', item)
        self.assertNotIn('event-badge--upcoming', item)

    def test_count_no_longer_says_upcoming(self):
        html = self.page(reverse('event_list'), view='list', tab='events')

        self.assertIn('<strong>2</strong> events', html)


class ParkPageBadgeTests(PhaseBadgeTestCase):
    def test_park_page_lists_live_then_upcoming_with_badges(self):
        html = self.page(reverse('park_detail', args=[self.park.pk]))

        self.assertIn('Live and upcoming events', html)
        self.assertIn(LIVE_BADGE, card(html, 'On now'))
        self.assertIn(UPCOMING_BADGE, card(html, 'Tomorrow'))
        self.assertLess(html.index('On now'), html.index('Tomorrow'))
        self.assertNotIn('>Over<', html)


class EventPageTests(PhaseBadgeTestCase):
    def test_header_badge_shows_the_phase_instead_of_scheduled(self):
        for event, badge in ((self.upcoming, UPCOMING_BADGE), (self.live, LIVE_BADGE), (self.over, ENDED_BADGE)):
            with self.subTest(event.title):
                html = self.page(event.get_absolute_url())
                self.assertIn(badge, html)
                self.assertNotIn('>Scheduled<', html)

        cancelled = self.event('Called off', days=2, status=EventStatus.CANCELLED)
        self.assertIn('event-badge--status">Cancelled</span>', self.page(cancelled.get_absolute_url()))

    def test_ended_page_lists_who_played(self):
        RSVP.objects.create(event=self.over, user=self.player)

        html = self.page(self.over.get_absolute_url())

        self.assertIn('Played <span class="event-attendance-count">2</span>', html)
        self.assertIn('<span class="event-spots event-spots--muted">2 played</span>', html)
        self.assertIn('Host · Played', html)
        self.assertIn('This event has ended.', html)
        for gone in ('Going', 'spots left', 'event-spots-bar', '/ 10 players'):
            self.assertNotIn(gone, html)

    def test_ended_page_without_other_players(self):
        self.over.host_is_playing = False
        self.over.save(update_fields=['host_is_playing'])

        html = self.page(self.over.get_absolute_url())

        self.assertIn("Host · Didn't play", html)
        self.assertIn('No one else played.', html)
        self.assertIn('0 played', html)

    def test_live_page_still_shows_who_is_going(self):
        html = self.page(self.live.get_absolute_url())

        self.assertIn('Going <span class="event-attendance-count">1</span>', html)
        self.assertIn('Host · Playing', html)

    def test_closed_discussion_doesnt_invite_questions(self):
        self.assertIn('No messages yet. Ask a question below!', self.page(self.live.get_absolute_url()))
        over = self.page(self.over.get_absolute_url())
        self.assertIn('No messages were posted.', over)
        self.assertNotIn('Ask a question below', over)


class SampleEventTests(PhaseTestCase):
    def test_seed_adds_a_game_thats_live_right_now(self):
        out = StringIO()
        call_command('seed_events', stdout=out)

        live = Event.objects.get(title='Drop-In Basketball Run')
        self.assertEqual(live.phase, EventPhase.LIVE)
        self.assertIn('1 live right now', out.getvalue())
