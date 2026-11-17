"""US-7.6 (#49): administrators inspect accounts and suspend or restore their access."""

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse

from events.models import RSVP
from parks.models import Review

from .test_admin_access import PASSWORD, AdminAccessTestCase

CHANGELIST = reverse('admin:auth_user_changelist')


class AccountAccessTestCase(AdminAccessTestCase):
    def log_in(self, user):
        return self.client.post(reverse('login'), {'username': user.email, 'password': PASSWORD})

    def run_action(self, action, *users):
        return self.client.post(
            CHANGELIST, {'action': action, ACTION_CHECKBOX_NAME: [user.pk for user in users]}, follow=True
        )

    def assert_active(self, user, active):
        user.refresh_from_db()
        self.assertEqual(user.is_active, active)


class InspectAccountTests(AccountAccessTestCase):
    def test_account_page_shows_the_details_and_links_to_their_activity(self):
        RSVP.objects.create(event=self.event, user=self.participant)
        Review.objects.create(park=self.park, user=self.organizer, rating=4, body='Nice fields')
        self.client.force_login(self.admin)

        response = self.client.get(self.user_change_url(self.organizer))

        for shown in ('host', 'host@example.test', 'Date joined', 'Last login', 'Active'):
            self.assertContains(response, shown)
        pk = self.organizer.pk
        self.assertContains(response, f'{reverse("admin:events_event_changelist")}?organizer__id__exact={pk}')
        self.assertContains(response, 'Hosted events (1)')
        self.assertContains(response, 'RSVPs (0)')
        self.assertContains(response, 'Park reviews (1)')

    def test_activity_links_open_lists_of_just_that_account(self):
        RSVP.objects.create(event=self.event, user=self.participant)
        self.client.force_login(self.admin)

        for url, shown, hidden in (
            (f'{reverse("admin:events_event_changelist")}?organizer__id__exact={self.organizer.pk}', 'Pickup', None),
            (f'{reverse("admin:events_rsvp_changelist")}?user__id__exact={self.participant.pk}', 'player', None),
            (f'{reverse("admin:events_rsvp_changelist")}?user__id__exact={self.organizer.pk}', '0 RSVPs', 'player'),
        ):
            with self.subTest(url):
                response = self.client.get(url)
                self.assertContains(response, shown)
                if hidden:
                    self.assertNotContains(response, f'>{hidden}<')

    def test_account_list_shows_join_date_last_login_and_active(self):
        self.client.force_login(self.admin)

        response = self.client.get(CHANGELIST)

        for column in ('Date joined', 'Last login', 'Active'):
            self.assertContains(response, column)


class SuspendTests(AccountAccessTestCase):
    def test_suspended_account_cant_log_in(self):
        self.client.force_login(self.admin)
        self.run_action('suspend_accounts', self.participant)
        self.client.logout()

        response = self.log_in(self.participant)

        self.assert_active(self.participant, False)
        self.assertContains(response, 'Invalid email or password')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_existing_session_stops_working_on_the_next_request(self):
        self.client.force_login(self.participant)
        self.assertEqual(self.client.get(reverse('event_create')).status_code, 200)

        self.participant.is_active = False
        self.participant.save(update_fields=['is_active'])
        response = self.client.get(reverse('event_create'))

        self.assertRedirects(response, f'{reverse("login")}?next={reverse("event_create")}')

    def test_suspended_profile_returns_404(self):
        self.participant.is_active = False
        self.participant.save(update_fields=['is_active'])

        response = self.client.get(reverse('public_profile', kwargs={'username': self.participant.username}))

        self.assertEqual(response.status_code, 404)

    def test_suspending_is_logged(self):
        self.client.force_login(self.admin)

        response = self.run_action('suspend_accounts', self.participant, self.organizer)

        self.assertContains(response, 'Suspended 2 accounts.')
        entries = LogEntry.objects.filter(change_message__startswith='Suspended account')
        self.assertEqual(entries.count(), 2)
        self.assertEqual({entry.user for entry in entries}, {self.admin})
        self.assertEqual({entry.action_flag for entry in entries}, {CHANGE})
        self.assertNotIn('password', entries.first().get_change_message())

    def test_suspending_from_the_account_form_works_too(self):
        self.client.force_login(self.admin)

        self.post_user_change(self.participant, is_active=False)

        self.assert_active(self.participant, False)
        self.assertTrue(LogEntry.objects.filter(object_id=str(self.participant.pk), action_flag=CHANGE).exists())

    def test_suspending_deletes_their_sessions_so_restoring_needs_a_new_login(self):
        for how in ('action', 'form'):
            with self.subTest(how):
                player = Client()
                player.force_login(self.participant)
                other = Client()
                other.force_login(self.organizer)
                self.client.force_login(self.admin)

                if how == 'action':
                    self.run_action('suspend_accounts', self.participant)
                else:
                    self.post_user_change(self.participant, is_active=False)
                self.assertFalse(Session.objects.filter(session_key=player.session.session_key).exists())
                self.assertTrue(Session.objects.filter(session_key=other.session.session_key).exists())

                self.run_action('restore_accounts', self.participant)
                response = player.get(reverse('event_create'))
                self.assertRedirects(response, f'{reverse("login")}?next={reverse("event_create")}')


class RestoreTests(AccountAccessTestCase):
    def test_restored_account_can_log_in_again(self):
        self.participant.is_active = False
        self.participant.save(update_fields=['is_active'])
        self.client.force_login(self.admin)

        response = self.run_action('restore_accounts', self.participant)
        self.client.logout()

        self.assertContains(response, 'Restored 1 account.')
        self.assert_active(self.participant, True)
        self.assertRedirects(self.log_in(self.participant), reverse('home'))
        self.assertTrue(LogEntry.objects.filter(change_message__startswith='Restored account').exists())


class NonAdministratorTests(AccountAccessTestCase):
    def test_participants_cant_view_or_change_accounts(self):
        self.client.force_login(self.participant)
        change_url = self.user_change_url(self.organizer)

        view = self.client.get(change_url)
        change = self.post_user_change(self.organizer, is_active=False)
        action = self.client.post(CHANGELIST, {'action': 'suspend_accounts', ACTION_CHECKBOX_NAME: [self.organizer.pk]})

        self.assertRedirects(view, f'{reverse("admin:login")}?next={change_url}')
        self.assertRedirects(change, f'{reverse("admin:login")}?next={change_url}')
        self.assertRedirects(action, f'{reverse("admin:login")}?next={CHANGELIST}')
        self.assert_active(self.organizer, True)

    def test_administrators_cant_suspend_other_staff_or_themselves(self):
        self.client.force_login(self.admin)

        response = self.run_action('suspend_accounts', self.other_admin, self.admin, self.participant)

        self.assertContains(response, 'Suspended 1 account.')
        self.assertContains(response, "Skipped 2 accounts you can&#x27;t change")
        self.assert_active(self.other_admin, True)
        self.assert_active(self.admin, True)
        self.assert_active(self.participant, False)

    def test_nobody_can_suspend_themselves_from_the_form(self):
        for user in (self.admin, self.superuser):
            with self.subTest(user.username):
                self.client.force_login(user)
                self.post_user_change(user, is_active=False)
                self.assert_active(user, True)

    def test_superusers_can_suspend_staff(self):
        self.client.force_login(self.superuser)

        self.run_action('suspend_accounts', self.admin)

        self.assert_active(self.admin, False)


class NoDeleteTests(AccountAccessTestCase):
    def test_accounts_cant_be_deleted_in_the_admin(self):
        for user in (self.admin, self.superuser):
            with self.subTest(user.username):
                self.client.force_login(user)
                changelist = self.client.get(CHANGELIST)
                self.assertNotContains(changelist, 'value="delete_selected"')
                delete_url = reverse('admin:auth_user_delete', args=[self.participant.pk])
                self.assertEqual(self.client.post(delete_url, {'post': 'yes'}).status_code, 403)
        self.assertTrue(type(self.participant).objects.filter(pk=self.participant.pk).exists())
