"""API sign-in and sign-out for the Parent / Coach / Student apps."""

from django.contrib.auth import authenticate
from django.db import transaction
from rest_framework import serializers, status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.audit.utils import security_event

from . import lockout
from .services import revoke_tokens

GENERIC_FAILURE = "Unable to sign in with the provided credentials."


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, trim_whitespace=False, style={"input_type": "password"})


class ObtainTokenView(APIView):
    """POST username + password -> a fresh API token.

    * Wrong credentials, an inactive account and a locked-out username or IP all
      get the same 400 answer (no account enumeration).
    * Each sign-in replaces the user's previous token (one active token per user).
    * Rate-limited per client (throttle scope "login") on top of the lockout.
    * Successful sign-ins are audited (actor, IP, user agent); failures are
      recorded as LoginFailure rows. Passwords are never stored or logged."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    @transaction.atomic
    def post(self, request):
        payload = LoginSerializer(data=request.data)
        if not payload.is_valid():
            return Response({"detail": GENERIC_FAILURE}, status=status.HTTP_400_BAD_REQUEST)
        username = payload.validated_data["username"]
        user = authenticate(request, username=username, password=payload.validated_data["password"])
        if user is None or not user.is_active:
            return Response({"detail": GENERIC_FAILURE}, status=status.HTTP_400_BAD_REQUEST)
        lockout.clear(username)
        # Lock the user row: two simultaneous sign-ins rotate the token one after the other.
        user = type(user).objects.select_for_update().get(pk=user.pk)
        revoked = revoke_tokens(user)
        token = Token.objects.create(user=user)
        security_event(user, "API_TOKEN_ISSUED", {"previous_tokens_revoked": revoked}, actor=user)
        return Response({"token": token.key})


class LogoutView(APIView):
    """POST with the token -> the token is deleted and can never be used again."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        revoked = revoke_tokens(request.user)
        security_event(request.user, "API_LOGOUT", {"tokens_revoked": revoked}, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
