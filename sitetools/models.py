"""Site-wide banner announcements (US-7.7 / #50)."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import Truncator

BANNER_MESSAGE_MAX_LENGTH = 280


class SiteBannerQuerySet(models.QuerySet):
    def showing(self, now=None):
        """Active banners whose end date, if they have one, hasn't passed yet."""
        now = now or timezone.now()
        return self.filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now), is_active=True)

    def newest_first(self):
        return self.order_by('-created_at', '-pk')

    def current(self, now=None):
        """The banner every page shows: the newest one showing, or None."""
        return self.showing(now).newest_first().first()


class SiteBanner(models.Model):
    message = models.CharField(max_length=BANNER_MESSAGE_MAX_LENGTH)
    is_active = models.BooleanField('active', default=True, help_text='Untick to take the banner down.')
    ends_at = models.DateTimeField(
        'end date', null=True, blank=True, help_text='Optional. The banner stops showing at this time.'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name='site_banners',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SiteBannerQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return Truncator(self.message).chars(60)
