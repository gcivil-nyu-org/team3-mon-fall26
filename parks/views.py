from django.db.models import Prefetch
from django.views.generic import DetailView

from events.models import Event

from .models import Facility, Park


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

    def get_queryset(self):
        active = Facility.objects.filter(feature_status='Active')
        return Park.objects.filter(is_retired=False).prefetch_related(Prefetch('facilities', queryset=active))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        events = (
            Event.objects.upcoming()
            .filter(park=self.object)
            .with_confirmed_players()
            .select_related('organizer__profile')
        )
        context.update(
            facility_groups=facility_groups(self.object.facilities.all()), park_events=with_availability(events)
        )
        return context
