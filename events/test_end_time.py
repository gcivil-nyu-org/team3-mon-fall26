"""US-3.11 (#116): event end times, their checks, older events and where the time range shows."""

import datetime
import importlib
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from parks.models import Borough, Park

from .forms import END_BEFORE_START_MESSAGE, END_TIME_REQUIRED_MESSAGE, TOO_LONG_MESSAGE, TOO_SHORT_MESSAGE
from .models import NYC_TZ, Activity, Event, default_end_time

User = get_user_model()
NOW = datetime.datetime(2026, 11, 10, 12, tzinfo=NYC_TZ)
GAME_DAY = datetime.date(2026, 11, 14)


class EndTimeTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_user('end-time-host', 'end-time-host@example.test')
        cls.park = Park.objects.create(
            gispropnum='B058', name='McCarren Park', borough=Borough.BROOKLYN, latitude=40.72, longitude=-73.95
        )

    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.client.force_login(self.host)

    def event(self, start, end=None, **changes):
        data = dict(
            organizer=self.host,
            park=self.park,
            title='Evening hoops',
            activity=Activity.BASKETBALL,
            date=GAME_DAY,
            time=datetime.time.fromisoformat(start),
            end_time=datetime.time.fromisoformat(end) if end else None,
            description='Pickup run',
        )
        data.update(changes)
        return Event.objects.create(**data)

    def payload(self, **changes):
        data = {
            'title': 'Evening hoops',
            'park': self.park.pk,
            'meeting_spot': 'Court 3',
            'activity': Activity.BASKETBALL,
            'date': GAME_DAY.isoformat(),
            'time': '18:00',
            'end_time': '20:00',
            'skill_level': 'all',
            'capacity': '10',
            'host_is_playing': 'on',
            'description': 'Pickup run',
        }
        data.update(changes)
        return data

    def create(self, **changes):
        return self.client.post(reverse('event_create'), self.payload(**changes))


class EndTimeFormTests(EndTimeTestCase):
    def test_valid_end_time_saves_and_shows_the_range(self):
        response = self.create()

        event = Event.objects.get()
        self.assertRedirects(response, event.get_absolute_url())
        self.assertEqual(event.end_time, datetime.time(20))
        self.assertContains(self.client.get(event.get_absolute_url()), 'Saturday, November 14, 2026<br>6:00–8:00 PM')

    def test_shortest_and_longest_games_are_allowed(self):
        for start, end in (('18:00', '18:15'), ('15:59', '23:59')):
            with self.subTest(start=start, end=end):
                self.create(time=start, end_time=end)
                self.assertTrue(Event.objects.filter(end_time=datetime.time.fromisoformat(end)).exists())

    def test_invalid_end_times_show_an_error_on_the_end_time_field(self):
        cases = {
            'same as the start': ('18:00', '18:00', END_BEFORE_START_MESSAGE),
            'before the start': ('18:00', '17:30', END_BEFORE_START_MESSAGE),
            'after midnight': ('22:00', '01:00', END_BEFORE_START_MESSAGE),
            'under 15 minutes': ('18:00', '18:14', TOO_SHORT_MESSAGE),
            'over 8 hours': ('06:00', '14:01', TOO_LONG_MESSAGE),
        }
        for name, (start, end, message) in cases.items():
            with self.subTest(name):
                response = self.create(time=start, end_time=end)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['form'].errors['end_time'], [message])
                self.assertFalse(Event.objects.exists())

    def test_missing_end_time_is_required(self):
        response = self.create(end_time='')

        self.assertEqual(response.context['form'].errors['end_time'], [END_TIME_REQUIRED_MESSAGE])
        self.assertFalse(Event.objects.exists())

    def test_form_shows_end_time_next_to_the_start(self):
        response = self.client.get(reverse('event_create'))

        self.assertContains(response, '<label for="id_end_time"')
        self.assertContains(response, 'Same day, 15 minutes to 8 hours after the start.')

    def test_edit_event_changes_the_end_time(self):
        event = self.event('18:00', '20:00', meeting_spot='Court 3')
        url = reverse('event_edit', args=[event.pk])
        edit = self.payload(end_time='21:30')
        del edit['park']

        response = self.client.post(url, edit)

        self.assertRedirects(response, event.get_absolute_url())
        event.refresh_from_db()
        self.assertEqual(event.end_time, datetime.time(21, 30))
        self.assertContains(self.client.get(url), 'value="21:30')

        response = self.client.post(url, {**edit, 'end_time': '17:00'})
        self.assertEqual(response.context['form'].errors['end_time'], [END_BEFORE_START_MESSAGE])
        event.refresh_from_db()
        self.assertEqual(event.end_time, datetime.time(21, 30))


class OlderEventTests(EndTimeTestCase):
    def test_events_without_an_end_time_run_two_hours(self):
        event = self.event('18:30')
        self.assertEqual(event.end_time, datetime.time(20, 30), 'Saving without an end time fills in the default')
        # A row the previous release saved during a rollback has no end time at all.
        Event.objects.filter(pk=event.pk).update(end_time=None)
        event.refresh_from_db()

        self.assertEqual(event.effective_end_time, datetime.time(20, 30))
        self.assertEqual(event.ends_at, datetime.datetime(2026, 11, 14, 20, 30, tzinfo=NYC_TZ))
        self.assertEqual(event.time_range, '6:30–8:30 PM')

        event.title = 'Renamed'
        event.save(update_fields=['title'])
        event.refresh_from_db()
        self.assertEqual(event.end_time, datetime.time(20, 30), 'A partial save fills it in too')

    def test_two_hour_fallback_stops_at_11_59_pm(self):
        self.assertEqual(default_end_time(datetime.time(21, 59)), datetime.time(23, 59))
        self.assertEqual(default_end_time(datetime.time(22, 30)), datetime.time(23, 59))
        self.assertEqual(default_end_time(datetime.time(23, 0)), datetime.time(23, 59))

    def test_migration_backfills_events_saved_without_an_end_time(self):
        migration = importlib.import_module('events.migrations.0005_event_end_time')
        early = self.event('09:00')
        late = self.event('22:30')
        kept = self.event('18:00', '19:15')
        Event.objects.filter(pk__in=[early.pk, late.pk]).update(end_time=None)

        migration.backfill_end_times(apps, None)

        for event, end in ((early, datetime.time(11)), (late, datetime.time(23, 59)), (kept, datetime.time(19, 15))):
            event.refresh_from_db()
            self.assertEqual(event.end_time, end)


class TimeRangeDisplayTests(EndTimeTestCase):
    def test_range_wording(self):
        self.assertEqual(self.event('18:00', '20:00').time_range, '6:00–8:00 PM')
        self.assertEqual(self.event('11:00', '13:00').time_range, '11:00 AM–1:00 PM')
        self.assertEqual(self.event('07:30', '08:45').time_range, '7:30–8:45 AM')
        self.assertEqual(self.event('12:00', '12:30').time_range, '12:00–12:30 PM')

    def test_explore_cards_and_park_lists_show_the_range(self):
        self.event('18:00', '20:00')

        explore = self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'events'})
        park_page = self.client.get(reverse('park_detail', args=[self.park.pk]))
        park_panel = self.client.get(reverse('explore_park_preview', args=[self.park.pk]))

        for response in (explore, park_page, park_panel):
            self.assertContains(response, 'Sat, Nov 14 · 6:00–8:00 PM')

    def test_json_feed_includes_the_end_time(self):
        self.event('18:00', '19:30')
        self.event('22:30', title='Late run')

        results = self.client.get(reverse('event_list_api')).json()['results']

        self.assertEqual([(row['time'], row['end_time']) for row in results], [('18:00', '19:30'), ('22:30', '23:59')])
        self.assertEqual(results[0]['ends_at'], '2026-11-14T19:30:00-05:00')

    def test_sample_events_have_valid_end_times(self):
        call_command('seed_events', stdout=StringIO())

        events = Event.objects.filter(organizer__username='sample_organizer')
        self.assertTrue(events.exists())
        for event in events:
            length = event.ends_at - event.starts_at
            self.assertTrue(
                datetime.timedelta(minutes=15) <= length <= datetime.timedelta(hours=8), event.display_title
            )
