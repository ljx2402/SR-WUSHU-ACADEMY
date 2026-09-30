"""Brute-force protection for sign-in (Django admin and the API token login).

Failed attempts are stored in the database (``LoginFailure``), so the limit
holds across every worker process. A username is locked after
MAX_FAILURES_PER_USERNAME failures within WINDOW_MINUTES, and a client IP after
MAX_FAILURES_PER_IP. While locked, the password is not even checked, and the
caller gets the same generic "invalid credentials" answer, so the lockout does
not reveal whether an account exists. A successful sign-in clears the
username's failures."""

import datetime

from django.conf import settings
from django.utils import timezone

from .middleware import client_ip, user_agent
from .models import LoginFailure

RETENTION_DAYS = 30


def _config():
    return settings.LOGIN_LOCKOUT


def normalize(username):
    return (username or "").strip().lower()[:150]


def is_locked(username, request=None):
    cfg = _config()
    since = timezone.now() - datetime.timedelta(minutes=cfg["WINDOW_MINUTES"])
    recent = LoginFailure.objects.filter(attempted_at__gte=since)
    if recent.filter(username=normalize(username)).count() >= cfg["MAX_FAILURES_PER_USERNAME"]:
        return True
    ip = client_ip(request) if request is not None else None
    return bool(ip) and recent.filter(ip_address=ip).count() >= cfg["MAX_FAILURES_PER_IP"]


def record_failure(username, request=None, locked=False):
    LoginFailure.objects.create(
        username=normalize(username),
        ip_address=client_ip(request) if request is not None else None,
        user_agent=user_agent(request) if request is not None else "",
        locked=locked,
        attempted_at=timezone.now(),
    )
    LoginFailure.objects.filter(attempted_at__lt=timezone.now() - datetime.timedelta(days=RETENTION_DAYS)).delete()


def clear(username):
    LoginFailure.objects.filter(username=normalize(username)).delete()
