"""Remember only validated Explore state; never accept a caller-supplied return URL."""

import re

from django.urls import reverse

from .explore import filter_params

EXPLORE_SESSION_KEY = 'events_last_explore_query'


def positive_url_number(value):
    return int(value) if re.fullmatch(r'[0-9]{1,10}', value) and 0 < int(value) <= 2147483647 else None


def split_panel(params):
    """Read the panel separately from the park filter, including old shared links."""
    panel = params.get('panel', '')
    legacy = panel == '1' and 'park' in params
    if legacy:
        params = params.copy()
        panel = params.pop('park')[-1]
    return params, positive_url_number(panel), legacy


def panel_param(park_id):
    # Reserve the exact string "1" alongside park= for legacy links.
    return '01' if park_id == 1 else str(park_id)


def remember_explore(request, filters, page=1, panel_id=None):
    params = filter_params(filters)
    if panel_id:
        params['panel'] = panel_param(panel_id)
    if page > 1:
        params['page'] = str(page)
    query = params.urlencode()
    if request.session.get(EXPLORE_SESSION_KEY) != query:
        request.session[EXPLORE_SESSION_KEY] = query


def explore_results_url(request):
    query = request.session.get(EXPLORE_SESSION_KEY, '')
    return reverse('event_list') + ('?' + query if query else '')
