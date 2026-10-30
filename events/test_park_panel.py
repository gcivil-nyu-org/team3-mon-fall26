"""US-2.9: filtered park panels reuse the park-page event list without extra per-row queries."""
import datetime
from html import unescape
import re
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from parks.models import Facility, Park
from .hub import EXPLORE_SESSION_KEY
from .models import Event, NYC_TZ, RSVP, RSVPStatus
from .views import explore_park_preview


class ParkPanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = get_user_model().objects.create_user('panel-host', 'panel-host@example.test')
        cls.viewer = get_user_model().objects.create_user('panel-player', 'panel-player@example.test')
        cls.park = Park.objects.create(gispropnum='PANEL1', name='Panel Park', borough='B',
            address='Bedford Avenue', zipcode='11211, 11222', latitude=40.72, longitude=-73.95)
        cls.other = Park.objects.create(gispropnum='PANEL2', name='Other Park', borough='M',
            latitude=40.78, longitude=-73.96)
        for index in range(2):
            Facility.objects.create(system_id=f'PANEL-BKB-{index}', park=cls.park,
                primary_sport='BKB', sports='basketball', is_lighted=True, is_accessible=index == 0)
        Facility.objects.create(system_id='PANEL-INACTIVE', park=cls.park,
            primary_sport='TNS', sports='tennis', feature_status='Inactive')

    def setUp(self):
        self.now = datetime.datetime(2026, 10, 30, 12, tzinfo=NYC_TZ)
        clock = patch('django.utils.timezone.now', return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def event(self, **changes):
        data = dict(organizer=self.host, park=self.park, title='Panel pickup', activity='basketball',
            date=self.now.date() + datetime.timedelta(days=1), time=datetime.time(15),
            description='A friendly game', meeting_spot='Court 2', capacity=4)
        data.update(changes)
        return Event.objects.create(**data)

    def panel(self, query=''):
        return self.client.get(reverse('explore_park_preview', args=[self.park.pk]) + '?' + query)

    def links(self, response):
        return [(unescape(url), unescape(re.sub('<[^>]+>', '', text)).strip()) for url, text in re.findall(
            r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', response.content.decode(), re.S)]

    def test_facilities_and_address_use_active_grouped_data(self):
        response = self.panel()
        for text in ('Panel Park', 'Brooklyn', 'Bedford Avenue', 'Basketball',
                     '2 lit · 1 accessible', 'Not yet rated'):
            self.assertContains(response, text)
        self.assertNotContains(response, 'Tennis')
        self.assertEqual(response.context['facility_groups'][0]['count'], 2)

    def test_missing_facilities_and_address_have_fallbacks(self):
        self.park.facilities.all().delete()
        self.park.address = ''
        self.park.save()
        response = self.panel()
        self.assertContains(response, 'Facility details not specified')
        self.assertContains(response, 'Address not specified')

    def test_all_matching_events_are_ordered_and_reuse_the_shared_partial(self):
        later = self.event(title='Later game', date=self.now.date() + datetime.timedelta(days=2))
        earlier = [self.event(title=f'Game {index}', time=datetime.time(13 + index)) for index in range(4)]
        response = self.panel('sport=basketball&zip=11211&zip=11222&skill=beginner')
        self.assertTemplateUsed(response, 'events/_park_events.html')
        self.assertEqual([e.pk for e in response.context['park_events']], [e.pk for e in earlier] + [later.pk])
        self.assertContains(response, '5 matching events')
        self.assertContains(response, 'See all 5 events here')
        self.assertContains(response, 'Meet at Court 2')
        self.assertContains(response, 'Hosted by @panel-host')

    def test_filters_agree_with_map_and_exclude_hidden_or_started_events(self):
        kept = self.event(title='Matching hoops')
        for changes in ({'activity': 'tennis'}, {'status': 'cancelled'}, {'status': 'removed'},
                        {'date': self.now.date(), 'time': datetime.time(11)}, {'park': self.other},
                        {'skill_level': 'competitive'}):
            self.event(title='Filtered away', **changes)
        query = f'park={self.park.pk}&sport=basketball&zip=11222&skill=beginner'
        response = self.panel(query)
        self.assertEqual([e.pk for e in response.context['park_events']], [kept.pk])
        self.assertNotContains(response, 'Filtered away')
        data = self.client.get('/events/api/map/?' + query).json()
        self.assertEqual(data['parks'][0]['event_count'], response.context['panel_event_total'])

    def test_personal_panel_still_hides_cancelled_and_removed_events(self):
        self.client.force_login(self.host)
        kept = self.event()
        self.event(title='Cancelled private event', status='cancelled')
        self.event(title='Removed private event', status='removed')
        response = self.panel('scope=hosting')
        self.assertEqual([e.pk for e in response.context['park_events']], [kept.pk])
        self.assertNotContains(response, 'Cancelled private event')
        self.assertNotContains(response, 'Removed private event')

    def test_availability_and_personal_badges_use_active_rsvps(self):
        self.client.force_login(self.viewer)
        attending = self.event(capacity=2)
        RSVP.objects.create(event=attending, user=self.viewer)
        hosting = self.event(title='My game', organizer=self.viewer, capacity=None)
        cancelled_rsvp = self.event(title='Not attending', host_is_playing=False)
        RSVP.objects.create(event=cancelled_rsvp, user=self.viewer, status=RSVPStatus.CANCELLED)
        response = self.panel()
        self.assertContains(response, "You're attending", count=1)
        self.assertContains(response, '>Hosting</span>', count=1)
        events = {e.pk: e for e in response.context['park_events']}
        self.assertEqual(events[attending.pk].spots_remaining, 0)
        self.assertIsNone(events[hosting.pk].spots_remaining)
        self.assertEqual(events[cancelled_rsvp.pk].spots_remaining, 4)
        self.assertContains(response, 'Full')
        self.assertContains(response, 'Unlimited spots')
        self.assertContains(response, '4 spots remaining')
        self.client.logout()
        anonymous = self.panel()
        self.assertNotContains(anonymous, "You're attending")
        self.assertNotContains(anonymous, '>Hosting</span>')

    def test_empty_state_invites_hosting_at_this_park(self):
        self.event(activity='tennis')
        response = self.panel('sport=basketball')
        self.assertContains(response, 'No upcoming events here match your filters.')
        self.assertContains(response, f'href="/events/create/?park={self.park.pk}"')
        self.assertContains(response, f'href="/parks/{self.park.pk}/"')
        self.assertNotContains(response, 'See all 0')

    def test_panel_links_preserve_repeated_filters_and_close_only_the_park(self):
        self.event()
        response = self.panel('view=map&tab=parks&sport=basketball&zip=11211&zip=11222&q=Panel')
        links = {text: url for url, text in self.links(response)}
        events = parse_qs(urlsplit(links['See all 1 event here']).query)
        self.assertEqual(events['zip'], ['11211', '11222'])
        self.assertEqual(events['q'], ['Panel'])
        self.assertEqual(events['park'], [str(self.park.pk)])
        self.assertEqual((events['view'], events['tab']), (['list'], ['events']))
        self.assertNotIn('panel', events)
        closed = parse_qs(urlsplit(links['Close park preview']).query)
        self.assertNotIn('park', closed)
        self.assertNotIn('panel', closed)
        self.assertEqual(closed['zip'], ['11211', '11222'])
        self.assertEqual((closed['view'], closed['tab']), (['map'], ['parks']))

    def test_selected_park_has_named_pill_and_limits_every_view(self):
        self.event()
        for view, tab in (('map', 'events'), ('list', 'events'), ('list', 'parks')):
            with self.subTest(view=view, tab=tab):
                response = self.client.get(f'/events/?park={self.park.pk}&panel=1&view={view}&tab={tab}')
                self.assertEqual(response.context['park_total'], 1)
                self.assertEqual(response.context['event_total'], 1)
                self.assertContains(response, 'Park: Panel Park')
                self.assertContains(response, 'aria-label="Park quick view" open')

    def test_no_js_quick_view_links_to_full_park_page(self):
        response = self.client.get('/events/?view=list&tab=parks&zip=11211&zip=11222')
        html = response.content.decode()
        link = re.search(r'<a class="explore-quick-view"[^>]+>', html).group()
        self.assertIn(f'href="/parks/{self.park.pk}/"', link)
        self.assertIn(f'park={self.park.pk}&amp;panel=1', link)
        self.assertIn('data-park-quick-view', link)

    def test_invalid_retired_or_mismatched_park_cannot_open_a_panel(self):
        retired = Park.objects.create(gispropnum='PANEL-OLD', name='Retired', borough='B', is_retired=True)
        for value in ('invalid', '-1', '999999999', str(retired.pk)):
            with self.subTest(value=value):
                response = self.client.get('/events/', {'park': value, 'panel': '1'})
                self.assertNotIn('selected_park', response.context)
        for pk in (999999999, retired.pk):
            self.assertEqual(self.client.get(reverse('explore_park_preview', args=[pk])).status_code, 404)
        self.assertEqual(self.panel('borough=M').status_code, 404)
        self.assertEqual(self.client.post(reverse('explore_park_preview', args=[self.park.pk])).status_code, 405)

    def test_anonymous_private_scope_requires_login(self):
        response = self.panel('scope=mine')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith('/login/?next='))

    def test_organize_login_and_return_to_results_keep_the_open_panel(self):
        self.event()
        self.client.get(f'/events/?view=map&park={self.park.pk}&panel=1&zip=11211&zip=11222')
        before = self.client.session[EXPLORE_SESSION_KEY]
        response = self.client.get(f'/events/create/?park={self.park.pk}')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(parse_qs(urlsplit(response.url).query)['next'], [f'/events/create/?park={self.park.pk}'])
        self.client.force_login(self.viewer)
        create = self.client.get(f'/events/create/?park={self.park.pk}')
        back = next(url for url, text in self.links(create) if text == '← Back to results')
        self.assertEqual(urlsplit(back).query, before)
        restored = self.client.get(back)
        self.assertEqual(restored.context['selected_park'].pk, self.park.pk)

    def test_panel_query_budget_is_constant_with_many_organizers_and_rsvps(self):
        def request():
            req = RequestFactory().get(reverse('explore_park_preview', args=[self.park.pk]))
            req.user = self.viewer
            return req
        self.event()
        with self.assertNumQueries(3):
            response = explore_park_preview(request(), self.park.pk)
        for index in range(8):
            host = get_user_model().objects.create_user(f'panel-host-{index}', f'panel{index}@example.test')
            event = self.event(organizer=host)
            RSVP.objects.create(event=event, user=self.viewer)
        with self.assertNumQueries(3):
            response = explore_park_preview(request(), self.park.pk)
        self.assertContains(response, '9 matching events')

    def test_untrusted_park_event_and_meeting_spot_text_is_escaped(self):
        self.park.name = '<script>park()</script>'
        self.park.save()
        self.event(title='<img src=x onerror=alert(1)>', meeting_spot='<svg onload=alert(2)>')
        response = self.panel()
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>park')
        self.assertNotContains(response, '<img src=x')
        self.assertNotContains(response, '<svg onload')

    def test_background_panel_request_does_not_overwrite_last_results(self):
        self.client.get('/events/?view=list&tab=parks&zip=11211')
        before = self.client.session[EXPLORE_SESSION_KEY]
        self.panel('sport=tennis')
        self.assertEqual(self.client.session[EXPLORE_SESSION_KEY], before)
