from django.conf import settings
from django.db import migrations


def use_public_usernames(apps, schema_editor):
    Profile = apps.get_model('accounts', 'Profile')
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    database = schema_editor.connection.alias
    users = User.objects.using(database)
    profiles = list(
        Profile.objects.using(database)
        .exclude(display_username='')
        .order_by('pk')
        .values_list('user_id', 'display_username')
    )
    profile_user_ids = {user_id for user_id, _ in profiles}
    public_usernames = {username for _, username in profiles}
    usernames_without_profiles = set(
        users.exclude(pk__in=profile_user_ids).values_list('username', flat=True)
    )
    conflicts = public_usernames & usernames_without_profiles
    if conflicts:
        raise RuntimeError(
            'Cannot sync public usernames because an account without a profile '
            'already uses one of them. Rename that account before applying this migration.'
        )

    occupied_usernames = set(users.values_list('username', flat=True))
    for user_id, _ in profiles:
        temporary_username = f'__public_username_sync_{user_id}'
        while temporary_username in occupied_usernames or temporary_username in public_usernames:
            temporary_username += '_'
        users.filter(pk=user_id).update(username=temporary_username)
        occupied_usernames.add(temporary_username)

    for user_id, public_username in profiles:
        users.filter(pk=user_id).update(username=public_username)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_profile_bio_and_unique_display_username'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(use_public_usernames, migrations.RunPython.noop),
    ]
