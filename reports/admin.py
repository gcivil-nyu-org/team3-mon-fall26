from django.contrib import admin
from django.urls import reverse
from django.utils.text import Truncator
from django.utils import timezone
from django.utils.html import format_html

from accounts.audit import log_admin_action
from events.models import Message

from .forms import ResolveReportForm
from .models import Report
from .targets import target_page


class ReportStatusFilter(admin.SimpleListFilter):
    title = 'status'
    parameter_name = 'status'

    def lookups(self, request, model_admin):
        return (*Report.Status.choices, ('all', 'All reports'))

    def queryset(self, request, queryset):
        status = self.value() or Report.Status.OPEN
        if status == 'all':
            return queryset
        return queryset.filter(status=status)

    def choices(self, changelist):
        for value, label in self.lookup_choices:
            yield {
                'selected': (self.value() or Report.Status.OPEN) == value,
                'query_string': changelist.get_query_string({self.parameter_name: value}),
                'display': label,
            }


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    form = ResolveReportForm
    list_display = ('__str__', 'target_link', 'snapshot_preview', 'reason', 'reporter', 'created_at', 'status')
    list_select_related = ('content_type', 'reporter')
    list_filter = (ReportStatusFilter,)
    readonly_fields = (
        'target_link',
        'target_snapshot',
        'reason',
        'details',
        'reporter',
        'created_at',
        'status',
        'resolved_by',
        'resolved_at',
    )
    fields = (*readonly_fields, 'outcome')
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return (obj is None or obj.status == Report.Status.OPEN) and super().has_change_permission(request, obj)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        # Django wraps change-form POSTs in a transaction. Lock before checking the current status.
        if request.method == 'POST' and request.resolver_match.url_name == 'reports_report_change':
            queryset = queryset.select_for_update()
        return queryset

    @admin.display(description='Target')
    def target_link(self, report):
        label = f'{report.content_type.name.capitalize()} #{report.object_id}'
        target = report.target
        if target is None:
            return f'{label} (deleted)'
        if isinstance(target, Message):
            url = f'{target_page(target)}#event-message-{target.pk}'
        else:
            opts = target._meta
            url = reverse(f'admin:{opts.app_label}_{opts.model_name}_change', args=[target.pk])
        return format_html('<a href="{}">{}</a>', url, label)

    @admin.display(description='Snapshot')
    def snapshot_preview(self, report):
        return Truncator(report.target_snapshot).chars(120)

    def save_model(self, request, obj, form, change):
        obj.status = Report.Status.RESOLVED
        obj.resolved_by = request.user
        obj.resolved_at = timezone.now()
        obj.save(update_fields=['status', 'outcome', 'resolved_by', 'resolved_at'])

    def log_change(self, request, obj, message):
        return log_admin_action(request.user, obj, 'change', f'Resolved report: {obj.get_outcome_display()}')
