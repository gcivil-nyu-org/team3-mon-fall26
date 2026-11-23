"""US-7.5 (#48): administrators correct or remove events."""

from django.contrib import admin
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from .admin import EventAdmin
from .models import RSVP, Event, EventPhase, EventStatus, Message
from .test_event_edit import EventEditTestCase
from .test_event_phases import PhaseTestCase

User = get_user_model()
REMOVED_NOTICE = 'This event was removed by a moderator'


class RemoveEventTestCase(PhaseTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = User.objects.create_user('remove-admin', 'remove-admin@example.test', is_staff=True)

    def setUp(self):
        super().setUp()
        self.game = self.event('Bad game', days=1, description='Spam link here')
        RSVP.objects.create(event=self.game, user=self.player)

    def remove(self, user=None, **data):
        if user:
            self.client.force_login(user)
        return self.client.post(reverse('event_remove', args=[self.game.pk]), data)

    def log_entries(self):
        return LogEntry.objects.filter(object_id=str(self.game.pk), content_type__model='event')


class RemoveTests(RemoveEventTestCase):
    def test_admin_removes_an_event_and_it_is_logged(self):
        response = self.remove(self.admin, reason='spam')

        self.assertRedirects(response, self.game.get_absolute_url())
        self.game.refresh_from_db()
        self.assertEqual(self.game.status, EventStatus.REMOVED)
        entry = self.log_entries().get()
        self.assertEqual((entry.user, entry.action_flag), (self.admin, CHANGE))
        self.assertTrue(entry.get_change_message().startswith('Removed event: spam'))
        self.assertIn('status: removed', entry.get_change_message())

    def test_reason_is_optional(self):
        self.remove(self.admin)

        self.assertTrue(self.log_entries().get().get_change_message().startswith('Removed event\n'))

    def test_live_and_ended_games_can_be_removed_too(self):
        for title, start, end in (('Live game', '11:00', '13:00'), ('Ended game', '08:00', '09:00')):
            with self.subTest(title):
                game = self.event(title, start=start, end=end)
                self.client.force_login(self.admin)
                self.client.post(reverse('event_remove', args=[game.pk]))
                game.refresh_from_db()
                self.assertEqual(game.status, EventStatus.REMOVED)

    def test_removing_twice_changes_nothing(self):
        self.remove(self.admin)

        response = self.remove(self.admin, reason='again')

        self.assertContains(self.client.get(response.url), 'This event has already been removed.')
        self.assertEqual(self.log_entries().count(), 1)

    def test_non_administrators_get_403_and_the_event_is_unchanged(self):
        for user in (self.host, self.player):
            with self.subTest(user.username):
                self.assertEqual(self.remove(user).status_code, 403)
        self.game.refresh_from_db()
        self.assertEqual(self.game.status, EventStatus.SCHEDULED)
        self.assertFalse(self.log_entries().exists())

    def test_logged_out_visitors_go_to_login(self):
        response = self.remove()

        # The remove URL only takes POST, so login returns to the event page.
        self.assertRedirects(response, reverse('login') + '?next=' + self.game.get_absolute_url())
        self.game.refresh_from_db()
        self.assertEqual(self.game.status, EventStatus.SCHEDULED)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get(reverse('event_remove', args=[self.game.pk])).status_code, 405)


class RemovedEventDisappearsTests(RemoveEventTestCase):
    def park_counts(self):
        response = self.client.get(reverse('event_map_api'))
        return {park['id']: park['event_count'] for park in response.json()['parks']}

    def test_it_leaves_explore_map_counts_park_panels_and_park_pages(self):
        self.event('Good game', days=2)
        preview = reverse('explore_park_preview', args=[self.park.pk])
        park_page = reverse('park_detail', args=[self.park.pk])
        self.assertIn('Bad game', self.titles(phase=list(EventPhase)))
        self.assertEqual(self.park_counts()[self.park.pk], 2)

        self.remove(self.admin)
        self.client.logout()

        self.assertEqual(self.titles(phase=list(EventPhase)), ['Good game'])
        self.assertEqual(self.park_counts()[self.park.pk], 1)
        self.assertNotContains(self.client.get(preview), 'Bad game')
        self.assertNotContains(self.client.get(park_page), 'Bad game')
        feed = self.client.get(reverse('event_list_api')).json()['results']
        self.assertEqual([event['title'] for event in feed], ['Good game'])

    def test_host_and_players_still_see_it_in_my_events_with_a_removed_badge(self):
        self.remove(self.admin)

        for user, scope in ((self.player, 'attending'), (self.host, 'hosting')):
            with self.subTest(scope):
                self.client.force_login(user)
                response = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'events', 'scope': scope})
                self.assertContains(response, 'Bad game')
                self.assertContains(response, 'Removed by a moderator')

    def test_removed_live_and_ended_games_stay_in_my_events(self):
        live = self.event('Live game', start='11:00', end='13:00')
        over = self.event('Ended game', start='08:00', end='09:00')
        for game in (live, over):
            RSVP.objects.create(event=game, user=self.player)
            self.client.force_login(self.admin)
            self.client.post(reverse('event_remove', args=[game.pk]))
        self.remove(self.admin)

        for user, scope in ((self.player, 'attending'), (self.host, 'hosting')):
            with self.subTest(scope):
                self.client.force_login(user)
                self.assertEqual(self.titles(scope=scope), ['Live game', 'Bad game'])
                self.assertEqual(self.titles(scope=scope, phase='ended'), ['Ended game'])
        self.client.logout()
        self.assertEqual(self.titles(phase=list(EventPhase)), [])

    def test_park_panel_hides_a_removed_ended_game_unless_ended_is_selected(self):
        over = self.event('Ended game', start='08:00', end='09:00')
        self.client.force_login(self.admin)
        self.client.post(reverse('event_remove', args=[over.pk]))
        self.client.force_login(self.host)
        preview = reverse('explore_park_preview', args=[self.park.pk])

        default = self.client.get(preview, {'scope': 'hosting'})
        with_ended = self.client.get(preview, {'scope': 'hosting', 'phase': list(EventPhase)})

        self.assertNotContains(default, 'Ended game')
        self.assertEqual(default.context['hidden_ended_total'], 1)
        self.assertContains(with_ended, 'Ended game')


class RemovedEventPageTests(RemoveEventTestCase):
    def setUp(self):
        super().setUp()
        Message.objects.create(event=self.game, author=self.player, body='Anyone bringing a ball?')
        self.remove(self.admin)

    def test_page_shows_only_the_notice(self):
        for user in (None, self.player, self.host):
            with self.subTest(user and user.username):
                self.client.logout()
                if user:
                    self.client.force_login(user)
                response = self.client.get(self.game.get_absolute_url())
                self.assertContains(response, REMOVED_NOTICE)
                self.assertContains(response, '<title>Removed event · NYC Active</title>', html=True)
                for gone in (
                    'Bad game',
                    'Spam link here',
                    '@phase-host',
                    '@phase-player',
                    'Anyone bringing a ball?',
                    'Join',
                ):
                    self.assertNotContains(response, gone)
                self.assertNotContains(response, 'Open it in the admin')

    def test_administrators_get_a_link_to_the_admin(self):
        self.client.force_login(self.admin)

        response = self.client.get(self.game.get_absolute_url())

        self.assertContains(response, reverse('admin:events_event_change', args=[self.game.pk]))
        self.assertNotContains(response, 'Remove event')

    def test_rsvps_and_posts_are_refused(self):
        newcomer = User.objects.create_user('remove-newcomer', 'remove-newcomer@example.test')
        self.client.force_login(newcomer)

        self.client.post(reverse('event_rsvp', args=[self.game.pk]))
        response = self.client.post(
            reverse('event_message_post', args=[self.game.pk]),
            {'body': 'Still on?'},
            headers={'x-requested-with': 'XMLHttpRequest'},
        )

        self.assertFalse(RSVP.objects.filter(event=self.game, user=newcomer).exists())
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Message.objects.filter(body='Still on?').exists())


class RemoveButtonTests(RemoveEventTestCase):
    def test_only_administrators_see_the_remove_button(self):
        for user, shown in ((self.admin, True), (self.host, False), (self.player, False)):
            with self.subTest(user.username):
                self.client.force_login(user)
                response = self.client.get(self.game.get_absolute_url())
                if shown:
                    self.assertContains(response, reverse('event_remove', args=[self.game.pk]))
                else:
                    self.assertNotContains(response, 'Remove event')

    def test_administrators_can_remove_an_ended_game_from_its_page(self):
        over = self.event('Over', start='08:00', end='09:00')
        self.client.force_login(self.admin)

        response = self.client.get(over.get_absolute_url())

        self.assertContains(response, reverse('event_remove', args=[over.pk]))
        self.assertNotContains(response, 'Cancel Event')


class CorrectEventTests(EventEditTestCase):
    def test_administrator_correction_shows_on_the_event_page(self):
        self.client.force_login(self.admin)

        self.client.post(self.url, self.payload(description='Fixed description'))

        self.assertContains(self.client.get(self.event.get_absolute_url()), 'Fixed description')


class AdminActionTests(RemoveEventTestCase):
    def test_admin_action_removes_and_logs_each_event(self):
        other = self.event('Duplicate', days=3)
        already = self.event('Gone already', days=3, status=EventStatus.REMOVED)
        request = RequestFactory().post('/')
        request.user = self.admin
        model_admin = EventAdmin(Event, admin.site)
        model_admin.message_user = lambda request, message: setattr(self, 'message', message)

        model_admin.get_actions(request)['remove_events'][0](
            model_admin, request, Event.objects.filter(pk__in=[self.game.pk, other.pk, already.pk])
        )

        self.assertEqual(self.message, 'Removed 2 events.')
        self.assertEqual(
            set(Event.objects.filter(status=EventStatus.REMOVED).values_list('title', flat=True)),
            {'Bad game', 'Duplicate', 'Gone already'},
        )
        self.assertEqual(LogEntry.objects.filter(change_message__startswith='Removed event').count(), 2)
