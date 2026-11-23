from django.urls import path

from .views import (
    EventCancelView,
    EventCreateView,
    EventDetailView,
    EventHostPlayingView,
    EventListView,
    EventUpdateView,
    EventRemoveParticipantView,
    EventRemoveView,
    upcoming_events_api,
    event_map_api,
    explore_park_preview,
    explore_state,
    event_rsvp,
    cancel_event_rsvp,
    post_event_message,
    event_discussion_updates,
    edit_event_message,
    delete_event_message,
)

urlpatterns = [
    path('events/', EventListView.as_view(), name='event_list'),
    # #123: the same Explore view, returning the changed results as JSON for an update without a reload.
    path('events/results/', EventListView.as_view(partial=True), name='explore_results'),
    path('events/api/upcoming/', upcoming_events_api, name='event_list_api'),
    path('events/api/map/', event_map_api, name='event_map_api'),
    path('events/explore/state/', explore_state, name='explore_state'),
    path('events/parks/<int:pk>/preview/', explore_park_preview, name='explore_park_preview'),
    path('events/create/', EventCreateView.as_view(), name='event_create'),
    path('events/<int:pk>/rsvp/', event_rsvp, name='event_rsvp'),
    path('events/<int:pk>/rsvp/cancel/', cancel_event_rsvp, name='event_rsvp_cancel'),
    path('events/<int:pk>/edit/', EventUpdateView.as_view(), name='event_edit'),
    path(
        'events/<int:pk>/participants/<int:rsvp_pk>/remove/',
        EventRemoveParticipantView.as_view(),
        name='event_participant_remove',
    ),
    path('events/<int:pk>/cancel/', EventCancelView.as_view(), name='event_cancel'),
    path('events/<int:pk>/playing/', EventHostPlayingView.as_view(), name='event_host_playing'),
    path('events/<int:pk>/remove/', EventRemoveView.as_view(), name='event_remove'),
    path('events/<int:pk>/discussion/', post_event_message, name='event_message_post'),
    path('events/<int:pk>/discussion/updates/', event_discussion_updates, name='event_discussion_updates'),
    path(
        'events/<int:pk>/discussion/<int:message_pk>/edit/',
        edit_event_message,
        name='event_message_edit',
    ),
    path(
        'events/<int:pk>/discussion/<int:message_pk>/delete/',
        delete_event_message,
        name='event_message_delete',
    ),
    path('events/<int:pk>/', EventDetailView.as_view(), name='event_detail'),
]
