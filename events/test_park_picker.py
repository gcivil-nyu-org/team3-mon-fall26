"""US-3.9 (#81): the Create Event park picker, pre-filled from a park page or panel."""

import datetime
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from parks.models import Borough, Park

from .forms import PARK_ERROR_MESSAGE
from .models import NYC_TZ, Activity, Event

User = get_user_model()


class ParkPickerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_user('picker-host', 'picker-host@example.test')
        cls.mccarren = Park.objects.create(gispropnum='B058', name='McCarren Park', borough=Borough.BROOKLYN)
        cls.central = Park.objects.create(gispropnum='M010', name='Central Park', borough=Borough.MANHATTAN)
        cls.retired = Park.objects.create(
            gispropnum='B999', name='Closed Playground', borough=Borough.BROOKLYN, is_retired=True
        )

    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=datetime.datetime(2026, 11, 9, 12, tzinfo=NYC_TZ))
        clock.start()
        self.addCleanup(clock.stop)
        self.client.force_login(self.host)
        self.url = reverse('event_create')

    def form_page(self, park=None):
        return self.client.get(self.url, {'park': park} if park is not None else {})

    def payload(self, **changes):
        data = {
            'title': 'Sunday soccer',
            'park': self.mccarren.pk,
            'meeting_spot': 'East field',
            'activity': Activity.SOCCER,
            'date': '2026-11-15',
            'time': '10:00',
            'end_time': '12:00',
            'skill_level': 'all',
            'capacity': '12',
            'host_is_playing': 'on',
            'description': 'Friendly game',
        }
        data.update(changes)
        return data

    # Scenario: Pre-selected from a park
    def test_park_from_the_url_is_pre_selected(self):
        response = self.form_page(self.mccarren.pk)

        self.assertEqual(response.context['form']['park'].value(), self.mccarren.pk)
        self.assertContains(
            response, f'<option value="{self.mccarren.pk}" selected>McCarren Park (Brooklyn)</option>', html=True
        )

    def test_organize_button_on_the_park_page_opens_the_form_with_that_park(self):
        park_page = self.client.get(reverse('park_detail', kwargs={'pk': self.mccarren.pk}))
        organize_url = f'{self.url}?park={self.mccarren.pk}'
        self.assertContains(park_page, f'href="{organize_url}"')

        response = self.client.get(organize_url)

        self.assertEqual(response.context['form']['park'].value(), self.mccarren.pk)

    def test_pre_selected_park_can_still_be_changed(self):
        self.client.post(f'{self.url}?park={self.mccarren.pk}', self.payload(park=self.central.pk))

        self.assertEqual(Event.objects.get().park, self.central)

    # Scenario: Invalid park (an invalid `?park=` leaves the picker empty)
    def test_invalid_park_parameter_leaves_the_picker_empty(self):
        # '²', '٣' and '１' are Unicode digits that str.isdigit() accepts; '²' used to raise a 500.
        for value in ('abc', '', '-1', '999999', '9' * 30, str(self.retired.pk), '²', '٣', '１'):
            with self.subTest(park=value):
                response = self.form_page(value)

                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context['form']['park'].value())

    # Scenario: Invalid park on submit
    def test_retired_or_nonexistent_park_is_rejected_with_a_clear_message(self):
        for park in (self.retired.pk, 999999, 'abc'):
            with self.subTest(park=park):
                response = self.client.post(self.url, self.payload(park=park))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['form'].errors['park'], [PARK_ERROR_MESSAGE])
                self.assertContains(response, PARK_ERROR_MESSAGE)
                self.assertFalse(Event.objects.exists())

    def test_retired_parks_are_not_offered(self):
        response = self.form_page()

        self.assertContains(response, 'McCarren Park (Brooklyn)')
        self.assertNotContains(response, 'Closed Playground')

    # Scenario: Type to search (the JavaScript itself is tested in events/js/lookahead-select.test.cjs)
    def test_park_field_is_enhanced_into_a_searchable_picker(self):
        response = self.form_page()
        park_select = re.search(r'<select name="park"[^>]*>', response.content.decode()).group(0)

        self.assertIn('data-lookahead', park_select)
        self.assertIn('data-placeholder="Type a park name or borough"', park_select)
        self.assertIn('data-max-results="50"', park_select)
        self.assertNotIn('multiple', park_select)
        self.assertContains(response, 'js/lookahead-select.js')
        self.assertContains(response, 'css/lookahead-select.css')
