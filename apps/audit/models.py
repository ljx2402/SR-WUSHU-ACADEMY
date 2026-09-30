from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import models


class AuditCategory(models.TextChoices):
    GENERAL = "GENERAL", "General"
    STUDENT = "STUDENT", "Student"
    CLASS = "CLASS", "Class"
    ATTENDANCE = "ATTENDANCE", "Attendance"
    FINANCE = "FINANCE", "Finance"
    COMPETITION = "COMPETITION", "Competition"
    PAYROLL = "PAYROLL", "Payroll"
    ACCESS = "ACCESS", "Access"
    SECURITY = "SECURITY", "Security & roles"


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionDenied("Audit log entries cannot be modified.")

    def delete(self):
        raise PermissionDenied("Audit log entries cannot be deleted.")

    def bulk_update(self, *args, **kwargs):
        raise PermissionDenied("Audit log entries cannot be modified.")


class AuditLog(models.Model):
    """Append-only record of a change. Entries can never be edited or deleted
    (model and queryset guards, and a PostgreSQL trigger). Sensitive values are
    masked before they are written (``apps.audit.masking``)."""

    class Action(models.TextChoices):
        CREATE = "CREATE", "Created"
        UPDATE = "UPDATE", "Updated"
        DELETE = "DELETE", "Deleted"
        EVENT = "EVENT", "Event"

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    category = models.CharField(max_length=20, choices=AuditCategory.choices, db_index=True)
    action = models.CharField(max_length=10, choices=Action.choices)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.CharField(max_length=64, db_index=True)
    object_repr = models.CharField(max_length=255)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-timestamp", "-id"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.action} {self.object_repr}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionDenied("Audit log entries cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Audit log entries cannot be deleted.")


class AuditedModel(models.Model):
    """Base class: every create/update/delete is written to the AuditLog."""

    audit_category = AuditCategory.GENERAL
    audit_exclude = ("created_at", "updated_at")

    class Meta:
        abstract = True
