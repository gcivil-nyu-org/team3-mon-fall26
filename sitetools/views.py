from django.contrib import admin
from django.template.response import TemplateResponse
from django.utils import timezone

from .metrics import measure


def metrics(request):
    """US-7.8 (#51): live counts for administrators, inside the admin site.

    Wrapped in `admin.site.admin_view()` in config/urls.py, so it has the admin's access rules: anyone
    who isn't an active staff account is sent to the admin login, like the rest of /admin/ (#44).
    """
    now = timezone.now()
    context = {
        **admin.site.each_context(request),
        'title': 'Site metrics',
        'sections': measure(now),
        'measured_at': now,
    }
    return TemplateResponse(request, 'admin/metrics.html', context)
