from django import forms
from django.contrib import admin
from django.db.models import BooleanField, ExpressionWrapper, Q, Subquery

from .models import SiteBanner


class SiteBannerForm(forms.ModelForm):
    class Meta:
        model = SiteBanner
        fields = ('message', 'is_active', 'ends_at')
        widgets = {'message': forms.Textarea(attrs={'rows': 3})}
        help_texts = {
            'message': 'Shown at the top of every page. If more than one banner is active, only the newest shows.'
        }


@admin.register(SiteBanner)
class SiteBannerAdmin(admin.ModelAdmin):
    """US-7.7 (#50): publish, end and take down site banners. The admin site logs every change (#52)."""

    form = SiteBannerForm
    list_display = ('__str__', 'is_active', 'ends_at', 'on_site', 'created_by', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('message',)
    fields = ('message', 'is_active', 'ends_at', 'created_by', 'created_at')
    readonly_fields = ('created_by', 'created_at')

    def get_queryset(self, request):
        # Mark the one banner pages show right now, in the same query as the list.
        current = SiteBanner.objects.showing().newest_first().values('pk')[:1]
        on_site = ExpressionWrapper(Q(pk=Subquery(current)), output_field=BooleanField())
        return super().get_queryset(request).select_related('created_by').annotate(is_on_site=on_site)

    @admin.display(boolean=True, description='On the site now')
    def on_site(self, banner):
        return bool(banner.is_on_site)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
