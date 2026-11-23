"""#122: actions return to the section they came from, with any remaining messages there."""

from django.urls import reverse

from .models import RSVP, EventStatus, RSVPStatus
from .test_event_edit import EventEditTestCase

ROSTER = '#event-attendance-title'
DISCUSSION = '#event-discussion-title'


class KeepPlaceTestCase(EventEditTestCase):
    def setUp(self):
        super().setUp()
        self.detail = self.event.get_absolute_url()

    def where(self, response, text):
        """Which part of the event page `text` appears in: 'top', 'roster', 'discussion' or None."""
        html = response.content.decode().replace('&#x27;', "'")
        if text not in html:
            return None
        position = html.index(text)
        if position < html.index('<article class="event-card"'):
            return 'top'
        if html.index('id="event-attendance-title"') < position < html.index('class="event-discussion"'):
            return 'roster'
        if position > html.index('id="event-discussion-title"'):
            return 'discussion'
        return 'elsewhere'

    def post(self, name, data=None, user=None, **kwargs):
        if user:
            self.client.force_login(user)
        url = reverse(name, kwargs={'pk': self.event.pk, **kwargs})
        response = self.client.post(url, data or {})
        page = self.client.get(response['Location'])
        return response, page


class RosterActionsTests(KeepPlaceTestCase):
    """Join, Cancel RSVP, Cancel Event, Remove and the host's Save land on the roster."""

    def assertOnRoster(self, response, page, message):
        self.assertRedirects(response, self.detail + ROSTER, fetch_redirect_response=False)
        self.assertEqual(self.where(page, message), 'roster')

    def test_join_lands_on_the_roster_with_its_message(self):
        response, page = self.post('event_rsvp', user=self.players[0])
        self.assertOnRoster(response, page, 'You are attending this event.')

    def test_join_errors_also_land_on_the_roster(self):
        self.event.capacity = 1
        self.event.save()
        response, page = self.post('event_rsvp', user=self.players[0])
        self.assertOnRoster(response, page, 'This event is full.')
        response, page = self.post('event_rsvp', user=self.host)
        self.assertOnRoster(response, page, 'Organizers cannot RSVP to their own event.')

    def test_cancel_rsvp_lands_on_the_roster(self):
        self.join(1)
        response, page = self.post('event_rsvp_cancel', user=self.players[0])
        self.assertOnRoster(response, page, 'Your RSVP has been cancelled.')
        response, page = self.post('event_rsvp_cancel', user=self.players[0])
        self.assertOnRoster(response, page, 'You are not attending this event.')

    def test_cancel_event_lands_on_the_roster(self):
        response, page = self.post('event_cancel')
        self.assertOnRoster(response, page, 'Your event has been cancelled.')
        response, page = self.post('event_cancel')
        self.assertOnRoster(response, page, 'This event has already been cancelled.')

    def test_remove_participant_lands_on_the_roster(self):
        self.join(1)
        rsvp = RSVP.objects.get(event=self.event)
        response, page = self.post('event_participant_remove', rsvp_pk=rsvp.pk)
        self.assertOnRoster(response, page, 'Participant removed from the event.')
        self.assertEqual(RSVP.objects.get(pk=rsvp.pk).status, RSVPStatus.REMOVED)
        response, page = self.post('event_participant_remove', rsvp_pk=rsvp.pk)
        self.assertOnRoster(response, page, 'This participant is no longer attending this event.')

    def test_host_playing_save_lands_on_the_roster(self):
        response, page = self.post('event_host_playing', {})
        self.assertOnRoster(response, page, "You're no longer playing in this game.")
        self.event.refresh_from_db()
        self.event.capacity = 2
        self.event.save()
        self.join(2)
        response, page = self.post('event_host_playing', {'playing': '1'})
        self.assertOnRoster(response, page, 'The event is full. Raise the limit to add yourself.')

    def test_roster_messages_show_once(self):
        _, page = self.post('event_rsvp', user=self.players[0])
        self.assertContains(page, 'You are attending this event.', count=1)


class DiscussionTests(KeepPlaceTestCase):
    """Posting a message without JavaScript lands on the discussion."""

    def test_posting_without_javascript_lands_on_the_discussion(self):
        response, page = self.post('event_message_post', {'body': 'Bringing a pump.'})
        self.assertRedirects(response, self.detail + DISCUSSION, fetch_redirect_response=False)
        self.assertEqual(self.where(page, 'Bringing a pump.'), 'discussion')

    def test_discussion_errors_show_in_the_discussion(self):
        response, page = self.post('event_message_post', {'body': '   '})
        self.assertRedirects(response, self.detail + DISCUSSION, fetch_redirect_response=False)
        self.assertEqual(self.where(page, 'Message cannot be blank.'), 'discussion')

        self.event.status = EventStatus.CANCELLED
        self.event.save()
        _, page = self.post('event_message_post', {'body': 'Still on?'})
        self.assertEqual(self.where(page, 'Discussion is closed for this event.</div>'), 'discussion')

    def test_posting_with_javascript_still_gets_json(self):
        url = reverse('event_message_post', kwargs={'pk': self.event.pk})
        response = self.client.post(url, {'body': 'Hi'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response['Content-Type'], 'application/json')


class TopOfPageTests(KeepPlaceTestCase):
    def test_edit_event_save_still_returns_to_the_top_with_its_message(self):
        response = self.client.post(self.url, self.payload(title='Sunday hoops'))
        self.assertRedirects(response, self.detail, fetch_redirect_response=False)
        page = self.client.get(response['Location'])
        self.assertEqual(self.where(page, 'Your event has been updated.'), 'top')

    def test_other_pages_still_show_messages_at_the_top(self):
        self.client.post(self.url, self.payload(title='Sunday hoops'))
        page = self.client.get(reverse('event_list'))
        self.assertContains(page, 'Your event has been updated.')

    def test_page_without_messages_has_no_empty_message_boxes(self):
        page = self.client.get(self.detail)
        self.assertNotContains(page, 'class="messages')


class BackToResultsTests(KeepPlaceTestCase):
    """The map position itself is saved and restored by explore.js (events/js/explore.test.cjs)."""

    def test_back_to_results_marks_the_return_and_loads_its_script(self):
        page = self.client.get(self.detail)
        self.assertContains(page, 'data-explore-return>&larr; Back to results</a>')
        self.assertContains(page, 'js/explore-return.js')

    def test_explore_itself_has_no_back_link_script(self):
        self.assertNotContains(self.client.get(reverse('event_list')), 'explore-return.js')

    def test_show_on_map_still_focuses_on_the_park(self):
        page = self.client.get(reverse('park_detail', kwargs={'pk': self.park.pk}))
        self.assertContains(page, f'?view=map&amp;park={self.park.pk}&amp;panel=1">Show on map</a>')
        self.assertNotContains(page, 'data-explore-return>Show on map')
