"""Administrator metrics (US-7.8 / #51).

Each value is one `count()` query in its own function, so each can be tested on its own. They all
take the same `now`, so one page load measures everything at the same moment.
"""

import datetime

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.utils import timezone

from events.models import RSVP, Event, RSVPStatus
from parks.models import Review
from reports.models import Report

User = get_user_model()

RECENT_DAYS = 7


def recent_cutoff(now=None):
    return (now or timezone.now()) - datetime.timedelta(days=RECENT_DAYS)


def active_users(now=None):
    """Accounts that can log in, including administrators."""
    return User.objects.filter(is_active=True).count()


def new_users(now=None):
    """Accounts created in the last 7 days."""
    return User.objects.filter(date_joined__gte=recent_cutoff(now)).count()


def upcoming_events(now=None):
    """Scheduled events that haven't started yet (US-4.8)."""
    return Event.objects.upcoming(now).count()


def events_created(now=None):
    """Events created in the last 7 days, whatever has happened to them since."""
    return Event.objects.filter(created_at__gte=recent_cutoff(now)).count()


def active_rsvps_on_upcoming_events(now=None):
    """Active RSVPs to events that haven't started yet. Hosts who are playing have no RSVP."""
    return RSVP.objects.filter(status=RSVPStatus.ACTIVE, event__in=Event.objects.upcoming(now)).count()


def total_reviews(now=None):
    return Review.objects.count()


def open_reports(now=None):
    """Reports waiting in the queue (#47)."""
    return Report.objects.filter(status=Report.Status.OPEN).count()


def suspended_accounts(now=None):
    """Accounts with Active turned off (#49)."""
    return User.objects.filter(is_active=False).count()


def admin_actions(now=None):
    """Entries in the audit trail (#52) from the last 7 days."""
    return LogEntry.objects.filter(action_time__gte=recent_cutoff(now)).count()


METRICS = (
    (
        'Usage',
        (
            ('Active users', active_users),
            (f'New users (last {RECENT_DAYS} days)', new_users),
            ('Upcoming events', upcoming_events),
            (f'Events created (last {RECENT_DAYS} days)', events_created),
            ('Active RSVPs on upcoming events', active_rsvps_on_upcoming_events),
            ('Park reviews', total_reviews),
        ),
    ),
    (
        'Moderation',
        (
            ('Open reports', open_reports),
            ('Suspended accounts', suspended_accounts),
            (f'Administrator actions (last {RECENT_DAYS} days)', admin_actions),
        ),
    ),
)


def measure(now=None):
    """[(section, [(label, value), ...]), ...] for the metrics page."""
    now = now or timezone.now()
    return [(section, [(label, metric(now)) for label, metric in rows]) for section, rows in METRICS]
