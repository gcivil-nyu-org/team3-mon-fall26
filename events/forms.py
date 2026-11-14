import datetime
from zoneinfo import ZoneInfo

from django import forms
from django.utils import timezone

from parks.models import Park

from .models import CAPACITY_ERROR_MESSAGE, Activity, Event

NYC_TZ = ZoneInfo('America/New_York')
MAX_CAPACITY = 10000
MEETING_SPOT_ERROR_MESSAGE = 'Please specify where in the park to meet'
PARK_ERROR_MESSAGE = 'Please select a valid park'
# US-3.11 (#116): games end on the day they start and run from 15 minutes to 8 hours.
MIN_GAME_LENGTH = datetime.timedelta(minutes=15)
MAX_GAME_LENGTH = datetime.timedelta(hours=8)
END_TIME_REQUIRED_MESSAGE = 'Please enter when the game ends'
END_BEFORE_START_MESSAGE = 'End time must be after the start time, on the same day'
TOO_SHORT_MESSAGE = 'Games must last at least 15 minutes'
TOO_LONG_MESSAGE = 'Games can last at most 8 hours'


class EventCreateForm(forms.ModelForm):
    capacity = forms.IntegerField(
        required=False,
        label='Participant limit (optional)',
        # US-3.3 (#24): the limit counts everyone playing, including the host when they play (US-3.10).
        help_text="Total players, including you if you're playing. Leave blank for unlimited.",
        widget=forms.NumberInput(
            attrs={
                'placeholder': 'Unlimited',
                'data-capacity-input': 'true',
                'data-capacity-max': MAX_CAPACITY,
            }
        ),
        error_messages={
            'invalid': CAPACITY_ERROR_MESSAGE,
        },
    )

    class Meta:
        model = Event
        fields = (
            'title',
            'park',
            'meeting_spot',
            'activity',
            'date',
            'time',
            'end_time',
            'skill_level',
            'capacity',
            'host_is_playing',
            'description',
        )
        labels = {
            'title': 'Event title',
            'park': 'Park location',
            'meeting_spot': 'Meeting spot',
            'activity': 'Activity / sport',
            'date': 'Event date',
            'time': 'Start time',
            'end_time': 'End time',
            'skill_level': 'Skill requirement',
            'host_is_playing': "I'm playing in this game",
            'description': 'Event description',
        }
        help_texts = {
            'end_time': 'Same day, 15 minutes to 8 hours after the start.',
            'host_is_playing': 'You count toward the participant limit. Uncheck if you are only organizing.',
        }
        error_messages = {
            'meeting_spot': {'required': MEETING_SPOT_ERROR_MESSAGE},
            'end_time': {'required': END_TIME_REQUIRED_MESSAGE},
        }
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Saturday Morning 5v5 Pickup'}),
            'meeting_spot': forms.TextInput(attrs={'placeholder': 'North Meadow, Court 3'}),
            'date': forms.DateInput(attrs={'type': 'date'}),
            'time': forms.TimeInput(attrs={'type': 'time'}),
            'end_time': forms.TimeInput(attrs={'type': 'time'}),
            'description': forms.Textarea(
                attrs={
                    'rows': 4,
                    'placeholder': 'Share court/field details, what to bring, and meetup instructions.',
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'park' in self.fields:  # Edit Event (EventUpdateForm) has no park field
            self.fields['park'].queryset = Park.objects.filter(is_retired=False).order_by('name', 'gispropnum')
            self.fields['park'].empty_label = 'Select an NYC park'
            # US-3.9 (#81): retired or nonexistent parks are rejected, and the select becomes a type-to-search
            # picker (static/js/lookahead-select.js). Without JavaScript it stays a plain dropdown.
            self.fields['park'].error_messages['invalid_choice'] = PARK_ERROR_MESSAGE
            self.fields['park'].widget.attrs.update(
                {
                    'data-lookahead': '',
                    'data-placeholder': 'Type a park name or borough',
                    'data-empty': 'No matching NYC parks',
                    'data-max-results': '50',
                    'data-more': 'Keep typing to see more parks',
                }
            )
        # Blank is allowed in the model for events created before this field existed (#80).
        self.fields['meeting_spot'].required = True
        # Nullable in the model only so a rollback keeps working (US-3.11); every new or edited event needs one.
        self.fields['end_time'].required = True
        self.fields['activity'].choices = [('', 'Select a sport')] + list(Activity.choices)

    def clean_capacity(self):
        capacity = self.cleaned_data.get('capacity')
        if capacity is not None:
            if capacity < 1:
                raise forms.ValidationError(CAPACITY_ERROR_MESSAGE)
            if capacity > MAX_CAPACITY:
                raise forms.ValidationError(f'Participant limit cannot exceed {MAX_CAPACITY:,}.')
        return capacity

    def clean(self):
        cleaned_data = super().clean()
        event_date = cleaned_data.get('date')
        event_time = cleaned_data.get('time')
        if event_date and event_time:
            scheduled = datetime.datetime.combine(event_date, event_time, tzinfo=NYC_TZ)
            now = timezone.now().astimezone(NYC_TZ)
            if scheduled <= now:
                self.add_error('date', 'Event date and time must be in the future.')
        end_time = cleaned_data.get('end_time')
        if event_time and end_time:
            length = datetime.datetime.combine(datetime.date.min, end_time) - datetime.datetime.combine(
                datetime.date.min, event_time
            )
            if length <= datetime.timedelta(0):
                self.add_error('end_time', END_BEFORE_START_MESSAGE)
            elif length < MIN_GAME_LENGTH:
                self.add_error('end_time', TOO_SHORT_MESSAGE)
            elif length > MAX_GAME_LENGTH:
                self.add_error('end_time', TOO_LONG_MESSAGE)
        return cleaned_data


class EventUpdateForm(EventCreateForm):
    """US-3.4 (#25): the create form's fields and rules, minus the park, which can't change after creation."""

    class Meta(EventCreateForm.Meta):
        fields = tuple(name for name in EventCreateForm.Meta.fields if name != 'park')
