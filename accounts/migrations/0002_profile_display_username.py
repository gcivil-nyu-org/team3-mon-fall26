from django.db import migrations, models


def populate_display_usernames(apps, schema_editor):
    Profile = apps.get_model('accounts', 'Profile')
    for profile in Profile.objects.select_related('user').iterator():
        profile.display_username = profile.user.username
        profile.save(update_fields=['display_username'])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='display_username',
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.RunPython(populate_display_usernames, migrations.RunPython.noop),
    ]
