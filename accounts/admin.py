from django.contrib import admin, messages
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.template.defaultfilters import linebreaksbr, pluralize
from django.urls import reverse
from django.utils.html import format_html_join
from django.utils.safestring import mark_safe

from events.models import RSVP, Event
from parks.models import Review

from .audit import log_admin_action
from .suspension import end_sessions

User = get_user_model()

# Fields that decide what an account may do. Only superusers manage them (US-7.1 / #44).
ROLE_FIELDS = ('is_staff', 'is_superuser', 'groups', 'user_permissions')


class UserAdmin(BaseUserAdmin):
    """Django's user admin, limited for administrators who aren't superusers (#44).

    Administrators can view accounts and activate or deactivate participants. Everything else,
    including staff status, groups, permissions, passwords and other staff accounts, is
    superuser-only, so an administrator can't grant themselves or anyone else more access.

    US-7.6 (#49): suspending an account means clearing Active, from the form or the Suspend action.
    Django then refuses the account at login and drops its existing sessions on their next request.
    Accounts are never deleted, because deleting one would also delete its events and other people's
    RSVPs to them.
    """

    list_display = ('username', 'email', 'date_joined', 'last_login', 'is_active', 'is_staff')
    readonly_fields = ('activity',)
    actions = ('suspend_accounts', 'restore_accounts')

    def get_readonly_fields(self, request, obj=None):
        readonly = tuple(super().get_readonly_fields(request, obj))
        if obj is not None and obj.pk == request.user.pk:
            # Nobody can suspend themselves and get locked out.
            readonly += ('is_active',)
        if request.user.is_superuser or obj is None:
            return readonly
        editable = {'is_active'} - set(readonly)
        fields = {field for _, options in self.get_fieldsets(request, obj) for field in options['fields']}
        return tuple(sorted((fields | set(ROLE_FIELDS) | set(readonly)) - editable - {'password'}))

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None:
            return fieldsets
        fieldsets = [*fieldsets, ('Activity', {'fields': ('activity',)})]
        if request.user.is_superuser:
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

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and 'is_active' in form.changed_data and not obj.is_active:
            end_sessions([obj])

    @admin.display(description='Links')
    def activity(self, user):
        """Links to what the account has done on the site, each filtered to this account."""
        links = (
            (
                'Hosted events',
                'admin:events_event_changelist',
                'organizer__id__exact',
                Event.objects.filter(organizer=user),
            ),
            ('RSVPs', 'admin:events_rsvp_changelist', 'user__id__exact', RSVP.objects.filter(user=user)),
            ('Park reviews', 'admin:parks_review_changelist', 'user__id__exact', Review.objects.filter(user=user)),
        )
        return format_html_join(
            mark_safe('<br>'),
            '<a href="{}?{}={}">{} ({})</a>',
            ((reverse(url), lookup, user.pk, label, rows.count()) for label, url, lookup, rows in links),
        )

    @admin.action(description='Suspend selected accounts', permissions=['change'])
    def suspend_accounts(self, request, queryset):
        self.set_active(request, queryset, False)

    @admin.action(description='Restore selected accounts', permissions=['change'])
    def restore_accounts(self, request, queryset):
        self.set_active(request, queryset, True)

    def set_active(self, request, queryset, active):
        """Suspend or restore the accounts this administrator may change, logging each one."""
        changed, skipped = [], 0
        with transaction.atomic():
            for user in queryset.select_for_update():
                if user.is_active == active:
                    continue
                if user.pk == request.user.pk or not self.has_change_permission(request, user):
                    skipped += 1
                    continue
                user.is_active = active
                user.save(update_fields=['is_active'])
                log_admin_action(request.user, user, 'change', 'Restored account' if active else 'Suspended account')
                changed.append(user)
            if not active:
                end_sessions(changed)
        verb = 'Restored' if active else 'Suspended'
        self.message_user(request, f'{verb} {len(changed)} account{pluralize(len(changed))}.')
        if skipped:
            self.message_user(
                request,
                f"Skipped {skipped} account{pluralize(skipped)} you can't change (your own, or other staff).",
                messages.WARNING,
            )


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
