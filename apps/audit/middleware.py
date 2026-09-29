from .context import reset_actor, set_actor


class AuditActorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_actor(getattr(request, "user", None))
        try:
            return self.get_response(request)
        finally:
            reset_actor(token)
