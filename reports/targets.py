from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts.audit import snapshot
from events.models import Event, Message
from parks.models import Review

User = get_user_model()

REPORT_TARGETS = {
    'event': (Event.objects.all(), 'organizer_id'),
    'message': (Message.objects.select_related('event'), 'author_id'),
    'review': (Review.objects.filter(park__is_retired=False), 'user_id'),
    'account': (User.objects.filter(is_active=True).select_related('profile'), 'pk'),
}


def target_page(target):
    if isinstance(target, Event):
        return target.get_absolute_url()
    if isinstance(target, Message):
        return target.event.get_absolute_url()
    if isinstance(target, Review):
        return reverse('park_detail', args=[target.park_id])
    return reverse('public_profile', args=[target.username])


def target_snapshot(target):
    if not isinstance(target, User):
        return snapshot(target)
    # Capture the public profile rather than the account's credentials or private fields.
    profile = getattr(target, 'profile', None)
    lines = [f'Username: {target.username}', f'Member since: {target.date_joined:%B %Y}']
    if profile:
        lines.extend(
            [
                f'Display name: {profile.display_username}',
                f'Home borough: {profile.get_home_borough_display()}',
                f'Skill level: {profile.get_skill_level_display()}',
                f'Bio: {profile.bio}',
                f'Favorite sports: {", ".join(profile.favorite_sports)}',
            ]
        )
    return '\n'.join(lines)
