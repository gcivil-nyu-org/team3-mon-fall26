from django.contrib import admin
from django.db import transaction
from django.template.defaultfilters import pluralize

from .models import RSVP, Event, EventStatus
from .moderation import remove_event


@admin.action(description='Remove selected events', permissions=['change'])
def remove_events(modeladmin, request, queryset):
    """US-7.5 (#48): the same removal as the event page's button, logged once per event."""
    with transaction.atomic():
        events = list(queryset.select_for_update().exclude(status=EventStatus.REMOVED))
        for event in events:
            remove_event(event, request.user)
    modeladmin.message_user(request, f'Removed {len(events)} event{pluralize(len(events))}.')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'activity',
        'park',
        'organizer',
        'date',
        'time',
        'end_time',
        'skill_level',
        'capacity',
        'status',
    )
    list_filter = ('status', 'activity', 'skill_level', 'date')
    search_fields = ('title', 'description', 'meeting_spot', 'park__name', 'organizer__username')
    raw_id_fields = ('park', 'organizer')
    actions = (remove_events,)


@admin.register(RSVP)
class RSVPAdmin(admin.ModelAdmin):
    list_display = ('event', 'user', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('event__title', 'user__username')
    raw_id_fields = ('event', 'user')
