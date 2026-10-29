from .views import health


class HealthCheckMiddleware:
    """Allow internal IP liveness probes without relaxing other host checks."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/health/":
            return health(request)
        return self.get_response(request)
