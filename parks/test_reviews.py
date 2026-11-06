"""US-6.1, US-6.2 and US-6.4: public reviews, safe submission and live summaries."""

import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from events.models import Event
from events.views import EventListView

from .forms import ReviewForm
from .models import Facility, Park, Review

User = get_user_model()


class ReviewTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.writer = User.objects.create_user('park-writer', 'park-writer@example.test')
        cls.other_writer = User.objects.create_user('park-other', 'park-other@example.test')
        cls.park = Park.objects.create(gispropnum='REV1', name='Review Park', borough='B')
        cls.other_park = Park.objects.create(gispropnum='REV2', name='Other Review Park', borough='Q')
        cls.retired = Park.objects.create(gispropnum='REV3', name='Retired Review Park', borough='M', is_retired=True)

    def setUp(self):
        self.url = reverse('park_detail', kwargs={'pk': self.park.pk})

    def review(self, **changes):
        data = {'park': self.park, 'user': self.writer, 'rating': 4, 'body': 'Clean courts and working lights.'}
        data.update(changes)
        return Review.objects.create(**data)


class ReviewModelTests(ReviewTestCase):
    def test_rating_validators_reject_out_of_range_values(self):
        for rating in (0, 6):
            with self.subTest(rating=rating), self.assertRaises(ValidationError):
                Review(park=self.park, user=self.writer, rating=rating, body='Useful details.').full_clean()

    def test_database_also_rejects_invalid_ratings(self):
        for rating in (0, 6):
            with self.subTest(rating=rating), self.assertRaises(IntegrityError), transaction.atomic():
                self.review(rating=rating)

    def test_database_rejects_duplicate_user_park_reviews(self):
        self.review()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.review(body='A duplicate.')
        self.assertEqual(Review.objects.count(), 1)

    def test_members_can_review_different_parks_and_other_members_can_review_the_same_park(self):
        self.review()
        self.review(park=self.other_park)
        self.review(user=self.other_writer)
        self.assertEqual(Review.objects.count(), 3)


class ReviewSubmissionTests(ReviewTestCase):
    def test_anonymous_page_invites_login_and_post_returns_to_the_park(self):
        self.assertContains(self.client.get(self.url), 'Log in to write a review')
        response = self.client.post(self.url, {'rating': 5, 'body': 'Great courts.'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(urlsplit(response.url).path, reverse('login'))
        self.assertEqual(parse_qs(urlsplit(response.url).query)['next'], [self.url + '#reviews'])
        self.assertFalse(Review.objects.exists())

    def test_member_can_submit_trimmed_text_and_sees_own_review_instead_of_another_form(self):
        self.client.force_login(self.writer)
        response = self.client.post(self.url, {'rating': '5', 'body': '  Courts are in great shape.  '}, follow=True)
        self.assertRedirects(response, self.url + '#reviews')
        review = Review.objects.get()
        self.assertEqual((review.user, review.park, review.rating), (self.writer, self.park, 5))
        self.assertEqual(review.body, 'Courts are in great shape.')
        self.assertContains(response, 'Your review has been posted.')
        self.assertContains(response, 'Your review')
        self.assertContains(response, 'Courts are in great shape.')
        self.assertNotContains(response, 'aria-label="Write a park review"')
        self.assertContains(response, '1 review)')

    def test_second_submission_is_refused_without_overwriting_the_original(self):
        original = self.review()
        self.client.force_login(self.writer)
        response = self.client.post(self.url, {'rating': 1, 'body': 'Second submission.'}, follow=True)
        original.refresh_from_db()
        self.assertEqual(Review.objects.count(), 1)
        self.assertEqual(original.rating, 4)
        self.assertEqual(original.body, 'Clean courts and working lights.')
        self.assertContains(response, 'You have already reviewed this park.')

    def test_form_rejects_invalid_ratings_and_empty_or_whitespace_text(self):
        self.client.force_login(self.writer)
        invalid = [{'rating': x, 'body': 'Usable courts.'} for x in ('', '0', '6', '2.5', 'invalid')]
        invalid += [{'rating': '4', 'body': x} for x in ('', '   ', '\n\t')]
        for payload in invalid:
            with self.subTest(payload=payload):
                response = self.client.post(self.url, payload)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['review_form'].errors)
                self.assertContains(response, 'aria-label="Write a park review"')
                self.assertFalse(Review.objects.exists())

    def test_posted_ownership_and_park_fields_cannot_redirect_the_review(self):
        self.client.force_login(self.writer)
        self.client.post(
            self.url,
            {'rating': 3, 'body': 'Good lighting.', 'user': self.other_writer.pk, 'park': self.other_park.pk},
        )
        review = Review.objects.get()
        self.assertEqual((review.user_id, review.park_id), (self.writer.pk, self.park.pk))

    def test_first_submission_race_is_caught_by_the_database_without_a_500(self):
        original_is_valid = ReviewForm.is_valid

        def competing_submission(form):
            valid = original_is_valid(form)
            if valid:
                self.review(body='The first submission won.')
            return valid

        self.client.force_login(self.writer)
        with patch.object(ReviewForm, 'is_valid', autospec=True, side_effect=competing_submission):
            response = self.client.post(self.url, {'rating': 1, 'body': 'The losing submission.'}, follow=True)
        self.assertContains(response, 'You have already reviewed this park.')
        self.assertEqual(Review.objects.count(), 1)
        self.assertEqual(Review.objects.get().body, 'The first submission won.')

    def test_unrelated_database_errors_are_not_hidden_as_duplicate_reviews(self):
        self.client.force_login(self.writer)
        with patch.object(Review, 'save', side_effect=IntegrityError('Unrelated database failure')):
            with self.assertRaises(IntegrityError):
                self.client.post(self.url, {'rating': 5, 'body': 'Useful feedback.'})

    def test_review_submission_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.writer)
        self.assertEqual(client.post(self.url, {'rating': 5, 'body': 'Useful feedback.'}).status_code, 403)
        self.assertFalse(Review.objects.exists())

    def test_missing_or_retired_park_cannot_receive_a_review(self):
        self.client.force_login(self.writer)
        for pk in (self.retired.pk, self.retired.pk + 1000):
            with self.subTest(pk=pk):
                response = self.client.post(
                    reverse('park_detail', kwargs={'pk': pk}), {'rating': 5, 'body': 'Details.'}
                )
                self.assertEqual(response.status_code, 404)
        self.assertFalse(Review.objects.exists())


class ReviewListTests(ReviewTestCase):
    def test_public_list_is_park_scoped_newest_first_with_author_links_rating_and_date(self):
        older = self.review(body='Older court report.')
        newer = self.review(user=self.other_writer, rating=5, body='Newer lighting report.')
        self.review(park=self.other_park, body='A different park report.')
        response = self.client.get(self.url)
        self.assertEqual([review.pk for review in response.context['reviews_page']], [newer.pk, older.pk])
        self.assertContains(response, f'href="{reverse("public_profile", kwargs={"username": self.writer.username})}"')
        self.assertContains(response, '5 / 5 stars')
        self.assertContains(response, newer.created_at.strftime('%b'))
        self.assertNotContains(response, 'A different park report.')

    def test_empty_park_has_honest_empty_and_unrated_states(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'No reviews yet')
        self.assertContains(response, 'Not yet rated')
        self.assertNotContains(response, '0.0')

    def test_review_text_is_escaped_and_line_breaks_are_preserved(self):
        self.review(body='<script>alert("unsafe")</script>\nWorking water fountain.')
        response = self.client.get(self.url)
        self.assertNotContains(response, '<script>alert(')
        self.assertContains(response, '&lt;script&gt;')
        self.assertContains(response, '<br>Working water fountain.')

    def test_review_pages_are_bounded_and_own_review_remains_visible(self):
        own = self.review(body='My older review.')
        for index in range(12):
            user = User.objects.create_user(f'review-page-{index}', f'page-{index}@example.test')
            self.review(user=user, body=f'New report {index}.')
        self.client.force_login(self.writer)
        first = self.client.get(self.url)
        self.assertEqual(len(first.context['reviews_page']), 10)
        self.assertNotIn(own.pk, [review.pk for review in first.context['reviews_page']])
        self.assertContains(first, 'My older review.')
        self.assertNotContains(first, 'aria-label="Write a park review"')
        self.assertContains(first, '?reviews_page=2#reviews')
        second = self.client.get(self.url, {'reviews_page': 2})
        self.assertEqual(len(second.context['reviews_page']), 3)
        self.assertEqual(self.client.get(self.url, {'reviews_page': 'invalid'}).status_code, 200)

    def test_author_loading_keeps_page_query_count_constant(self):
        self.review()
        with self.assertNumQueries(5):
            self.client.get(self.url)
        for index in range(5):
            user = User.objects.create_user(f'review-query-{index}', f'query-{index}@example.test')
            self.review(user=user)
        with self.assertNumQueries(5):
            self.client.get(self.url)


class ParkRatingSummaryTests(ReviewTestCase):
    def setUp(self):
        super().setUp()
        self.reviews = [self.review(rating=5)]
        for index, rating in enumerate((5, 5, 3)):
            user = User.objects.create_user(f'rating-{index}', f'rating-{index}@example.test')
            self.reviews.append(self.review(user=user, rating=rating))
        for index in range(3):
            Facility.objects.create(park=self.park, system_id=f'REV-FAC-{index}', sports='basketball')

    def surfaces(self):
        return [
            self.client.get(self.url),
            self.client.get(reverse('event_list'), {'view': 'list', 'tab': 'parks', 'q': self.park.name}),
            self.client.get(reverse('explore_park_preview', kwargs={'pk': self.park.pk})),
            self.client.get(reverse('event_list'), {'view': 'map', 'park': self.park.pk, 'panel': '1'}),
        ]

    def assert_summary(self, average, count):
        for response in self.surfaces():
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, f'{average} ({count} review' + ('s' if count != 1 else '') + ')')

    def test_same_average_and_count_appear_on_page_card_ajax_panel_and_initial_panel(self):
        self.assert_summary('4.5', 4)
        park = Park.objects.with_review_summary().get(pk=self.park.pk)
        self.assertEqual(park.avg_rating, 4.5)
        self.assertEqual(park.review_count, 4)

    def test_event_and_facility_filters_do_not_distort_review_or_event_totals(self):
        for index in range(2):
            Event.objects.create(
                park=self.park,
                organizer=self.writer,
                title=f'Rated park game {index}',
                activity='basketball',
                date=timezone.localdate() + datetime.timedelta(days=7),
                time=datetime.time(16),
                description='A test game.',
            )
        params = {'view': 'list', 'tab': 'parks', 'sport': 'basketball', 'facility': 'basketball', 'events_only': 'on'}
        response = self.client.get(reverse('event_list'), params)
        park = next(park for park in response.context['parks'] if park.pk == self.park.pk)
        self.assertEqual((park.event_count, park.review_count, park.avg_rating), (2, 4, 4.5))
        panel = self.client.get(reverse('explore_park_preview', kwargs={'pk': self.park.pk}), params)
        self.assertEqual(panel.context['panel_event_total'], 2)
        self.assertContains(panel, '4.5 (4 reviews)')
        markers = self.client.get(reverse('event_map_api'), params).json()['parks']
        # This fixture intentionally has no map coordinates; rating joins must not invent a pin.
        self.assertEqual(markers, [])

    def test_unrated_states_on_every_surface(self):
        Review.objects.all().delete()
        for response in self.surfaces():
            self.assertContains(response, 'Not yet rated')
            self.assertNotContains(response, '0.0')

    def test_summary_updates_after_a_review_is_edited(self):
        self.reviews[0].rating = 1
        self.reviews[0].save()
        self.assert_summary('3.5', 4)

    def test_summary_updates_after_a_review_is_deleted(self):
        self.reviews[0].delete()
        self.assert_summary('4.3', 3)

    def test_summary_updates_after_administrator_deletion(self):
        admin = User.objects.create_user('review-admin', 'review-admin@example.test', is_staff=True, is_superuser=True)
        self.client.force_login(admin)
        response = self.client.post(reverse('admin:parks_review_delete', args=[self.reviews[0].pk]), {'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        self.assert_summary('4.3', 3)

    def test_repeated_ratings_are_weighted_and_facilities_do_not_multiply_reviews(self):
        self.reviews[-1].rating = 1
        self.reviews[-1].save()
        # (5 + 5 + 5 + 1) / 4 is 4.0, not AVG(DISTINCT rating) == 3.0.
        self.assert_summary('4.0', 4)

    def test_rating_summaries_do_not_add_per_card_queries_or_change_event_counts(self):
        factory = RequestFactory()

        def render_parks():
            request = factory.get('/events/', {'view': 'list', 'tab': 'parks'})
            request.user = AnonymousUser()
            request.session = {}
            return EventListView.as_view()(request).render()

        with CaptureQueriesContext(connection) as captured:
            response = render_parks()
        self.assertContains(response, '4.5 (4 reviews)')
        query_count = len(captured)
        for index in range(8):
            park = Park.objects.create(gispropnum=f'RATING-{index}', name=f'Rating Park {index}', borough='B')
            self.review(park=park)
        with self.assertNumQueries(query_count):
            response = render_parks()
        self.assertEqual(len(response.context_data['parks']), 10)
        self.assertTrue(all(park.event_count == 0 for park in response.context_data['parks']))
        self.assertEqual(Event.objects.count(), 0)
