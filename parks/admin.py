from django.contrib import admin

from .models import Facility, Park, Review


@admin.register(Park)
class ParkAdmin(admin.ModelAdmin):
    list_display = ('name', 'gispropnum', 'borough', 'park_type', 'zipcode', 'is_retired', 'admin_modified')
    list_filter = ('borough', 'is_retired', 'waterfront', 'admin_modified')
    search_fields = ('name', 'gispropnum', 'address', 'location', 'zipcode')


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
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


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('park', 'user', 'rating', 'created_at')
    list_filter = ('rating',)
    search_fields = ('park__name', 'user__username', 'body')
    list_select_related = ('park', 'user')
    raw_id_fields = ('park', 'user')
