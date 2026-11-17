"""US-3.6 (#27) and US-3.7 (#28): who can manage an event, and what cancelling it does."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import Profile
from parks.models import Borough, Park

from .models import Activity, Event, EventStatus, NYC_TZ, RSVP, RSVPStatus

User = get_user_model()


class EventManagementTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_user('manage-host', 'manage-host@example.test')
        cls.other = User.objects.create_user('manage-other', 'manage-other@example.test')
        cls.player = User.objects.create_user('manage-player', 'manage-player@example.test')
        cls.admin = User.objects.create_user('manage-admin', 'manage-admin@example.test', is_staff=True)
        cls.park = Park.objects.create(
            gispropnum='MANAGE1', name='Manage Park', borough=Borough.BROOKLYN, latitude=40.72, longitude=-73.95
        )

    def setUp(self):
        self.now = datetime.datetime(2026, 11, 2, 12, tzinfo=NYC_TZ)
        clock = patch('django.utils.timezone.now', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.event = self.make_event()
        self.url = reverse('event_cancel', kwargs={'pk': self.event.pk})
        self.detail_url = self.event.get_absolute_url()

    def make_event(self, **changes):
        data = dict(
            organizer=self.host,
            park=self.park,
            title='Sunset Pickup',
            activity=Activity.BASKETBALL,
            date=self.now.date() + datetime.timedelta(days=1),
            time=datetime.time(18),
            description='Bring water.',
            capacity=5,
        )
        data.update(changes)
        return Event.objects.create(**data)

    def assert_status(self, status, event=None):
        event = event or self.event
        event.refresh_from_db()
        self.assertEqual(event.status, status)


class EventAccessControlTests(EventManagementTestCase):
    """#27. The edit (#25) and remove-attendee (#33) URLs reuse the same mixin."""

    def test_non_owner_sees_no_cancel_control(self):
        self.client.force_login(self.other)
        response = self.client.get(self.detail_url)
        self.assertFalse(response.context['can_manage'])
        self.assertNotContains(response, 'Cancel Event')

    def test_anonymous_visitor_sees_no_cancel_control(self):
        response = self.client.get(self.detail_url)
        self.assertFalse(response.context['can_manage'])
        self.assertNotContains(response, 'Cancel Event')

    def test_organizer_and_administrator_see_the_cancel_control(self):
        for user in (self.host, self.admin):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(self.detail_url)
                self.assertTrue(response.context['can_manage'])
                self.assertContains(response, 'Cancel Event')

    def test_non_owner_gets_403_for_get_and_post_and_nothing_changes(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assert_status(EventStatus.SCHEDULED)

    def test_attendee_who_is_not_the_organizer_gets_403(self):
        RSVP.objects.create(event=self.event, user=self.player)
        self.client.force_login(self.player)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assert_status(EventStatus.SCHEDULED)

    def test_anonymous_requests_redirect_to_login_and_nothing_changes(self):
        login_url = f"{reverse('login')}?next={self.detail_url}"
        self.assertRedirects(self.client.get(self.url), login_url)
        self.assertRedirects(self.client.post(self.url), login_url)
        self.assert_status(EventStatus.SCHEDULED)

    def test_unknown_event_returns_404(self):
        self.client.force_login(self.host)
        response = self.client.post(reverse('event_cancel', kwargs={'pk': self.event.pk + 1000}))
        self.assertEqual(response.status_code, 404)


class EventParticipantRemovalTests(EventManagementTestCase):
    def setUp(self):
        super().setUp()
        self.rsvp = RSVP.objects.create(event=self.event, user=self.player)
        self.url = reverse('event_participant_remove', kwargs={'pk': self.event.pk, 'rsvp_pk': self.rsvp.pk})

    def test_organizer_removes_active_participant_without_deleting_rsvp(self):
        self.client.force_login(self.host)

        response = self.client.post(self.url, follow=True)

        self.rsvp.refresh_from_db()
        self.assertRedirects(response, self.detail_url + '#event-attendance-title')  # #122
        self.assertEqual(self.rsvp.status, RSVPStatus.REMOVED)
        self.assertContains(response, 'Participant removed from the event.')
        self.assertNotContains(response, '@manage-player')

    def test_administrator_can_remove_active_participant(self):
        self.client.force_login(self.admin)

        self.client.post(self.url)

        self.rsvp.refresh_from_db()
        self.assertEqual(self.rsvp.status, RSVPStatus.REMOVED)

    def test_only_managers_see_remove_controls(self):
        self.assertNotContains(self.client.get(self.detail_url), '>Remove</button>')
        self.client.force_login(self.host)
        response = self.client.get(self.detail_url)
        self.assertContains(response, 'class="btn btn--ghost event-roster-remove"')
        self.assertContains(response, r"Remove @manage\u002Dplayer from this event?")

    def test_roster_members_link_to_their_profiles(self):
        response = self.client.get(self.detail_url)

        self.assertContains(response, reverse('public_profile', kwargs={'username': self.host.username}))
        self.assertContains(response, reverse('public_profile', kwargs={'username': self.player.username}))

    def test_started_event_hides_remove_control_and_rejects_removal(self):
        self.event.date = self.now.date()
        self.event.time = datetime.time(11)
        self.event.save(update_fields=['date', 'time'])
        self.client.force_login(self.host)

        page = self.client.get(self.detail_url)
        response = self.client.post(self.url, follow=True)

        self.rsvp.refresh_from_db()
        self.assertNotContains(page, '>Remove</button>')
        self.assertEqual(self.rsvp.status, RSVPStatus.ACTIVE)
        self.assertContains(response, 'This event has already started, so participants can no longer be removed.')

    def test_cancelled_or_removed_event_hides_remove_control_and_rejects_removal(self):
        self.client.force_login(self.host)
        for status in (EventStatus.CANCELLED, EventStatus.REMOVED):
            with self.subTest(status=status):
                Event.objects.filter(pk=self.event.pk).update(status=status)

                page = self.client.get(self.detail_url)
                response = self.client.post(self.url, follow=True)

                self.rsvp.refresh_from_db()
                self.assertNotContains(page, '>Remove</button>')
                self.assertEqual(self.rsvp.status, RSVPStatus.ACTIVE)
                self.assertContains(response, 'Participants can only be removed from scheduled events.')
                Event.objects.filter(pk=self.event.pk).update(status=EventStatus.SCHEDULED)

    def test_roster_shows_member_skill_level_when_set(self):
        profile, _ = Profile.objects.get_or_create(user=self.player)
        profile.skill_level = 'intermediate'
        profile.save(update_fields=['skill_level'])
        self.client.force_login(self.host)

        response = self.client.get(self.detail_url)

        self.assertContains(response, '@manage-player</a> <span class="event-roster-skill">Intermediate</span>')

    def test_roster_omits_member_skill_level_when_not_set(self):
        self.client.force_login(self.host)

        response = self.client.get(self.detail_url)

        self.assertNotContains(response, '<span class="event-roster-skill"></span>')

    def test_non_manager_cannot_remove_participant(self):
        self.client.force_login(self.other)

        self.assertEqual(self.client.post(self.url).status_code, 403)

        self.rsvp.refresh_from_db()
        self.assertEqual(self.rsvp.status, RSVPStatus.ACTIVE)

    def test_anonymous_request_redirects_to_login(self):
        login_url = f"{reverse('login')}?next={self.detail_url}"

        self.assertRedirects(self.client.post(self.url), login_url)

        self.rsvp.refresh_from_db()
        self.assertEqual(self.rsvp.status, RSVPStatus.ACTIVE)

    def test_removal_is_post_only(self):
        self.client.force_login(self.host)

        self.assertEqual(self.client.get(self.url).status_code, 405)

        self.rsvp.refresh_from_db()
        self.assertEqual(self.rsvp.status, RSVPStatus.ACTIVE)

    def test_wrong_event_rsvp_returns_404(self):
        other_event = self.make_event()
        self.client.force_login(self.host)

        response = self.client.post(
            reverse('event_participant_remove', kwargs={'pk': other_event.pk, 'rsvp_pk': self.rsvp.pk})
        )

        self.assertEqual(response.status_code, 404)
        self.rsvp.refresh_from_db()
        self.assertEqual(self.rsvp.status, RSVPStatus.ACTIVE)

    def test_already_inactive_rsvp_is_not_changed(self):
        self.rsvp.status = RSVPStatus.CANCELLED
        self.rsvp.save(update_fields=['status'])
        self.client.force_login(self.host)

        response = self.client.post(self.url, follow=True)

        self.rsvp.refresh_from_db()
        self.assertEqual(self.rsvp.status, RSVPStatus.CANCELLED)
        self.assertContains(response, 'This participant is no longer attending this event.')


class EventCancelTests(EventManagementTestCase):
    """#28."""

    def test_organizer_cancels_and_the_row_is_kept(self):
        self.client.force_login(self.host)
        response = self.client.post(self.url, follow=True)
        self.assertRedirects(response, self.detail_url + '#event-attendance-title')  # #122
        self.assert_status(EventStatus.CANCELLED)
        self.assertContains(response, 'Cancelled by the host')
        self.assertContains(response, 'Your event has been cancelled.')

    def test_administrator_can_cancel(self):
        self.client.force_login(self.admin)
        self.client.post(self.url)
        self.assert_status(EventStatus.CANCELLED)

    def test_cancel_is_post_only(self):
        self.client.force_login(self.host)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assert_status(EventStatus.SCHEDULED)

    def test_cancel_control_asks_for_confirmation(self):
        self.client.force_login(self.host)
        self.assertContains(self.client.get(self.detail_url), "return confirm('Cancel this event?")

    def test_cancelled_event_keeps_its_roster_and_refuses_rsvp_changes(self):
        RSVP.objects.create(event=self.event, user=self.player)
        Event.objects.filter(pk=self.event.pk).update(status=EventStatus.CANCELLED)
        self.assertContains(self.client.get(self.detail_url), '@manage-player')

        self.client.force_login(self.other)
        joined = self.client.post(reverse('event_rsvp', kwargs={'pk': self.event.pk}), follow=True)
        self.assertContains(joined, 'This event has been cancelled.')
        self.assertFalse(joined.context['can_rsvp'])
        self.assertFalse(RSVP.objects.filter(event=self.event, user=self.other).exists())

        self.client.force_login(self.player)
        self.client.post(reverse('event_rsvp_cancel', kwargs={'pk': self.event.pk}))
        self.assertEqual(RSVP.objects.get(event=self.event, user=self.player).status, RSVPStatus.ACTIVE)

    def test_started_event_cannot_be_cancelled_and_shows_no_control(self):
        cases = {
            'earlier today': self.make_event(date=self.now.date(), time=datetime.time(10)),
            'yesterday': self.make_event(date=self.now.date() - datetime.timedelta(days=1)),
        }
        self.client.force_login(self.host)
        for label, event in cases.items():
            with self.subTest(label):
                self.assertNotContains(self.client.get(event.get_absolute_url()), 'Cancel Event')
                response = self.client.post(reverse('event_cancel', kwargs={'pk': event.pk}), follow=True)
                self.assertContains(response, 'This event has already started, so it can no longer be cancelled.')
                self.assert_status(EventStatus.SCHEDULED, event)

    def test_already_cancelled_or_removed_event_is_left_unchanged(self):
        messages_by_status = {
            EventStatus.CANCELLED: 'This event has already been cancelled.',
            EventStatus.REMOVED: 'This event has been removed.',
        }
        self.client.force_login(self.host)
        for status, message in messages_by_status.items():
            with self.subTest(status=status):
                event = self.make_event(status=status)
                self.assertNotContains(self.client.get(event.get_absolute_url()), 'Cancel Event')
                response = self.client.post(reverse('event_cancel', kwargs={'pk': event.pk}), follow=True)
                self.assertContains(response, message)
                self.assert_status(status, event)

    def test_only_a_cancelled_event_says_cancelled_by_the_host(self):
        self.assertNotContains(self.client.get(self.detail_url), 'Cancelled by the host')
        removed = self.make_event(status=EventStatus.REMOVED)
        self.assertNotContains(self.client.get(removed.get_absolute_url()), 'Cancelled by the host')


class CancelledEventDiscoveryTests(EventManagementTestCase):
    """#28: a cancelled event leaves public discovery but stays in the personal scopes."""

    def setUp(self):
        super().setUp()
        self.event.title = 'Called Off Game'
        self.event.save(update_fields=['title'])
        self.client.force_login(self.host)
        self.client.post(self.url)
        self.assert_status(EventStatus.CANCELLED)

    def test_hidden_from_the_public_events_tab(self):
        response = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'events'})
        self.assertNotContains(response, 'Called Off Game')

    def test_not_counted_on_the_public_map(self):
        parks = self.client.get(reverse('event_map_api')).json()['parks']
        self.assertEqual([park['event_count'] for park in parks if park['id'] == self.park.pk], [0])

    def test_hidden_from_the_public_park_panel(self):
        response = self.client.get(reverse('event_list'), {'view': 'map', 'park': self.park.pk, 'panel': '1'})
        self.assertNotContains(response, 'Called Off Game')

    def test_hidden_from_the_json_feed(self):
        self.assertNotContains(self.client.get(reverse('event_list_api')), 'Called Off Game')

    def test_hidden_from_the_park_page(self):
        response = self.client.get(reverse('park_detail', kwargs={'pk': self.park.pk}))
        self.assertNotContains(response, 'Called Off Game')

    def test_listed_with_a_cancelled_badge_under_hosting_and_attending(self):
        RSVP.objects.create(event=self.event, user=self.player)
        for user, scope in ((self.host, 'hosting'), (self.player, 'attending')):
            with self.subTest(scope=scope):
                self.client.force_login(user)
                response = self.client.get(reverse('event_list'), {'scope': scope, 'view': 'list', 'tab': 'events'})
                self.assertContains(response, 'Called Off Game')
                self.assertContains(response, '<span class="event-badge explore-status-badge">Cancelled</span>')
