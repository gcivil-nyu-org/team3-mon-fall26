from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView

from events.models import DEFAULT_PHASES, Event

from .forms import ReviewForm
from .models import Facility, Park, Review

REVIEW_EXISTS_MESSAGE = 'You have already reviewed this park.'


def facility_groups(facilities):
    """Facilities grouped by primary sport with lighting and accessibility counts, largest group first."""
    groups = {}
    for facility in facilities:
        label = facility.primary_sport_label()
        group = groups.setdefault(label, {'sport': label, 'count': 0, 'lighted': 0, 'accessible': 0})
        group['count'] += 1
        group['lighted'] += facility.is_lighted
        group['accessible'] += facility.is_accessible
    return sorted(groups.values(), key=lambda group: (-group['count'], group['sport']))


class ParkDetailView(DetailView):
    """Full park page: details, facilities, upcoming events and reviews (US-2.6, US-3.2)."""

    model = Park
    template_name = 'parks/park_detail.html'
    context_object_name = 'park'
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        active = Facility.objects.filter(feature_status='Active')
        return (
            Park.objects.filter(is_retired=False)
            .with_review_summary()
            .prefetch_related(Prefetch('facilities', queryset=active))
        )

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        review_url = reverse('park_detail', kwargs={'pk': self.object.pk}) + '#reviews'
        if not request.user.is_authenticated:
            return redirect_to_login(review_url)
        if self.object.reviews.filter(user=request.user).exists():
            messages.error(request, REVIEW_EXISTS_MESSAGE)
            return redirect(review_url)

        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.park = self.object
            review.user = request.user
            try:
                # The unique constraint also protects simultaneous first submissions.
                with transaction.atomic():
                    review.save()
            except IntegrityError:
                if not self.object.reviews.filter(user=request.user).exists():
                    raise
                messages.error(request, REVIEW_EXISTS_MESSAGE)
            else:
                messages.success(request, 'Your review has been posted.')
            return redirect(review_url)
        return self.render_to_response(self.get_context_data(review_form=form))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        events = (
            # US-4.8 (#117): live games stay listed until they end; ended ones drop off.
            Event.objects.in_phases(DEFAULT_PHASES)
            .filter(park=self.object)
            .with_confirmed_players()
            .select_related('organizer__profile')
        )
        context.update(facility_groups=facility_groups(self.object.facilities.all()), park_events=list(events))
        reviews = self.object.reviews.select_related('user__profile')
        context['reviews_page'] = Paginator(reviews, 10).get_page(self.request.GET.get('reviews_page'))
        context['own_review'] = (
            reviews.filter(user=self.request.user).first() if self.request.user.is_authenticated else None
        )
        context.setdefault('review_form', ReviewForm())
        # #41: the owner's Edit button opens their review as a form (?edit_review=1).
        own_review = context['own_review']
        if own_review and 'review_edit_form' not in context and self.request.GET.get('edit_review') == '1':
            context['review_edit_form'] = ReviewForm(instance=own_review)
        return context


def reviews_url(park_pk):
    return reverse('park_detail', kwargs={'pk': park_pk}) + '#reviews'


class OwnReviewView(View):
    """Base for a member's edit and delete of their own review (#41). POST only.

    Anonymous users go to login and back to the park's reviews; anyone but the author gets 403
    and the review is unchanged. Administrator removal is #43.
    """

    http_method_names = ['post']

    def post(self, request, pk):
        review = get_object_or_404(Review.objects.select_related('park'), pk=pk)
        if not request.user.is_authenticated:
            return redirect_to_login(reviews_url(review.park_id))
        if review.user_id != request.user.pk:
            raise PermissionDenied
        return self.handle(request, review)

    def done(self, review):
        # A retired park has no page to return to.
        return redirect('event_list' if review.park.is_retired else reviews_url(review.park_id))


class ReviewUpdateView(OwnReviewView):
    def handle(self, request, review):
        form = ReviewForm(request.POST, instance=review)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your review has been updated.')
            return self.done(review)
        if review.park.is_retired:
            messages.error(request, ' '.join(error for errors in form.errors.values() for error in errors))
            return self.done(review)
        # Show the park page again with the edit form and its errors.
        page = ParkDetailView()
        page.setup(request, pk=review.park_id)
        page.object = page.get_object()
        return page.render_to_response(page.get_context_data(review_edit_form=form))


class ReviewDeleteView(OwnReviewView):
    def handle(self, request, review):
        review.delete()
        messages.success(request, 'Your review has been deleted.')
        return self.done(review)
