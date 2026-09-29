"""API authorization: every view declares which capability each action needs.

    capabilities = {
        "list": (Cap.FINANCE_VIEW_ALL, Cap.FINANCE_VIEW_OWN_CHILDREN),  # any of
        "create": Cap.FINANCE_CHARGES_MANAGE,
    }

Actions that are not declared are denied (fail closed). Which records the
caller then sees is decided separately by ``apps.academy.access``.
"""

from rest_framework.permissions import BasePermission

from apps.accounts.capabilities import can


class HasCapability(BasePermission):
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        action_map = getattr(view, "action_map", None)
        if action_map is not None and request.method.lower() not in action_map:
            # Method not routed on this viewset: let DRF answer 405. Nothing runs.
            return True
        action = getattr(view, "action", None) or request.method.lower()
        requirement = view.capabilities.get(action)
        if requirement is None:
            return False
        return can(user, requirement)
