from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q

from apps.academy import access
from apps.academy.models import TrainingSession
from apps.audit.context import audit_context

from .models import AttendanceRecord, AttendanceStatus


@transaction.atomic
def mark_attendance(session, student, status, actor, remarks="", reason=""):
    """Create or change one attendance record, enforcing access rules.

    Changing an existing record requires a reason, which is stored in the audit log.
    """
    if not access.can_take_attendance(actor, session):
        raise PermissionDenied("You are not allowed to take attendance for this session.")
    if session.status == TrainingSession.Status.CANCELLED:
        raise ValidationError("Attendance cannot be taken for a cancelled session.")
    if status not in AttendanceStatus.values:
        raise ValidationError(f"Unknown attendance status: {status}")
    if not session.roster().filter(pk=student.pk).exists():
        raise ValidationError(f"{student.full_name} is not on the roster for this session.")

    record = AttendanceRecord.objects.filter(session=session, student=student).first()
    if record is not None and record.status == status and record.remarks == remarks:
        return record
    if record is not None and not reason:
        raise ValidationError("A reason is required when changing attendance that was already recorded.")

    with audit_context(actor, reason):
        if record is None:
            record = AttendanceRecord(session=session, student=student)
        record.status = status
        record.remarks = remarks
        record.recorded_by = actor
        record.save()
    return record


def _percentage(numerator, denominator):
    if not denominator:
        return None
    return (Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def summarize(records):
    """Attendance percentage = attended / (sessions counted) × 100.

    * attended = Present (+ Late when LATE_COUNTS_AS_PRESENT, the default)
    * Excused sessions are left out of the calculation entirely.
    """
    counts = records.aggregate(
        present=Count("id", filter=Q(status=AttendanceStatus.PRESENT)),
        late=Count("id", filter=Q(status=AttendanceStatus.LATE)),
        absent=Count("id", filter=Q(status=AttendanceStatus.ABSENT)),
        excused=Count("id", filter=Q(status=AttendanceStatus.EXCUSED)),
    )
    late_counts = settings.ACADEMY.get("LATE_COUNTS_AS_PRESENT", True)
    attended = counts["present"] + (counts["late"] if late_counts else 0)
    counted = counts["present"] + counts["late"] + counts["absent"]
    counts["total"] = counted + counts["excused"]
    counts["attended"] = attended
    counts["percentage"] = _percentage(attended, counted)
    return counts


def student_summary(student, training_class=None, start=None, end=None):
    records = AttendanceRecord.objects.filter(student=student).exclude(
        session__status=TrainingSession.Status.CANCELLED
    )
    if training_class is not None:
        records = records.filter(session__training_class=training_class)
    if start:
        records = records.filter(session__date__gte=start)
    if end:
        records = records.filter(session__date__lte=end)
    return summarize(records)


def session_summary(session):
    return summarize(session.attendance.all())
