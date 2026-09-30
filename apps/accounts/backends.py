from django.contrib.auth.backends import ModelBackend

from . import lockout
from .capabilities import MODEL_CAPABILITIES, can


class CapabilityBackend(ModelBackend):
    """Password authentication as usual, but Django model permissions (used by
    the admin site) come from the capability map instead of permission rows in
    the database. Group or per-user permission rows are ignored entirely.

    Sign-in is refused without checking the password while the username or the
    client IP is locked out (``apps.accounts.lockout``). Inactive users can
    never authenticate or keep a session (ModelBackend.user_can_authenticate)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(self._username_field())
        if lockout.is_locked(username, request):
            if request is not None:
                request._login_locked = True
            return None
        return super().authenticate(request, username=username, password=password, **kwargs)

    @staticmethod
    def _username_field():
        from django.contrib.auth import get_user_model

        return get_user_model().USERNAME_FIELD

    def get_user_permissions(self, user_obj, obj=None):
        return set()

    def get_group_permissions(self, user_obj, obj=None):
        return set()

    def get_all_permissions(self, user_obj, obj=None):
        if not user_obj.is_active or user_obj.is_anonymous or obj is not None:
            return set()
        granted = set()
        for label, (view_cap, manage_cap) in MODEL_CAPABILITIES.items():
            app_label, model = label.split(".")
            if view_cap and can(user_obj, view_cap):
                granted.add(f"{app_label}.view_{model}")
            if manage_cap and can(user_obj, manage_cap):
                granted.update(f"{app_label}.{action}_{model}" for action in ("view", "add", "change", "delete"))
        return granted

    def has_perm(self, user_obj, perm, obj=None):
        return user_obj.is_active and perm in self.get_all_permissions(user_obj, obj)

    def has_module_perms(self, user_obj, app_label):
        return user_obj.is_active and any(p.startswith(f"{app_label}.") for p in self.get_all_permissions(user_obj))
