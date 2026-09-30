"""API token authentication with expiry."""

import datetime

from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed


def token_expired(token, at=None):
    return (at or timezone.now()) >= token.created + datetime.timedelta(hours=settings.API_TOKEN_TTL_HOURS)


class ExpiringTokenAuthentication(TokenAuthentication):
    """DRF token authentication, plus: a token stops working API_TOKEN_TTL_HOURS
    after it was issued (it is then deleted). Tokens are also deleted when the
    user's roles, password or active status change, and inactive users are
    refused by DRF itself. Every refusal uses the same generic message."""

    def authenticate_credentials(self, key):
        model = self.get_model()
        token = model.objects.select_related("user").filter(key=key).first()
        if token is None or not token.user.is_active:
            raise AuthenticationFailed("Invalid or expired token.")
        if token_expired(token):
            token.delete()
            raise AuthenticationFailed("Invalid or expired token.")
        return token.user, token
