"""Tracks who is making changes so audit entries can name the actor.

The actor and an optional reason are stored in context variables. The web
middleware sets the actor for session-authenticated requests, the API views set
it after token authentication, and services can wrap work in ``audit_context``.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_actor = ContextVar("audit_actor", default=None)
_reason = ContextVar("audit_reason", default="")


def get_actor():
    return _actor.get()


def get_reason():
    return _reason.get()


def set_actor(user):
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    return _actor.set(user)


def reset_actor(token):
    _actor.reset(token)


@contextmanager
def audit_context(actor=None, reason=""):
    actor_token = _actor.set(actor if actor is not None else _actor.get())
    reason_token = _reason.set(reason or _reason.get())
    try:
        yield
    finally:
        _reason.reset(reason_token)
        _actor.reset(actor_token)
