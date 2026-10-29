from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower

from parks.models import Borough


def avatar_upload_to(instance, filename):
    # Random names so uploads never expose the original filename.
    return f'avatars/{uuid4().hex}.jpg'


class ProfileSkillLevel(models.TextChoices):
    BEGINNER = 'beginner', 'Beginner / Casual'
    INTERMEDIATE = 'intermediate', 'Intermediate'
    COMPETITIVE = 'competitive', 'Competitive'


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    display_username = models.CharField(max_length=150, blank=True)
    avatar = models.ImageField(upload_to=avatar_upload_to, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    home_borough = models.CharField(max_length=1, choices=Borough.choices, blank=True)
    favorite_sports = models.JSONField(default=list, blank=True)
    skill_level = models.CharField(max_length=16, choices=ProfileSkillLevel.choices, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower('display_username'),
                condition=~Q(display_username=''),
                name='profile_display_username_ci_unique',
            ),
        ]

    def __str__(self):
        return f'{self.user.username} profile'
