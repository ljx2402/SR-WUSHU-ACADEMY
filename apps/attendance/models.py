from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models

from apps.academy.models import Student, TrainingSession
from apps.audit.models import AuditCategory, AuditedModel


class AttendanceStatus(models.TextChoices):
    PRESENT = "PRESENT", "Present"
    ABSENT = "ABSENT", "Absent"
    LATE = "LATE", "Late"
    EXCUSED = "EXCUSED", "Excused"


class AttendanceRecord(AuditedModel):
    """One student's attendance for one session.

    Every create and change is written to the audit log with who made it and
    why (see apps.attendance.services.mark_attendance). Records cannot be deleted.
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

    class Meta:
        ordering = ["session", "student__full_name"]
        constraints = [models.UniqueConstraint(fields=["session", "student"], name="unique_attendance")]

    def __str__(self):
        return f"{self.student.full_name} – {self.session} – {self.get_status_display()}"

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Attendance records cannot be deleted; change the status instead.")
