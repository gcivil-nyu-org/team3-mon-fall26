from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import DetailView

from events.models import DEFAULT_PHASES, Event

from .forms import ReviewForm
from .models import Facility, Park

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


def with_availability(events):
    """Set `spots_remaining` (None when unlimited) on events annotated with confirmed players."""
    events = list(events)
    for event in events:
        event.spots_remaining = None if event.is_unlimited else max(event.capacity - event.confirmed_players, 0)
    return events


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
        context.update(
            facility_groups=facility_groups(self.object.facilities.all()), park_events=with_availability(events)
        )
        reviews = self.object.reviews.select_related('user__profile')
        context['reviews_page'] = Paginator(reviews, 10).get_page(self.request.GET.get('reviews_page'))
        context['own_review'] = (
            reviews.filter(user=self.request.user).first() if self.request.user.is_authenticated else None
        )
        context.setdefault('review_form', ReviewForm())
        return context
