"""Suspending accounts (US-7.6 / #49)."""

from django.contrib.auth import SESSION_KEY
from django.contrib.sessions.models import Session
from django.utils import timezone


def end_sessions(users):
    """Delete the users' logged-in sessions.

    Django already ignores a suspended account's session, but keeps it, so restoring the account
    would log the old session back in. Deleting it means they have to log in again.
    """
    user_ids = {str(user.pk) for user in users}
    if not user_ids:
        return
    live = Session.objects.filter(expire_date__gt=timezone.now())
    theirs = [session.pk for session in live.iterator() if session.get_decoded().get(SESSION_KEY) in user_ids]
    Session.objects.filter(pk__in=theirs).delete()
