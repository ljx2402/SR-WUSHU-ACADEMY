from contextvars import ContextVar

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from rest_framework.authtoken.models import Token

from apps.audit.models import AuditCategory, AuditLog
from apps.audit.utils import record as audit_record

from . import capabilities
from .capabilities import Cap, Role, roles_of

# Set while set_roles is running; the groups guard in signals.py refuses any
# other change to a user's groups so that every role change is audited.
role_change_in_progress = ContextVar("role_change_in_progress", default=False)


def ensure_role_groups():
    return {role: Group.objects.get_or_create(name=role)[0] for role in Role}


@transaction.atomic
def set_roles(user, roles, actor, reason):
    """Replace a user's roles.

    Requires ``roles.manage`` (``actor=None`` is a trusted system job) and a
    reason. The change is audited (actor, user, old and new roles, reason,
    tokens revoked) and the user's API tokens and web sessions are revoked, so
    no existing login keeps working under the old roles.
    """
    capabilities.require(actor, Cap.ROLES_MANAGE)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to change roles.")
    try:
        new_roles = frozenset(Role(r) for r in roles)
    except ValueError as exc:
        raise ValidationError(f"Unknown role: {exc}") from None

    # Lock the user row so concurrent role changes are applied one at a time.
    type(user).objects.select_for_update().filter(pk=user.pk).first()
    capabilities.clear_cache(user)
    old_roles = frozenset(
        Role(name) for name in user.groups.filter(name__in=Role.values).values_list("name", flat=True)
    )
    if old_roles == new_roles:
        return user

    if Role.SUPER_ADMIN in old_roles and Role.SUPER_ADMIN not in new_roles:
        others = type(user).objects.filter(is_active=True, groups__name=Role.SUPER_ADMIN).exclude(pk=user.pk)
        if not others.exists():
            raise PermissionDenied("The last active super admin cannot lose the SUPER_ADMIN role.")

    groups = ensure_role_groups()
    token = role_change_in_progress.set(True)
    try:
        user.groups.remove(*[groups[r] for r in old_roles - new_roles])
        user.groups.add(*[groups[r] for r in new_roles - old_roles])
    finally:
        role_change_in_progress.reset(token)

    user.auth_version += 1  # invalidates every existing web session
    user.save()  # re-derives is_staff / is_superuser / role from the groups
    revoked = revoke_tokens(user)
    capabilities.clear_cache(user)

    audit_record(
        user,
        AuditLog.Action.EVENT,
        changes={
            "roles": {"from": sorted(old_roles), "to": sorted(new_roles)},
            "tokens_revoked": revoked,
            "sessions_invalidated": True,
        },
        reason=reason,
        category=AuditCategory.SECURITY,
        actor=actor,
    )
    return user


def roles_for_display(user):
    return sorted(roles_of(user), key=capabilities.ROLE_PRECEDENCE.index)


def revoke_tokens(user):
    """Delete every API token of the user; returns how many were revoked."""
    revoked, _ = Token.objects.filter(user=user).delete()
    return revoked
