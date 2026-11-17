"""Administrators taking down events (US-7.5 / #48).

A removed event keeps its row, so the host and players still find it under My Events with a "Removed"
badge, but it leaves Explore and park pages and its page shows only a notice. Restoring one is done in
/admin/ by setting its status back to Scheduled.
"""

from accounts.audit import log_admin_action

from .models import EventStatus

REMOVAL_REASON_MAX_LENGTH = 200


def remove_event(event, user, reason=''):
    """Mark `event` removed and record it in the audit trail. Call inside a transaction."""
    event.status = EventStatus.REMOVED
    event.save(update_fields=['status', 'updated_at'])
    message = f'Removed event: {reason}' if reason else 'Removed event'
    return log_admin_action(user, event, 'change', message)
