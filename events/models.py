import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

CAPACITY_ERROR_MESSAGE = 'Participant limit must be at least 1 or left blank for unlimited.'
MESSAGE_MAX_LENGTH = 2000

# Event date/time are entered as New York local time (see EventCreateForm.clean).
NYC_TZ = ZoneInfo('America/New_York')

# US-3.11 (#116): games without an end time end 2 hours after they start, but no later than 11:59 PM.
DEFAULT_GAME_LENGTH = datetime.timedelta(hours=2)
LATEST_END_TIME = datetime.time(23, 59)


def default_end_time(start):
    """The end time for an event saved without one (older events, or a row written during a rollback)."""
    end = datetime.datetime.combine(datetime.date.min, start) + DEFAULT_GAME_LENGTH
    return end.time() if end.date() == datetime.date.min else LATEST_END_TIME


def clock(value):
    """'6:00' for 6 PM, matching the `g:i` date filter."""
    return f'{value.hour % 12 or 12}:{value.minute:02d}'


def meridiem(value):
    return 'AM' if value.hour < 12 else 'PM'


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


class EventPhase(models.TextChoices):
    """Where a scheduled game is in its day (US-4.8). Cancelled and removed games are in no phase."""

    UPCOMING = 'upcoming', 'Upcoming'
    LIVE = 'live', 'Live'
    ENDED = 'ended', 'Ended'


# Explore shows upcoming and live games unless ?phase= asks for others (US-4.8, #117).
DEFAULT_PHASES = (EventPhase.UPCOMING, EventPhase.LIVE)
# Public Explore keeps ended games for this many days, counted back from today's New York date.
ENDED_WINDOW_DAYS = 30


def phase_q(phase, now=None):
    """A filter for events in `phase` at `now` (New York time), by time only; callers add the status check.

    Games end on the day they start (#116), so a game is live from its start until its end time today.
    """
    now = (now or timezone.now()).astimezone(NYC_TZ)
    today, clock = now.date(), now.time()
    if phase == EventPhase.UPCOMING:
        return models.Q(date__gt=today) | models.Q(date=today, time__gte=clock)
    if phase == EventPhase.LIVE:
        return models.Q(date=today, time__lt=clock, end_time__gt=clock)
    return models.Q(date__lt=today) | models.Q(date=today, end_time__lte=clock)


def phase_ordering(now=None):
    """Live first, then upcoming soonest first, then ended most recent first. Done in SQL so pages stay right."""
    started = ~phase_q(EventPhase.UPCOMING, now)
    not_ended = ~phase_q(EventPhase.ENDED, now)
    rank = models.Case(
        models.When(started & not_ended, then=models.Value(0)),
        models.When(not_ended, then=models.Value(1)),
        default=models.Value(2),
        output_field=models.IntegerField(),
    )
    soonest = [
        models.Case(models.When(not_ended, then=models.F(field)), output_field=output()).asc()
        for field, output in (('date', models.DateField), ('time', models.TimeField))
    ]
    # Ended games have no "soonest" keys, so they fall through to most recent first.
    return [rank.asc(), *soonest, models.F('date').desc(), models.F('time').desc(), 'pk']


class EventQuerySet(models.QuerySet):
    def scheduled(self):
        """Events that have not been cancelled or removed."""
        return self.filter(status=EventStatus.SCHEDULED)

    def upcoming(self, now=None):
        """Scheduled events starting now or later (New York time), soonest first."""
        return self.scheduled().filter(phase_q(EventPhase.UPCOMING, now)).order_by('date', 'time', 'id')

    def live(self, now=None):
        """Scheduled events that have started and haven't ended yet (US-4.8)."""
        return self.scheduled().filter(phase_q(EventPhase.LIVE, now)).order_by('date', 'time', 'id')

    def ended(self, now=None):
        """Scheduled events whose end time has passed, most recent first (US-4.8)."""
        return self.scheduled().filter(phase_q(EventPhase.ENDED, now)).order_by('-date', '-time', '-id')

    def in_phases(self, phases, now=None):
        """Scheduled events in any of `phases`, live first, then upcoming, then ended (US-4.8)."""
        if not phases:
            return self.none()
        membership = models.Q()
        for phase in phases:
            membership |= phase_q(phase, now)
        return self.scheduled().filter(membership).by_phase(now)

    def by_phase(self, now=None):
        return self.order_by(*phase_ordering(now))

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
    # US-3.11 (#116). Nullable so the previous release can still save events after a rollback; the form
    # requires it, and migration 0005 filled in older events. Read it through `effective_end_time`.
    end_time = models.TimeField(null=True, blank=True)
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
    def effective_end_time(self):
        """The end time, or start + 2 hours (capped at 11:59 PM) for a row saved without one."""
        return self.end_time or default_end_time(self.time)

    @property
    def ends_at(self):
        """End as an aware datetime in New York time. Games end on the day they start."""
        return datetime.datetime.combine(self.date, self.effective_end_time, tzinfo=NYC_TZ)

    @property
    def time_range(self):
        """'6:00–8:00 PM', or '11:00 AM–1:00 PM' when the game crosses noon."""
        start, end = self.time, self.effective_end_time
        if meridiem(start) == meridiem(end):
            return f'{clock(start)}–{clock(end)} {meridiem(end)}'
        return f'{clock(start)} {meridiem(start)}–{clock(end)} {meridiem(end)}'

    @property
    def has_started(self):
        """The start time has passed, so the game is live or over (renamed from is_past for US-4.8, #117)."""
        return self.starts_at < timezone.now()

    @property
    def has_ended(self):
        """The end time has passed (US-4.8)."""
        return self.ends_at <= timezone.now()

    @property
    def discussion_open(self):
        """Posting is open until the game ends, and closed for cancelled and removed games (#35, US-4.8)."""
        return self.status == EventStatus.SCHEDULED and not self.has_ended

    @property
    def phase(self):
        """'upcoming', 'live' or 'ended'; None for cancelled and removed games, which are in no phase."""
        if self.status != EventStatus.SCHEDULED:
            return None
        if self.has_ended:
            return EventPhase.ENDED
        return EventPhase.LIVE if self.has_started else EventPhase.UPCOMING

    @property
    def is_unlimited(self):
        return self.capacity is None

    @property
    def display_title(self):
        """The event title, or its sport for events created before titles existed."""
        return self.title or self.get_activity_display()

    def count_confirmed_players(self):
        """Active RSVPs plus the host when playing, counted fresh (US-3.10).

        Under the event lock (#31) this is the number to check capacity against; lists and cards use
        the `with_confirmed_players()` annotation instead.
        """
        return self.rsvps.filter(status=RSVPStatus.ACTIVE).count() + int(self.host_is_playing)

    def can_be_managed_by(self, user):
        """Only the organizer or an administrator may edit, cancel, or remove attendees (#27)."""
        return user.is_authenticated and (user.pk == self.organizer_id or user.is_staff)

    def get_absolute_url(self):
        return reverse('event_detail', kwargs={'pk': self.pk})

    def save(self, *args, **kwargs):
        # US-3.11 (#116): code that saves an event without an end time (the admin, seed data, tests) gets the
        # same start + 2 hours default as older events, so phase queries (#117) can rely on the column.
        if self.end_time is None and self.time is not None:
            self.end_time = default_end_time(self.time)
            update_fields = kwargs.get('update_fields')
            if update_fields is not None and 'end_time' not in update_fields:
                kwargs['update_fields'] = [*update_fields, 'end_time']
        super().save(*args, **kwargs)

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


class Message(models.Model):
    """A public message or organizer announcement on an event discussion."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='event_messages')
    body = models.TextField(max_length=MESSAGE_MAX_LENGTH)
    is_announcement = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at', 'pk']

    def __str__(self):
        return f'{self.author} on {self.event}: {self.body[:40]}'
