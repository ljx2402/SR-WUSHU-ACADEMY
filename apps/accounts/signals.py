from django.core.exceptions import PermissionDenied
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
