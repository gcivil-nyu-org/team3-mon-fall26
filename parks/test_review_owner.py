"""#41: members edit or delete their own park review."""

import re

from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth import get_user_model
from django.urls import reverse

from .models import Park, Review
from .test_reviews import ReviewTestCase

User = get_user_model()


class OwnReviewTestCase(ReviewTestCase):
    def setUp(self):
        super().setUp()
        self.own = self.review()
        self.edit_url = reverse('park_review_edit', kwargs={'pk': self.own.pk})
        self.delete_url = reverse('park_review_delete', kwargs={'pk': self.own.pk})
        self.client.force_login(self.writer)

    def messages(self, response):
        return [str(message) for message in response.context['messages']]

    def assertUnchanged(self):
        self.own.refresh_from_db()
        self.assertEqual((self.own.rating, self.own.body), (4, 'Clean courts and working lights.'))


class OwnerEditsTests(OwnReviewTestCase):
    # Scenario: Owner edits
    def test_edit_button_opens_the_review_as_a_prefilled_form(self):
        page = self.client.get(self.url)
        self.assertContains(page, f'href="{self.url}?edit_review=1#reviews">Edit</a>')

        form_page = self.client.get(self.url, {'edit_review': '1'})
        self.assertContains(form_page, f'action="{self.edit_url}"')
        self.assertContains(form_page, '<option value="4" selected>4 stars</option>', html=True)
        self.assertContains(form_page, 'Clean courts and working lights.</textarea>')
        self.assertContains(form_page, '>Save changes</button>')
        self.assertContains(form_page, f'href="{self.url}#reviews">Cancel</a>')

    def test_saving_valid_changes_shows_the_updated_review_on_the_park_page(self):
        response = self.client.post(
            self.edit_url, {'rating': '2', 'body': '  Lights were out all week.  '}, follow=True
        )

        self.assertRedirects(response, self.url + '#reviews')
        self.own.refresh_from_db()
        self.assertEqual((self.own.rating, self.own.body), (2, 'Lights were out all week.'))
        self.assertEqual(Review.objects.count(), 1)
        self.assertContains(response, 'Lights were out all week.')
        self.assertContains(response, '2 / 5 stars')
        self.assertNotContains(response, 'Clean courts and working lights.')

    def test_rating_summary_follows_the_edit(self):
        self.client.post(self.edit_url, {'rating': '1', 'body': 'Changed my mind.'})
        park = Park.objects.with_review_summary().get(pk=self.park.pk)
        self.assertEqual((park.review_count, park.avg_rating), (1, 1))

    def test_invalid_changes_show_errors_and_keep_the_review(self):
        for data in ({'rating': '0', 'body': 'Fine'}, {'rating': '6', 'body': 'Fine'}, {'rating': '3', 'body': '   '}):
            with self.subTest(data=data):
                response = self.client.post(self.edit_url, data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f'action="{self.edit_url}"')
                self.assertTrue(response.context['review_edit_form'].errors)
                self.assertUnchanged()

    def test_posted_park_or_user_fields_are_ignored(self):
        self.client.post(
            self.edit_url,
            {'rating': '5', 'body': 'Great', 'park': self.other_park.pk, 'user': self.other_writer.pk},
        )
        self.own.refresh_from_db()
        self.assertEqual((self.own.park, self.own.user, self.own.rating), (self.park, self.writer, 5))

    def test_invalid_edit_on_a_retired_park_explains_without_a_404(self):
        retired_review = self.review(park=self.retired)
        url = reverse('park_review_edit', kwargs={'pk': retired_review.pk})
        response = self.client.post(url, {'rating': '9', 'body': 'x'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Choose a rating from 1 to 5 stars.', self.messages(response))
        retired_review.refresh_from_db()
        self.assertEqual(retired_review.rating, 4)


class OwnerDeletesTests(OwnReviewTestCase):
    # Scenario: Owner deletes
    def test_delete_asks_for_confirmation(self):
        page = self.client.get(self.url)
        self.assertContains(page, f'action="{self.delete_url}" onsubmit="return confirm(')

    def test_deleting_removes_the_review_and_lets_me_write_a_new_one(self):
        response = self.client.post(self.delete_url, follow=True)

        self.assertRedirects(response, self.url + '#reviews')
        self.assertFalse(Review.objects.filter(pk=self.own.pk).exists())
        self.assertContains(response, 'Write a review')
        self.assertContains(response, 'No reviews yet')

        self.client.post(self.url, {'rating': '3', 'body': 'Second look: decent.'}, follow=True)
        self.assertEqual(Review.objects.get().body, 'Second look: decent.')

    def test_own_review_on_a_retired_park_can_still_be_deleted(self):
        retired_review = self.review(park=self.retired)
        response = self.client.post(reverse('park_review_delete', kwargs={'pk': retired_review.pk}))
        self.assertRedirects(response, reverse('event_list'), fetch_redirect_response=False)
        self.assertFalse(Review.objects.filter(pk=retired_review.pk).exists())


class NonOwnerTests(OwnReviewTestCase):
    # Scenario: Non-owner
    def test_other_members_get_403_and_the_review_is_unchanged(self):
        self.client.force_login(self.other_writer)
        self.assertEqual(self.client.post(self.edit_url, {'rating': '1', 'body': 'Hijacked'}).status_code, 403)
        self.assertEqual(self.client.post(self.delete_url).status_code, 403)
        self.assertUnchanged()

    def test_staff_are_non_owners_here_too(self):
        # Administrator removal is #43, with its own audit entry.
        staff = User.objects.create_user('park-staff', 'park-staff@example.test', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(self.delete_url).status_code, 403)
        self.assertUnchanged()

    def test_other_members_see_no_edit_or_delete_controls(self):
        self.client.force_login(self.other_writer)
        page = self.client.get(self.url, {'edit_review': '1'})
        self.assertNotContains(page, self.edit_url)
        self.assertNotContains(page, self.delete_url)
        self.assertNotContains(page, 'edit_review=1')

    def test_anonymous_users_go_to_login_and_nothing_changes(self):
        self.client.logout()
        for url in (self.edit_url, self.delete_url):
            with self.subTest(url=url):
                response = self.client.post(url, {'rating': '1', 'body': 'x'})
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response['Location'].startswith(reverse('login')))
        self.assertUnchanged()

    def test_get_is_not_allowed(self):
        for url in (self.edit_url, self.delete_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)
        self.assertUnchanged()

    def test_missing_review_is_404(self):
        missing = reverse('park_review_delete', kwargs={'pk': self.own.pk + 999})
        self.assertEqual(self.client.post(missing).status_code, 404)

    def test_edit_and_delete_require_csrf(self):
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(self.writer)
        self.assertEqual(client.post(self.edit_url, {'rating': '1', 'body': 'x'}).status_code, 403)
        self.assertEqual(client.post(self.delete_url).status_code, 403)
        self.assertUnchanged()


class AdministratorReviewModerationTests(ReviewTestCase):
    def test_staff_can_remove_any_review_and_audit_snapshot_and_summary_update(self):
        review = self.review(rating=5, body='Broken glass near the playground.')
        remaining = self.review(user=self.other_writer, rating=1, body='Water fountain works.')
        admin = User.objects.create_user('park-review-moderator', 'moderator@example.test', is_staff=True)
        self.client.force_login(admin)
        moderation_url = reverse('park_review_moderate_delete', kwargs={'pk': review.pk})

        page = self.client.get(self.url)
        self.assertContains(page, f'action="{moderation_url}"')
        self.assertContains(page, 'Remove')
        response = self.client.post(moderation_url, follow=True)

        self.assertRedirects(response, self.url + '#reviews')
        self.assertFalse(Review.objects.filter(pk=review.pk).exists())
        self.assertContains(response, 'Water fountain works.')
        self.assertNotContains(response, 'Broken glass near the playground.')
        self.assertNotContains(response, 'Review has been removed.')
        self.assertContains(response, '1.0 (1 review)')
        self.assertTrue(Review.objects.filter(pk=remaining.pk).exists())

        entry = LogEntry.objects.get(action_flag=DELETION, object_id=str(review.pk))
        self.assertEqual(entry.user, admin)
        self.assertEqual(entry.content_type.model, 'review')
        self.assertIn('Removed park review', entry.get_change_message())
        self.assertIn('body: Broken glass near the playground.', entry.get_change_message())
        self.assertIsNotNone(entry.action_time)

    def test_non_staff_cannot_see_or_use_moderation_action(self):
        review = self.review()
        self.client.force_login(self.other_writer)
        moderation_url = reverse('park_review_moderate_delete', kwargs={'pk': review.pk})

        page = self.client.get(self.url)
        self.assertNotContains(page, moderation_url)
        self.assertNotContains(page, 'Remove')
        self.assertEqual(self.client.post(moderation_url).status_code, 403)
        self.assertTrue(Review.objects.filter(pk=review.pk).exists())
        self.assertFalse(LogEntry.objects.filter(object_id=str(review.pk), action_flag=DELETION).exists())

    def test_moderation_endpoint_is_post_only(self):
        review = self.review()
        self.client.force_login(User.objects.create_user('park-review-mod', is_staff=True))

        response = self.client.get(reverse('park_review_moderate_delete', kwargs={'pk': review.pk}))

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Review.objects.filter(pk=review.pk).exists())

    def test_staff_can_submit_the_rendered_moderation_form_with_csrf_checks_enabled(self):
        review = self.review()
        admin = User.objects.create_user('park-review-csrf-mod', is_staff=True)
        client = self.client_class(enforce_csrf_checks=True)
        client.force_login(admin)
        page = client.get(self.url)

        moderation_form = re.search(
            rb'<form\b[^>]*action="'
            + re.escape(reverse('park_review_moderate_delete', kwargs={'pk': review.pk}).encode())
            + rb'"[^>]*>(.*?)</form>',
            page.content,
            re.DOTALL,
        )
        self.assertIsNotNone(moderation_form)
        token = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', moderation_form.group(1))
        self.assertIsNotNone(token)
        response = client.post(
            reverse('park_review_moderate_delete', kwargs={'pk': review.pk}),
            {'csrfmiddlewaretoken': token.group(1).decode()},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Review.objects.filter(pk=review.pk).exists())
