from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Profile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('home_borough', models.CharField(blank=True, choices=[('M', 'Manhattan'), ('B', 'Brooklyn'), ('Q', 'Queens'), ('X', 'Bronx'), ('R', 'Staten Island')], max_length=1)),
                ('favorite_sports', models.JSONField(blank=True, default=list)),
                ('skill_level', models.CharField(blank=True, choices=[('beginner', 'Beginner / Casual'), ('intermediate', 'Intermediate'), ('competitive', 'Competitive')], max_length=16)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='profile', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
