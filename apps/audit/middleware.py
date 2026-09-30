from .context import reset_actor, reset_client, set_actor, set_client


class AuditActorMiddleware:
    """Makes the signed-in user, their IP and user agent available to audit entries."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from apps.accounts.middleware import client_ip, user_agent

        client_token = set_client(client_ip(request), user_agent(request))
        token = set_actor(getattr(request, "user", None))
        try:
            return self.get_response(request)
        finally:
            reset_actor(token)
            reset_client(client_token)
