from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.academy import access


class IsAcademyAdmin(BasePermission):
    def has_permission(self, request, view):
        return access.is_admin(request.user)


class IsAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.method in SAFE_METHODS or access.is_admin(request.user)


class IsAdminOrParent(BasePermission):
    """Finance data: admins and parents only. Coaches never see finance."""

    def has_permission(self, request, view):
        user = request.user
        return access.is_admin(user) or access.parent_of(user) is not None


class IsAdminOrCoach(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return access.is_admin(user) or access.coach_of(user) is not None
