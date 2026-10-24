"""Shared Explore Events filters for the map, both lists and park previews.

Filter stories extend this module and includes/explore_filters.html rather than
implementing a separate queryset for each presentation (US-2.1 / US-2.2).
"""
import re
from functools import reduce
from operator import or_

from django import forms
from django.db.models import Count, Exists, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import QueryDict
from django.utils import timezone

from parks.models import Borough, Facility, Park, comma_token_q

from .models import ACTIVITY_FACILITY_KEYS, Activity, Event, NYC_TZ, RSVP, RSVPStatus, SkillLevel


class ValidChoices(forms.MultipleChoiceField):
    """Ignore unknown URL values without throwing away the valid selections."""

    def clean(self, value):
        allowed = {str(key) for key, _ in self.choices}
        return list(dict.fromkeys(item for item in (value or []) if item in allowed))


class ZipCodes(forms.Field):
    widget = forms.SelectMultiple

    def clean(self, value):
        return list(dict.fromkeys(item for item in (value or []) if re.fullmatch(r'[0-9]{5}', item)))


class ExploreFilterForm(forms.Form):
    q = forms.CharField(required=False, label='Search', widget=forms.SearchInput(
        attrs={'placeholder': 'Park, address or event', 'maxlength': 100}))
    borough = forms.ChoiceField(required=False, choices=[('', 'All boroughs'), *Borough.choices])
    zip = ZipCodes(required=False, label='Zip codes')
    sport = ValidChoices(required=False, choices=Activity.choices, label='Sports')
    facility = ValidChoices(required=False, choices=Facility.SPORT_FIELDS, label='Facilities')
    when = forms.ChoiceField(required=False, choices=[('', 'Any date'), ('today', 'Today')], label='Date')
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='From')
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={'type': 'date'}), label='To')
    skill = forms.ChoiceField(required=False, choices=[('', 'Any skill level'), *SkillLevel.choices])
    scope = forms.ChoiceField(required=False, choices=[('all', 'All events'), ('mine', 'All mine'),
        ('attending', 'Attending'), ('hosting', 'Hosting')])
    events_only = forms.BooleanField(required=False, label='Only parks with events')
    park = forms.IntegerField(required=False, min_value=1)
    view = forms.ChoiceField(required=False, choices=[('map', 'Map'), ('list', 'List')])
    tab = forms.ChoiceField(required=False, choices=[('events', 'Events'), ('parks', 'Parks')])

    def clean_q(self):
        return self.cleaned_data['q'].strip()[:100]

    def clean_events_only(self):
        return self.data.get('events_only', '') in ('1', 'true', 'on')

    def clean_park(self):
        park = self.cleaned_data.get('park')
        if park and not Park.objects.filter(pk=park, is_retired=False).exists():
            return None
        return park

    def clean(self):
        data = super().clean()
        start, end = data.get('date_from'), data.get('date_to')
        if start and end and start > end:
            data.pop('date_from', None)
            data.pop('date_to', None)
            raise forms.ValidationError('The From date must be on or before the To date.')
        return data


def parse_filters(params):
    form = ExploreFilterForm(params)
    form.is_valid()
    filters = {name: form.cleaned_data.get(name) for name in form.fields}
    for name in ('zip', 'sport', 'facility'):
        filters[name] = filters.get(name) or []
    filters['view'] = filters.get('view') or 'map'
    filters['tab'] = filters.get('tab') or 'events'
    filters['scope'] = filters.get('scope') or 'all'
    filters['events_only'] = bool(filters.get('events_only') or filters['scope'] != 'all')
    # A malformed range must never accidentally become an open-ended range.
    if 'date_from' in form.errors or 'date_to' in form.errors:
        filters['date_from'] = filters['date_to'] = None
    return form, filters


def filter_params(filters):
    """Canonical, validated, multi-value URL state. Page numbers are presentation-only."""
    params = QueryDict(mutable=True)
    for name in ExploreFilterForm.base_fields:
        value = filters.get(name)
        if isinstance(value, list):
            params.setlist(name, value)
        elif value:
            params[name] = '1' if value is True else str(value)
    return params


def configure_filter_form(form, filters):
    """Basic controls; filter owners can replace widgets without changing URL state."""
    zipcodes = {z.strip() for field in Park.objects.filter(is_retired=False).values_list('zipcode', flat=True)
                for z in field.split(',') if re.fullmatch(r'[0-9]{5}', z.strip())}
    zipcodes.update(filters['zip'])
    form.fields['zip'].widget.choices = [(z, z) for z in sorted(zipcodes)]
    if filters['scope'] != 'all':
        form.data = form.data.copy()
        form.data['events_only'] = '1'
        form.fields['events_only'].widget.attrs['disabled'] = True


def filter_pills(filters):
    params = filter_params(filters)
    pills = []
    for name in ('q', 'borough', 'zip', 'sport', 'facility', 'when', 'date_from', 'date_to', 'skill', 'park'):
        values = filters.get(name)
        if not values:
            continue
        values = values if isinstance(values, list) else [values]
        field = ExploreFilterForm.base_fields[name]
        choices = dict(getattr(field, 'choices', []))
        for value in values:
            removal = params.copy()
            removal.setlist(name, [item for item in params.getlist(name) if item != str(value)])
            pills.append({'label': f'{field.label or name.title()}: {choices.get(value, value)}',
                          'url': '?' + removal.urlencode()})
    return pills


def park_location_filters(parks, filters):
    if filters.get('borough'):
        parks = parks.filter(borough=filters['borough'])
    if filters.get('zip'):
        parks = parks.filter(reduce(or_, (comma_token_q('zipcode', z) for z in filters['zip'])))
    if filters.get('park'):
        parks = parks.filter(pk=filters['park'])
    # EXISTS avoids duplicate parks/events when a park has several matching courts.
    for key in filters.get('facility', []):
        facilities = Facility.objects.filter(park_id=OuterRef('pk'), feature_status='Active')
        parks = parks.filter(Exists(facilities.filter(comma_token_q('sports', key))))
    return parks


def matching_events(filters, user=None):
    """One definition of event membership; presentation code adds its own eager loading."""
    events = Event.objects.all()
    if filters.get('scope', 'all') == 'all':
        events = events.upcoming()
    else:
        now = timezone.now().astimezone(NYC_TZ)
        events = events.filter(Q(date__gt=now.date()) | Q(date=now.date(), time__gte=now.time()))
        if user is None or not user.is_authenticated:
            return events.none()
        attending = RSVP.objects.filter(event_id=OuterRef('pk'), user=user, status=RSVPStatus.ACTIVE)
        if filters['scope'] == 'hosting':
            events = events.filter(organizer=user)
        elif filters['scope'] == 'attending':
            events = events.filter(Exists(attending))
        else:
            events = events.filter(Q(organizer=user) | Q(Exists(attending)))

    parks = park_location_filters(Park.objects.filter(is_retired=False), filters)
    events = events.filter(park_id__in=parks.values('pk'))
    if filters.get('q'):
        q = filters['q']
        events = events.filter(Q(title__icontains=q) | Q(description__icontains=q)
            | Q(meeting_spot__icontains=q) | Q(park__name__icontains=q)
            | Q(park__address__icontains=q) | Q(park__location__icontains=q))
    if filters.get('sport'):
        events = events.filter(activity__in=filters['sport'])
    if filters.get('when') == 'today':
        events = events.filter(date=timezone.now().astimezone(NYC_TZ).date())
    else:
        if filters.get('date_from'):
            events = events.filter(date__gte=filters['date_from'])
        if filters.get('date_to'):
            events = events.filter(date__lte=filters['date_to'])
    if filters.get('skill'):
        events = events.filter(skill_level__in=[filters['skill'], SkillLevel.ALL])
    return events.order_by('date', 'time', 'pk')


def displayed_parks(filters, user=None):
    events = matching_events(filters, user)
    parks = park_location_filters(Park.objects.filter(is_retired=False), filters)
    if filters.get('q'):
        q = filters['q']
        parks = parks.filter(Q(name__icontains=q) | Q(address__icontains=q) | Q(location__icontains=q))
    sports = filters.get('sport', [])
    if sports and all(sport in ACTIVITY_FACILITY_KEYS for sport in sports):
        keys = {key for sport in sports for key in ACTIVITY_FACILITY_KEYS[sport]}
        facilities = Facility.objects.filter(park_id=OuterRef('pk'), feature_status='Active')
        facilities = facilities.filter(reduce(or_, (comma_token_q('sports', key) for key in keys)))
        parks = parks.filter(Exists(facilities))
    # An event-text hit must bring its park into every presentation, even if the
    # park name or facilities don't independently match the search/sport.
    parks = Park.objects.filter(is_retired=False).filter(
        Q(pk__in=parks.values('pk')) | Q(pk__in=events.values('park_id')))
    counts = events.order_by().values('park_id').annotate(total=Count('pk')).values('total')
    parks = parks.annotate(event_count=Coalesce(
        Subquery(counts.filter(park_id=OuterRef('pk')), output_field=IntegerField()), Value(0)))
    if filters.get('events_only'):
        parks = parks.filter(event_count__gt=0)
    return parks.order_by('-event_count', 'name', 'pk')


def facility_labels(park):
    keys = {key for facility in park.facilities.all() if facility.feature_status == 'Active'
            for key in facility.sports.split(',')}
    return [label for key, label in Facility.SPORT_FIELDS if key in keys]
