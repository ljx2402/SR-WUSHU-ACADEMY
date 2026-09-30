from django.core.exceptions import PermissionDenied
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from .capabilities import Role
from .models import User
from .services import role_change_in_progress, set_roles


@receiver(m2m_changed, sender=User.groups.through)
def guard_group_changes(sender, action, **kwargs):
    """Roles may only change through set_roles (audited, revokes tokens)."""
    if action in ("pre_add", "pre_remove", "pre_clear") and not role_change_in_progress.get():
        raise PermissionDenied("Change roles with apps.accounts.services.set_roles().")


@receiver(post_save, sender=User)
def bootstrap_super_admin(sender, instance, created, raw=False, **kwargs):
    """``createsuperuser`` creates a user with is_superuser=True; turn that into
    the SUPER_ADMIN role so the flag and the role can never disagree."""
    if created and not raw and getattr(instance, "_bootstrap_super_admin", False):
        instance._bootstrap_super_admin = False
        set_roles(instance, {Role.SUPER_ADMIN}, actor=None, reason="Bootstrap: account created as superuser")


@receiver(user_login_failed)
def record_login_failure(sender, credentials, request=None, **kwargs):
    """Count the failure for the lockout. ``credentials`` has the password
    masked by Django; only the username is kept."""
    from . import lockout

    username = credentials.get("username") or credentials.get(User.USERNAME_FIELD) or ""
    lockout.record_failure(username, request, locked=bool(getattr(request, "_login_locked", False)))


@receiver(user_logged_in)
def record_login(sender, request, user, **kwargs):
    from apps.audit.utils import security_event

    from . import lockout

    lockout.clear(user.get_username())
    security_event(user, "LOGIN", {}, actor=user)


@receiver(user_logged_out)
def record_logout(sender, request, user, **kwargs):
    from apps.audit.utils import security_event

    if user is not None:
        security_event(user, "LOGOUT", {}, actor=user)
