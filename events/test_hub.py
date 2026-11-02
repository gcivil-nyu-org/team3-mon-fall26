"""Hub navigation and results restoration across real requests (US-2.8)."""

import datetime
from html import unescape
import re
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from parks.models import Park
from .hub import EXPLORE_SESSION_KEY
from .models import Event


class EventsHubTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('hub-host', 'hub@example.test', 'Hub-test-password-42')
        cls.park = Park.objects.create(
            gispropnum='HUB1',
            name='Hub Park',
            borough='B',
            zipcode='11211, 11222',
            latitude='40.72',
            longitude='-73.95',
        )
        cls.event = Event.objects.create(
            organizer=cls.user,
            park=cls.park,
            title='Hub game',
            activity='basketball',
            date=timezone.localdate() + datetime.timedelta(days=7),
            time=datetime.time(16),
            description='Friendly game',
        )

    def hub_link(self, response, label):
        html = response.content.decode()
        hub = re.search(r'<nav class="events-hub-toggle".*?</nav>', html, re.S).group()
        return unescape(re.search(r'<a href="([^"]+)"[^>]*>' + label + r'</a>', hub).group(1))

    def test_navbar_and_landing_use_events_and_keep_create_shortcut(self):
        landing = self.client.get('/')
        header = landing.content.decode().split('</header>')[0]
        self.assertEqual(header.count('href="/events/">Events</a>'), 1)
        self.assertNotContains(landing, '>Browse events</a>')
        self.client.force_login(self.user)
        home = self.client.get('/')
        self.assertContains(home, 'href="/events/">Events</a>')
        self.assertContains(home, 'href="/events/create/">Create event</a>')
        self.assertContains(home, 'href="/events/?scope=mine">My Events</a>')
        self.assertNotContains(home, '>Browse events</a>')

    def test_default_explore_view_and_header(self):
        response = self.client.get('/events/')
        self.assertEqual(response.context['filters']['view'], 'map')
        link = self.hub_link(response, 'Explore Events')
        self.assertEqual(parse_qs(urlsplit(link).query), {'view': ['map'], 'tab': ['events'], 'scope': ['all']})
        self.assertContains(response, f'<a href="{link}" aria-current="page">Explore Events</a>', html=True)
        self.assertEqual(self.hub_link(response, 'Create Event'), '/events/create/')
        self.assertTemplateUsed(response, 'events/_hub_header.html')

    def test_create_and_detail_select_the_correct_hub_tab(self):
        self.client.force_login(self.user)
        create = self.client.get('/events/create/')
        self.assertContains(create, '<a href="/events/create/" aria-current="page">Create Event</a>', html=True)
        self.assertEqual(self.hub_link(create, 'Explore Events'), '/events/')
        detail = self.client.get(self.event.get_absolute_url())
        self.assertContains(detail, '<a href="/events/" aria-current="page">Explore Events</a>', html=True)
        self.assertContains(detail, '<a class="events-hub-back" href="/events/">&larr; Back to results</a>', html=True)

    def test_anonymous_create_login_return_keeps_results_session(self):
        self.client.get('/events/?view=list&tab=parks&zip=11211&zip=11222')
        redirect = self.client.get('/events/create/')
        self.assertRedirects(redirect, '/login/?next=/events/create/')
        login = self.client.post(
            '/login/?next=/events/create/',
            {'username': 'hub@example.test', 'password': 'Hub-test-password-42', 'next': '/events/create/'},
        )
        self.assertRedirects(login, '/events/create/')
        create = self.client.get('/events/create/')
        restored = parse_qs(urlsplit(self.hub_link(create, 'Explore Events')).query)
        self.assertEqual(restored['view'], ['list'])
        self.assertEqual(restored['tab'], ['parks'])
        self.assertEqual(restored['zip'], ['11211', '11222'])

    def test_detail_and_create_restore_filters_tab_page_and_selected_park(self):
        self.client.force_login(self.user)
        for index in range(21):
            Event.objects.create(
                organizer=self.user,
                park=self.park,
                title=f'Other {index}',
                activity='basketball',
                date=self.event.date,
                time=self.event.time,
                description='Game',
            )
        response = self.client.get(
            '/events/?view=list&tab=events&zip=11211&zip=11222&sport=basketball' f'&park={self.park.pk}&panel=1&page=2'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_park'], self.park)
        self.assertContains(response, 'aria-label="Park quick view" open')
        before = self.client.session[EXPLORE_SESSION_KEY]
        for url in ('/events/create/', self.event.get_absolute_url()):
            other = self.client.get(url)
            self.assertEqual(urlsplit(self.hub_link(other, 'Explore Events')).query, before)
        restored = self.client.get('/events/?' + before)
        self.assertEqual(restored.context['page_obj'].number, 2)
        self.assertEqual(restored.context['selected_park'], self.park)
        # Viewing events at this park keeps the park filter, without reopening the panel.
        listing = self.client.get(f'/events/?view=list&tab=events&park={self.park.pk}')
        self.assertNotIn('selected_park', listing.context)
        self.assertEqual(listing.context['event_total'], 22)
        self.assertNotIn('panel', parse_qs(self.client.session[EXPLORE_SESSION_KEY]))
        # Closing via a normal link updates the stored state too.
        self.client.get('/events/?view=list&tab=events&zip=11211&zip=11222')
        self.assertNotIn('park', parse_qs(self.client.session[EXPLORE_SESSION_KEY]))

    def test_invalid_and_unrelated_parameters_are_not_remembered(self):
        self.client.get(
            '/events/?view=invalid&tab=nope&zip=bad&zip=11211&next=https://evil.example/'
            '&csrfmiddlewaretoken=private&park=-1'
        )
        params = parse_qs(self.client.session[EXPLORE_SESSION_KEY])
        self.assertEqual(params, {'view': ['map'], 'tab': ['events'], 'scope': ['all'], 'zip': ['11211']})
        detail = self.client.get(self.event.get_absolute_url())
        self.assertTrue(self.hub_link(detail, 'Explore Events').startswith('/events/?'))
        self.assertNotContains(detail, 'evil.example')

    def test_failed_page_and_background_requests_do_not_replace_last_results(self):
        self.client.get('/events/?view=list&tab=parks&zip=11211&zip=11222')
        before = self.client.session[EXPLORE_SESSION_KEY]
        self.assertEqual(self.client.get('/events/?view=list&page=999').status_code, 404)
        self.client.get('/events/api/map/?sport=tennis')
        self.client.get(reverse('explore_park_preview', args=[self.park.pk]))
        self.assertEqual(self.client.session[EXPLORE_SESSION_KEY], before)

    def test_my_events_is_authenticated_and_sessions_are_isolated(self):
        self.client.force_login(self.user)
        mine = self.client.get('/events/?scope=mine')
        self.assertEqual(mine.context['filters']['scope'], 'mine')
        self.assertTrue(mine.context['filters']['events_only'])
        other = Client()
        detail = other.get(self.event.get_absolute_url())
        self.assertEqual(self.hub_link(detail, 'Explore Events'), '/events/')
        self.assertRedirects(other.get('/events/?scope=mine'), '/login/?next=/events/%3Fscope%3Dmine')

    def test_park_detail_owner_can_include_header_without_view_changes(self):
        self.client.get('/events/?view=list&tab=parks&zip=11211')
        response = self.client.get(self.event.get_absolute_url())
        rendered = Template('{% include "events/_hub_header.html" with active="explore" back=True %}').render(
            Context({'request': response.wsgi_request})
        )
        self.assertIn('Back to results', rendered)
        self.assertIn('zip=11211', rendered)
