import datetime
from zoneinfo import ZoneInfo

from django import forms
from django.utils import timezone

from parks.models import Park

from .models import CAPACITY_ERROR_MESSAGE, Activity, Event

NYC_TZ = ZoneInfo('America/New_York')
MAX_CAPACITY = 10000
MEETING_SPOT_ERROR_MESSAGE = 'Please specify where in the park to meet'


class EventCreateForm(forms.ModelForm):
    capacity = forms.IntegerField(
        required=False,
        label='Participant limit (optional)',
        help_text='Leave blank for unlimited participants, or enter at least 1.',
        widget=forms.NumberInput(
            attrs={
                'placeholder': 'Unlimited',
                'data-capacity-input': 'true',
            }
        ),
        error_messages={
            'invalid': CAPACITY_ERROR_MESSAGE,
        },
    )

    class Meta:
        model = Event
        fields = ('title', 'park', 'meeting_spot', 'activity', 'date', 'time', 'skill_level', 'capacity', 'description')
        labels = {
            'title': 'Event title',
            'park': 'Park location',
            'meeting_spot': 'Meeting spot',
            'activity': 'Activity / sport',
            'date': 'Event date',
            'time': 'Start time',
            'skill_level': 'Skill requirement',
            'description': 'Event description',
        }
        error_messages = {
            'meeting_spot': {'required': MEETING_SPOT_ERROR_MESSAGE},
        }
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': 'Saturday Morning 5v5 Pickup'}),
            'meeting_spot': forms.TextInput(attrs={'placeholder': 'North Meadow, Court 3'}),
            'date': forms.DateInput(attrs={'type': 'date'}),
            'time': forms.TimeInput(attrs={'type': 'time'}),
            'description': forms.Textarea(
                attrs={
                    'rows': 4,
                    'placeholder': 'Share court/field details, what to bring, and meetup instructions.',
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['park'].queryset = Park.objects.filter(is_retired=False).order_by('name', 'gispropnum')
        self.fields['park'].empty_label = 'Select an NYC park'
        # Blank is allowed in the model for events created before this field existed (#80).
        self.fields['meeting_spot'].required = True
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
        return cleaned_data
