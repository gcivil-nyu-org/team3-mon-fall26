from django.db.models import Prefetch
from django.views.generic import DetailView

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


class ParkDetailView(DetailView):
    """Full park page: details, facilities and reviews (US-2.6)."""

    model = Park
    template_name = 'parks/park_detail.html'
    context_object_name = 'park'

    def get_queryset(self):
        active = Facility.objects.filter(feature_status='Active')
        return Park.objects.filter(is_retired=False).prefetch_related(Prefetch('facilities', queryset=active))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['facility_groups'] = facility_groups(self.object.facilities.all())
        return context
