"""US-3.4 (#25) edit an event, US-3.5 (#26) keep the limit above confirmed players, US-3.6 (#27) who may edit."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from parks.models import Borough, Park

from .hosting import FULL_MESSAGE
from .models import NYC_TZ, RSVP, Activity, Event, EventStatus, RSVPStatus

User = get_user_model()


class EventEditTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_user('edit-host', 'edit-host@example.test')
        cls.other = User.objects.create_user('edit-other', 'edit-other@example.test')
        cls.admin = User.objects.create_user('edit-admin', 'edit-admin@example.test', is_staff=True)
        cls.players = [User.objects.create_user(f'edit-p{i}', f'edit-p{i}@example.test') for i in range(8)]
        cls.park = Park.objects.create(
            gispropnum='EDIT1', name='Edit Park', borough=Borough.BROOKLYN, latitude=40.72, longitude=-73.95
        )
        cls.other_park = Park.objects.create(gispropnum='EDIT2', name='Other Park', borough=Borough.QUEENS)

    def setUp(self):
        self.now = datetime.datetime(2026, 11, 9, 12, tzinfo=NYC_TZ)
        clock = patch('django.utils.timezone.now', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.event = self.make_event()
        self.url = reverse('event_edit', kwargs={'pk': self.event.pk})
        self.client.force_login(self.host)

    def make_event(self, **changes):
        data = dict(
            organizer=self.host,
            park=self.park,
            title='Saturday hoops',
            meeting_spot='Court 2',
            activity=Activity.BASKETBALL,
            date=self.now.date() + datetime.timedelta(days=5),
            time=datetime.time(18),
            skill_level='all',
            capacity=10,
            description='Pickup run',
        )
        data.update(changes)
        return Event.objects.create(**data)

    def join(self, count, event=None):
        for player in self.players[:count]:
            RSVP.objects.create(event=event or self.event, user=player)

    def payload(self, **changes):
        """The form as the edit page submits it for the current event, with `changes` applied."""
        event = self.event
        data = {
            'title': event.title,
            'meeting_spot': event.meeting_spot,
            'activity': event.activity,
            'date': event.date.isoformat(),
            'time': event.time.strftime('%H:%M'),
            'end_time': event.effective_end_time.strftime('%H:%M'),
            'skill_level': event.skill_level,
            'capacity': '' if event.capacity is None else str(event.capacity),
            'host_is_playing': 'on' if event.host_is_playing else None,
            'description': event.description,
        }
        data.update(changes)
        return {key: value for key, value in data.items() if value is not None}

    def save(self, **changes):
        return self.client.post(self.url, self.payload(**changes), follow=True)

    def messages(self, response):
        return [str(message) for message in response.context['messages']]


class EventEditTests(EventEditTestCase):
    """US-3.4 (#25)."""

    def test_organizer_sees_the_form_filled_in_with_the_park_locked(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'Edit Your Event')
        self.assertContains(response, 'value="Saturday hoops"')
        self.assertContains(response, 'To change parks, cancel this event and create a new one.')
        self.assertContains(response, 'value="Edit Park (Brooklyn)" readonly disabled')
        self.assertNotIn('park', response.context['form'].fields)
        self.assertContains(response, '>Save changes</button>')

    # Scenario: Successful update
    def test_valid_changes_persist_and_show_on_the_event_page_and_in_explore(self):
        response = self.save(title='Sunday hoops', time='19:30', meeting_spot='Court 5')

        self.event.refresh_from_db()
        self.assertRedirects(response, self.event.get_absolute_url())
        self.assertEqual(
            (self.event.title, self.event.time, self.event.meeting_spot),
            ('Sunday hoops', datetime.time(19, 30), 'Court 5'),
        )
        self.assertIn('Your event has been updated.', self.messages(response))
        self.assertContains(response, 'Court 5')
        explore = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'events'})
        self.assertContains(explore, 'Sunday hoops')

    # Scenario: Invalid update preserves data
    def test_past_date_or_cleared_required_field_shows_errors_and_saves_nothing(self):
        cases = {
            'past date': {'date': (self.now.date() - datetime.timedelta(days=1)).isoformat()},
            'blank title': {'title': '   '},
            'blank meeting spot': {'meeting_spot': ''},
        }
        for label, changes in cases.items():
            with self.subTest(label):
                response = self.save(**changes)

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
                self.event.refresh_from_db()
                self.assertEqual((self.event.title, self.event.meeting_spot), ('Saturday hoops', 'Court 2'))
                self.assertEqual(self.event.date, self.now.date() + datetime.timedelta(days=5))

    # Scenario: Park cannot be changed
    def test_a_posted_park_is_ignored(self):
        self.save(park=self.other_park.pk, title='Still here')

        self.event.refresh_from_db()
        self.assertEqual(self.event.park, self.park)
        self.assertEqual(self.event.title, 'Still here')

    # Scenario: Cancelled, removed, or past events are read-only
    def test_cancelled_removed_or_started_events_refuse_the_edit_with_an_explanation(self):
        cases = {
            'cancelled': (
                dict(status=EventStatus.CANCELLED),
                'This event has been cancelled, so it can no longer be edited.',
            ),
            'removed': (
                dict(status=EventStatus.REMOVED),
                'This event has been removed, so it can no longer be edited.',
            ),
            'started earlier today': (
                dict(date=self.now.date(), time=datetime.time(10)),
                'This event has already started, so it can no longer be edited.',
            ),
        }
        for label, (changes, message) in cases.items():
            with self.subTest(label):
                self.event = self.make_event(**changes)
                self.url = reverse('event_edit', kwargs={'pk': self.event.pk})

                page = self.client.get(self.url, follow=True)
                self.assertRedirects(page, self.event.get_absolute_url())
                self.assertIn(message, self.messages(page))
                self.assertNotContains(page, 'Edit Event</a>')

                response = self.save(title='Changed')
                self.assertIn(message, self.messages(response))
                self.event.refresh_from_db()
                self.assertEqual(self.event.title, 'Saturday hoops')

    def test_a_cancellation_saved_meanwhile_is_not_overwritten(self):
        # The organizer opened the edit page, then the event was cancelled before they saved.
        Event.objects.filter(pk=self.event.pk).update(status=EventStatus.CANCELLED)

        self.save(title='Changed')

        self.event.refresh_from_db()
        self.assertEqual((self.event.status, self.event.title), (EventStatus.CANCELLED, 'Saturday hoops'))

    def test_event_page_shows_edit_only_to_managers_of_an_editable_event(self):
        edit_link = f'href="{self.url}">Edit Event</a>'
        self.assertContains(self.client.get(self.event.get_absolute_url()), edit_link)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(self.event.get_absolute_url()), edit_link)

    def test_administrator_can_edit_but_not_change_whether_the_host_plays(self):
        self.client.force_login(self.admin)

        page = self.client.get(self.url)
        self.assertNotIn('host_is_playing', page.context['form'].fields)
        self.save(title='Fixed by admin', host_is_playing=None)

        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Fixed by admin')
        self.assertTrue(self.event.host_is_playing, 'the host flag is the organizer\'s choice')


class EventEditAccessTests(EventEditTestCase):
    """US-3.6 (#27): the edit URL, like cancel and remove, is for the organizer or an administrator."""

    def test_non_owner_gets_403_for_get_and_post_and_nothing_changes(self):
        self.client.force_login(self.other)

        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.payload(title='Hijacked')).status_code, 403)

        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Saturday hoops')

    def test_anonymous_requests_go_to_login_and_return_to_the_edit_page(self):
        self.client.logout()
        login_url = f"{reverse('login')}?next={self.url}"

        self.assertRedirects(self.client.get(self.url), login_url)
        self.assertRedirects(self.client.post(self.url, self.payload(title='Hijacked')), login_url)

        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Saturday hoops')

    def test_unknown_event_returns_404(self):
        self.assertEqual(self.client.get(reverse('event_edit', kwargs={'pk': self.event.pk + 1000})).status_code, 404)


class EventLimitTests(EventEditTestCase):
    """US-3.5 (#26), plus switching "I'm playing" in Edit Event (US-3.10 / #82)."""

    def setUp(self):
        super().setUp()
        Event.objects.filter(pk=self.event.pk).update(capacity=15)
        self.event.refresh_from_db()
        self.join(7)  # 7 active RSVPs + the playing host = 8 confirmed players

    def confirmed(self):
        return Event.objects.with_confirmed_players().get(pk=self.event.pk).confirmed_players

    # Scenario: Reduction below confirmed players rejected
    def test_a_limit_below_confirmed_players_is_rejected_and_nothing_changes(self):
        response = self.save(capacity='6')

        self.assertEqual(
            response.context['form'].errors['capacity'],
            ['Participant limit cannot be lower than the 8 currently confirmed players.'],
        )
        self.event.refresh_from_db()
        self.assertEqual(self.event.capacity, 15)
        self.assertEqual(self.confirmed(), 8)

    # Scenario: Reduction to at least confirmed players accepted
    def test_a_limit_at_or_above_confirmed_players_is_saved(self):
        for capacity in (10, 8):
            with self.subTest(capacity=capacity):
                self.save(capacity=str(capacity))

                self.event.refresh_from_db()
                self.assertEqual(self.event.capacity, capacity)

    # Scenario: Combined change in one save
    def test_unchecking_playing_and_lowering_the_limit_together_is_accepted(self):
        Event.objects.filter(pk=self.event.pk).update(capacity=8)
        self.event.refresh_from_db()

        response = self.save(capacity='7', host_is_playing=None)

        self.event.refresh_from_db()
        self.assertIn('Your event has been updated.', self.messages(response))
        self.assertEqual((self.event.capacity, self.event.host_is_playing), (7, False))
        self.assertEqual(self.confirmed(), 7)

    def test_unlimited_events_accept_any_change(self):
        self.save(capacity='')

        self.event.refresh_from_db()
        self.assertIsNone(self.event.capacity)

    # US-3.10 (#82) Scenario: Switching to playing on a full event, from Edit Event
    def test_switching_to_playing_on_a_full_event_is_rejected(self):
        Event.objects.filter(pk=self.event.pk).update(capacity=7, host_is_playing=False)
        self.event.refresh_from_db()

        response = self.save(host_is_playing='on')

        self.assertEqual(response.context['form'].errors['host_is_playing'], [FULL_MESSAGE])
        self.event.refresh_from_db()
        self.assertFalse(self.event.host_is_playing)

    def test_raising_the_limit_lets_the_host_switch_to_playing_in_the_same_save(self):
        Event.objects.filter(pk=self.event.pk).update(capacity=7, host_is_playing=False)
        self.event.refresh_from_db()

        self.save(capacity='8', host_is_playing='on')

        self.event.refresh_from_db()
        self.assertEqual((self.event.capacity, self.event.host_is_playing), (8, True))
        self.assertEqual(self.confirmed(), 8)

    def test_cancelled_rsvps_do_not_count_toward_the_limit(self):
        RSVP.objects.filter(event=self.event, user=self.players[0]).update(status=RSVPStatus.CANCELLED)

        self.save(capacity='7')

        self.event.refresh_from_db()
        self.assertEqual(self.event.capacity, 7)
