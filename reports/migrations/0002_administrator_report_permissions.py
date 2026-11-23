from django.db import migrations

CODENAMES = ('view_report', 'change_report')


def report_permissions(apps, alias):
    Permission = apps.get_model('auth', 'Permission')
    return Permission.objects.using(alias).filter(
        content_type__app_label='reports', content_type__model='report', codename__in=CODENAMES
    )


def grant_permissions(apps, schema_editor):
    alias = schema_editor.connection.alias
    content_type, _ = apps.get_model('contenttypes', 'ContentType').objects.using(alias).get_or_create(
        app_label='reports', model='report'
    )
    permissions = apps.get_model('auth', 'Permission').objects.using(alias)
    for action in ('view', 'change'):
        permissions.get_or_create(
            content_type=content_type, codename=f'{action}_report', defaults={'name': f'Can {action} report'}
        )
    group = apps.get_model('auth', 'Group').objects.using(alias).get(name='Administrators')
    group.permissions.add(*report_permissions(apps, alias))


def revoke_permissions(apps, schema_editor):
    alias = schema_editor.connection.alias
    group = apps.get_model('auth', 'Group').objects.using(alias).get(name='Administrators')
    group.permissions.remove(*report_permissions(apps, alias))


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0006_administrators_group'),
        ('reports', '0001_initial'),
    ]

    operations = [migrations.RunPython(grant_permissions, revoke_permissions)]
