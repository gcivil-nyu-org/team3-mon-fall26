"""US-7.7 (#50) site banners and US-7.8 (#51) administrator metrics."""

import datetime
import re

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.audit import log_admin_action
from events.models import RSVP, Activity, Event, EventStatus, Message, RSVPStatus
from parks.models import Borough, Park, Review
from reports.models import Report

from . import metrics
from .context_processors import site_banner
from .models import BANNER_MESSAGE_MAX_LENGTH, SiteBanner

User = get_user_model()

BANNER_PATTERN = re.compile(
    r'<div class="site-banner" role="region" aria-label="Site announcement">\s*'
    r'<p class="site-banner-message">(.*?)</p>',
    re.DOTALL,
)


def make_event(organizer, park, days=3, **fields):
    return Event.objects.create(
        **{
            'organizer': organizer,
            'park': park,
            'title': 'Pickup',
            'activity': Activity.BASKETBALL,
            'date': timezone.localdate() + datetime.timedelta(days=days),
            'time': datetime.time(10),
            'end_time': datetime.time(12),
            'description': 'Bring water.',
            **fields,
        }
    )


class BannerTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('banner-admin', 'banner-admin@example.test', is_staff=True)
        cls.member = User.objects.create_user('banner-member', 'banner-member@example.test')

    def banner(self, message='Courts at Pier 2 are closed Saturday.', **fields):
        return SiteBanner.objects.create(message=message, **fields)

    def shown(self, response):
        """The messages of every banner on the page."""
        return BANNER_PATTERN.findall(response.content.decode())

    def add_banner(self, **data):
        return self.client.post(
            reverse('admin:sitetools_sitebanner_add'),
            {'message': '', 'is_active': 'on', 'ends_at_0': '', 'ends_at_1': '', **data},
        )


class PublishTests(BannerTestCase):
    # Scenario: Publish
    def test_active_banner_shows_once_at_the_top_of_every_page_for_visitors(self):
        self.banner()
        for url in ('/', reverse('login'), reverse('register'), reverse('event_list')):
            with self.subTest(url=url):
                self.assertEqual(self.shown(self.client.get(url)), ['Courts at Pier 2 are closed Saturday.'])

    def test_active_banner_shows_on_every_page_for_members(self):
        self.banner()
        self.client.force_login(self.member)
        for url in (
            '/',
            reverse('event_list'),
            reverse('event_create'),
            reverse('public_profile', kwargs={'username': self.member.username}),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.shown(self.client.get(url)), ['Courts at Pier 2 are closed Saturday.'])

    def test_banner_comes_before_the_header(self):
        self.banner()
        page = self.client.get(reverse('event_list')).content.decode()
        self.assertLess(page.index('class="site-banner"'), page.index('class="site-header"'))

    def test_landing_page_shows_it_inside_the_floating_nav(self):
        self.banner()
        page = self.client.get('/').content.decode()
        nav = page.index('<header class="lp-nav"')
        self.assertLess(nav, page.index('class="site-banner"'))
        self.assertLess(page.index('class="site-banner"'), page.index('lp-nav-inner'))

    def test_banner_shows_until_it_is_deactivated(self):
        banner = self.banner()
        self.assertEqual(len(self.shown(self.client.get('/'))), 1)
        banner.is_active = False
        banner.save()
        self.assertEqual(self.shown(self.client.get('/')), [])

    def test_banner_shows_until_its_end_date_passes(self):
        ends_at = timezone.now() + datetime.timedelta(hours=1)
        banner = self.banner(ends_at=ends_at)
        self.assertEqual(len(self.shown(self.client.get('/'))), 1)
        self.assertEqual(SiteBanner.objects.current(now=ends_at - datetime.timedelta(seconds=1)), banner)
        self.assertIsNone(SiteBanner.objects.current(now=ends_at))

        banner.ends_at = timezone.now() - datetime.timedelta(minutes=1)
        banner.save()
        self.assertEqual(self.shown(self.client.get('/')), [])

    def test_no_banner_leaves_no_empty_markup(self):
        self.banner(is_active=False)
        self.assertNotContains(self.client.get('/'), 'site-banner')

    def test_message_is_shown_as_text(self):
        self.banner(message='<b>Storm</b> & rain')
        self.assertEqual(self.shown(self.client.get('/')), ['&lt;b&gt;Storm&lt;/b&gt; &amp; rain'])

    def test_administrator_publishes_a_banner_in_the_admin(self):
        self.client.force_login(self.admin)
        response = self.add_banner(message='  Explore is down for maintenance tonight.  ')
        self.assertRedirects(response, reverse('admin:sitetools_sitebanner_changelist'))

        banner = SiteBanner.objects.get()
        self.assertEqual(banner.message, 'Explore is down for maintenance tonight.')
        self.assertEqual(banner.created_by, self.admin)
        self.assertIsNone(banner.ends_at)
        self.assertTrue(LogEntry.objects.filter(object_id=str(banner.pk), action_flag=ADDITION).exists())
        self.client.logout()
        self.assertEqual(self.shown(self.client.get('/')), ['Explore is down for maintenance tonight.'])

    def test_administrator_can_set_an_end_date(self):
        self.client.force_login(self.admin)
        self.add_banner(message='Holiday hours', ends_at_0='2030-01-02', ends_at_1='18:00:00')
        ends_at = SiteBanner.objects.get().ends_at
        self.assertEqual(timezone.localtime(ends_at).replace(tzinfo=None), datetime.datetime(2030, 1, 2, 18))

    def test_administrator_takes_a_banner_down_by_unticking_active(self):
        banner = self.banner()
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('admin:sitetools_sitebanner_change', args=[banner.pk]),
            {'message': banner.message, 'ends_at_0': '', 'ends_at_1': ''},
        )
        self.assertEqual(response.status_code, 302)
        banner.refresh_from_db()
        self.assertFalse(banner.is_active)
        self.assertEqual(self.shown(self.client.get('/')), [])


class MultipleBannersTests(BannerTestCase):
    # Scenario: Multiple banners
    def test_newest_active_banner_is_the_only_one_shown(self):
        now = timezone.now()
        banners = [
            self.banner(message='Oldest'),
            self.banner(message='Newest showing'),
            self.banner(message='Newer but inactive', is_active=False),
            self.banner(message='Newer but ended', ends_at=now - datetime.timedelta(minutes=1)),
        ]
        for age, banner in zip((4, 3, 2, 1), banners):
            SiteBanner.objects.filter(pk=banner.pk).update(created_at=now - datetime.timedelta(hours=age))

        self.assertEqual(self.shown(self.client.get('/')), ['Newest showing'])

    def test_admin_list_marks_the_banner_on_the_site(self):
        older = self.banner(message='Older')
        newer = self.banner(message='Newer')
        SiteBanner.objects.filter(pk=older.pk).update(created_at=timezone.now() - datetime.timedelta(days=1))
        self.client.force_login(self.admin)

        response = self.client.get(reverse('admin:sitetools_sitebanner_changelist'))
        on_site = {banner.pk: banner.is_on_site for banner in response.context['cl'].result_list}
        self.assertEqual(on_site, {newer.pk: True, older.pk: False})
        self.assertContains(response, 'On the site now')


class InvalidOrUnauthorizedTests(BannerTestCase):
    # Scenario: Empty or unauthorized
    def test_blank_message_is_rejected_and_nothing_is_published(self):
        self.client.force_login(self.admin)
        for message in ('', '   '):
            with self.subTest(message=message):
                response = self.add_banner(message=message)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'This field is required.')
        self.assertFalse(SiteBanner.objects.exists())

    def test_message_over_the_limit_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.add_banner(message='x' * (BANNER_MESSAGE_MAX_LENGTH + 1))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'at most {BANNER_MESSAGE_MAX_LENGTH} characters')
        self.assertFalse(SiteBanner.objects.exists())

    def test_members_and_visitors_are_sent_to_the_admin_login_and_publish_nothing(self):
        url = reverse('admin:sitetools_sitebanner_add')
        for user in (None, self.member):
            with self.subTest(user=user):
                if user:
                    self.client.force_login(user)
                self.assertRedirects(self.client.get(url), f"{reverse('admin:login')}?next={url}")
                self.assertRedirects(self.add_banner(message='Hi'), f"{reverse('admin:login')}?next={url}")
        self.assertFalse(SiteBanner.objects.exists())

    def test_administrators_take_banners_down_instead_of_deleting_them(self):
        banner = self.banner()
        self.client.force_login(self.admin)
        response = self.client.post(reverse('admin:sitetools_sitebanner_delete', args=[banner.pk]), {'post': 'yes'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(SiteBanner.objects.filter(pk=banner.pk).exists())


class BannerLookupTests(BannerTestCase):
    def test_responses_that_do_not_show_the_banner_do_not_look_it_up(self):
        park = Park.objects.create(gispropnum='SB1', name='Banner Park', borough=Borough.BROOKLYN)
        event = make_event(self.member, park)
        Message.objects.create(event=event, author=self.member, body='Running late')
        self.banner()

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('event_discussion_updates', kwargs={'pk': event.pk}))
        self.assertEqual(len(response.json()['messages']), 1)
        self.assertFalse(any('sitetools_sitebanner' in query['sql'] for query in queries))


class DeploymentCheckModeTests(SimpleTestCase):
    @override_settings(DEPLOYMENT_CHECK_ONLY=True)
    def test_check_mode_never_looks_up_a_banner(self):
        self.assertEqual(site_banner(RequestFactory().get('/')), {})


class MetricsTestCase(TestCase):
    """One of everything a metric should count, next to things it shouldn't."""

    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        month_ago = cls.now - datetime.timedelta(days=30)

        cls.admin = User.objects.create_user('metrics-admin', 'metrics-admin@example.test', is_staff=True)
        cls.veteran = User.objects.create_user('veteran', 'veteran@example.test')
        cls.organizer = User.objects.create_user('organizer', 'organizer@example.test')
        cls.suspended = User.objects.create_user('suspended', 'suspended@example.test', is_active=False)
        cls.newcomer = User.objects.create_user('newcomer', 'newcomer@example.test')
        User.objects.exclude(pk=cls.newcomer.pk).update(date_joined=month_ago)
        User.objects.filter(pk=cls.newcomer.pk).update(date_joined=cls.now - datetime.timedelta(days=2))

        cls.park = Park.objects.create(gispropnum='MET1', name='Metrics Park', borough=Borough.QUEENS)
        cls.upcoming = make_event(cls.organizer, cls.park, days=3)
        cls.old_upcoming = make_event(cls.organizer, cls.park, days=5)
        Event.objects.filter(pk=cls.old_upcoming.pk).update(created_at=month_ago)
        cls.cancelled = make_event(cls.organizer, cls.park, days=3, status=EventStatus.CANCELLED)
        cls.ended = make_event(cls.organizer, cls.park, days=-3)

        RSVP.objects.create(event=cls.upcoming, user=cls.veteran)
        RSVP.objects.create(event=cls.old_upcoming, user=cls.newcomer)
        RSVP.objects.create(event=cls.upcoming, user=cls.newcomer, status=RSVPStatus.CANCELLED)
        RSVP.objects.create(event=cls.cancelled, user=cls.veteran)
        RSVP.objects.create(event=cls.ended, user=cls.veteran)

        cls.review = Review.objects.create(park=cls.park, user=cls.veteran, rating=4, body='Good courts')
        Review.objects.create(park=cls.park, user=cls.newcomer, rating=2, body='Nets torn')

        event_type = ContentType.objects.get_for_model(Event)
        for reporter, status in ((cls.veteran, 'open'), (cls.newcomer, 'open'), (cls.admin, 'resolved')):
            Report.objects.create(
                reporter=reporter,
                content_type=event_type,
                object_id=cls.upcoming.pk,
                target_snapshot='Pickup',
                reason=Report.Reason.SPAM,
                status=status,
            )

        log_admin_action(cls.admin, cls.ended, 'change', 'Recent action')
        old = log_admin_action(cls.admin, cls.ended, 'change', 'Old action')
        LogEntry.objects.filter(pk=old.pk).update(action_time=cls.now - datetime.timedelta(days=8))


class MetricFunctionTests(MetricsTestCase):
    # "Each metric is a single count() query in a small, separately tested function."
    def assertMetric(self, metric, expected):
        with self.assertNumQueries(1):
            self.assertEqual(metric(self.now), expected)

    def test_active_users_leave_out_suspended_accounts(self):
        self.assertMetric(metrics.active_users, 4)

    def test_new_users_are_the_last_7_days(self):
        self.assertMetric(metrics.new_users, 1)

    def test_upcoming_events_leave_out_cancelled_and_ended_games(self):
        self.assertMetric(metrics.upcoming_events, 2)

    def test_events_created_are_the_last_7_days_whatever_their_status(self):
        self.assertMetric(metrics.events_created, 3)

    def test_active_rsvps_count_only_active_ones_on_upcoming_events(self):
        self.assertMetric(metrics.active_rsvps_on_upcoming_events, 2)

    def test_total_reviews(self):
        self.assertMetric(metrics.total_reviews, 2)

    def test_open_reports_leave_out_resolved_ones(self):
        self.assertMetric(metrics.open_reports, 2)

    def test_suspended_accounts(self):
        self.assertMetric(metrics.suspended_accounts, 1)

    def test_administrator_actions_are_the_last_7_days(self):
        self.assertMetric(metrics.admin_actions, 1)

    def test_a_cancelled_event_drops_out_of_upcoming_and_its_rsvps_with_it(self):
        Event.objects.filter(pk=self.upcoming.pk).update(status=EventStatus.CANCELLED)
        self.assertMetric(metrics.upcoming_events, 1)
        self.assertMetric(metrics.active_rsvps_on_upcoming_events, 1)


class MetricsPageTests(MetricsTestCase):
    url = reverse('admin_metrics')

    def values(self, response):
        return {label: value for _section, rows in response.context['sections'] for label, value in rows}

    # Scenario: View metrics
    def test_administrator_sees_every_required_metric_with_its_value(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'admin/metrics.html')
        self.assertEqual(
            self.values(response),
            {
                'Active users': 4,
                'New users (last 7 days)': 1,
                'Upcoming events': 2,
                'Events created (last 7 days)': 3,
                'Active RSVPs on upcoming events': 2,
                'Park reviews': 2,
                'Open reports': 2,
                'Suspended accounts': 1,
                'Administrator actions (last 7 days)': 1,
            },
        )
        self.assertContains(response, '<th scope="row">Open reports</th>', html=False)
        self.assertContains(response, 'Site metrics')

    # Scenario: Records change
    def test_refreshing_shows_current_values(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.values(self.client.get(self.url))['Park reviews'], 2)
        self.review.delete()
        User.objects.filter(pk=self.veteran.pk).update(is_active=False)
        values = self.values(self.client.get(self.url))
        self.assertEqual(values['Park reviews'], 1)
        self.assertEqual(values['Suspended accounts'], 2)
        self.assertEqual(values['Active users'], 3)

    # Scenario: Non-administrator
    def test_members_and_visitors_are_sent_to_the_admin_login(self):
        for user in (None, self.veteran):
            with self.subTest(user=user):
                if user:
                    self.client.force_login(user)
                response = self.client.get(self.url)
                self.assertRedirects(response, f"{reverse('admin:login')}?next={self.url}")
                self.assertNotIn('sections', response.context or {})

    def test_admin_pages_link_to_the_metrics_page(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('admin:index')), f'<a href="{self.url}">Site metrics</a>')

    def test_participants_never_see_the_link(self):
        self.client.force_login(self.veteran)
        self.assertNotContains(self.client.get(reverse('event_list')), self.url)
