"""Whether the organizer plays in their own event (US-3.10 / #82).

The host never gets an RSVP row; `Event.host_is_playing` decides whether they take a spot, and
`EventQuerySet.with_confirmed_players()` counts them. Every change goes through
`set_host_playing()`, which holds the event's row lock (the same lock Join takes, #31) so a host
stepping back in can't race a last-spot join. The Edit Event form (#25) should call it too, passing
the submitted capacity so both checks use the values being saved (#26).
"""

from django.db import transaction

from .models import Event, EventStatus, RSVPStatus

FULL_MESSAGE = 'The event is full. Raise the limit to add yourself.'
STARTED_MESSAGE = "This game has already started, so you can't change whether you're playing."


class HostPlayingError(Exception):
    """The requested change was refused; `str(error)` is the message to show the host."""


def host_playing_error(event, playing, *, capacity, active_rsvps):
    """Why `playing` can't be saved for `event` with this capacity, or None if it can."""
    if event.status == EventStatus.CANCELLED:
        return 'This event has been cancelled.'
    if event.status == EventStatus.REMOVED:
        return 'This event has been removed.'
    if event.is_past:
        return STARTED_MESSAGE
    if playing and not event.host_is_playing and capacity is not None and active_rsvps >= capacity:
        return FULL_MESSAGE
    return None


def set_host_playing(event_pk, playing, *, capacity=...):
    """Save whether the host plays, under the event lock. Returns (event, changed).

    `capacity` defaults to the event's saved limit; Edit Event passes the submitted one.
    Raises HostPlayingError, leaving the event unchanged, when the change isn't allowed.
    """
    with transaction.atomic():
        event = Event.objects.select_for_update().get(pk=event_pk)
        if capacity is ...:
            capacity = event.capacity
        active_rsvps = event.rsvps.filter(status=RSVPStatus.ACTIVE).count()
        error = host_playing_error(event, playing, capacity=capacity, active_rsvps=active_rsvps)
        if error:
            raise HostPlayingError(error)
        if event.host_is_playing == playing:
            return event, False
        event.host_is_playing = playing
        event.save(update_fields=['host_is_playing', 'updated_at'])
        return event, True
