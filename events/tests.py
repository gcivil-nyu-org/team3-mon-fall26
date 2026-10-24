import datetime
import importlib

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.forms import COMMON_SPORTS
from parks.models import Borough, Facility, Park

from .models import (
    ACTIVITY_FACILITY_KEYS,
    CAPACITY_ERROR_MESSAGE,
    NYC_TZ,
    RSVP,
    Activity,
    Event,
    EventStatus,
    RSVPStatus,
    SkillLevel,
)

User = get_user_model()
PASSWORD = 'Hudson-River-Run-77'


class EventCreationAndCapacityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('organizer', 'organizer@nyu.edu', PASSWORD)
        self.park = Park.objects.create(
            gispropnum='M110',
            name='William McCray Playground',
            borough=Borough.MANHATTAN,
            address='53-41 WEST 138 STREET',
            is_retired=False,
        )
        self.retired_park = Park.objects.create(
            gispropnum='M999',
            name='Retired Playground',
            borough=Borough.MANHATTAN,
            is_retired=True,
        )
        self.future_date = (timezone.localdate() + datetime.timedelta(days=7)).isoformat()

    def valid_payload(self, **overrides):
        data = {
            'park': self.park.pk,
            'activity': Activity.BASKETBALL,
            'date': self.future_date,
            'time': '18:30',
            'skill_level': SkillLevel.INTERMEDIATE,
            'capacity': '10',
            'description': 'Half-court 5v5 run, bring a dark and light shirt.',
        }
        data.update(overrides)
        return data

    def test_event_page_links_organizer_to_their_public_profile(self):
        event = Event.objects.create(
            organizer=self.user,
            park=self.park,
            activity='Pickup Basketball',
            date=timezone.localdate() + datetime.timedelta(days=7),
            time=datetime.time(18, 30),
            description='Half-court 5v5 run.',
        )

        response = self.client.get(reverse('event_detail', kwargs={'pk': event.pk}))

        profile_url = reverse('public_profile', args=['organizer'])
        self.assertContains(response, f'href="{profile_url}"><strong>@organizer</strong></a>')

    def test_unauthenticated_user_is_redirected_to_login(self):
        url = reverse('event_create')
        get_response = self.client.get(url)
        self.assertRedirects(get_response, f"{reverse('login')}?next={url}")

        post_response = self.client.post(url, self.valid_payload())
        self.assertRedirects(post_response, f"{reverse('login')}?next={url}")
        self.assertEqual(Event.objects.count(), 0)

    def test_landing_page_shows_create_event_button_only_when_authenticated(self):
        anon_response = self.client.get(reverse('home'))
        self.assertNotContains(anon_response, f'href="{reverse("event_create")}"')

        self.client.login(username='organizer@nyu.edu', password=PASSWORD)
        auth_response = self.client.get(reverse('home'))
        self.assertContains(auth_response, f'href="{reverse("event_create")}"')

    def test_create_event_page_renders_active_parks_in_dropdown(self):
        self.client.login(username='organizer@nyu.edu', password=PASSWORD)
        response = self.client.get(reverse('event_create'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/event_form.html')
        self.assertContains(response, 'William McCray Playground (Manhattan)')
        self.assertNotContains(response, 'Retired Playground')

    def test_successful_event_creation_assigns_owner_and_redirects_to_event_page(self):
        self.client.login(username='organizer@nyu.edu', password=PASSWORD)
        response = self.client.post(reverse('event_create'), self.valid_payload(), follow=True)

        self.assertEqual(Event.objects.count(), 1)
        event = Event.objects.first()
        self.assertRedirects(response, reverse('event_detail', kwargs={'pk': event.pk}))
        self.assertEqual(event.organizer, self.user)
        self.assertEqual(event.owner, self.user)
        self.assertEqual(event.park, self.park)
        self.assertEqual(event.activity, Activity.BASKETBALL)
        self.assertEqual(event.skill_level, SkillLevel.INTERMEDIATE)
        self.assertEqual(event.capacity, 10)
        self.assertContains(response, 'Event created and published!')
        self.assertContains(response, 'Basketball')
        self.assertContains(response, 'William McCray Playground')
        self.assertContains(response, 'organizer')
        self.assertContains(response, '10 participants')

    def test_missing_required_fields_rejected_without_creating_partial_record(self):
        self.client.login(username='organizer@nyu.edu', password=PASSWORD)
        for missing_field in ('park', 'activity', 'date', 'time', 'description'):
            with self.subTest(missing_field=missing_field):
                response = self.client.post(
                    reverse('event_create'),
                    self.valid_payload(**{missing_field: ''}),
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn(missing_field, response.context['form'].errors)
                self.assertEqual(Event.objects.count(), 0)

    def test_past_date_is_rejected_without_creating_event(self):
        self.client.login(username='organizer@nyu.edu', password=PASSWORD)
        past_date = (timezone.localdate() - datetime.timedelta(days=1)).isoformat()
        response = self.client.post(reverse('event_create'), self.valid_payload(date=past_date))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Event date and time must be in the future.')
        self.assertEqual(Event.objects.count(), 0)

    def test_blank_capacity_saves_as_unlimited_and_displays_on_event_page(self):
        self.client.login(username='organizer@nyu.edu', password=PASSWORD)
        response = self.client.post(reverse('event_create'), self.valid_payload(capacity=''), follow=True)
        self.assertEqual(Event.objects.count(), 1)
        event = Event.objects.first()
        self.assertIsNone(event.capacity)
        self.assertContains(response, 'Unlimited')

    def test_zero_and_negative_capacity_rejected_with_exact_error_message(self):
        self.client.login(username='organizer@nyu.edu', password=PASSWORD)
        for invalid_capacity in ('0', '-1', '-10', 'abc'):
            with self.subTest(capacity=invalid_capacity):
                response = self.client.post(
                    reverse('event_create'),
                    self.valid_payload(capacity=invalid_capacity),
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, CAPACITY_ERROR_MESSAGE)
                self.assertEqual(
                    response.context['form'].errors.get('capacity'),
                    [CAPACITY_ERROR_MESSAGE],
                )
                self.assertEqual(Event.objects.count(), 0)

    def test_whitespace_only_text_fields_and_overflow_capacity_rejected(self):
        self.client.login(username='organizer@nyu.edu', password=PASSWORD)
        response = self.client.post(
            reverse('event_create'),
            self.valid_payload(activity='   ', description='   ', capacity='99999999'),
        )
        self.assertEqual(response.status_code, 200)
        errors = response.context['form'].errors
        self.assertIn('activity', errors)
        self.assertIn('description', errors)
        self.assertIn('capacity', errors)
        self.assertEqual(Event.objects.count(), 0)



class EventDiscoveryFeedTests(TestCase):
    """Upcoming event discovery feed and details (#25)."""

    def setUp(self):
        self.organizer = User.objects.create_user('host', 'host@nyu.edu', PASSWORD)
        self.manhattan_park = Park.objects.create(
            gispropnum='M010', name='Central Park', borough=Borough.MANHATTAN, address='Central Park West'
        )
        self.brooklyn_park = Park.objects.create(
            gispropnum='B073', name='Prospect Park', borough=Borough.BROOKLYN
        )
        self.today = timezone.now().astimezone(NYC_TZ).date()

    def make_event(self, days=3, **overrides):
        data = {
            'organizer': self.organizer,
            'park': self.manhattan_park,
            'title': 'Pickup Basketball',
            'activity': Activity.BASKETBALL,
            'date': self.today + datetime.timedelta(days=days),
            'time': datetime.time(18, 30),
            'skill_level': SkillLevel.ALL,
            'capacity': 10,
            'description': 'Half-court 5v5, all levels.',
        }
        data.update(overrides)
        return Event.objects.create(**data)

    # Queryset

    def test_upcoming_excludes_past_and_cancelled_and_orders_soonest_first(self):
        later = self.make_event(days=5, title='Later Run')
        sooner = self.make_event(days=1, title='Sooner Run')
        self.make_event(days=-1, title='Yesterday Run')
        self.make_event(days=2, title='Cancelled Run', status=EventStatus.CANCELLED)
        self.make_event(days=2, title='Removed Run', status=EventStatus.REMOVED)

        self.assertEqual(list(Event.objects.upcoming()), [sooner, later])

    def test_upcoming_uses_new_york_time_for_events_today(self):
        now = datetime.datetime(2026, 10, 19, 12, 0, tzinfo=NYC_TZ)
        morning = self.make_event(days=0, date=now.date(), time=datetime.time(9, 0))
        evening = self.make_event(days=0, date=now.date(), time=datetime.time(19, 0))

        upcoming = list(Event.objects.upcoming(now=now))
        self.assertIn(evening, upcoming)
        self.assertNotIn(morning, upcoming)

    # Feed page

    def test_feed_is_public_and_linked_from_navigation(self):
        response = self.client.get(reverse('event_list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'base.html')

        landing = self.client.get(reverse('home'))
        self.assertContains(landing, f'href="{reverse("event_list")}"')

    def test_feed_shows_activity_date_time_location_and_capacity(self):
        event = self.make_event(capacity=12)
        self.make_event(title='Morning Yoga', activity=Activity.YOGA, capacity=None)

        response = self.client.get(reverse('event_list'))

        self.assertContains(response, 'Pickup Basketball')
        self.assertContains(response, event.date.strftime('%b'))
        self.assertContains(response, '6:30 PM')
        self.assertContains(response, 'Central Park')
        self.assertContains(response, 'Manhattan')
        self.assertContains(response, 'Up to 12')
        self.assertContains(response, 'Unlimited spots')

    def test_feed_excludes_past_even_with_retired_include_past_parameter(self):
        self.make_event(days=-3, title='Old Soccer Game', activity=Activity.SOCCER)
        self.make_event(title='New Soccer Game', activity=Activity.SOCCER)

        default = self.client.get(reverse('event_list'))
        self.assertContains(default, 'New Soccer Game')
        self.assertNotContains(default, 'Old Soccer Game')

        with_past = self.client.get(reverse('event_list'), {'include_past': '1'})
        self.assertNotContains(with_past, 'Old Soccer Game')
        self.assertNotContains(with_past, 'Include past events')

    def test_feed_shows_events_created_through_the_create_form(self):
        self.client.login(username='host@nyu.edu', password=PASSWORD)
        self.client.post(reverse('event_create'), {
            'park': self.brooklyn_park.pk,
            'activity': Activity.ULTIMATE_FRISBEE,
            'date': (self.today + datetime.timedelta(days=4)).isoformat(),
            'time': '15:00',
            'skill_level': SkillLevel.BEGINNER,
            'capacity': '',
            'description': 'Bring a disc if you have one.',
        })
        self.client.logout()

        response = self.client.get(reverse('event_list'))
        self.assertContains(response, 'Ultimate frisbee')
        self.assertContains(response, 'Prospect Park')

    def test_borough_skill_and_sport_filters_combine(self):
        self.make_event(title='Brooklyn Beginner Soccer', activity=Activity.SOCCER, park=self.brooklyn_park,
                        skill_level=SkillLevel.BEGINNER)
        self.make_event(title='Brooklyn Competitive Soccer', activity=Activity.SOCCER, park=self.brooklyn_park,
                        skill_level=SkillLevel.COMPETITIVE)
        self.make_event(title='Manhattan Beginner Soccer', activity=Activity.SOCCER, skill_level=SkillLevel.BEGINNER)
        self.make_event(title='Brooklyn Beginner Tennis', activity=Activity.TENNIS, park=self.brooklyn_park,
                        skill_level=SkillLevel.BEGINNER)

        response = self.client.get(reverse('event_list'), {
            'borough': Borough.BROOKLYN, 'skill': SkillLevel.BEGINNER, 'sport': 'soccer',
        })

        self.assertContains(response, 'Brooklyn Beginner Soccer')
        self.assertNotContains(response, 'Brooklyn Competitive Soccer')
        self.assertNotContains(response, 'Manhattan Beginner Soccer')
        self.assertNotContains(response, 'Brooklyn Beginner Tennis')

    def test_invalid_filter_values_are_ignored(self):
        self.make_event(title='Any Event')
        response = self.client.get(reverse('event_list'), {'borough': 'ZZ', 'skill': 'expert'})
        self.assertContains(response, 'Any Event')

    def test_empty_state(self):
        response = self.client.get(reverse('event_list'))
        self.assertContains(response, 'No upcoming events')

    def test_feed_links_to_existing_event_detail_page(self):
        event = self.make_event()
        response = self.client.get(reverse('event_list'))
        self.assertContains(response, f'href="{event.get_absolute_url()}"')

        detail = self.client.get(event.get_absolute_url())
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, event.description)
        self.assertContains(detail, 'Central Park')
        self.assertContains(detail, '10 participants')
        self.assertContains(detail, f'href="{reverse("event_list")}"')

    def test_feed_is_paginated(self):
        for i in range(25):
            self.make_event(days=1 + i, title=f'Run {i:02d}')
        first = self.client.get(reverse('event_list'))
        self.assertEqual(len(first.context['events']), 20)
        self.assertContains(first, '25 events')
        second = self.client.get(reverse('event_list'), {'page': 2})
        self.assertEqual(len(second.context['events']), 5)

    # JSON endpoint

    def test_api_returns_upcoming_events_with_capacity_metrics(self):
        limited = self.make_event(days=1, capacity=8)
        unlimited = self.make_event(days=2, title='Open Run', capacity=None, park=self.brooklyn_park)
        self.make_event(days=-1, title='Past Run')

        response = self.client.get(reverse('event_list_api'))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 2)
        first, second = data['results']
        self.assertEqual(first['id'], limited.pk)
        self.assertEqual(first['capacity'], 8)
        self.assertFalse(first['is_unlimited'])
        self.assertEqual(first['park']['name'], 'Central Park')
        self.assertEqual(second['id'], unlimited.pk)
        self.assertIsNone(second['capacity'])
        self.assertTrue(second['is_unlimited'])
        self.assertEqual(second['park']['borough_label'], 'Brooklyn')

    def test_api_supports_filters_and_rejects_post(self):
        self.make_event(title='Manhattan Run')
        self.make_event(title='Brooklyn Run', park=self.brooklyn_park)

        data = self.client.get(reverse('event_list_api'), {'borough': Borough.BROOKLYN}).json()
        self.assertEqual([e['title'] for e in data['results']], ['Brooklyn Run'])

        self.assertEqual(self.client.post(reverse('event_list_api')).status_code, 405)

    def test_landing_page_renders_when_event_routes_are_disabled(self):
        # Production deployment-check mode serves only the landing page (config.deployment_urls).
        with self.settings(ROOT_URLCONF='config.deployment_urls'):
            response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Browse events')
        self.assertContains(response, 'href="#events"')

    def test_seed_command_creates_past_events_hidden_by_default(self):
        from django.core.management import call_command
        from io import StringIO

        call_command('seed_events', stdout=StringIO())

        default = self.client.get(reverse('event_list'))
        self.assertNotContains(default, 'Earlier Reservoir Run')
        with_past = self.client.get(reverse('event_list'), {'include_past': '1'})
        self.assertNotContains(with_past, 'Earlier Reservoir Run')
        self.assertNotContains(with_past, 'Include past events')


class FoundationSchemaTests(TestCase):
    """Shared schema for the sprint stories: title, meeting spot, host flag, activities, RSVPs."""

    def setUp(self):
        self.host = User.objects.create_user('host', 'host@nyu.edu', PASSWORD)
        self.park = Park.objects.create(gispropnum='B058', name='McCarren Park', borough=Borough.BROOKLYN)
        self.tomorrow = timezone.localdate() + datetime.timedelta(days=1)

    def make_event(self, **overrides):
        data = {
            'organizer': self.host,
            'park': self.park,
            'activity': Activity.BASKETBALL,
            'date': self.tomorrow,
            'time': datetime.time(18, 30),
            'description': 'Full-court 5v5.',
        }
        data.update(overrides)
        return Event.objects.create(**data)

    def add_rsvps(self, event, *statuses):
        for i, status in enumerate(statuses):
            user = User.objects.create_user(f'player{event.pk}-{i}', f'player{event.pk}-{i}@nyu.edu', PASSWORD)
            RSVP.objects.create(event=event, user=user, status=status)

    def test_local_time_is_new_york(self):
        self.assertEqual(timezone.localdate(), timezone.now().astimezone(NYC_TZ).date())

    def test_new_fields_have_safe_defaults_and_title_falls_back_to_sport(self):
        event = self.make_event()
        event.refresh_from_db()

        self.assertEqual(event.title, '')
        self.assertEqual(event.meeting_spot, '')
        self.assertTrue(event.host_is_playing)
        self.assertEqual(event.display_title, 'Basketball')
        self.assertEqual(self.make_event(title='After-Work Hoops').display_title, 'After-Work Hoops')

    def test_previous_release_can_still_create_events_after_migrating(self):
        # A rolled-back release inserts rows without the new columns; the database fills them in.
        ops = connection.ops
        now = ops.adapt_datetimefield_value(timezone.now())
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO events_event (organizer_id, park_id, activity, date, time, skill_level, '
                'description, status, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                [self.host.pk, self.park.pk, 'Pickup Basketball', ops.adapt_datefield_value(self.tomorrow),
                 ops.adapt_timefield_value(datetime.time(18, 30)), SkillLevel.ALL, 'Old release',
                 EventStatus.SCHEDULED, now, now],
            )
        event = Event.objects.get(description='Old release')

        self.assertEqual((event.title, event.meeting_spot, event.host_is_playing), ('', '', True))

    def test_with_confirmed_players_counts_active_rsvps_plus_a_playing_host(self):
        playing = self.make_event()
        not_playing = self.make_event(host_is_playing=False)
        no_rsvps = self.make_event()
        for event in (playing, not_playing):
            self.add_rsvps(event, RSVPStatus.ACTIVE, RSVPStatus.ACTIVE, RSVPStatus.CANCELLED, RSVPStatus.REMOVED)

        counts = dict(Event.objects.with_confirmed_players().values_list('pk', 'confirmed_players'))

        self.assertEqual(counts, {playing.pk: 3, not_playing.pk: 2, no_rsvps.pk: 1})
        self.assertEqual(Event.objects.upcoming().with_confirmed_players().get(pk=playing.pk).confirmed_players, 3)

    def test_one_rsvp_row_per_user_per_event(self):
        event = self.make_event()
        player = User.objects.create_user('player', 'player@nyu.edu', PASSWORD)
        RSVP.objects.create(event=event, user=player, status=RSVPStatus.CANCELLED)

        with self.assertRaises(IntegrityError):
            RSVP.objects.create(event=event, user=player)

    def test_activities_match_profile_sports(self):
        labels = [label for value, label in Activity.choices if value != Activity.OTHER]
        self.assertEqual(labels, list(COMMON_SPORTS))

    def test_activity_facility_keys_are_real_facility_types(self):
        facility_keys = {key for key, _ in Facility.SPORT_FIELDS}
        for activity, keys in ACTIVITY_FACILITY_KEYS.items():
            with self.subTest(activity=activity):
                self.assertTrue(set(keys) <= facility_keys)

    def test_create_form_only_accepts_standard_sports(self):
        self.client.login(username='host@nyu.edu', password=PASSWORD)
        page = self.client.get(reverse('event_create'))
        self.assertContains(page, 'Select a sport')
        self.assertContains(page, '<option value="ultimate_frisbee">Ultimate frisbee</option>', html=True)

        response = self.client.post(reverse('event_create'), {
            'park': self.park.pk, 'activity': 'Pickup Basketball', 'date': self.tomorrow.isoformat(),
            'time': '18:30', 'skill_level': SkillLevel.ALL, 'description': 'Free text is no longer allowed.',
        })
        self.assertIn('activity', response.context['form'].errors)
        self.assertEqual(Event.objects.count(), 0)


class StandardActivityMappingTests(TestCase):
    """Free-text activities from before #75 map to a standard sport (events/migrations/0003)."""

    def test_free_text_maps_to_the_closest_standard_sport(self):
        standard_activity = importlib.import_module('events.migrations.0003_standardize_activities').standard_activity
        cases = {
            'Pickup Basketball': Activity.BASKETBALL,
            'Half-court hoops': Activity.BASKETBALL,
            'Reservoir Loop Run': Activity.RUNNING,
            'Morning jog': Activity.RUNNING,
            'Ultimate Frisbee Scrimmage': Activity.ULTIMATE_FRISBEE,
            'Pickleball Round Robin': Activity.PICKLEBALL,
            'Beginner Tennis Doubles': Activity.TENNIS,
            'Pickup Soccer 7v7': Activity.SOCCER,
            'Kids Softball': Activity.SOFTBALL,
            'Adult Baseball': Activity.BASEBALL,
            'Flag Football': Activity.FOOTBALL,
            'Sunrise Yoga': Activity.YOGA,
            'Trail Walk and Hike': Activity.OTHER,
            'Sunday Brunch': Activity.OTHER,
            'basketball': Activity.BASKETBALL,
            'Ultimate frisbee': Activity.ULTIMATE_FRISBEE,
            '': Activity.OTHER,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(standard_activity(text), expected)


class ActivityDataMigrationTests(TransactionTestCase):
    """Runs 0003 on events saved with free-text activities, as on the shared database."""

    before = [('events', '0002_event_title_meeting_spot_host_rsvp')]
    after = [('events', '0003_standardize_activities')]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        self.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_events_keep_their_wording_as_title_and_get_a_standard_sport(self):
        old_apps = self.migrate(self.before)
        user = old_apps.get_model('auth', 'User').objects.create(username='host')
        park = old_apps.get_model('parks', 'Park').objects.create(gispropnum='M010', name='Central Park', borough='M')
        OldEvent = old_apps.get_model('events', 'Event')
        common = {'organizer': user, 'park': park, 'date': datetime.date(2026, 11, 1),
                  'time': datetime.time(9, 0), 'description': 'Sample'}
        run = OldEvent.objects.create(activity='Reservoir Loop Run', **common)
        hike = OldEvent.objects.create(activity='Greenbelt Hike', **common)
        titled = OldEvent.objects.create(activity='Pickup Soccer', title='Saturday 7v7', **common)

        new_apps = self.migrate(self.after)
        Event_ = new_apps.get_model('events', 'Event')

        self.assertEqual(
            {e.pk: (e.title, e.activity) for e in Event_.objects.all()},
            {
                run.pk: ('Reservoir Loop Run', 'running'),
                hike.pk: ('Greenbelt Hike', 'other'),
                titled.pk: ('Saturday 7v7', 'soccer'),
            },
        )
