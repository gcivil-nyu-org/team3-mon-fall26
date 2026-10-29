"""Public landing page and infrastructure probes."""

from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe


@require_safe
def home(request):
    # Members get their (for now, placeholder) home page; visitors see the marketing page.
    if request.user.is_authenticated:
        return render(request, "home.html")
    return render(request, "landing.html")


@require_safe
def health(request):
    return JsonResponse({"status": "ok"})


@require_safe
def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
