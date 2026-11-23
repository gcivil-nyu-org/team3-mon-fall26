from django import forms
from django.contrib import admin
from django.db import transaction

from .models import Facility, Park, Review

SPORT_KEYS = [key for key, _label in Facility.SPORT_FIELDS]
PRIMARY_SPORT_CHOICES = sorted(Facility.PRIMARY_SPORT_LABELS.items(), key=lambda item: item[1])


def next_admin_system_id(park):
    """`ADMIN-<gispropnum>-<n>` for a facility added by hand; NYC Open Data never uses that prefix."""
    prefix = f'ADMIN-{park.gispropnum}-'
    taken = set(Facility.objects.filter(system_id__startswith=prefix).values_list('system_id', flat=True))
    n = 1
    while f'{prefix}{n}' in taken:
        n += 1
    return f'{prefix}{n}'


def mark_admin_facility(facility):
    """Every admin save protects the facility from the sync; new ones get a generated system id."""
    facility.admin_modified = True
    if not facility.system_id:
        facility.system_id = next_admin_system_id(facility.park)


class FacilityForm(forms.ModelForm):
    """Facility edits with known sports only (#45).

    `primary_sport` groups the facility on the park page; `sports` (the keys Explore filters on) is
    chosen from `Facility.SPORT_FIELDS` and saved in the existing comma-separated format.
    """

    primary_sport = forms.ChoiceField(choices=[('', '---------')] + PRIMARY_SPORT_CHOICES)
    sports = forms.MultipleChoiceField(
        choices=Facility.SPORT_FIELDS,
        widget=forms.SelectMultiple(attrs={'size': 8}),
        help_text='Hold Cmd (Mac) or Ctrl to pick more than one.',
    )

    class Meta:
        model = Facility
        fields = (
            'primary_sport',
            'sports',
            'surface_type',
            'dimensions',
            'field_number',
            'feature_status',
            'is_lighted',
            'is_accessible',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.primary_sport if self.instance.pk else ''
        if current and current not in Facility.PRIMARY_SPORT_LABELS:
            # Keep an unrecognized code from the open data selectable so other edits can still be saved.
            self.fields['primary_sport'].choices += [(current, f'{current} (from NYC Open Data)')]
        if self.instance.pk:
            self.initial['sports'] = self.instance.sport_list()

    def clean_sports(self):
        chosen = set(self.cleaned_data['sports'])
        return ','.join(key for key in SPORT_KEYS if key in chosen)


class FacilityInline(admin.TabularInline):
    model = Facility
    form = FacilityForm
    extra = 1
    fields = ('system_id',) + FacilityForm.Meta.fields + ('admin_modified',)
    readonly_fields = ('system_id', 'admin_modified')
    show_change_link = True


@admin.register(Park)
class ParkAdmin(admin.ModelAdmin):
    list_display = ('name', 'gispropnum', 'borough', 'park_type', 'zipcode', 'is_retired', 'admin_modified')
    list_filter = ('borough', 'is_retired', 'waterfront', 'admin_modified')
    search_fields = ('name', 'gispropnum', 'address', 'location', 'zipcode')
    readonly_fields = ('admin_modified',)
    inlines = (FacilityInline,)

    def get_readonly_fields(self, request, obj=None):
        # The GIS number links the park to NYC Open Data, so it is fixed once the park exists.
        return self.readonly_fields + (('gispropnum',) if obj else ())

    def save_model(self, request, obj, form, change):
        obj.admin_modified = True
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        if formset.model is not Facility:
            return super().save_formset(request, form, formset, change)
        with transaction.atomic():
            for facility in formset.save(commit=False):
                mark_admin_facility(facility)
                facility.save()
            for facility in formset.deleted_objects:
                facility.delete()
            formset.save_m2m()


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    form = FacilityForm
    list_display = (
        'system_id',
        'park',
        'primary_sport',
        'surface_type',
        'feature_status',
        'is_lighted',
        'admin_modified',
    )
    list_filter = ('feature_status', 'primary_sport', 'is_lighted', 'is_accessible', 'admin_modified')
    search_fields = ('system_id', 'park__name', 'park__gispropnum', 'primary_sport', 'sports')
    raw_id_fields = ('park',)
    readonly_fields = ('system_id', 'admin_modified')

    def get_fields(self, request, obj=None):
        return ('system_id', 'park') + FacilityForm.Meta.fields + ('admin_modified',)

    def get_form(self, request, obj=None, change=False, **kwargs):
        kwargs['fields'] = ('park',) + FacilityForm.Meta.fields
        return super().get_form(request, obj, change, **kwargs)

    def save_model(self, request, obj, form, change):
        mark_admin_facility(obj)
        super().save_model(request, obj, form, change)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('park', 'user', 'rating', 'created_at')
    list_filter = ('rating',)
    search_fields = ('park__name', 'user__username', 'body')
    list_select_related = ('park', 'user')
    raw_id_fields = ('park', 'user')
