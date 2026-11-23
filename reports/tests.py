import datetime
import re

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile
from events.models import Activity, Event, Message
from parks.models import Park, Review

from .models import Report

User = get_user_model()


class ReportTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user('report-author', email='private@example.test', password='secret')
        cls.reporter = User.objects.create_user('reporter')
        cls.admin = User.objects.create_user('moderator', is_staff=True)
        Profile.objects.update_or_create(user=cls.author, defaults={'bio': 'Public account biography'})
        cls.park = Park.objects.create(gispropnum='REPORT1', name='Report Park', borough='B')
        cls.event = Event.objects.create(
            organizer=cls.author,
            park=cls.park,
            activity=Activity.BASKETBALL,
            title='Event to report',
            date=timezone.localdate() + datetime.timedelta(days=1),
            time=datetime.time(18),
            description='Event contents',
        )
        cls.message = Message.objects.create(event=cls.event, author=cls.author, body='Discussion contents')
        cls.review = Review.objects.create(park=cls.park, user=cls.author, rating=3, body='Review contents')

    def targets(self):
        return (
            ('event', self.event, self.event.get_absolute_url(), 'Event contents'),
            ('message', self.message, self.event.get_absolute_url(), 'Discussion contents'),
            ('review', self.review, reverse('park_detail', args=[self.park.pk]), 'Review contents'),
            (
                'account',
                self.author,
                reverse('public_profile', args=[self.author.username]),
                'Public account biography',
            ),
        )

    def submit_url(self, kind, target):
        return reverse('submit_report', args=[kind, target.pk])

    def make_report(self, target, **changes):
        fields = {
            'reporter': self.reporter,
            'target': target,
            'target_snapshot': 'Saved snapshot',
            'reason': Report.Reason.SPAM,
        }
        return Report.objects.create(**{**fields, **changes})


class SubmitReportTests(ReportTestCase):
    def test_report_each_target_through_its_page_with_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.reporter)
        for kind, target, page_url, snapshot_text in self.targets():
            with self.subTest(kind=kind):
                page = client.get(page_url)
                url = self.submit_url(kind, target)
                self.assertContains(page, f'action="{url}"', count=1)
                form = page.content.decode().split(f'action="{url}"', 1)[1].split('</form>', 1)[0]
                token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', form)
                self.assertIsNotNone(token)
                response = client.post(
                    url,
                    {'reason': Report.Reason.SPAM, 'details': 'Please check.', 'csrfmiddlewaretoken': token.group(1)},
                    follow=True,
                )
                self.assertContains(response, 'Thanks, an administrator will review this.')
                report = Report.objects.get(content_type=ContentType.objects.get_for_model(target), object_id=target.pk)
                self.assertEqual((report.target, report.reporter, report.status), (target, self.reporter, 'open'))
                self.assertEqual((report.reason, report.details), ('spam', 'Please check.'))
                self.assertIn(snapshot_text, report.target_snapshot)
                self.assertNotIn(self.author.email, report.target_snapshot)
                self.assertNotIn(self.author.password, report.target_snapshot)

    def test_own_content_is_hidden_and_direct_posts_are_refused(self):
        self.client.force_login(self.author)
        for kind, target, page_url, _ in self.targets():
            with self.subTest(kind=kind):
                url = self.submit_url(kind, target)
                self.assertNotContains(self.client.get(page_url), url)
                self.assertEqual(self.client.post(url, {'reason': 'spam'}).status_code, 403)
        self.assertFalse(Report.objects.exists())

    def test_anonymous_cannot_report(self):
        for kind, target, page_url, _ in self.targets():
            with self.subTest(kind=kind):
                url = self.submit_url(kind, target)
                self.assertNotContains(self.client.get(page_url), url)
                self.assertRedirects(self.client.post(url, {'reason': 'spam'}), f"{reverse('login')}?next={page_url}")
        self.assertFalse(Report.objects.exists())

    def test_missing_reason_returns_field_error_and_preserves_details(self):
        self.client.force_login(self.reporter)
        response = self.client.post(self.submit_url('message', self.message), {'details': 'Keep these details'})
        self.assertContains(response, 'This field is required.', status_code=400)
        self.assertContains(response, 'Keep these details', status_code=400)
        self.assertFalse(Report.objects.exists())

    def test_only_post_and_known_targets_are_accepted(self):
        self.client.force_login(self.reporter)
        self.assertEqual(self.client.get(self.submit_url('event', self.event)).status_code, 405)
        self.assertEqual(self.client.post(self.submit_url('park', self.park), {'reason': 'spam'}).status_code, 404)
        self.assertFalse(Report.objects.exists())

    def test_duplicate_open_report_and_resubmission_after_resolution(self):
        self.client.force_login(self.reporter)
        url = self.submit_url('event', self.event)
        first = self.make_report(self.event)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_report(self.event)
        response = self.client.post(url, {'reason': 'other'}, follow=True)
        self.assertContains(response, 'Your report is already under review.')
        self.assertEqual(list(Report.objects.all()), [first])
        first.status = Report.Status.RESOLVED
        first.save(update_fields=['status'])
        self.client.post(url, {'reason': 'other'})
        self.assertEqual(Report.objects.filter(status=Report.Status.OPEN, details='').count(), 1)
        self.assertEqual(Report.objects.count(), 2)


class ReportQueueTests(ReportTestCase):
    def test_defaults_to_open_and_links_to_targets(self):
        open_report = self.make_report(self.message)
        resolved_report = self.make_report(self.event, status=Report.Status.RESOLVED)
        self.client.force_login(self.admin)
        queue = reverse('admin:reports_report_changelist')

        response = self.client.get(queue)
        self.assertEqual(list(response.context['cl'].result_list), [open_report])
        self.assertContains(response, f'{self.event.get_absolute_url()}#event-message-{self.message.pk}')
        self.assertContains(response, 'Saved snapshot')
        self.assertContains(response, self.reporter.username)
        resolved = self.client.get(queue, {'status': 'resolved'})
        self.assertEqual(list(resolved.context['cl'].result_list), [resolved_report])
        all_reports = self.client.get(queue, {'status': 'all'})
        self.assertEqual(list(all_reports.context['cl'].result_list), [open_report, resolved_report])

    def test_staff_resolves_once_and_records_outcome_in_audit_log(self):
        report = self.make_report(self.review)
        url = reverse('admin:reports_report_change', args=[report.pk])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), reverse('admin:parks_review_change', args=[self.review.pk]))
        invalid = self.client.post(url, {'outcome': ''})
        self.assertContains(invalid, 'This field is required.')
        report.refresh_from_db()
        self.assertEqual(report.status, Report.Status.OPEN)

        before = timezone.now()
        queue = reverse('admin:reports_report_changelist')
        result = self.client.post(url, {'outcome': 'content_removed', 'details': 'Forged details'}, follow=True)
        self.assertRedirects(result, queue)
        self.assertEqual(list(result.context['cl'].result_list), [])
        report.refresh_from_db()
        self.assertEqual(
            (report.status, report.outcome, report.resolved_by), ('resolved', 'content_removed', self.admin)
        )
        self.assertGreaterEqual(report.resolved_at, before)
        self.assertEqual(report.details, '')
        self.assertTrue(Review.objects.filter(pk=self.review.pk).exists())

        logs = LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(Report), object_id=str(report.pk))
        entry = logs.get()
        self.assertEqual((entry.user, entry.action_flag), (self.admin, CHANGE))
        self.assertIn('Resolved report: Content removed', entry.get_change_message())
        self.assertIn('Saved snapshot', entry.get_change_message())
        self.assertNotContains(self.client.get(url), 'name="outcome"')
        self.assertEqual(self.client.post(url, {'outcome': 'no_action'}).status_code, 403)
        self.assertEqual(logs.count(), 1)

    def test_deleted_target_keeps_snapshot(self):
        report = self.make_report(self.message, target_snapshot='Discussion contents')
        self.message.delete()
        self.client.force_login(self.admin)
        page = self.client.get(reverse('admin:reports_report_change', args=[report.pk]))
        self.assertContains(page, 'Discussion contents')
        self.assertContains(page, ' (deleted)')
        report.refresh_from_db()
        self.assertIsNone(report.target)

    def test_non_staff_cannot_view_or_resolve(self):
        report = self.make_report(self.author)
        self.client.force_login(self.reporter)
        queue = reverse('admin:reports_report_changelist')
        url = reverse('admin:reports_report_change', args=[report.pk])
        for response in (self.client.get(queue), self.client.post(url, {'outcome': 'no_action'})):
            self.assertTrue(response.url.startswith(reverse('admin:login')))
        report.refresh_from_db()
        self.assertEqual(report.status, Report.Status.OPEN)
        self.assertFalse(LogEntry.objects.exists())
