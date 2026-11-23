from django.db import migrations

# US-7.7 (#50): administrators publish and take down banners. Matches ADMINISTRATOR_PERMISSIONS.
ACTIONS = ('view', 'add', 'change')
CODENAMES = tuple(f'{action}_sitebanner' for action in ACTIONS)


def banner_permissions(apps, alias):
    Permission = apps.get_model('auth', 'Permission')
    return Permission.objects.using(alias).filter(
        content_type__app_label='sitetools', content_type__model='sitebanner', codename__in=CODENAMES
    )


def grant_permissions(apps, schema_editor):
    alias = schema_editor.connection.alias
    content_type, _ = (
        apps.get_model('contenttypes', 'ContentType')
        .objects.using(alias)
        .get_or_create(app_label='sitetools', model='sitebanner')
    )
    permissions = apps.get_model('auth', 'Permission').objects.using(alias)
    for action in ACTIONS:
        permissions.get_or_create(
            content_type=content_type, codename=f'{action}_sitebanner', defaults={'name': f'Can {action} site banner'}
        )
    group = apps.get_model('auth', 'Group').objects.using(alias).get(name='Administrators')
    group.permissions.add(*banner_permissions(apps, alias))


def revoke_permissions(apps, schema_editor):
    alias = schema_editor.connection.alias
    group = apps.get_model('auth', 'Group').objects.using(alias).get(name='Administrators')
    group.permissions.remove(*banner_permissions(apps, alias))


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0006_administrators_group'),
        ('sitetools', '0001_initial'),
    ]

    operations = [migrations.RunPython(grant_permissions, revoke_permissions)]
