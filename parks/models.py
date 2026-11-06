from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def comma_token_q(field_name, token):
    """Match `token` as a whole item of a comma-separated text field.

    Works on SQLite and PostgreSQL. Handles both separators in our data:
    Park.zipcode ("10029, 10128") and Facility.sports ("adult_softball,ll_softball").
    Used by the zip code (#18) and facility (#19) filters.
    """
    query = models.Q(**{field_name: token}) | models.Q(**{f'{field_name}__startswith': f'{token},'})
    for separator in (',', ', '):
        query |= models.Q(**{f'{field_name}__endswith': f'{separator}{token}'})
        query |= models.Q(**{f'{field_name}__contains': f'{separator}{token},'})
    return query


class Borough(models.TextChoices):
    MANHATTAN = 'M', 'Manhattan'
    BROOKLYN = 'B', 'Brooklyn'
    QUEENS = 'Q', 'Queens'
    BRONX = 'X', 'Bronx'
    STATEN_ISLAND = 'R', 'Staten Island'


class ParkQuerySet(models.QuerySet):
    def with_review_summary(self):
        """Live ratings shared by park pages, cards and panels; never cache a stored average."""
        return self.annotate(
            avg_rating=models.Avg('reviews__rating'),
            review_count=models.Count('reviews', distinct=True),
        )


class Park(models.Model):
    """NYC Parks property seeded from NYC Open Data (enfh-gkve)."""

    gispropnum = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        help_text='Unique NYC Parks GIS property number (e.g. M110, B237).',
    )
    name = models.CharField(max_length=255, db_index=True)
    borough = models.CharField(max_length=2, choices=Borough.choices, db_index=True)
    address = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    zipcode = models.CharField(max_length=32, blank=True)
    acres = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    park_type = models.CharField(max_length=100, blank=True)
    subcategory = models.CharField(max_length=100, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    waterfront = models.BooleanField(default=False)
    is_retired = models.BooleanField(default=False)
    admin_modified = models.BooleanField(
        default=False,
        help_text='When True, automated NYC Open Data syncs will not overwrite this park.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    objects = ParkQuerySet.as_manager()

    class Meta:
        ordering = ['name', 'gispropnum']

    def __str__(self):
        borough_label = self.get_borough_display()
        return f'{self.name} ({borough_label})' if borough_label else self.name


class Review(models.Model):
    """One public park review per member (US-6.2)."""

    park = models.ForeignKey(Park, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='park_reviews')
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['user', 'park'], name='review_one_per_user_per_park'),
            models.CheckConstraint(condition=models.Q(rating__gte=1, rating__lte=5), name='review_rating_1_to_5'),
        ]

    def __str__(self):
        return f'{self.user} on {self.park}: {self.rating}/5'


class Facility(models.Model):
    """Individual athletic court or field inside a park, seeded from NYC Open Data (qnem-b8re)."""

    SPORT_FIELDS = (
        ('basketball', 'Basketball'),
        ('tennis', 'Tennis'),
        ('pickleball', 'Pickleball'),
        ('volleyball', 'Volleyball'),
        ('handball', 'Handball'),
        ('regulation_soccer', 'Regulation Soccer'),
        ('nonregulation_soccer', 'Non-Regulation Soccer'),
        ('adult_baseball', 'Adult Baseball'),
        ('adult_softball', 'Adult Softball'),
        ('ll_baseb_12andunder', 'Little League Baseball (12 & Under)'),
        ('ll_baseb_13andolder', 'Little League Baseball (13 & Older)'),
        ('ll_softball', 'Little League Softball'),
        ('t_ball', 'T-Ball'),
        ('adult_football', 'Adult Football'),
        ('youth_football', 'Youth Football'),
        ('flagfootball', 'Flag Football'),
        ('wheelchairfootball', 'Wheelchair Football'),
        ('track_and_field', 'Track & Field'),
        ('hockey', 'Hockey'),
        ('cricket', 'Cricket'),
        ('rugby', 'Rugby'),
        ('bocce', 'Bocce'),
        ('frisbee', 'Frisbee'),
        ('kickball', 'Kickball'),
        ('lacrosse', 'Lacrosse'),
        ('netball', 'Netball'),
    )

    # NYC Open Data primary_sport codes, for grouping facilities on the park page (US-2.6).
    PRIMARY_SPORT_LABELS = {
        'BKB': 'Basketball',
        'BOC': 'Bocce',
        'BSB': 'Baseball',
        'CRK': 'Cricket',
        'FFB': 'Flag Football',
        'FTB': 'Football',
        'HDB': 'Handball',
        'HKY': 'Hockey',
        'MPPA': 'Multi-Purpose Play Area',
        'NTB': 'Netball',
        'PKB': 'Pickleball',
        'RBY': 'Rugby',
        'SCR': 'Soccer',
        'SFB': 'Softball',
        'TNS': 'Tennis',
        'TRK': 'Track & Field',
        'VLB': 'Volleyball',
    }

    system_id = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text='Unique NYC Parks facility system identifier (e.g. B237-HANDBALL-2).',
    )
    park = models.ForeignKey(
        Park,
        on_delete=models.CASCADE,
        related_name='facilities',
    )
    primary_sport = models.CharField(max_length=64, blank=True, db_index=True)
    sports = models.CharField(
        max_length=500,
        blank=True,
        help_text='Comma-separated list of active sport keys supported by this facility.',
    )
    surface_type = models.CharField(max_length=100, blank=True)
    dimensions = models.CharField(max_length=100, blank=True)
    field_number = models.CharField(max_length=32, blank=True)
    feature_status = models.CharField(max_length=32, default='Active', db_index=True)
    is_lighted = models.BooleanField(default=False)
    is_accessible = models.BooleanField(default=False)
    admin_modified = models.BooleanField(
        default=False,
        help_text='When True, automated NYC Open Data syncs will not overwrite this facility.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['park', 'primary_sport', 'field_number', 'system_id']
        verbose_name_plural = 'facilities'

    def sport_list(self):
        return [s for s in self.sports.split(',') if s]

    def primary_sport_label(self):
        return self.PRIMARY_SPORT_LABELS.get(self.primary_sport, 'Other')

    def __str__(self):
        sport = self.primary_sport or 'Facility'
        return f'{self.park.name} - {sport} ({self.system_id})'
