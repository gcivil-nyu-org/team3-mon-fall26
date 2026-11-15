import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from parks.models import Borough, Park

from .models import Activity, Event, EventStatus, MESSAGE_MAX_LENGTH, Message

User = get_user_model()


class EventDiscussionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host = User.objects.create_user('discussion-host', 'discussion-host@example.test')
        cls.player = User.objects.create_user('discussion-player', 'discussion-player@example.test')
        cls.park = Park.objects.create(
            gispropnum='DISCUSS1',
            name='Discussion Park',
            borough=Borough.BROOKLYN,
            latitude=40.72,
            longitude=-73.95,
        )
        cls.event = cls.make_event('Discussion event')
        cls.other_event = cls.make_event('Other event')

    @classmethod
    def make_event(cls, title):
        return Event.objects.create(
            organizer=cls.host,
            park=cls.park,
            title=title,
            activity=Activity.BASKETBALL,
            date=timezone.localdate() + datetime.timedelta(days=1),
            time=datetime.time(18),
            description='Bring water.',
        )

    def make_message(self, body, *, event=None, author=None, is_announcement=False, created_at=None):
        message = Message.objects.create(
            event=event or self.event,
            author=author or self.player,
            body=body,
            is_announcement=is_announcement,
        )
        if created_at:
            Message.objects.filter(pk=message.pk).update(created_at=created_at)
        return message

    def test_announcements_come_first_newest_first_then_messages_oldest_first(self):
        base_time = timezone.now()
        self.make_message(
            'Old announcement',
            author=self.host,
            is_announcement=True,
            created_at=base_time - datetime.timedelta(days=3),
        )
        self.make_message(
            'New announcement',
            author=self.host,
            is_announcement=True,
            created_at=base_time - datetime.timedelta(days=1),
        )
        self.make_message('Old question', created_at=base_time - datetime.timedelta(hours=3))
        self.make_message('New question', created_at=base_time - datetime.timedelta(hours=1))
        self.make_message(
            'Participant announcement flag',
            is_announcement=True,
            created_at=base_time - datetime.timedelta(hours=2),
        )

        response = self.client.get(self.event.get_absolute_url())
        content = response.content.decode()

        self.assertLess(content.index('New announcement'), content.index('Old announcement'))
        self.assertLess(content.index('Old announcement'), content.index('Old question'))
        self.assertLess(content.index('Old question'), content.index('Participant announcement flag'))
        self.assertLess(content.index('Participant announcement flag'), content.index('New question'))
        self.assertContains(response, reverse('public_profile', kwargs={'username': self.host.username}))
        self.assertContains(response, reverse('public_profile', kwargs={'username': self.player.username}))
        self.assertContains(response, '<time datetime=', html=False)

    def test_discussion_is_scoped_to_the_current_event(self):
        self.make_message('Visible here')
        self.make_message('Only on the other event', event=self.other_event)

        response = self.client.get(self.event.get_absolute_url())

        self.assertContains(response, 'Visible here')
        self.assertNotContains(response, 'Only on the other event')

    def test_empty_discussion_shows_prompt(self):
        response = self.client.get(self.event.get_absolute_url())

        self.assertContains(response, 'No messages yet. Ask a question below!')

    def test_message_body_is_html_escaped(self):
        self.make_message('<script>alert("unsafe")</script>')

        response = self.client.get(self.event.get_absolute_url())

        self.assertContains(response, '&lt;script&gt;alert(&quot;unsafe&quot;)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert("unsafe")</script>')

    def test_authenticated_non_attendee_can_post_a_trimmed_message(self):
        self.client.force_login(self.player)

        response = self.client.post(
            reverse('event_message_post', kwargs={'pk': self.event.pk}),
            {'body': '  Can we bring extra balls?  '},
            follow=True,
        )

        message = Message.objects.get(event=self.event)
        self.assertRedirects(response, self.event.get_absolute_url())
        self.assertEqual(message.author, self.player)
        self.assertEqual(message.body, 'Can we bring extra balls?')
        self.assertFalse(message.is_announcement)
        self.assertContains(response, 'Can we bring extra balls?')
        self.assertContains(response, '@discussion-player')
        self.assertContains(response, '<time datetime=', html=False)

    def test_author_can_edit_message_and_thread_shows_edited_badge(self):
        message = self.make_message('Original message')
        self.client.force_login(self.player)
        edit_url = reverse('event_message_edit', kwargs={'pk': self.event.pk, 'message_pk': message.pk})

        page = self.client.get(self.event.get_absolute_url())
        self.assertContains(page, 'Edit your message')

        response = self.client.post(edit_url, {'body': '  Corrected message  '}, follow=True)

        message.refresh_from_db()
        self.assertEqual(message.body, 'Corrected message')
        self.assertIsNotNone(message.edited_at)
        self.assertContains(response, 'Corrected message')
        self.assertContains(response, 'Edited')
        self.assertNotContains(response, 'Message updated.')

    def test_author_can_edit_message_over_ajax_without_redirect(self):
        message = self.make_message('Original message')
        self.client.force_login(self.player)

        response = self.client.post(
            reverse('event_message_edit', kwargs={'pk': self.event.pk, 'message_pk': message.pk}),
            {'body': 'Updated without refresh'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Location', response.headers)
        self.assertEqual(response.json()['id'], message.pk)
        self.assertContains(response, 'Updated without refresh')
        self.assertContains(response, 'Edited')
        self.assertNotContains(response, 'Message updated.')
        message.refresh_from_db()
        self.assertEqual(message.body, 'Updated without refresh')
        self.assertIsNotNone(message.edited_at)

    def test_author_can_delete_message_and_deletion_persists(self):
        message = self.make_message('Remove this message')
        self.client.force_login(self.player)
        delete_url = reverse('event_message_delete', kwargs={'pk': self.event.pk, 'message_pk': message.pk})

        page = self.client.get(self.event.get_absolute_url())
        self.assertContains(page, 'Delete this message? This cannot be undone.')
        response = self.client.post(delete_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Location', response.headers)
        self.assertEqual(response.json(), {'deleted': True, 'id': message.pk})
        self.assertFalse(Message.objects.filter(pk=message.pk).exists())
        response = self.client.get(self.event.get_absolute_url())
        self.assertNotContains(response, 'Remove this message')
        self.assertContains(response, 'No messages yet. Ask a question below!')
        self.assertNotContains(response, 'Message deleted.')

    def test_non_author_cannot_edit_or_delete_message(self):
        message = self.make_message('Keep this message')
        self.client.force_login(self.host)
        edit_url = reverse('event_message_edit', kwargs={'pk': self.event.pk, 'message_pk': message.pk})
        delete_url = reverse('event_message_delete', kwargs={'pk': self.event.pk, 'message_pk': message.pk})
        page = self.client.get(self.event.get_absolute_url())

        self.assertNotContains(page, edit_url)
        self.assertNotContains(page, delete_url)
        edit_response = self.client.post(edit_url, {'body': 'Unauthorized edit'})
        delete_response = self.client.post(delete_url)

        self.assertEqual(edit_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        message.refresh_from_db()
        self.assertEqual(message.body, 'Keep this message')
        self.assertIsNone(message.edited_at)
        self.assertTrue(Message.objects.filter(pk=message.pk).exists())

    def test_ended_event_hides_and_refuses_participant_message_changes(self):
        message = self.make_message('Ended event message')
        Event.objects.filter(pk=self.event.pk).update(date=timezone.localdate() - datetime.timedelta(days=1))
        self.client.force_login(self.player)
        page = self.client.get(self.event.get_absolute_url())
        edit_url = reverse('event_message_edit', kwargs={'pk': self.event.pk, 'message_pk': message.pk})
        delete_url = reverse('event_message_delete', kwargs={'pk': self.event.pk, 'message_pk': message.pk})

        self.assertNotContains(page, edit_url)
        self.assertNotContains(page, delete_url)
        self.assertEqual(self.client.post(edit_url, {'body': 'Too late'}).status_code, 403)
        self.assertEqual(self.client.post(delete_url).status_code, 403)
        message.refresh_from_db()
        self.assertEqual(message.body, 'Ended event message')
        self.assertIsNone(message.edited_at)
        self.assertTrue(Message.objects.filter(pk=message.pk).exists())

    def test_organizer_and_admin_can_manage_their_own_messages_after_event_ends(self):
        admin = User.objects.create_user('discussion-admin', 'discussion-admin@example.test', is_staff=True)
        Event.objects.filter(pk=self.event.pk).update(date=timezone.localdate() - datetime.timedelta(days=1))

        for author in (self.host, admin):
            with self.subTest(author=author.username):
                message = self.make_message(f'{author.username} message', author=author)
                self.client.force_login(author)
                edit_url = reverse('event_message_edit', kwargs={'pk': self.event.pk, 'message_pk': message.pk})
                page = self.client.get(self.event.get_absolute_url())

                self.assertContains(page, edit_url)
                response = self.client.post(edit_url, {'body': 'Updated after event'}, follow=True)

                message.refresh_from_db()
                self.assertEqual(message.body, 'Updated after event')
                self.assertIsNotNone(message.edited_at)
                self.assertContains(response, 'Edited')
                message.delete()

    def test_ajax_post_returns_rendered_message_without_redirect(self):
        self.client.force_login(self.player)

        response = self.client.post(
            reverse('event_message_post', kwargs={'pk': self.event.pk}),
            {'body': 'Posted without refreshing'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('message', response.json())
        self.assertContains(response, 'Posted without refreshing')
        self.assertEqual(Message.objects.get(event=self.event).author, self.player)

    def test_blank_and_whitespace_only_messages_are_rejected(self):
        self.client.force_login(self.player)
        url = reverse('event_message_post', kwargs={'pk': self.event.pk})

        for body in ('', ' \t \n '):
            with self.subTest(body=body):
                response = self.client.post(url, {'body': body}, follow=True)

                self.assertContains(response, 'Message cannot be blank.')
                self.assertFalse(Message.objects.filter(event=self.event).exists())

    def test_message_length_is_limited_after_stripping(self):
        self.client.force_login(self.player)
        url = reverse('event_message_post', kwargs={'pk': self.event.pk})
        valid_body = f"  {'a' * MESSAGE_MAX_LENGTH}  "
        response = self.client.post(url, {'body': valid_body}, follow=True)
        self.assertEqual(Message.objects.get(event=self.event).body, 'a' * MESSAGE_MAX_LENGTH)
        self.assertNotContains(response, 'Message posted.')

        response = self.client.post(url, {'body': 'a' * (MESSAGE_MAX_LENGTH + 1)}, follow=True)

        self.assertContains(response, f'Message cannot exceed {MESSAGE_MAX_LENGTH} characters.')
        self.assertEqual(Message.objects.filter(event=self.event).count(), 1)

    def test_anonymous_visitor_sees_login_link_and_direct_post_redirects_to_login(self):
        url = reverse('event_message_post', kwargs={'pk': self.event.pk})

        page = self.client.get(self.event.get_absolute_url())
        response = self.client.post(url, {'body': 'A question'})

        self.assertContains(page, 'Log in to join the discussion')
        self.assertNotContains(page, 'name="body"')
        self.assertRedirects(response, f"{reverse('login')}?next={self.event.get_absolute_url()}")
        self.assertFalse(Message.objects.filter(event=self.event).exists())

    def test_anonymous_ajax_post_returns_login_required_response(self):
        response = self.client.post(
            reverse('event_message_post', kwargs={'pk': self.event.pk}),
            {'body': 'A question'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {'error': 'Please log in to join the discussion.'})
        self.assertFalse(Message.objects.filter(event=self.event).exists())

    def test_live_updates_return_only_new_messages_for_this_event(self):
        first = self.make_message('Already displayed')
        self.make_message('Other event message', event=self.other_event)
        new_message = self.make_message('New live message', author=self.host, is_announcement=True)
        url = reverse('event_discussion_updates', kwargs={'pk': self.event.pk})

        response = self.client.get(url, {'after': first.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['latest_id'], new_message.pk)
        self.assertEqual(len(response.json()['messages']), 1)
        self.assertTrue(response.json()['messages'][0]['is_announcement'])
        self.assertContains(response, 'New live message')
        self.assertNotContains(response, 'Other event message')

    def test_live_updates_reject_an_invalid_cursor(self):
        response = self.client.get(
            reverse('event_discussion_updates', kwargs={'pk': self.event.pk}),
            {'after': 'not-a-number'},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Invalid message cursor.'})

    def test_live_updates_stop_when_event_discussion_is_closed(self):
        Event.objects.filter(pk=self.event.pk).update(status=EventStatus.CANCELLED)

        response = self.client.get(
            reverse('event_discussion_updates', kwargs={'pk': self.event.pk}),
            {'after': 0},
        )

        self.assertEqual(
            response.json(),
            {'messages': [], 'latest_id': 0, 'discussion_open': False},
        )

    def test_cancelled_and_removed_events_hide_form_and_refuse_posts(self):
        self.client.force_login(self.player)
        url = reverse('event_message_post', kwargs={'pk': self.event.pk})

        for status in (EventStatus.CANCELLED, EventStatus.REMOVED):
            with self.subTest(status=status):
                Event.objects.filter(pk=self.event.pk).update(status=status)
                page = self.client.get(self.event.get_absolute_url())
                response = self.client.post(url, {'body': 'A question'}, follow=True)

                self.assertNotContains(page, 'name="body"')
                self.assertContains(page, 'Discussion is closed for this event.')
                self.assertContains(response, 'Discussion is closed for this event.')
                self.assertFalse(Message.objects.filter(event=self.event).exists())
                Event.objects.filter(pk=self.event.pk).update(status=EventStatus.SCHEDULED)

    def test_post_endpoint_rejects_get(self):
        response = self.client.get(reverse('event_message_post', kwargs={'pk': self.event.pk}))

        self.assertEqual(response.status_code, 405)

    def test_edit_and_delete_endpoints_reject_get(self):
        message = self.make_message('Post-only endpoints')

        edit_response = self.client.get(
            reverse('event_message_edit', kwargs={'pk': self.event.pk, 'message_pk': message.pk})
        )
        delete_response = self.client.get(
            reverse('event_message_delete', kwargs={'pk': self.event.pk, 'message_pk': message.pk})
        )

        self.assertEqual(edit_response.status_code, 405)
        self.assertEqual(delete_response.status_code, 405)
