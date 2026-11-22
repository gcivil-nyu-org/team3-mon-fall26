from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import PermissionDenied
from django.template.defaultfilters import linebreaksbr

User = get_user_model()

# Fields that decide what an account may do. Only superusers manage them (US-7.1 / #44).
ROLE_FIELDS = ('is_staff', 'is_superuser', 'groups', 'user_permissions')


class UserAdmin(BaseUserAdmin):
    """Django's user admin, limited for administrators who aren't superusers (#44).

    Administrators can view accounts and activate or deactivate participants. Everything else,
    including staff status, groups, permissions, passwords and other staff accounts, is
    superuser-only, so an administrator can't grant themselves or anyone else more access.
    """

    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if request.user.is_superuser or obj is None:
            return readonly
        editable = {'is_active'}
        fields = {field for _, options in self.get_fieldsets(request, obj) for field in options['fields']}
        return tuple(readonly) + tuple(sorted((fields | set(ROLE_FIELDS)) - editable - {'password'}))

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if request.user.is_superuser or obj is None:
            return fieldsets
        # The password field links to the password change form, which non-superusers can't use.
        return [
            (name, {**options, 'fields': tuple(f for f in options['fields'] if f != 'password')})
            for name, options in fieldsets
        ]

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        # Other staff and superuser accounts are view-only for administrators.
        return obj.pk == request.user.pk or not (obj.is_staff or obj.is_superuser)

    def user_change_password(self, request, id, form_url=''):
        if not request.user.is_superuser:
            raise PermissionDenied
        return super().user_change_password(request, id, form_url)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


class ActorFilter(admin.SimpleListFilter):
    """Only the accounts that have entries, rather than every user on the site."""

    title = 'actor'
    parameter_name = 'actor'

    def lookups(self, request, model_admin):
        actors = User.objects.filter(logentry__isnull=False).distinct().order_by('username')
        return [(str(user.pk), user.username) for user in actors]

    def queryset(self, request, queryset):
        if self.value():
            return queryset.filter(user_id=self.value())
        return queryset


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    """The audit trail (#52): every admin-site action plus in-app staff actions, read-only."""

    list_display = ('action_time', 'actor', 'action', 'target_type', 'target', 'summary')
    list_filter = (ActorFilter, 'action_flag', ('action_time', admin.DateFieldListFilter), 'content_type')
    date_hierarchy = 'action_time'
    search_fields = ('object_repr', 'change_message', 'user__username')
    list_select_related = ('user', 'content_type')
    fields = ('action_time', 'user', 'action_flag', 'content_type', 'object_id', 'object_repr', 'details')
    readonly_fields = fields
    ordering = ('-action_time',)

    @admin.display(description='Actor', ordering='user__username')
    def actor(self, entry):
        return entry.user

    @admin.display(description='Action', ordering='action_flag')
    def action(self, entry):
        return entry.get_action_flag_display()

    @admin.display(description='Target type', ordering='content_type')
    def target_type(self, entry):
        return entry.content_type

    @admin.display(description='Target', ordering='object_repr')
    def target(self, entry):
        return entry.object_repr

    @admin.display(description='Change')
    def summary(self, entry):
        return entry.get_change_message().split('\n', 1)[0]

    @admin.display(description='Details')
    def details(self, entry):
        return linebreaksbr(entry.get_change_message(), autoescape=True)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
