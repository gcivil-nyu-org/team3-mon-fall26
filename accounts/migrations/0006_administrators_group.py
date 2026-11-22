"""US-7.1 (#44): the "Administrators" group that every staff account's permissions come from."""

from django.contrib.auth.management import create_permissions
from django.db import migrations

GROUP = 'Administrators'

# Frozen copy of accounts.roles.ADMINISTRATOR_PERMISSIONS at the time of this migration.
PERMISSIONS = (
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
    ('auth', 'view_user'),
    ('auth', 'change_user'),
    ('admin', 'view_logentry'),
)


def create_group(apps, schema_editor):
    # On a fresh database, permissions are normally created after all migrations run; make sure
    # they exist now so the group can be given them.
    for app_config in apps.get_app_configs():
        app_config.models_module = True
        create_permissions(app_config, apps=apps, verbosity=0)
        app_config.models_module = None

    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    group, _ = Group.objects.get_or_create(name=GROUP)
    for app_label, codename in PERMISSIONS:
        group.permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))


def remove_group(apps, schema_editor):
    apps.get_model('auth', 'Group').objects.filter(name=GROUP).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0005_profile_avatar'),
        ('admin', '0003_logentry_add_action_flag_choices'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('events', '0005_event_end_time'),
        ('parks', '0002_park_reviews'),
    ]

    operations = [migrations.RunPython(create_group, remove_group)]
