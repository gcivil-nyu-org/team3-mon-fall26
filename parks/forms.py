from django import forms

from .models import Review


class ReviewForm(forms.ModelForm):
    """Shared validation for creating a review and the future owner-edit flow (#41)."""

    rating = forms.TypedChoiceField(
        coerce=int,
        choices=[('', 'Select a rating')]
        + [(value, f'{value} star' + ('s' if value != 1 else '')) for value in range(1, 6)],
        label='Rating',
        error_messages={'invalid_choice': 'Choose a rating from 1 to 5 stars.'},
    )

    class Meta:
        model = Review
        fields = ['rating', 'body']
        labels = {'body': 'Your review'}
        widgets = {
            'body': forms.Textarea(
                attrs={'rows': 4, 'placeholder': 'Share useful details about the courts, lighting or other facilities.'}
            ),
        }
