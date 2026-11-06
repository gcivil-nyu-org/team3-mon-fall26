from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DetailView, ListView, View

from parks.models import Facility, Park
from parks.views import facility_groups, with_availability

from .forms import EventCreateForm
from .hosting import HostPlayingError, set_host_playing
from .hub import remember_explore
from .models import Event, EventStatus, RSVP, RSVPStatus
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


def with_viewer_attending(events, user):
    """Annotate `viewer_attending` (the viewer has an active RSVP) for the Explore card badges."""
    if not user.is_authenticated:
        return events
    return events.annotate(
        viewer_attending=Exists(RSVP.objects.filter(event_id=OuterRef('pk'), user=user, status=RSVPStatus.ACTIVE))
    )


def event_cards(events, user):
    """Explore event cards: spots left, and `viewer_hosting` for the Host badge (US-4.7)."""
    events = with_availability(events)
    for event in events:
        event.viewer_hosting = user.is_authenticated and event.organizer_id == user.pk
    return events


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
    # US-4.7: an empty My Events points back to all events (the account menu link opens the map).
    no_events = 'no_my_events' if filters['scope'] != 'all' else 'no_events'
    map_state = None
    if not park_total:
        map_state = no_events if no_events == 'no_my_events' and not event_total else no_parks
    if filters['tab'] == 'parks':
        return (no_parks if not park_total else None), map_state
    return (no_events if not event_total else None), map_state


def park_details_url(park):
    return reverse('park_detail', kwargs={'pk': park.pk})


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
        events = matching_events(self.filters, self.request.user).with_confirmed_players()
        return with_viewer_attending(events, self.request.user).select_related('park', 'organizer')

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
        else:
            context['events'] = event_cards(context['object_list'], self.request.user)
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
            selected = (
                displayed_parks(self.filters, self.request.user)
                .prefetch_related(Prefetch('facilities', queryset=Facility.objects.filter(feature_status='Active')))
                .first()
            )
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
    """Read-only park panel using the same validated membership as Explore (US-2.9)."""
    _, filters = parse_filters(request.GET)
    redirect = personal_scope_redirect(request, filters)
    if redirect:
        return redirect
    park = get_object_or_404(
        displayed_parks(filters, request.user).prefetch_related(
            Prefetch('facilities', queryset=Facility.objects.filter(feature_status='Active'))
        ),
        pk=pk,
    )
    return render(request, 'events/includes/park_preview.html', park_preview_context(park, filters, request.user))


def park_preview_context(park, filters, user):
    params = filter_params(filters)
    params['park'] = str(park.pk)
    params['view'] = 'list'
    params['tab'] = 'events'
    events = (
        matching_events(filters, user).filter(park=park).with_confirmed_players().select_related('organizer__profile')
    )
    park_events = event_cards(with_viewer_attending(events, user), user)
    close_params = filter_params(filters)
    close_params.pop('park', None)
    return {
        'park': park,
        'facility_groups': facility_groups(park.facilities.all()),
        'details_url': park_details_url(park),
        'events_url': reverse('event_list') + '?' + params.urlencode(),
        'park_events': park_events,
        'panel_event_total': len(park_events),
        'in_park_panel': True,
        'organize_url': reverse('event_create') + '?park=' + str(park.pk),
        'close_panel_url': reverse('event_list') + '?' + close_params.urlencode(),
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


class OrganizerOrAdminRequiredMixin(UserPassesTestMixin):
    """Guards an event's management URLs (#27): anonymous users go to login, anyone else unauthorized gets 403."""

    def test_func(self):
        return get_object_or_404(Event, pk=self.kwargs['pk']).can_be_managed_by(self.request.user)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied
        # Return to the event page, because management URLs may not accept the GET that follows login.
        return redirect_to_login(reverse('event_detail', kwargs={'pk': self.kwargs['pk']}))


class EventCancelView(OrganizerOrAdminRequiredMixin, View):
    """Soft-cancels an upcoming event (#28). The row and its roster are kept, and un-cancel is out of scope."""

    def post(self, request, pk):
        with transaction.atomic():
            event = get_object_or_404(Event.objects.select_for_update(), pk=pk)
            if event.status == EventStatus.CANCELLED:
                messages.error(request, 'This event has already been cancelled.')
            elif event.status == EventStatus.REMOVED:
                messages.error(request, 'This event has been removed.')
            elif event.is_past:
                messages.error(request, 'This event has already started, so it can no longer be cancelled.')
            else:
                event.status = EventStatus.CANCELLED
                event.save(update_fields=['status', 'updated_at'])
                messages.success(request, 'Your event has been cancelled.')
        return redirect(event.get_absolute_url())


class EventHostPlayingView(LoginRequiredMixin, View):
    """The host's own "I'm playing in this game" switch on the event page (US-3.10 / #82).

    Only the organizer chooses whether they play. Until Edit Event (#25) lands this is the place to
    change it after creation; #25's form should call the same `set_host_playing()`.
    """

    def post(self, request, pk):
        event = get_object_or_404(Event, pk=pk)
        if request.user != event.organizer:
            raise PermissionDenied
        playing = request.POST.get('playing') == '1'
        try:
            _, changed = set_host_playing(event.pk, playing)
        except HostPlayingError as error:
            messages.error(request, str(error))
        else:
            if changed:
                messages.success(
                    request, "You're playing in this game." if playing else "You're no longer playing in this game."
                )
        return redirect(event.get_absolute_url())


class EventDetailView(DetailView):
    model = Event
    template_name = 'events/event_detail.html'
    context_object_name = 'event'

    def get_queryset(self):
        return Event.objects.with_confirmed_players().select_related('park', 'organizer__profile')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = self.object
        active_rsvps = event.rsvps.filter(status=RSVPStatus.ACTIVE).select_related('user__profile')
        confirmed_players = event.confirmed_players
        user_rsvp = None
        if self.request.user.is_authenticated and self.request.user != event.organizer:
            user_rsvp = event.rsvps.filter(user=self.request.user).first()

        event_block_reason = None
        if event.status == EventStatus.CANCELLED:
            event_block_reason = 'This event has been cancelled.'
        elif event.status == EventStatus.REMOVED:
            event_block_reason = 'This event has been removed.'
        elif event.is_past:
            event_block_reason = 'This event has already started.'

        reason = event_block_reason
        if reason is None and user_rsvp and user_rsvp.status == RSVPStatus.REMOVED:
            reason = 'Your RSVP was removed by the organizer.'
        elif reason is None and event.capacity is not None and confirmed_players >= event.capacity:
            reason = 'This event is full.'

        can_manage = event.can_be_managed_by(self.request.user)
        context.update(
            can_manage=can_manage,
            can_cancel_event=can_manage and event_block_reason is None,
            active_rsvps=active_rsvps,
            confirmed_players=confirmed_players,
            spots_remaining=(None if event.capacity is None else max(event.capacity - confirmed_players, 0)),
            user_rsvp=user_rsvp,
            rsvp_block_reason=reason,
            event_block_reason=event_block_reason,
            show_rsvp_block_reason=(
                reason is not None and (user_rsvp is None or user_rsvp.status != RSVPStatus.ACTIVE)
            ),
            can_rsvp=(
                self.request.user.is_authenticated
                and self.request.user != event.organizer
                and (user_rsvp is None or user_rsvp.status == RSVPStatus.CANCELLED)
                and reason is None
            ),
        )
        return context


@require_POST
def event_rsvp(request, pk):
    event = get_object_or_404(Event.objects.with_confirmed_players(), pk=pk)
    if not request.user.is_authenticated:
        return redirect_to_login(event.get_absolute_url())

    detail_url = event.get_absolute_url()
    if request.user == event.organizer:
        messages.error(request, 'Organizers cannot RSVP to their own event.')
        return redirect(detail_url)

    rsvp = RSVP.objects.filter(event=event, user=request.user).first()
    if rsvp and rsvp.status == RSVPStatus.ACTIVE:
        messages.error(request, "You're already attending.")
        return redirect(detail_url)
    if rsvp and rsvp.status == RSVPStatus.REMOVED:
        messages.error(request, 'Your RSVP was removed by the organizer.')
        return redirect(detail_url)

    if event.status == EventStatus.CANCELLED:
        messages.error(request, 'This event has been cancelled.')
    elif event.status == EventStatus.REMOVED:
        messages.error(request, 'This event has been removed.')
    elif event.is_past:
        messages.error(request, 'This event has already started.')
    elif event.capacity is not None and event.confirmed_players >= event.capacity:
        messages.error(request, 'This event is full.')
    else:
        if rsvp:
            rsvp.status = RSVPStatus.ACTIVE
            rsvp.save(update_fields=['status', 'updated_at'])
        else:
            RSVP.objects.create(event=event, user=request.user)
        messages.success(request, 'You are attending this event.')

    return redirect(detail_url)


@require_POST
def cancel_event_rsvp(request, pk):
    event = get_object_or_404(Event, pk=pk)
    if not request.user.is_authenticated:
        return redirect_to_login(event.get_absolute_url())

    detail_url = event.get_absolute_url()
    if request.user == event.organizer:
        messages.error(request, 'Organizers cannot cancel an RSVP to their own event.')
        return redirect(detail_url)

    with transaction.atomic():
        rsvp = RSVP.objects.select_for_update().filter(event=event, user=request.user).first()
        if event.status != EventStatus.SCHEDULED:
            messages.error(request, 'RSVPs can only be cancelled for scheduled events.')
        elif event.is_past:
            messages.error(request, 'This event has already started.')
        elif rsvp is None or rsvp.status != RSVPStatus.ACTIVE:
            messages.error(request, 'You are not attending this event.')
        else:
            rsvp.status = RSVPStatus.CANCELLED
            rsvp.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Your RSVP has been cancelled.')

    return redirect(detail_url)
