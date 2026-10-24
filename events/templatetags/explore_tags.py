from django import template

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
