from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.db.models import Q

from apps.academy.models import Student, TrainingSession
from apps.audit.models import AuditCategory, AuditedModel


class AttendanceStatus(models.TextChoices):
    UNMARKED = "UNMARKED", "Unmarked"
    PRESENT = "PRESENT", "Present"
    ABSENT = "ABSENT", "Absent"
    LATE = "LATE", "Late"
    EXCUSED = "EXCUSED", "Excused"


# Statuses that count as "marked". UNMARKED (or no record at all) is never
# treated as Absent and is left out of the attendance percentage.
MARKED_STATUSES = (AttendanceStatus.PRESENT, AttendanceStatus.ABSENT, AttendanceStatus.LATE, AttendanceStatus.EXCUSED)


class AttendanceQuerySet(models.QuerySet):
    """Bulk writes would skip the roster, edit-window, reason and audit rules."""

    def update(self, **kwargs):
        raise PermissionDenied("Attendance can only be changed through the attendance service.")

    def delete(self):
        raise PermissionDenied("Attendance records cannot be deleted; change the status instead.")

    def bulk_create(self, *args, **kwargs):
        raise PermissionDenied("Attendance can only be recorded through the attendance service.")

    def bulk_update(self, *args, **kwargs):
        raise PermissionDenied("Attendance can only be changed through the attendance service.")


class AttendanceRecord(AuditedModel):
    """One expected student's attendance for one session.

    Recorded only through ``apps.attendance.services.record_session_attendance``,
    which enforces the roster, the 48-hour coach edit window and reasons. Every
    create and change is written to the audit log (actor, before/after, reason).
    Records cannot be deleted. An expected student without a record is UNMARKED.
    """

    audit_category = AuditCategory.ATTENDANCE

    session = models.ForeignKey(TrainingSession, on_delete=models.PROTECT, related_name="attendance")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="attendance")
    status = models.CharField(max_length=8, choices=AttendanceStatus.choices)
    remarks = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AttendanceQuerySet.as_manager()

    class Meta:
        ordering = ["session", "student__full_name"]
        constraints = [
            models.UniqueConstraint(fields=["session", "student"], name="unique_attendance"),
            models.CheckConstraint(condition=Q(status__in=AttendanceStatus.values), name="attendance_status_valid"),
        ]

    def __str__(self):
        return f"{self.student.full_name} – {self.session} – {self.get_status_display()}"

    def save(self, *args, **kwargs):
        if self.pk is None and not self.session.roster().filter(pk=self.student_id).exists():
            raise ValidationError(f"{self.student.full_name} is not on the roster for this session.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Attendance records cannot be deleted; change the status instead.")
