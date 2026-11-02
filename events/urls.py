from django.urls import path

from .views import (
    EventCancelView,
    EventCreateView,
    EventDetailView,
    EventListView,
    upcoming_events_api,
    event_map_api,
    explore_park_preview,
    event_rsvp,
    cancel_event_rsvp,
)

urlpatterns = [
    path('events/', EventListView.as_view(), name='event_list'),
    path('events/api/upcoming/', upcoming_events_api, name='event_list_api'),
    path('events/api/map/', event_map_api, name='event_map_api'),
    path('events/parks/<int:pk>/preview/', explore_park_preview, name='explore_park_preview'),
    path('events/create/', EventCreateView.as_view(), name='event_create'),
    path('events/<int:pk>/rsvp/', event_rsvp, name='event_rsvp'),
    path('events/<int:pk>/rsvp/cancel/', cancel_event_rsvp, name='event_rsvp_cancel'),
    path('events/<int:pk>/cancel/', EventCancelView.as_view(), name='event_cancel'),
    path('events/<int:pk>/', EventDetailView.as_view(), name='event_detail'),
]
