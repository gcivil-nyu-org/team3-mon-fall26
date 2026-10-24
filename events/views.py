from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.generic import CreateView, DetailView, ListView

from .forms import EventCreateForm
from .models import Event
from .explore import (configure_filter_form, filter_params, filter_pills, matching_events, parse_filters)


def filter_events(params, user=None):
    """Compatibility entry point for callers of #68's feed helper."""
    _, filters = parse_filters(params)
    return matching_events(filters, user).select_related('park', 'organizer'), filters


def personal_scope_redirect(request, filters):
    if filters['scope'] != 'all' and not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())


class EventListView(ListView):
    """Public discovery feed of upcoming events (#25)."""

    model = Event
    template_name = 'events/event_list.html'
    context_object_name = 'events'
    paginate_by = 20

    def dispatch(self, request, *args, **kwargs):
        self.filter_form, self.filters = parse_filters(request.GET)
        redirect = personal_scope_redirect(request, self.filters)
        return redirect or super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return matching_events(self.filters, self.request.user).select_related('park', 'organizer')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        configure_filter_form(self.filter_form, self.filters)
        pills = filter_pills(self.filters)
        context.update(filters=self.filters, filter_form=self.filter_form,
                       filter_pills=pills, any_filter=bool(pills or self.filters['events_only']),
                       explore_params=filter_params(self.filters))
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

