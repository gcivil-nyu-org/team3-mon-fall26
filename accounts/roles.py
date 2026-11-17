"""Administrator role (US-7.1 / #44).

An administrator is an account with `is_staff=True`. Staff accounts get the "Administrators" group's
model permissions automatically (see `EmailOrUsernameBackend`), so granting or revoking staff
status is the only switch, and only superusers can flip it (`accounts.admin.UserAdmin`).

In-app staff-only views use `StaffRequiredMixin`; templates check `is_administrator` (added by
`accounts.context_processors.roles`) before showing staff controls.
"""

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied

ADMINISTRATORS_GROUP = 'Administrators'

# Model permissions Epic 7 needs, as (app_label, codename). Migration 0006 creates the group with
# these; a later story that adds a model (e.g. reports) adds its permissions in its own migration
# and here, and test_admin_access checks the two stay in step.
ADMINISTRATOR_PERMISSIONS = (
    ('parks', 'view_park'),
    ('parks', 'add_park'),
    ('parks', 'change_park'),
    ('parks', 'view_facility'),
    ('parks', 'add_facility'),
    ('parks', 'change_facility'),
    ('parks', 'view_review'),
    ('parks', 'delete_review'),
    ('events', 'view_event'),
    ('events', 'change_event'),
    ('events', 'view_rsvp'),
    ('events', 'change_rsvp'),
    ('events', 'view_message'),
    ('events', 'delete_message'),
    # Users: view, and change, which for non-superusers is limited to is_active.
    ('auth', 'view_user'),
    ('auth', 'change_user'),
    ('admin', 'view_logentry'),
)


def is_administrator(user):
    """True for active staff accounts: the people allowed into /admin/ and staff-only views."""
    return bool(user and user.is_authenticated and user.is_active and user.is_staff)


class StaffRequiredMixin(AccessMixin):
    """In-app staff-only views: anonymous users go to login, everyone else who isn't staff gets 403."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not is_administrator(request.user):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
