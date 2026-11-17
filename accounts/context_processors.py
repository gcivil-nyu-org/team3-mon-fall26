from .roles import is_administrator


def roles(request):
    """`is_administrator` for templates, so staff-only controls never render for anyone else (#44)."""
    return {'is_administrator': is_administrator(getattr(request, 'user', None))}
