from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, DetailView, ListView, UpdateView, View

from parks.models import Facility, Park
from parks.views import facility_groups, with_availability

from .forms import EventCreateForm, EventUpdateForm
from .hosting import HostPlayingError, host_playing_error, set_host_playing
from .hub import remember_explore
from .models import Event, EventStatus, MESSAGE_MAX_LENGTH, Message, RSVP, RSVPStatus
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
            return (
                displayed_parks(self.filters, self.request.user)
                .with_review_summary()
                .prefetch_related(Prefetch('facilities', queryset=Facility.objects.filter(feature_status='Active')))
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
                .with_review_summary()
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
        displayed_parks(filters, request.user)
        .with_review_summary()
        .prefetch_related(Prefetch('facilities', queryset=Facility.objects.filter(feature_status='Active'))),
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

    def get_initial(self):
        """Pre-select the park when starting from a park page or panel (US-3.9). Invalid `?park=` values are ignored."""
        initial = super().get_initial()
        park = self.request.GET.get('park', '')
        # isdigit() alone also accepts characters such as '²' that int() rejects, so allow only ASCII 0-9.
        # The length check keeps oversized numbers away from the database's integer range.
        is_park_id = park.isascii() and park.isdigit() and len(park) <= 9
        if is_park_id and Park.objects.filter(pk=int(park), is_retired=False).exists():
            initial['park'] = int(park)
        return initial

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
            elif event.has_started:
                messages.error(request, 'This event has already started, so it can no longer be cancelled.')
            else:
                event.status = EventStatus.CANCELLED
                event.save(update_fields=['status', 'updated_at'])
                messages.success(request, 'Your event has been cancelled.')
        return redirect(event.get_absolute_url())


def edit_block_reason(event):
    """Why `event` can't be edited right now, or None (US-3.4 / #25)."""
    if event.status == EventStatus.CANCELLED:
        return 'This event has been cancelled, so it can no longer be edited.'
    if event.status == EventStatus.REMOVED:
        return 'This event has been removed, so it can no longer be edited.'
    if event.has_started:
        return 'This event has already started, so it can no longer be edited.'
    return None


def limit_below_confirmed_message(confirmed):
    """US-3.5 (#26): the error when a new limit would drop someone who's already confirmed."""
    noun = 'player' if confirmed == 1 else 'players'
    return f'Participant limit cannot be lower than the {confirmed} currently confirmed {noun}.'


class EventUpdateView(OrganizerOrAdminRequiredMixin, UpdateView):
    """Edit an upcoming event (US-3.4 / #25), for its organizer or an administrator (#27).

    The park stays locked. The limit and the host's "I'm playing" choice are checked under the event
    row lock that Join takes (#31), so a new limit never drops a confirmed player (#26), even if
    someone joins while the form is being saved.
    """

    model = Event
    form_class = EventUpdateForm
    template_name = 'events/event_form.html'

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            # Unlike the POST-only management URLs, the edit page can be shown again after login.
            return redirect_to_login(self.request.get_full_path())
        return super().handle_no_permission()

    def refused(self, event):
        reason = edit_block_reason(event)
        if reason is None:
            return None
        messages.error(self.request, reason)
        return redirect(event.get_absolute_url())

    def get(self, request, *args, **kwargs):
        return self.refused(self.get_object()) or super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.refused(self.get_object()) or super().post(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if self.request.user.pk != self.object.organizer_id:
            # Only the organizer chooses whether they play (US-3.10); an administrator edits everything else.
            del form.fields['host_is_playing']
        return form

    def get_context_data(self, **kwargs):
        return super().get_context_data(is_edit=True, **kwargs)

    def form_valid(self, form):
        with transaction.atomic():
            event = Event.objects.select_for_update().get(pk=self.object.pk)
            refused = self.refused(event)
            if refused:
                return refused
            playing = form.cleaned_data.get('host_is_playing', event.host_is_playing)
            capacity = form.cleaned_data.get('capacity')
            active_rsvps = event.rsvps.filter(status=RSVPStatus.ACTIVE).count()
            error = host_playing_error(event, playing, capacity=capacity, active_rsvps=active_rsvps)
            if error:
                form.add_error('host_is_playing', error)
                return self.form_invalid(form)
            confirmed = active_rsvps + int(playing)
            if capacity is not None and capacity < confirmed:
                form.add_error('capacity', limit_below_confirmed_message(confirmed))
                return self.form_invalid(form)
            updated = form.save(commit=False)
            # Save only the edited fields, so a cancellation saved meanwhile isn't overwritten.
            updated.save(update_fields=[*form.fields, 'updated_at'])
        messages.success(self.request, 'Your event has been updated.')
        return redirect(updated.get_absolute_url())


class EventRemoveParticipantView(OrganizerOrAdminRequiredMixin, View):
    """Remove an active RSVP without deleting its history (#33)."""

    def post(self, request, pk, rsvp_pk):
        with transaction.atomic():
            event = get_object_or_404(Event.objects.select_for_update(), pk=pk)
            rsvp = get_object_or_404(RSVP.objects.select_for_update(), pk=rsvp_pk, event=event)
            if event.status != EventStatus.SCHEDULED:
                messages.error(request, 'Participants can only be removed from scheduled events.')
            elif event.has_started:
                messages.error(request, 'This event has already started, so participants can no longer be removed.')
            elif rsvp.status != RSVPStatus.ACTIVE:
                messages.error(request, 'This participant is no longer attending this event.')
            else:
                rsvp.status = RSVPStatus.REMOVED
                rsvp.save(update_fields=['status', 'updated_at'])
                messages.success(request, 'Participant removed from the event.')
        return redirect(event.get_absolute_url())


class EventHostPlayingView(LoginRequiredMixin, View):
    """The host's own "I'm playing in this game" switch on the event page (US-3.10 / #82).

    Only the organizer chooses whether they play. Edit Event (#25) applies the same rule
    (`host_playing_error()`) together with its limit check.
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
        discussion_messages = Message.objects.filter(event=event).select_related('author__profile')
        latest_discussion_message_id = discussion_messages.order_by('-pk').values_list('pk', flat=True).first() or 0
        announcement_messages = discussion_messages.filter(author=event.organizer, is_announcement=True).order_by(
            '-created_at', '-pk'
        )
        regular_messages = discussion_messages.exclude(author=event.organizer, is_announcement=True).order_by(
            'created_at', 'pk'
        )
        confirmed_players = event.confirmed_players
        user_rsvp = None
        if self.request.user.is_authenticated and self.request.user != event.organizer:
            user_rsvp = event.rsvps.filter(user=self.request.user).first()

        event_block_reason = None
        if event.status == EventStatus.CANCELLED:
            event_block_reason = 'This event has been cancelled.'
        elif event.status == EventStatus.REMOVED:
            event_block_reason = 'This event has been removed.'
        elif event.has_started:
            event_block_reason = 'This event has already started.'

        reason = event_block_reason
        if reason is None and user_rsvp and user_rsvp.status == RSVPStatus.REMOVED:
            reason = 'Your RSVP was removed by the organizer. You can\'t rejoin this event.'
        elif reason is None and event.capacity is not None and confirmed_players >= event.capacity:
            reason = 'This event is full.'

        can_manage = event.can_be_managed_by(self.request.user)
        context.update(
            can_manage=can_manage,
            can_cancel_event=can_manage and event_block_reason is None,
            can_edit_event=can_manage and event_block_reason is None,
            active_rsvps=active_rsvps,
            announcement_messages=announcement_messages,
            regular_messages=regular_messages,
            has_discussion_messages=announcement_messages.exists() or regular_messages.exists(),
            latest_discussion_message_id=latest_discussion_message_id,
            can_post_message=event.status == EventStatus.SCHEDULED,
            message_max_length=MESSAGE_MAX_LENGTH,
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
def post_event_message(request, pk):
    event_url = reverse('event_detail', kwargs={'pk': pk})
    wants_json = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    if not request.user.is_authenticated:
        if wants_json:
            return JsonResponse({'error': 'Please log in to join the discussion.'}, status=401)
        return redirect_to_login(event_url)

    with transaction.atomic():
        event = get_object_or_404(Event.objects.select_for_update(), pk=pk)
        if event.status != EventStatus.SCHEDULED:
            error = 'Discussion is closed for this event.'
            if wants_json:
                return JsonResponse({'error': error}, status=409)
            messages.error(request, error)
        else:
            body = request.POST.get('body', '').strip()
            if not body:
                error = 'Message cannot be blank.'
                if wants_json:
                    return JsonResponse({'error': error}, status=400)
                messages.error(request, error)
            elif len(body) > MESSAGE_MAX_LENGTH:
                error = f'Message cannot exceed {MESSAGE_MAX_LENGTH} characters.'
                if wants_json:
                    return JsonResponse({'error': error}, status=400)
                messages.error(request, error)
            else:
                message = Message.objects.create(event=event, author=request.user, body=body)
                if wants_json:
                    message = Message.objects.select_related('author__profile').get(pk=message.pk)
                    return JsonResponse(
                        {
                            'html': render_to_string(
                                'events/includes/event_message.html',
                                {'message': message},
                                request=request,
                            ),
                            'id': message.pk,
                        }
                    )
    return redirect(event_url)


@require_GET
def event_discussion_updates(request, pk):
    event = get_object_or_404(Event, pk=pk)
    try:
        after_id = max(int(request.GET.get('after', '0')), 0)
    except ValueError:
        return JsonResponse({'error': 'Invalid message cursor.'}, status=400)

    if event.status != EventStatus.SCHEDULED:
        return JsonResponse({'messages': [], 'latest_id': after_id, 'discussion_open': False})

    new_messages = (
        Message.objects.filter(event=event, pk__gt=after_id).select_related('author__profile').order_by('pk')[:100]
    )
    results = []
    latest_id = after_id
    for message in new_messages:
        is_announcement = message.author_id == event.organizer_id and message.is_announcement
        results.append(
            {
                'id': message.pk,
                'is_announcement': is_announcement,
                'html': render_to_string(
                    'events/includes/event_message.html',
                    {'message': message, 'is_announcement': is_announcement},
                    request=request,
                ),
            }
        )
        latest_id = message.pk

    return JsonResponse({'messages': results, 'latest_id': latest_id, 'discussion_open': True})


def join_block_reason(event, rsvp):
    """Why `rsvp`'s user can't join `event` right now, or None. Call under the event lock (#31)."""
    if rsvp and rsvp.status == RSVPStatus.ACTIVE:
        return "You're already attending."
    if rsvp and rsvp.status == RSVPStatus.REMOVED:
        return 'Your RSVP was removed by the organizer. You can\'t rejoin this event.'
    if event.status == EventStatus.CANCELLED:
        return 'This event has been cancelled.'
    if event.status == EventStatus.REMOVED:
        return 'This event has been removed.'
    if event.has_started:
        return 'This event has already started.'
    if event.capacity is not None and event.count_confirmed_players() >= event.capacity:
        return 'This event is full.'
    return None


@require_POST
def event_rsvp(request, pk):
    event = get_object_or_404(Event, pk=pk)
    if not request.user.is_authenticated:
        return redirect_to_login(event.get_absolute_url())

    detail_url = event.get_absolute_url()
    if request.user == event.organizer:
        messages.error(request, 'Organizers cannot RSVP to their own event.')
        return redirect(detail_url)

    # US-4.3 (#31): lock the plain event row first (as EventCancelView does), so simultaneous joins,
    # the host's "I'm playing" switch and a cancel run one at a time. Only then re-read the RSVP,
    # re-check the event and count confirmed players.
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=pk)
        rsvp = RSVP.objects.filter(event=event, user=request.user).first()
        reason = join_block_reason(event, rsvp)
        if reason:
            messages.error(request, reason)
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

    # US-4.3 (#31): same lock order as Join, then re-check the event as it is now.
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=pk)
        rsvp = RSVP.objects.filter(event=event, user=request.user).first()
        if event.status != EventStatus.SCHEDULED:
            messages.error(request, 'RSVPs can only be cancelled for scheduled events.')
        elif event.has_started:
            messages.error(request, 'This event has already started.')
        elif rsvp is None or rsvp.status != RSVPStatus.ACTIVE:
            messages.error(request, 'You are not attending this event.')
        else:
            rsvp.status = RSVPStatus.CANCELLED
            rsvp.save(update_fields=['status', 'updated_at'])
            messages.success(request, 'Your RSVP has been cancelled.')

    return redirect(detail_url)
