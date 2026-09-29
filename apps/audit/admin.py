from django.contrib import admin

from apps.accounts.capabilities import Cap, can

from .models import AuditCategory, AuditLog

OPERATIONS_CATEGORIES = [AuditCategory.GENERAL, AuditCategory.STUDENT, AuditCategory.CLASS,
                         AuditCategory.ATTENDANCE, AuditCategory.COMPETITION, AuditCategory.ACCESS]
FINANCE_CATEGORIES = [AuditCategory.FINANCE, AuditCategory.PAYROLL]


def visible_categories(user):
    """Audit categories a user may read. SECURITY (role changes) is super admin only."""
    if can(user, Cap.AUDIT_VIEW_ALL):
        return list(AuditCategory.values)
    categories = []
    if can(user, Cap.AUDIT_VIEW_OPERATIONS):
        categories += OPERATIONS_CATEGORIES
    if can(user, Cap.AUDIT_VIEW_FINANCE):
        categories += FINANCE_CATEGORIES
    return categories


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "category", "action", "object_repr", "actor", "reason")
    list_filter = ("category", "action", "content_type")
    search_fields = ("object_repr", "object_id", "reason", "actor__username")
    date_hierarchy = "timestamp"
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def get_queryset(self, request):
        return super().get_queryset(request).filter(category__in=visible_categories(request.user))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
