from io import BytesIO

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from PIL import Image, ImageOps

from parks.models import Borough

from .models import Profile, ProfileSkillLevel

AVATAR_MAX_BYTES = 5 * 1024 * 1024
AVATAR_SIZE = 512

COMMON_SPORTS = (
    'Basketball',
    'Soccer',
    'Running',
    'Tennis',
    'Pickleball',
    'Volleyball',
    'Handball',
    'Baseball',
    'Softball',
    'Football',
    'Cricket',
    'Cycling',
    'Swimming',
    'Yoga',
    'Ultimate frisbee',
)


class RegisterForm(UserCreationForm):
    email = forms.EmailField()
    display_username = forms.CharField(
        max_length=150,
        label='Username',
        help_text='This is the name other members see.',
    )

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ('display_username', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {
            'display_username': 'Pick a username',
            'email': 'you@example.com',
            'password1': 'Password',
            'password2': 'Confirm password',
        }
        for name, text in placeholders.items():
            self.fields[name].widget.attrs['placeholder'] = text
            self.fields[name].help_text = ''
            self.fields[name].widget.attrs['autocomplete'] = 'off'
        self.fields['password1'].help_text = 'At least 8 characters, not too common or all numbers.'
        self.fields['password2'].label = 'Confirm password'
        self.fields['password1'].widget.attrs['autocomplete'] = 'new-password'
        self.fields['password2'].widget.attrs['autocomplete'] = 'new-password'

    def clean_display_username(self):
        display_username = self.cleaned_data['display_username'].strip()
        self.instance.username = display_username
        UnicodeUsernameValidator()(display_username)
        if (
            Profile.objects.filter(display_username__iexact=display_username).exists()
            or get_user_model().objects.filter(username__iexact=display_username).exists()
        ):
            raise forms.ValidationError('That username is already in use.')
        return display_username

    def clean_email(self):
        email = self.cleaned_data['email']
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email


def square_avatar(upload):
    # Re-encoding also drops EXIF data such as the GPS location of phone photos.
    with Image.open(upload) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in ('RGBA', 'LA', 'P'):
            image = image.convert('RGBA')
            background = Image.new('RGB', image.size, 'white')
            background.paste(image, mask=image.getchannel('A'))
            image = background
        image = ImageOps.fit(image.convert('RGB'), (AVATAR_SIZE, AVATAR_SIZE))
        buffer = BytesIO()
        image.save(buffer, format='JPEG', quality=85)
    return ContentFile(buffer.getvalue(), name='avatar.jpg')


class ProfileSetupForm(forms.ModelForm):
    avatar = forms.ImageField(
        label='Profile photo',
        required=False,
        widget=forms.FileInput(
            attrs={
                'accept': 'image/jpeg,image/png,image/webp',
                'class': 'profile-photo-input',
            }
        ),
    )
    # Set by the photo's X button (profile-photo.js).
    remove_avatar = forms.BooleanField(required=False, widget=forms.HiddenInput)
    display_username = forms.CharField(
        max_length=150,
        label='Username',
        help_text='This is the name other members see.',
    )
    bio = forms.CharField(
        max_length=500,
        label='Bio',
        required=False,
        widget=forms.Textarea(
            attrs={
                'rows': 6,
                'style': 'resize: none !important;',
                'placeholder': 'Share a little bit about yourself or some fun facts.',
            }
        ),
    )
    first_name = forms.CharField(
        max_length=150,
        label='First name',
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'John'}),
    )
    last_name = forms.CharField(
        max_length=150,
        label='Last name',
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Doe'}),
    )
    favorite_sports = forms.MultipleChoiceField(
        choices=[(sport, sport) for sport in COMMON_SPORTS],
        label='Favorite sports',
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    custom_sport = forms.CharField(
        max_length=50,
        label='Add another sport',
        required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Type a sport'}),
    )

    class Meta:
        model = Profile
        fields = ('avatar', 'display_username', 'bio', 'home_borough', 'favorite_sports', 'skill_level')
        labels = {
            'home_borough': 'Home borough',
            'skill_level': 'Overall skill level',
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved_avatar_name = self.instance.avatar.name
        if user is not None:
            current_username = self.instance.display_username or user.username
            self.fields['display_username'].widget.attrs['placeholder'] = current_username
            if not self.is_bound:
                if not self.initial.get('display_username'):
                    self.initial['display_username'] = current_username
                self.initial.setdefault('first_name', user.first_name)
                self.initial.setdefault('last_name', user.last_name)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxSelectMultiple):
                field.widget.attrs['autocomplete'] = 'off'
        self.fields['home_borough'].required = False
        self.fields['home_borough'].choices = [('', 'Choose your borough'), *Borough.choices]
        self.fields['skill_level'].required = False
        self.fields['skill_level'].choices = [
            ('', 'Choose your skill level'),
            *ProfileSkillLevel.choices,
        ]
        submitted_sports = []
        if self.is_bound:
            submitted_sports = self.data.getlist(self.add_prefix('favorite_sports'))
            custom_sport = self.data.get(self.add_prefix('custom_sport'), '').strip()
            if custom_sport:
                submitted_sports.append(custom_sport)
        choices = list(COMMON_SPORTS)
        if self.instance and self.instance.pk:
            choices.extend(sport for sport in self.instance.favorite_sports if sport not in choices)
        choices.extend(sport for sport in submitted_sports if sport not in choices)
        self.fields['favorite_sports'].choices = [(sport, sport) for sport in choices]

    def clean_display_username(self):
        display_username = self.cleaned_data['display_username'].strip()
        UnicodeUsernameValidator()(display_username)
        if display_username:
            profile_taken = (
                Profile.objects.filter(display_username__iexact=display_username).exclude(pk=self.instance.pk).exists()
            )
            user_taken = (
                get_user_model()
                .objects.filter(username__iexact=display_username)
                .exclude(pk=self.instance.user_id)
                .exists()
            )
            if profile_taken or user_taken:
                raise forms.ValidationError('That username is already in use.')
        return display_username

    def clean_avatar(self):
        avatar = self.cleaned_data.get('avatar')
        if not isinstance(avatar, UploadedFile):
            return avatar
        if avatar.size > AVATAR_MAX_BYTES:
            raise forms.ValidationError('Choose a photo under 5 MB.')
        try:
            return square_avatar(avatar)
        except (OSError, SyntaxError, ValueError, EOFError, Image.DecompressionBombError):
            # ImageField only checks the file header, so damaged files such as a
            # truncated JPEG get this far and fail once the pixels are decoded.
            raise forms.ValidationError(self.fields['avatar'].error_messages['invalid_image'])

    def clean_favorite_sports(self):
        return self.normalize_sports(self.cleaned_data['favorite_sports'])

    def clean(self):
        cleaned_data = super().clean()
        if 'favorite_sports' in cleaned_data:
            sports = [
                *cleaned_data['favorite_sports'],
                cleaned_data.get('custom_sport', ''),
            ]
            cleaned_data['favorite_sports'] = self.normalize_sports(sports)
        # A new upload wins over "Remove photo". False tells Django to clear the field.
        if cleaned_data.get('remove_avatar') and not self.files.get(self.add_prefix('avatar')):
            cleaned_data['avatar'] = False
        return cleaned_data

    def save(self, commit=True):
        profile = super().save(commit)
        old_name = self.saved_avatar_name
        if commit and old_name and profile.avatar.name != old_name:
            storage = profile.avatar.storage
            transaction.on_commit(lambda: storage.delete(old_name))
        return profile

    @staticmethod
    def normalize_sports(sports):
        normalized = []
        seen = set()
        for sport in sports:
            sport = sport.strip()
            if sport and sport.casefold() not in seen:
                if len(sport) > 50:
                    raise forms.ValidationError('Sports must be 50 characters or fewer.')
                normalized.append(sport)
                seen.add(sport.casefold())
        if len(normalized) > 20:
            raise forms.ValidationError('Choose no more than 20 favorite sports.')
        return normalized


class LoginForm(AuthenticationForm):
    # One message for a wrong password or an unknown account, so the form
    # doesn't reveal which emails are registered (#11).
    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': 'Invalid email or password',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'] = forms.CharField(
            label='Email',
            widget=forms.EmailInput(attrs={'placeholder': 'you@example.com'}),
        )
        self.fields['username'].widget.attrs['autocomplete'] = 'off'
        self.fields['password'].widget.attrs['placeholder'] = 'Password'
        self.fields['password'].widget.attrs['autocomplete'] = 'current-password'
