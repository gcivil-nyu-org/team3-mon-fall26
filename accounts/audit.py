"""Audit trail of administrative actions (#52).

The admin site already writes a Django `LogEntry` for every add, change and delete. In-app staff
actions (removing a message #38, a review #43 or an event #48, resolving a report #47) call
`log_admin_action()` so they land in the same trail, with a text snapshot of what was acted on.
Call it after the action succeeds, inside the same transaction, and before deleting the object.
"""

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType

ACTIONS = {'add': ADDITION, 'change': CHANGE, 'delete': DELETION}

# Never copied into a snapshot.
SNAPSHOT_EXCLUDE = {'password'}


def snapshot(obj):
    """Plain-text copy of `obj`'s fields, so the entry still says what it was after it's gone."""
    lines = []
    for field in obj._meta.concrete_fields:
        if field.name in SNAPSHOT_EXCLUDE:
            continue
        if field.is_relation:
            related = getattr(obj, field.name, None)
            value = f'{related} (#{getattr(obj, field.attname)})' if related is not None else None
        else:
            value = getattr(obj, field.attname)
        lines.append(f'{field.verbose_name}: {"" if value is None else value}')
    return '\n'.join(lines)


def log_admin_action(user, obj, action, message):
    """Record an administrator's in-app action on `obj` in the audit trail.

    `action` is 'add', 'change' or 'delete' (or a LogEntry flag); a soft removal that only
    changes a status is a 'change'. `message` says what happened, e.g. "Removed message: spam".
    Returns the LogEntry.
    """
    action_flag = ACTIONS.get(action, action)
    if action_flag not in ACTIONS.values():
        raise ValueError(f'Unknown audit action: {action!r}')
    return LogEntry.objects.create(
        user_id=user.pk,
        content_type=ContentType.objects.get_for_model(obj, for_concrete_model=False),
        object_id=str(obj.pk),
        object_repr=str(obj)[:200],
        action_flag=action_flag,
        change_message=f'{message}\n\nSnapshot:\n{snapshot(obj)}',
    )
