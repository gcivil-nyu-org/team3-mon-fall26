"""Remember only validated Explore state; never accept a caller-supplied return URL."""

from django.urls import reverse

from .explore import filter_params

EXPLORE_SESSION_KEY = 'events_last_explore_query'


def remember_explore(request, filters, page=1, panel=False):
    params = filter_params(filters)
    if panel:
        params['panel'] = '1'
    if page > 1:
        params['page'] = str(page)
    query = params.urlencode()
    if request.session.get(EXPLORE_SESSION_KEY) != query:
        request.session[EXPLORE_SESSION_KEY] = query


def explore_results_url(request):
    query = request.session.get(EXPLORE_SESSION_KEY, '')
    return reverse('event_list') + ('?' + query if query else '')
