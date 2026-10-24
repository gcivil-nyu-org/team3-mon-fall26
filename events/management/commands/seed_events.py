"""Create sample events at real NYC parks for local development.

Requires parks to be loaded first (python manage.py sync_parks).

Usage:
    python manage.py seed_events            # add sample events
    python manage.py seed_events --clear    # remove previous sample events first

Only events owned by the 'sample_organizer' user are ever removed.
"""
import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from events.models import NYC_TZ, Activity, Event, SkillLevel
from parks.models import Borough, Park

SAMPLE_ORGANIZER = 'sample_organizer'

# (title, activity, preferred park name, borough, days from today, start time, skill, capacity or None,
#  meeting spot, description)
SAMPLE_EVENTS = [
    ('Pickup Soccer', Activity.SOCCER, 'Prospect Park', Borough.BROOKLYN, 1, '10:00', SkillLevel.ALL, 22,
     'Long Meadow, north end', 'Casual 11v11 on the Long Meadow. Bring a light and a dark shirt.'),
    ('Reservoir Loop Run', Activity.RUNNING, 'Central Park', Borough.MANHATTAN, 1, '07:30', SkillLevel.ALL, None,
     'Reservoir entrance at East 90th St',
     'One lap of the reservoir loop at conversational pace. Optional second lap.'),
    ('Half-Court Basketball', Activity.BASKETBALL, 'Washington Square Park', Borough.MANHATTAN, 2, '14:00',
     SkillLevel.COMPETITIVE, 15, 'Basketball courts by the arch', 'Competitive half-court runs, winners stay on.'),
    ('Ultimate Frisbee Scrimmage', Activity.ULTIMATE_FRISBEE, 'Flushing Meadows', Borough.QUEENS, 3, '16:00',
     SkillLevel.INTERMEDIATE, 20, 'Field 7 near the Unisphere',
     'Co-ed pickup ultimate. Discs and cones provided. Cleats recommended.'),
    ('Beginner Tennis Doubles', Activity.TENNIS, 'Riverside Park', Borough.MANHATTAN, 4, '09:00',
     SkillLevel.BEGINNER, 8, 'Courts at West 96th St', 'Friendly doubles rotation for newer players.'),
    ('Trail Walk and Hike', Activity.OTHER, 'Van Cortlandt Park', Borough.BRONX, 5, '09:30', SkillLevel.ALL, None,
     'Nature Center parking lot', 'Easy-to-moderate 5-mile loop through the woods.'),
    ('Volleyball Social', Activity.VOLLEYBALL, 'Astoria Park', Borough.QUEENS, 6, '13:00', SkillLevel.ALL, 16,
     'Sand courts by the pool', 'Rotating 4v4 games, all levels welcome.'),
    ('Greenbelt Hike', Activity.OTHER, 'Greenbelt', Borough.STATEN_ISLAND, 7, '10:00', SkillLevel.INTERMEDIATE, 20,
     'Greenbelt Nature Center', 'Wooded trails and a climb up Moses Mountain. Wear sturdy shoes.'),
    ('After-Work Basketball', Activity.BASKETBALL, 'McCarren Park', Borough.BROOKLYN, 8, '18:30',
     SkillLevel.INTERMEDIATE, 12, 'Courts on Driggs Ave', 'Full-court 5v5 after work. Mixed skill levels.'),
    ('Morning Tennis Drills', Activity.TENNIS, 'Fort Greene Park', Borough.BROOKLYN, 10, '07:00',
     SkillLevel.INTERMEDIATE, 6, 'Court 3', 'Serves, volleys and baseline rallies.'),
    ('Pickup Soccer 7v7', Activity.SOCCER, 'Pelham Bay Park', Borough.BRONX, 12, '11:00', SkillLevel.ALL, 14,
     'Turf field behind the stadium', 'Small-sided games. Shin guards recommended.'),
    ('Sunrise Yoga', Activity.YOGA, 'Brooklyn Bridge Park', Borough.BROOKLYN, 14, '07:00', SkillLevel.BEGINNER, None,
     'Pier 1 lawn', 'All-levels outdoor flow. Bring your own mat.'),
    ('Pickleball Round Robin', Activity.PICKLEBALL, 'Clove Lakes Park', Borough.STATEN_ISLAND, 16, '17:00',
     SkillLevel.BEGINNER, 12, 'Courts near the boathouse', 'Paddles available to borrow.'),
    # Past events: hidden by default, shown with "Include past events".
    ('Last Weekend Pickup Soccer', Activity.SOCCER, 'Prospect Park', Borough.BROOKLYN, -6, '10:00', SkillLevel.ALL,
     22, 'Long Meadow, north end', 'Already happened. Shows up only with "Include past events".'),
    ('Earlier Reservoir Run', Activity.RUNNING, 'Central Park', Borough.MANHATTAN, -3, '07:30', SkillLevel.ALL, None,
     'Reservoir entrance at East 90th St', 'Already happened. Shows up only with "Include past events".'),
]


def pick_park(name, borough):
    parks = Park.objects.filter(is_retired=False)
    return (
        parks.filter(name__icontains=name, borough=borough).order_by('-acres').first()
        or parks.filter(borough=borough).order_by('-acres').first()
        or parks.order_by('-acres').first()
    )


class Command(BaseCommand):
    help = 'Create sample events at real NYC parks for local development.'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Remove previous sample events first.')

    @transaction.atomic
    def handle(self, *args, **options):
        if not Park.objects.filter(is_retired=False).exists():
            raise CommandError('No parks found. Load them first with: python manage.py sync_parks')

        User = get_user_model()
        organizer, created = User.objects.get_or_create(username=SAMPLE_ORGANIZER)
        if created:
            organizer.set_unusable_password()
            organizer.save()

        if options['clear']:
            deleted, _ = Event.objects.filter(organizer=organizer).delete()
            self.stdout.write(f'Removed {deleted} previous sample events.')

        today = timezone.now().astimezone(NYC_TZ).date()
        for (title, activity, park_name, borough, days, start, skill, capacity,
             meeting_spot, description) in SAMPLE_EVENTS:
            Event.objects.create(
                organizer=organizer,
                park=pick_park(park_name, borough),
                title=title,
                activity=activity,
                meeting_spot=meeting_spot,
                date=today + datetime.timedelta(days=days),
                time=datetime.time.fromisoformat(start),
                skill_level=skill,
                capacity=capacity,
                description=description,
            )

        past = sum(1 for row in SAMPLE_EVENTS if row[4] < 0)
        self.stdout.write(self.style.SUCCESS(f'Created {len(SAMPLE_EVENTS)} sample events ({past} in the past).'))
