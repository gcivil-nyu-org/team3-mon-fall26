from django.db import migrations, models
from django.db.models import Q
from django.db.models.functions import Lower


def populate_unique_display_usernames(apps, schema_editor):
    Profile = apps.get_model('accounts', 'Profile')
    used_usernames = set()

    for profile in Profile.objects.select_related('user').order_by('pk').iterator():
        base_username = (profile.display_username or profile.user.username).strip()
        candidate = base_username
        suffix_number = 2
        while candidate.casefold() in used_usernames:
            suffix = f'-{suffix_number}'
            candidate = f'{base_username[:150 - len(suffix)]}{suffix}'
            suffix_number += 1

        if profile.display_username != candidate:
            profile.display_username = candidate
            profile.save(update_fields=['display_username'])
        used_usernames.add(candidate.casefold())


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_profile_display_username'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='bio',
            field=models.TextField(blank=True, max_length=500),
        ),
        migrations.RunPython(populate_unique_display_usernames, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='profile',
            constraint=models.UniqueConstraint(
                Lower('display_username'),
                condition=~Q(display_username=''),
                name='profile_display_username_ci_unique',
            ),
        ),
    ]
