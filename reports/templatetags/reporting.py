from django import template
from django.middleware.csrf import get_token

from reports.forms import ReportForm

register = template.Library()


@register.inclusion_tag('reports/report_control.html', takes_context=True)
def report_control(context, kind, target_id, owner_id):
    request = getattr(context, 'request', None)
    if request is None or not request.user.is_authenticated or request.user.pk == owner_id:
        return {}
    return {
        'form': ReportForm(auto_id=f'report-{kind}-{target_id}-%s'),
        'kind': kind,
        'target_id': target_id,
        # Park review cards are included with an isolated context.
        'csrf_token': context.get('csrf_token') or get_token(request),
    }
