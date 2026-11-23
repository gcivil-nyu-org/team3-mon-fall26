from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.db import models


class Report(models.Model):
    class Reason(models.TextChoices):
        SPAM = 'spam', 'Spam'
        HARASSMENT = 'harassment', 'Harassment or abuse'
        INACCURATE = 'inaccurate', 'Inaccurate information'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        RESOLVED = 'resolved', 'Resolved'

    class Outcome(models.TextChoices):
        NO_ACTION = 'no_action', 'No action'
        CONTENT_REMOVED = 'content_removed', 'Content removed'
        EVENT_REMOVED = 'event_removed', 'Event removed'
        ACCOUNT_SUSPENDED = 'account_suspended', 'Account suspended'

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='submitted_reports'
    )
    content_type = models.ForeignKey('contenttypes.ContentType', on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey()
    target_snapshot = models.TextField()
    reason = models.CharField(max_length=20, choices=Reason.choices)
    details = models.TextField(max_length=2000, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_reports'
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['reporter', 'content_type', 'object_id'],
                condition=models.Q(status='open'),
                name='reports_one_open_per_reporter_target',
            ),
        ]

    def __str__(self):
        return f'Report #{self.pk}'
