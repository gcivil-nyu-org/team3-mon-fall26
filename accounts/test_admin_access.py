"""US-7.1 (#44): administrative tools are restricted to administrators."""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import include, path, reverse
from django.views import View

from events.models import Activity, Event, EventStatus
from parks.models import Borough, Park

from .roles import ADMINISTRATOR_PERMISSIONS, ADMINISTRATORS_GROUP, StaffRequiredMixin

User = get_user_model()
PASSWORD = 'Hudson-River-Run-77'


class StaffOnlyAction(StaffRequiredMixin, View):
    """Stands in for the in-app moderation controls later Epic 7 stories add."""

    def get(self, request):
        return HttpResponse('staff tools')

    def post(self, request):
        Park.objects.filter(pk=request.POST['park']).update(name='Renamed by staff')
        return HttpResponse('done')


urlpatterns = [
    path('test-staff-only/', StaffOnlyAction.as_view(), name='test_staff_only'),
    path('', include('config.urls')),
]


class AdminAccessTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser('root', 'root@example.test', PASSWORD)
        cls.admin = User.objects.create_user('mod', 'mod@example.test', PASSWORD, is_staff=True)
        cls.other_admin = User.objects.create_user('mod2', 'mod2@example.test', PASSWORD, is_staff=True)
        cls.participant = User.objects.create_user('player', 'player@example.test', PASSWORD)
        cls.organizer = User.objects.create_user('host', 'host@example.test', PASSWORD)
        cls.park = Park.objects.create(gispropnum='ADM1', name='Admin Park', borough=Borough.QUEENS)
        cls.event = Event.objects.create(
            organizer=cls.organizer,
            park=cls.park,
            title='Pickup',
            activity=Activity.SOCCER,
            date=datetime.date.today() + datetime.timedelta(days=3),
            time=datetime.time(10),
            description='x',
        )

    def user_change_url(self, user):
        return reverse('admin:auth_user_change', args=[user.pk])

    def post_user_change(self, target, **changes):
        """Submit the admin change form for `target`, keeping its current values unless changed."""
        target.refresh_from_db()
        data = {
            'username': target.username,
            'first_name': target.first_name,
            'last_name': target.last_name,
            'email': target.email,
            'date_joined_0': target.date_joined.strftime('%Y-%m-%d'),
            'date_joined_1': target.date_joined.strftime('%H:%M:%S'),
        }
        flags = {'is_active': target.is_active, 'is_staff': target.is_staff, 'is_superuser': target.is_superuser}
        flags.update({key: value for key, value in changes.items() if key in flags})
        data.update({key: 'on' for key, value in flags.items() if value})
        data.update({key: value for key, value in changes.items() if key not in flags})
        return self.client.post(self.user_change_url(target), data)


class AdministratorAccessTests(AdminAccessTestCase):
    # Scenario: Administrator access
    def test_administrator_can_open_admin_and_its_models(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)
        for name in ('parks_park', 'parks_facility', 'parks_review', 'events_event', 'events_rsvp'):
            with self.subTest(model=name):
                self.assertEqual(self.client.get(reverse(f'admin:{name}_changelist')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:auth_user_changelist')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:admin_logentry_changelist')).status_code, 200)

    def test_administrator_can_log_in_to_admin_with_username(self):
        response = self.client.post(
            reverse('admin:login'), {'username': 'mod', 'password': PASSWORD, 'next': reverse('admin:index')}
        )
        self.assertRedirects(response, reverse('admin:index'))

    def test_administrator_can_complete_an_authorized_action_in_admin(self):
        self.client.force_login(self.admin)
        response = self.post_user_change(self.participant, is_active=False)
        self.assertEqual(response.status_code, 302)
        self.participant.refresh_from_db()
        self.assertFalse(self.participant.is_active)

    @override_settings(ROOT_URLCONF=__name__)
    def test_administrator_can_use_staff_only_views(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('test_staff_only')), 'staff tools')
        self.client.post(reverse('test_staff_only'), {'park': self.park.pk})
        self.park.refresh_from_db()
        self.assertEqual(self.park.name, 'Renamed by staff')

    def test_staff_get_the_administrators_permissions_without_joining_the_group(self):
        self.assertFalse(self.admin.groups.exists())
        expected = {f'{app}.{codename}' for app, codename in ADMINISTRATOR_PERMISSIONS}
        self.assertEqual(User.objects.get(pk=self.admin.pk).get_all_permissions(), expected)

    def test_revoking_staff_status_removes_the_permissions(self):
        User.objects.filter(pk=self.admin.pk).update(is_staff=False)
        demoted = User.objects.get(pk=self.admin.pk)
        self.assertEqual(demoted.get_all_permissions(), set())
        self.client.force_login(demoted)
        self.assertRedirects(
            self.client.get(reverse('admin:index')),
            f"{reverse('admin:login')}?next={reverse('admin:index')}",
        )

    def test_migration_gives_the_group_exactly_the_listed_permissions(self):
        group = Group.objects.get(name=ADMINISTRATORS_GROUP)
        actual = set(group.permissions.values_list('content_type__app_label', 'codename'))
        self.assertEqual(actual, set(ADMINISTRATOR_PERMISSIONS))

    def test_admin_dashboard_link_shows_only_for_administrators(self):
        link = f'href="{reverse("admin:index")}">Admin dashboard</a>'
        self.client.force_login(self.admin)
        page = self.client.get(reverse('event_list'))
        self.assertContains(page, link)
        self.assertContains(page, '<span class="account-menu-role">Admin</span>', html=True)
        for user in (self.participant, self.organizer):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                page = self.client.get(reverse('event_list'))
                self.assertNotContains(page, 'Admin dashboard')
                self.assertContains(page, '<span class="account-menu-role">Member</span>', html=True)


class NonAdministratorAccessTests(AdminAccessTestCase):
    # Scenario: Non-administrator access
    def test_admin_sends_participants_and_organizers_to_the_admin_login(self):
        for user in (self.participant, self.organizer):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                for url in (reverse('admin:index'), reverse('admin:events_event_changelist')):
                    response = self.client.get(url)
                    self.assertRedirects(response, f"{reverse('admin:login')}?next={url}")

    def test_posting_to_admin_changes_nothing(self):
        self.client.force_login(self.organizer)
        url = reverse('admin:events_event_change', args=[self.event.pk])
        response = self.client.post(url, {'title': 'Hijacked', 'status': EventStatus.CANCELLED})
        self.assertRedirects(response, f"{reverse('admin:login')}?next={url}")
        response = self.post_user_change(self.organizer, is_staff=True)
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.organizer.refresh_from_db()
        self.assertEqual((self.event.title, self.event.status), ('Pickup', EventStatus.SCHEDULED))
        self.assertFalse(self.organizer.is_staff)

    def test_participants_cannot_log_in_to_admin(self):
        response = self.client.post(reverse('admin:login'), {'username': 'player@example.test', 'password': PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    @override_settings(ROOT_URLCONF=__name__)
    def test_staff_only_views_return_403_and_change_nothing(self):
        url = reverse('test_staff_only')
        for user in (self.participant, self.organizer):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {'park': self.park.pk}).status_code, 403)
        self.client.logout()
        response = self.client.post(url, {'park': self.park.pk})
        self.assertRedirects(response, f"{reverse('login')}?next={url}", fetch_redirect_response=False)
        self.park.refresh_from_db()
        self.assertEqual(self.park.name, 'Admin Park')

    @override_settings(ROOT_URLCONF=__name__)
    def test_deactivated_staff_are_not_administrators(self):
        User.objects.filter(pk=self.admin.pk).update(is_active=False)
        self.client.force_login(User.objects.get(pk=self.admin.pk))
        self.assertNotEqual(self.client.get(reverse('admin:index')).status_code, 200)
        self.assertNotEqual(self.client.get(reverse('test_staff_only')).status_code, 200)

    def test_participants_have_no_admin_permissions(self):
        self.assertEqual(User.objects.get(pk=self.participant.pk).get_all_permissions(), set())


class GrantingAdministratorStatusTests(AdminAccessTestCase):
    # Scenario: Granting administrator status
    def setUp(self):
        self.client.force_login(self.admin)

    def test_administrator_cannot_grant_staff_status(self):
        self.post_user_change(self.participant, is_staff=True)
        self.participant.refresh_from_db()
        self.assertFalse(self.participant.is_staff)

    def test_administrator_cannot_revoke_staff_status(self):
        response = self.post_user_change(self.other_admin, is_staff=False)
        self.assertEqual(response.status_code, 403)
        self.other_admin.refresh_from_db()
        self.assertTrue(self.other_admin.is_staff)

    def test_administrator_cannot_escalate_themselves(self):
        group = Group.objects.get(name=ADMINISTRATORS_GROUP)
        delete_user = Permission.objects.get(codename='delete_user')
        self.post_user_change(
            self.admin, is_superuser=True, groups=[group.pk], user_permissions=[delete_user.pk], username='boss'
        )
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_superuser)
        self.assertFalse(self.admin.groups.exists())
        self.assertFalse(self.admin.user_permissions.exists())
        self.assertEqual(self.admin.username, 'mod')

    def test_role_fields_are_read_only_for_administrators(self):
        form = self.client.get(self.user_change_url(self.participant))
        for field in ('is_staff', 'is_superuser', 'groups', 'user_permissions', 'username', 'email'):
            with self.subTest(field=field):
                self.assertNotContains(form, f'name="{field}"')
        self.assertContains(form, 'name="is_active"')

    def test_other_staff_and_superusers_are_view_only(self):
        for target in (self.other_admin, self.superuser):
            with self.subTest(user=target.username):
                self.assertEqual(self.client.get(self.user_change_url(target)).status_code, 200)
                self.assertEqual(self.post_user_change(target, is_active=False).status_code, 403)
                target.refresh_from_db()
                self.assertTrue(target.is_active)

    def test_administrator_cannot_change_passwords(self):
        url = reverse('admin:auth_user_password_change', args=[self.participant.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        response = self.client.post(url, {'password1': 'Taken-Over-123', 'password2': 'Taken-Over-123'})
        self.assertEqual(response.status_code, 403)
        self.participant.refresh_from_db()
        self.assertTrue(self.participant.check_password(PASSWORD))

    def test_administrator_cannot_edit_groups_or_add_users(self):
        group = Group.objects.get(name=ADMINISTRATORS_GROUP)
        self.assertEqual(self.client.get(reverse('admin:auth_group_change', args=[group.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:auth_user_add')).status_code, 403)

    def test_superuser_can_grant_and_revoke_staff_status(self):
        self.client.force_login(self.superuser)
        self.assertEqual(self.post_user_change(self.participant, is_staff=True).status_code, 302)
        self.participant.refresh_from_db()
        self.assertTrue(self.participant.is_staff)
        self.assertEqual(self.post_user_change(self.participant, is_staff=False).status_code, 302)
        self.participant.refresh_from_db()
        self.assertFalse(self.participant.is_staff)
