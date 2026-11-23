from django.conf import settings

from .models import SiteBanner


def site_banner(request):
    """`site_banner` for base.html (US-7.7 / #50).

    It's the lookup itself, not its result, so the query only runs on pages that show the banner,
    never for JSON or partial responses. Deployment check mode has no real database, so it skips it.
    """
    if getattr(settings, 'DEPLOYMENT_CHECK_ONLY', False):
        return {}
    return {'site_banner': SiteBanner.objects.current}
