import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

CAPACITY_ERROR_MESSAGE = 'Participant limit must be at least 1 or left blank for unlimited.'

# Event date/time are entered as New York local time (see EventCreateForm.clean).
NYC_TZ = ZoneInfo('America/New_York')


class SkillLevel(models.TextChoices):
    ALL = 'all', 'All skill levels'
    BEGINNER = 'beginner', 'Beginner / Casual'
    INTERMEDIATE = 'intermediate', 'Intermediate'
    COMPETITIVE = 'competitive', 'Competitive'


class Activity(models.TextChoices):
    """Standard sports for events (#75). Labels match the profile sports list (accounts.forms.COMMON_SPORTS)."""

    BASKETBALL = 'basketball', 'Basketball'
    SOCCER = 'soccer', 'Soccer'
    RUNNING = 'running', 'Running'
    TENNIS = 'tennis', 'Tennis'
    PICKLEBALL = 'pickleball', 'Pickleball'
    VOLLEYBALL = 'volleyball', 'Volleyball'
    HANDBALL = 'handball', 'Handball'
    BASEBALL = 'baseball', 'Baseball'
    SOFTBALL = 'softball', 'Softball'
    FOOTBALL = 'football', 'Football'
    CRICKET = 'cricket', 'Cricket'
    CYCLING = 'cycling', 'Cycling'
    SWIMMING = 'swimming', 'Swimming'
    YOGA = 'yoga', 'Yoga'
    ULTIMATE_FRISBEE = 'ultimate_frisbee', 'Ultimate frisbee'
    OTHER = 'other', 'Other'


# Park facility types (parks.Facility.SPORT_FIELDS keys) that can host each activity, for the
# Sport filter (#19). Activities missing here don't need a dedicated facility.
ACTIVITY_FACILITY_KEYS = {
    Activity.BASKETBALL: ('basketball',),
    Activity.SOCCER: ('regulation_soccer', 'nonregulation_soccer'),
    Activity.TENNIS: ('tennis',),
    Activity.PICKLEBALL: ('pickleball',),
    Activity.VOLLEYBALL: ('volleyball',),
    Activity.HANDBALL: ('handball',),
    Activity.BASEBALL: ('adult_baseball', 'll_baseb_12andunder', 'll_baseb_13andolder', 't_ball'),
    Activity.SOFTBALL: ('adult_softball', 'll_softball'),
    Activity.FOOTBALL: ('adult_football', 'youth_football', 'flagfootball', 'wheelchairfootball'),
    Activity.CRICKET: ('cricket',),
    Activity.ULTIMATE_FRISBEE: ('frisbee',),
}


class EventStatus(models.TextChoices):
    SCHEDULED = 'scheduled', 'Scheduled'
    CANCELLED = 'cancelled', 'Cancelled'
    REMOVED = 'removed', 'Removed by a moderator'


class RSVPStatus(models.TextChoices):
    ACTIVE = 'active', 'Active'
    CANCELLED = 'cancelled', 'Cancelled'
    REMOVED = 'removed', 'Removed by the organizer'


class EventQuerySet(models.QuerySet):
    def scheduled(self):
        """Events that have not been cancelled or removed."""
        return self.filter(status=EventStatus.SCHEDULED)

    def upcoming(self, now=None):
        """Scheduled events starting now or later (New York time), soonest first."""
        now = (now or timezone.now()).astimezone(NYC_TZ)
        today = now.date()
        return (
            self.scheduled()
            .filter(models.Q(date__gt=today) | models.Q(date=today, time__gte=now.time()))
            .order_by('date', 'time', 'id')
        )

    def with_confirmed_players(self):
        """Annotate `confirmed_players`: active RSVPs plus the host when playing (US-3.10)."""
        return self.annotate(
            confirmed_players=models.Count('rsvps', filter=models.Q(rsvps__status=RSVPStatus.ACTIVE))
            + models.Case(
                models.When(host_is_playing=True, then=models.Value(1)),
                default=models.Value(0),
                output_field=models.IntegerField(),
            )
        )


class Event(models.Model):
    """Public recreational meetup tied to an NYC park (#22, #24)."""

    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='organized_events',
    )
    park = models.ForeignKey(
        'parks.Park',
        on_delete=models.PROTECT,
        related_name='events',
    )
    # db_default keeps the database filling these in, so the previous release still works
    # if a failed deploy rolls the code back (migrations aren't reversed; see docs/deployment.md).
    title = models.CharField(max_length=120, default='', db_default='')
    meeting_spot = models.CharField(max_length=200, blank=True, default='', db_default='')
    activity = models.CharField(max_length=100, choices=Activity.choices, db_index=True)
    date = models.DateField(db_index=True)
    time = models.TimeField()
    skill_level = models.CharField(
        max_length=32,
        choices=SkillLevel.choices,
        default=SkillLevel.ALL,
    )
    capacity = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1, message=CAPACITY_ERROR_MESSAGE)],
        help_text='Optional maximum number of participants. Leave blank for unlimited.',
    )
    # The host never gets an RSVP row; this flag decides whether they take a spot.
    host_is_playing = models.BooleanField(default=True, db_default=True)
    description = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=EventStatus.choices,
        default=EventStatus.SCHEDULED,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EventQuerySet.as_manager()

    class Meta:
        ordering = ['date', 'time', 'id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(capacity__isnull=True) | models.Q(capacity__gte=1),
                name='event_capacity_at_least_1',
            ),
        ]

    @property
    def owner(self):
        return self.organizer

    @property
    def starts_at(self):
        """Start as an aware datetime in New York time."""
        return datetime.datetime.combine(self.date, self.time, tzinfo=NYC_TZ)

    @property
    def is_past(self):
        return self.starts_at < timezone.now()

    @property
    def is_unlimited(self):
        return self.capacity is None

    @property
    def display_title(self):
        """The event title, or its sport for events created before titles existed."""
        return self.title or self.get_activity_display()

    def get_absolute_url(self):
        return reverse('event_detail', kwargs={'pk': self.pk})

    def __str__(self):
        return f'{self.display_title} at {self.park.name} ({self.date})'


class RSVP(models.Model):
    """A participant's reservation for an event (#30). Rows are never deleted, only re-statused."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='rsvps')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='rsvps')
    status = models.CharField(
        max_length=20,
        choices=RSVPStatus.choices,
        default=RSVPStatus.ACTIVE,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'RSVP'
        ordering = ['created_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['event', 'user'], name='rsvp_one_per_user_per_event'),
        ]

    def __str__(self):
        return f'{self.user} for {self.event} ({self.status})'

