"""Attendance rules.

* Attendance belongs to the session's **expected roster**: the students who
  were members of the class on the session date. No one else can be marked.
* An expected student without a record (or with an UNMARKED record) is
  **UNMARKED**. It is never treated as Absent and is left out of the
  attendance percentage; it is reported separately.
* Nothing can be recorded before the session **starts** (from the start time,
  inclusive, in the academy time zone), by anyone.
* Coaches (the class's regular coaches, or the session's authorized
  substitute inside their access window) may record and change attendance
  until **48 hours after the session ends** (academy time zone).
* After that the session is **locked**: only an administrator with
  ``attendance.correct`` may change it, and always with a reason.
* Changing a mark that was already recorded always needs a reason.
* Every write is audited (actor, previous and new value, reason, time).
"""

import datetime
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.academy import access
from apps.academy.models import Enrollment, TrainingSession
from apps.accounts.capabilities import Cap, can
from apps.audit.context import audit_context

from .models import MARKED_STATUSES, AttendanceRecord, AttendanceStatus


class SessionState:
    NOT_STARTED = "NOT_STARTED"  # before the session start: nothing can be recorded yet
    OPEN = "OPEN"            # coaches may still record; some expected students are unmarked
    COMPLETE = "COMPLETE"    # every expected student is marked; coaches may still correct
    LOCKED = "LOCKED"        # coach edit window closed: administrator corrections only
    CANCELLED = "CANCELLED"  # no attendance for a cancelled session


def coach_edit_hours():
    return settings.ACADEMY["ATTENDANCE_COACH_EDIT_HOURS"]


def coach_edit_deadline(session):
    """The moment the coach edit window closes (exclusive): session end + 48 hours,
    in the academy time zone."""
    return session.ends_at + datetime.timedelta(hours=coach_edit_hours())


def coach_window_open(session, at=None):
    return (at or timezone.now()) < coach_edit_deadline(session)


def _authorize_write(session, actor, reason, now):
    """Return True if this write is an administrative correction (window closed)."""
    if coach_window_open(session, now):
        if not access.can_take_attendance(actor, session, now):
            raise PermissionDenied("You are not allowed to take attendance for this session.")
        return False
    if not can(actor, Cap.ATTENDANCE_CORRECT):
        raise PermissionDenied(
            f"The {coach_edit_hours()}-hour attendance edit window for this session closed at "
            f"{timezone.localtime(coach_edit_deadline(session)):%Y-%m-%d %H:%M}. "
            "Ask an administrator to correct it."
        )
    if not reason:
        raise ValidationError("A reason is required to correct attendance after the coach edit window.")
    return True


@transaction.atomic
def record_session_attendance(session, entries, actor, reason=""):
    """Record attendance for several expected students of one session, all or nothing.

    ``entries`` is an iterable of ``(student or student id, status, remarks)``.
    Returns the records written or unchanged. UNMARKED for a student with no
    record is a no-op (no row is created for nothing).
    """
    reason = (reason or "").strip()
    # Lock the session: writes for one session happen one at a time, so
    # simultaneous submissions cannot create duplicates or skip the reason rule.
    session = TrainingSession.objects.select_for_update().get(pk=session.pk)
    now = timezone.now()
    if session.status == TrainingSession.Status.CANCELLED:
        if not access.can_take_attendance(actor, session, now):
            raise PermissionDenied("You are not allowed to take attendance for this session.")
        raise ValidationError("Attendance cannot be taken for a cancelled session.")
    correction = _authorize_write(session, actor, reason, now)
    if now < session.starts_at:
        raise ValidationError(
            f"Attendance can be recorded from the session start "
            f"({timezone.localtime(session.starts_at):%Y-%m-%d %H:%M}), not before.")

    roster = {student.id: student for student in session.roster()}
    wanted = []
    seen = set()
    for student, status, remarks in entries:
        student_id = getattr(student, "pk", student)
        if status not in AttendanceStatus.values:
            raise ValidationError(f"Unknown attendance status: {status}")
        if student_id not in roster:
            name = getattr(student, "full_name", f"Student {student_id}")
            raise ValidationError(f"{name} is not on the roster for this session.")
        if student_id in seen:
            raise ValidationError(f"{roster[student_id].full_name} appears more than once.")
        seen.add(student_id)
        wanted.append((roster[student_id], status, remarks or ""))

    existing = {r.student_id: r for r in AttendanceRecord.objects.select_for_update().filter(session=session)}
    audit_reason = (
        f"Administrative correction after the {coach_edit_hours()}-hour coach edit window: {reason}"
        if correction else reason
    )
    saved = []
    for student, status, remarks in wanted:
        record = existing.get(student.id)
        if record is None and status == AttendanceStatus.UNMARKED:
            continue
        if record is not None and record.status == status and record.remarks == remarks:
            saved.append(record)
            continue
        if record is not None and not reason:
            raise ValidationError("A reason is required when changing attendance that was already recorded.")
        with audit_context(actor, audit_reason):
            if record is None:
                record = AttendanceRecord(session=session, student=student)
            record.status = status
            record.remarks = remarks
            record.recorded_by = actor
            record.save()
        saved.append(record)
    return saved


def mark_attendance(session, student, status, actor, remarks="", reason=""):
    """Record one student's attendance (see ``record_session_attendance``)."""
    saved = record_session_attendance(session, [(student, status, remarks)], actor, reason)
    return saved[0] if saved else None


# --------------------------------------------------------------------------- expected roster


def expected_pairs(sessions):
    """{(session_id, student_id)} of students expected at each session: members
    of the class on the session date (same rule as ``TrainingSession.roster``)."""
    sessions = list(sessions)
    by_class = defaultdict(list)
    enrollments = Enrollment.objects.filter(training_class_id__in={s.training_class_id for s in sessions})
    for e in enrollments.values("student_id", "training_class_id", "start_date", "end_date"):
        by_class[e["training_class_id"]].append(e)
    return {
        (session.id, e["student_id"])
        for session in sessions
        for e in by_class[session.training_class_id]
        if e["start_date"] <= session.date and (e["end_date"] is None or session.date <= e["end_date"])
    }


def held_sessions(start=None, end=None, training_class=None, at=None):
    """Sessions that have started and were not cancelled: the ones that can be unmarked."""
    at = at or timezone.now()
    sessions = TrainingSession.objects.exclude(status=TrainingSession.Status.CANCELLED).filter(
        date__lte=timezone.localtime(at).date()
    )
    if training_class is not None:
        sessions = sessions.filter(training_class=training_class)
    if start:
        sessions = sessions.filter(date__gte=start)
    if end:
        sessions = sessions.filter(date__lte=end)
    return [s for s in sessions if s.starts_at <= at]


# --------------------------------------------------------------------------- summaries


def _percentage(numerator, denominator):
    if not denominator:
        return None
    return (Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def summarize(records):
    """Counts and percentage over attendance records.

    Attendance percentage = attended / (Present + Late + Absent) × 100, 2 dp, half-up.

    * attended = Present (+ Late when LATE_COUNTS_AS_PRESENT, the default)
    * Excused is left out of the calculation entirely.
    * UNMARKED is never counted as attended or absent; callers that know the
      expected roster add ``expected`` and ``unmarked``.
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
    counts["total"] = counts["marked"] = counted + counts["excused"]
    counts["attended"] = attended
    counts["percentage"] = _percentage(attended, counted)
    return counts


def session_summary(session):
    """Expected / marked / unmarked for one session, plus the counts and percentage."""
    expected_ids = set(session.roster().values_list("id", flat=True))
    summary = summarize(session.attendance.filter(student_id__in=expected_ids))
    summary["expected"] = len(expected_ids)
    summary["unmarked"] = summary["expected"] - summary["marked"]
    # Only possible if class membership was edited after attendance was taken.
    summary["not_on_roster"] = session.attendance.exclude(student_id__in=expected_ids).filter(
        status__in=MARKED_STATUSES).count()
    return summary


def session_state(session, summary=None, at=None):
    if session.status == TrainingSession.Status.CANCELLED:
        return SessionState.CANCELLED
    if (at or timezone.now()) < session.starts_at:
        return SessionState.NOT_STARTED
    if not coach_window_open(session, at):
        return SessionState.LOCKED
    summary = summary or session_summary(session)
    return SessionState.COMPLETE if summary["expected"] and not summary["unmarked"] else SessionState.OPEN


def session_sheet(session):
    """One row per expected student with their status (UNMARKED if none)."""
    records = {r.student_id: r for r in session.attendance.select_related("recorded_by")}
    rows = []
    for student in session.roster():
        record = records.get(student.id)
        rows.append({
            "student": student,
            "record": record,
            "status": record.status if record else AttendanceStatus.UNMARKED,
            "remarks": record.remarks if record else "",
        })
    return rows


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
    summary = summarize(records)
    sessions = held_sessions(start, end, training_class)
    expected = {session_id for session_id, student_id in expected_pairs(sessions) if student_id == student.pk}
    marked = set(records.filter(status__in=MARKED_STATUSES).values_list("session_id", flat=True))
    summary["expected"] = len(expected)
    summary["unmarked"] = len(expected - marked)
    return summary

