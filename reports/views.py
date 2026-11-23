from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import ReportForm
from .models import Report
from .targets import REPORT_TARGETS, target_page, target_snapshot


@require_POST
def submit_report(request, kind, pk):
    if kind not in REPORT_TARGETS:
        raise Http404
    queryset, owner_field = REPORT_TARGETS[kind]
    target = get_object_or_404(queryset, pk=pk)
    return_url = target_page(target)
    if not request.user.is_authenticated:
        return redirect_to_login(return_url)
    if getattr(target, owner_field) == request.user.pk:
        raise PermissionDenied

    form = ReportForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            'reports/invalid_report.html',
            {'form': form, 'kind': kind, 'target_id': pk, 'return_url': return_url},
            status=400,
        )

    _, created = Report.objects.get_or_create(
        reporter=request.user,
        content_type=ContentType.objects.get_for_model(target),
        object_id=target.pk,
        status=Report.Status.OPEN,
        defaults={
            'target_snapshot': lambda: target_snapshot(target),
            'reason': form.cleaned_data['reason'],
            'details': form.cleaned_data['details'],
        },
    )
    if created:
        messages.success(request, 'Thanks, an administrator will review this.')
    else:
        messages.info(request, 'Your report is already under review.')
    return redirect(return_url)
