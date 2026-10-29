from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_GET
from django.views.generic import CreateView, DetailView, ListView

from parks.models import Facility, Park

from .forms import EventCreateForm
from .hub import remember_explore
from .models import Event
from .explore import (
    configure_filter_form,
    displayed_parks,
    facility_labels,
    filter_params,
    filter_pills,
    matching_events,
    parse_filters,
)


def filter_events(params, user=None):
    """Compatibility entry point; every Explore surface uses the shared engine."""
    _, filters = parse_filters(params)
    return matching_events(filters, user).select_related('park', 'organizer'), filters


def personal_scope_redirect(request, filters):
    if filters['scope'] != 'all' and not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())


CLEARABLE_FILTERS = (
    'q',
    'borough',
    'zip',
    'sport',
    'facility',
    'when',
    'date_from',
    'date_to',
    'skill',
    'park',
    'events_only',
)


def clear_filters_url(filters):
    """Explore URL with every filter removed; keeps view, tab and the Show scope (US-2.5)."""
    params = filter_params(filters)
    for name in CLEARABLE_FILTERS:
        params.pop(name, None)
    return '?' + params.urlencode()


def explore_empty_state(filters, event_total, park_total, has_park_data):
    """Which shared empty-state variant (includes/empty_state.html) the results area shows (US-2.5).

    Returns (list_state, map_state); either is None when there are results to show.
    """
    if not has_park_data:
        return 'no_park_data', 'no_park_data'
    # "Only parks with events" can only be switched off on the All events scope.
    no_parks = (
        'events_only_empty'
        if filters['events_only'] and filters['scope'] == 'all' and not event_total
        else 'no_matches'
    )
    map_state = no_parks if not park_total else None
    if filters['tab'] == 'parks':
        return (no_parks if not park_total else None), map_state
    return ('no_events' if not event_total else None), map_state


def park_details_url(park):
    # #21 supplies the full park page. Never render a broken link before it lands.
    try:
        return reverse('park_detail', kwargs={'pk': park.pk})
    except NoReverseMatch:
        return None


class EventListView(ListView):
    """Explore Events: one filter state, a map and two paginated list tabs."""

    model = Event
    template_name = 'events/event_list.html'
    context_object_name = 'events'
    paginate_by = 20

    def dispatch(self, request, *args, **kwargs):
        self.filter_form, self.filters = parse_filters(request.GET)
        redirect = personal_scope_redirect(request, self.filters)
        return redirect or super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        if self.filters['tab'] == 'parks':
            return displayed_parks(self.filters, self.request.user).prefetch_related(
                Prefetch('facilities', queryset=Facility.objects.filter(feature_status='Active'))
            )
        return (
            matching_events(self.filters, self.request.user)
            .with_confirmed_players()
            .select_related('park', 'organizer')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = filter_params(self.filters)
        panel = bool(self.filters['park'] and self.request.GET.get('panel') == '1')
        if panel:
            params['panel'] = '1'
        configure_filter_form(self.filter_form, self.filters)
        if self.filters['tab'] == 'parks':
            context['parks'] = context['object_list']
            context['events'] = []
            for park in context['parks']:
                park.facility_labels = facility_labels(park)
                park.details_url = park_details_url(park)
        context.update(
            filters=self.filters,
            filter_form=self.filter_form,
            filter_pills=filter_pills(self.filters),
            explore_params=params,
            map_url=reverse('event_map_api') + '?' + params.urlencode(),
            event_total=matching_events(self.filters, self.request.user).count(),
            park_total=displayed_parks(self.filters, self.request.user).count(),
        )
        has_park_data = Park.objects.filter(is_retired=False).exists()
        list_state, map_state = explore_empty_state(
            self.filters, context['event_total'], context['park_total'], has_park_data
        )
        context.update(
            has_park_data=has_park_data,
            empty_state=list_state,
            map_empty_state=map_state,
            clear_filters_url=clear_filters_url(self.filters),
        )
        if panel:
            selected = displayed_parks(self.filters, self.request.user).prefetch_related('facilities').first()
            if selected:
                context.update(park_preview_context(selected, self.filters, self.request.user))
                context['selected_park'] = selected
        remember_explore(self.request, self.filters, context['page_obj'].number, panel=panel)
        return context


def serialize_event(event):
    return {
        'id': event.pk,
        'title': event.display_title,
        'activity': event.activity,
        'activity_label': event.get_activity_display(),
        'meeting_spot': event.meeting_spot,
        'skill_level': event.skill_level,
        'skill_level_label': event.get_skill_level_display(),
        'date': event.date.isoformat(),
        'time': event.time.strftime('%H:%M'),
        'starts_at': event.starts_at.isoformat(),
        'park': {
            'id': event.park_id,
            'name': event.park.name,
            'borough': event.park.borough,
            'borough_label': event.park.get_borough_display(),
        },
        'capacity': event.capacity,
        'is_unlimited': event.is_unlimited,
        'organizer': event.organizer.username,
        'url': event.get_absolute_url(),
    }


@require_GET
def upcoming_events_api(request):
    """JSON version of the discovery feed, with the same filters."""
    _, filters = parse_filters(request.GET)
    redirect = personal_scope_redirect(request, filters)
    if redirect:
        return redirect
    events, _ = filter_events(request.GET, request.user)
    results = [serialize_event(event) for event in events[:200]]
    return JsonResponse({'count': len(results), 'results': results})


@require_GET
def event_map_api(request):
    _, filters = parse_filters(request.GET)
    redirect = personal_scope_redirect(request, filters)
    if redirect:
        return redirect
    parks = displayed_parks(filters, request.user).exclude(latitude=None).exclude(longitude=None)
    results = [
        {
            'id': p.pk,
            'name': p.name,
            'borough': p.get_borough_display(),
            'lat': float(p.latitude),
            'lng': float(p.longitude),
            'event_count': p.event_count,
            'preview_url': reverse('explore_park_preview', kwargs={'pk': p.pk}),
        }
        for p in parks
    ]
    unmapped = (
        matching_events(filters, request.user)
        .filter(Q(park__latitude__isnull=True) | Q(park__longitude__isnull=True))
        .count()
    )
    return JsonResponse({'parks': results, 'unmapped_event_count': unmapped})


@require_GET
def explore_park_preview(request, pk):
    """Small read-only preview; the full park panel/detail stories can reuse this engine."""
    _, filters = parse_filters(request.GET)
    redirect = personal_scope_redirect(request, filters)
    if redirect:
        return redirect
    park = get_object_or_404(displayed_parks(filters, request.user).prefetch_related('facilities'), pk=pk)
    return render(request, 'events/includes/park_preview.html', park_preview_context(park, filters, request.user))


def park_preview_context(park, filters, user):
    params = filter_params(filters)
    params['park'] = str(park.pk)
    params['view'] = 'list'
    params['tab'] = 'events'
    return {
        'park': park,
        'facilities': facility_labels(park),
        'details_url': park_details_url(park),
        'events_url': reverse('event_list') + '?' + params.urlencode(),
        'preview_events': matching_events(filters, user).filter(park=park).select_related('park', 'organizer')[:3],
    }


class EventCreateView(LoginRequiredMixin, CreateView):
    model = Event
    form_class = EventCreateForm
    template_name = 'events/event_form.html'

    def form_valid(self, form):
        form.instance.organizer = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, 'Event created and published!')
        return response

    def get_success_url(self):
        return reverse('event_detail', kwargs={'pk': self.object.pk})


class EventDetailView(DetailView):
    model = Event
    template_name = 'events/event_detail.html'
    context_object_name = 'event'

    def get_queryset(self):
        return Event.objects.select_related('park', 'organizer')
