from django.contrib import admin

from .models import RSVP, Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'activity', 'park', 'organizer', 'date', 'time', 'skill_level', 'capacity', 'status')
    list_filter = ('status', 'activity', 'skill_level', 'date')
    search_fields = ('title', 'description', 'meeting_spot', 'park__name', 'organizer__username')
    raw_id_fields = ('park', 'organizer')


@admin.register(RSVP)
class RSVPAdmin(admin.ModelAdmin):
    list_display = ('event', 'user', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('event__title', 'user__username')
    raw_id_fields = ('event', 'user')

