from django import template

from events.hub import explore_results_url, panel_param

register = template.Library()


@register.simple_tag(takes_context=True)
def explore_url(context, **changes):
    """Preserve repeated values while changing only the selected view/tab/page."""
    params = context['explore_params'].copy()
    if 'page' not in changes:
        params.pop('page', None)
    for name, value in changes.items():
        params.pop(name, None)
        if value is not None:
            params[name] = value
    return '?' + params.urlencode()


@register.simple_tag(takes_context=True)
def explore_panel_url(context, park_id):
    params = context['explore_params'].copy()
    params['panel'] = panel_param(park_id)
    page = context['page_obj'].number
    if page > 1:
        params['page'] = str(page)
    return '?' + params.urlencode()


@register.simple_tag(takes_context=True)
def last_explore_url(context):
    """Reusable on event pages and the future park-detail page, without a view mixin."""
    return explore_results_url(context['request'])
