"""#52: administrative actions are recorded in a read-only audit trail."""

import datetime

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Activity, Event, EventStatus, Message
from parks.models import Borough, Park, Review

from .audit import log_admin_action

User = get_user_model()
PASSWORD = 'Hudson-River-Run-77'
CHANGELIST = 'admin:admin_logentry_changelist'


class AuditLogTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('mod', 'mod@example.test', PASSWORD, is_staff=True)
        cls.other_admin = User.objects.create_user('mod2', 'mod2@example.test', PASSWORD, is_staff=True)
        cls.player = User.objects.create_user('player', 'player@example.test', PASSWORD)
        cls.park = Park.objects.create(gispropnum='AUD1', name='Audit Park', borough=Borough.BRONX)
        cls.event = Event.objects.create(
            organizer=cls.player,
            park=cls.park,
            title='Sunday run',
            activity=Activity.RUNNING,
            date=datetime.date.today() + datetime.timedelta(days=2),
            time=datetime.time(9),
            description='Easy pace',
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def entries(self, **filters):
        return LogEntry.objects.filter(**filters)


class AdminSiteActionsTests(AuditLogTestCase):
    # Scenario: Admin site actions recorded
    def test_change_in_admin_records_actor_action_target_and_time(self):
        url = reverse('admin:parks_park_change', args=[self.park.pk])
        form = self.client.get(url).context['adminform'].form
        data = {name: '' if value is None else value for name, value in form.initial.items()}
        data.update({key: 'on' for key, value in data.items() if value is True})
        data = {key: value for key, value in data.items() if value is not False}
        data['name'] = 'Audit Park North'
        # The park form carries a facility inline (#45); submit it empty.
        data.update({'facilities-TOTAL_FORMS': '0', 'facilities-INITIAL_FORMS': '0'})
        self.assertEqual(self.client.post(url, data).status_code, 302)

        entry = self.entries(action_flag=CHANGE).get()
        self.assertEqual(entry.user, self.admin)
        self.assertEqual(entry.content_type, ContentType.objects.get_for_model(Park))
        self.assertEqual(entry.object_id, str(self.park.pk))
        self.assertTrue(entry.object_repr.startswith('Audit Park North'))
        self.assertIn('Name', entry.get_change_message())
        self.assertIsNotNone(entry.action_time)

    def test_delete_in_admin_is_recorded(self):
        review = Review.objects.create(park=self.park, user=self.player, rating=1, body='Spam spam spam')
        url = reverse('admin:parks_review_delete', args=[review.pk])
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 302)

        self.assertFalse(Review.objects.exists())
        entry = self.entries(action_flag=DELETION).get()
        self.assertEqual((entry.user, entry.object_id), (self.admin, str(review.pk)))


class InAppActionsTests(AuditLogTestCase):
    # Scenario: In-app staff actions recorded
    def test_helper_records_the_entry_with_a_snapshot(self):
        message = Message.objects.create(event=self.event, author=self.player, body='Buy cheap watches')

        entry = log_admin_action(self.admin, message, 'delete', 'Removed message: spam')
        message_pk = message.pk
        message.delete()

        entry.refresh_from_db()
        self.assertEqual(entry.user, self.admin)
        self.assertEqual(entry.action_flag, DELETION)
        self.assertEqual(entry.content_type, ContentType.objects.get_for_model(Message))
        self.assertEqual(entry.object_id, str(message_pk))
        details = entry.get_change_message()
        self.assertTrue(details.startswith('Removed message: spam'))
        self.assertIn('body: Buy cheap watches', details)
        self.assertIn('author: player', details)

    def test_soft_removal_is_a_change(self):
        self.event.status = EventStatus.REMOVED
        self.event.save()
        entry = log_admin_action(self.admin, self.event, 'change', 'Removed event')
        self.assertEqual(entry.action_flag, CHANGE)
        self.assertIn('status: removed', entry.get_change_message())

    def test_snapshot_never_includes_passwords(self):
        entry = log_admin_action(self.admin, self.player, 'change', 'Deactivated account')
        self.assertNotIn('password', entry.get_change_message())
        self.assertNotIn(self.player.password, entry.get_change_message())

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ValueError):
            log_admin_action(self.admin, self.event, 'hide', 'Hid it')
        self.assertFalse(self.entries().exists())

    def test_in_app_entries_show_in_the_audit_trail(self):
        log_admin_action(self.admin, self.event, 'change', 'Removed event: duplicate')
        entry = self.entries().get()
        self.assertContains(self.client.get(reverse(CHANGELIST)), 'Removed event: duplicate')
        detail = self.client.get(reverse('admin:admin_logentry_change', args=[entry.pk]))
        self.assertContains(detail, 'Snapshot:<br>')
        self.assertContains(detail, 'title: Sunday run')


class ReviewHistoryTests(AuditLogTestCase):
    # Scenario: Review history
    def setUp(self):
        super().setUp()
        self.mine = log_admin_action(self.admin, self.event, 'change', 'First change')
        self.theirs = log_admin_action(self.other_admin, self.park, 'add', 'Added a park')
        LogEntry.objects.filter(pk=self.theirs.pk).update(action_time=self.theirs.action_time - datetime.timedelta(40))

    def listed(self, **params):
        response = self.client.get(reverse(CHANGELIST), params)
        self.assertEqual(response.status_code, 200)
        return set(response.context['cl'].result_list)

    def test_filter_by_actor(self):
        self.assertEqual(self.listed(actor=self.admin.pk), {self.mine})
        self.assertEqual(self.listed(actor=self.other_admin.pk), {self.theirs})

    def test_actor_filter_lists_only_accounts_with_entries(self):
        response = self.client.get(reverse(CHANGELIST))
        self.assertContains(response, f'?actor={self.other_admin.pk}')
        self.assertNotContains(response, f'?actor={self.player.pk}"')

    def test_filter_by_action_type(self):
        self.assertEqual(self.listed(action_flag__exact=ADDITION), {self.theirs})
        self.assertEqual(self.listed(action_flag__exact=CHANGE), {self.mine})

    def test_filter_by_date(self):
        week_ago = (timezone.now() - datetime.timedelta(days=7)).isoformat()
        self.assertEqual(self.listed(action_time__gte=week_ago), {self.mine})

    def test_entries_are_read_only(self):
        url = reverse('admin:admin_logentry_change', args=[self.mine.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'name="_save"')
        self.assertEqual(self.client.post(url, {'change_message': 'Edited'}).status_code, 403)
        delete = reverse('admin:admin_logentry_delete', args=[self.mine.pk])
        self.assertEqual(self.client.post(delete, {'post': 'yes'}).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:admin_logentry_add')).status_code, 403)
        self.mine.refresh_from_db()
        self.assertTrue(self.mine.change_message.startswith('First change'))
        self.assertEqual(LogEntry.objects.count(), 2)

    def test_superusers_cannot_edit_entries_either(self):
        root = User.objects.create_superuser('root', 'root@example.test', PASSWORD)
        self.client.force_login(root)
        url = reverse('admin:admin_logentry_change', args=[self.mine.pk])
        self.assertEqual(self.client.post(url, {'change_message': 'Edited'}).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:admin_logentry_add')).status_code, 403)


class NonAdministratorTests(AuditLogTestCase):
    # Scenario: Non-administrator
    def test_audit_trail_is_denied_to_non_administrators(self):
        entry = log_admin_action(self.admin, self.event, 'change', 'Removed event')
        self.client.force_login(self.player)
        for url in (reverse(CHANGELIST), reverse('admin:admin_logentry_change', args=[entry.pk])):
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), f"{reverse('admin:login')}?next={url}")
        self.client.logout()
        url = reverse(CHANGELIST)
        self.assertRedirects(self.client.get(url), f"{reverse('admin:login')}?next={url}")
