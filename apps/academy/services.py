import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.capabilities import Cap, require
from apps.audit.context import audit_context

from .models import ClassCoach, Enrollment, SessionCoach, Student, TrainingSession


def daterange(start, end):
    day = start
    while day <= end:
        yield day
        day += datetime.timedelta(days=1)


@transaction.atomic
def generate_sessions(training_class, start, end, actor=None):
    """Create sessions from the weekly timetable for every date in [start, end].

    Existing sessions are left untouched, so the function is safe to re-run.
    Current class coaches are attached to each new session as regular coaches.
    """
    require(actor, Cap.SESSIONS_MANAGE)
    created = []
    schedules = list(training_class.schedules.all())
    with audit_context(actor, "Generated from class timetable"):
        for day in daterange(start, end):
            for slot in schedules:
                if not slot.applies_on(day):
                    continue
                session, was_created = TrainingSession.objects.get_or_create(
                    training_class=training_class,
                    date=day,
                    start_time=slot.start_time,
                    defaults={"end_time": slot.end_time, "venue": slot.venue or training_class.venue},
                )
                if not was_created:
                    continue
                for assignment in ClassCoach.objects.active_on(day).filter(training_class=training_class):
                    SessionCoach.objects.create(session=session, coach=assignment.coach, assigned_by=actor)
                created.append(session)
    return created


def substitute_access_window(session):
    cfg = settings.ACADEMY
    return (
        session.starts_at - datetime.timedelta(hours=cfg["SUBSTITUTE_ACCESS_HOURS_BEFORE"]),
        session.ends_at + datetime.timedelta(hours=cfg["SUBSTITUTE_ACCESS_HOURS_AFTER"]),
    )


@transaction.atomic
def assign_substitute(session, substitute, replaces=None, actor=None, reason="", access_starts_at=None, access_ends_at=None):
    """Authorize a substitute coach for one session.

    The substitute gets access to this session, its roster and its attendance
    only, and only inside the access window. The replaced coach's slot is
    marked REPLACED so it is not paid for this session.

    Rules: one active substitute per session (also a database constraint); the
    session must be scheduled and not yet past the access window; the
    substitute must not already coach this session; ``replaces`` must be a
    regular coach still assigned to it.
    """
    require(actor, Cap.SUBSTITUTE_ASSIGN)
    # Lock the session so two simultaneous authorizations are checked one after the other.
    session = TrainingSession.objects.select_for_update().get(pk=session.pk)
    if session.status == TrainingSession.Status.CANCELLED:
        raise ValidationError("Cannot assign a substitute to a cancelled session.")
    if session.status != TrainingSession.Status.SCHEDULED:
        raise ValidationError("A substitute can only be authorized for a scheduled session.")
    if replaces is not None and replaces == substitute:
        raise ValidationError("A coach cannot substitute for themselves.")
    if not substitute.is_active:
        raise ValidationError(f"{substitute} is not an active coach.")
    default_start, default_end = substitute_access_window(session)
    access_starts_at = access_starts_at or default_start
    access_ends_at = access_ends_at or default_end
    now = timezone.now()
    if access_ends_at <= access_starts_at:
        raise ValidationError("The access window must end after it starts.")
    if access_ends_at <= now:
        raise ValidationError("This session's substitute access window has already ended.")
    current = SessionCoach.objects.active_substitutes().filter(session=session).select_related("coach").first()
    if current is not None:
        if current.coach_id == substitute.pk:
            raise ValidationError(f"{substitute} is already the authorized substitute for this session.")
        raise ValidationError(f"{current.coach} is already the authorized substitute for this session; "
                              "revoke that authorization first.")
    if session.coach_slots.exclude(status__in=SessionCoach.ENDED).filter(coach=substitute).exists():
        raise ValidationError(f"{substitute} already coaches this session.")
    original = None
    if replaces is not None:
        original = session.coach_slots.filter(coach=replaces, role=SessionCoach.Role.REGULAR).first()
        if original is None or original.status != SessionCoach.Status.ASSIGNED:
            raise ValidationError(f"{replaces} is not a regular coach currently assigned to this session.")
    with audit_context(actor, reason or "Substitute coach assigned"):
        if original is not None:
            original.status = SessionCoach.Status.REPLACED
            original.save()
        try:
            with transaction.atomic():
                slot = SessionCoach.objects.create(
                    session=session,
                    coach=substitute,
                    role=SessionCoach.Role.SUBSTITUTE,
                    status=SessionCoach.Status.ASSIGNED,
                    replaces=replaces,
                    access_starts_at=access_starts_at,
                    access_ends_at=access_ends_at,
                    assigned_by=actor,
                    authorized_at=now,
                    reason=reason,
                )
        except IntegrityError:
            raise ValidationError("Another substitute was authorized for this session at the same time.") from None
    return slot


@transaction.atomic
def revoke_substitute(slot, actor=None, reason=""):
    """End a substitute authorization. Access stops immediately; the row stays as history."""
    require(actor, Cap.SUBSTITUTE_REVOKE)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to revoke a substitute authorization.")
    slot = SessionCoach.objects.select_for_update().get(pk=slot.pk)
    if not slot.is_substitute:
        raise ValidationError("Only substitute authorizations can be revoked.")
    if slot.status != SessionCoach.Status.ASSIGNED:
        raise ValidationError(f"This substitute authorization was already {slot.status.lower()}.")
    with audit_context(actor, reason):
        slot.status = SessionCoach.Status.REVOKED
        slot.revoked_at = timezone.now()
        slot.revoked_by = actor
        slot.revocation_reason = reason
        slot.save()
        slot.restore_replaced_coach()
    return slot


@transaction.atomic
def enroll(student, training_class, start_date=None, team=None, coach=None, actor=None):
    require(actor, Cap.STUDENTS_MANAGE)
    start_date = start_date or timezone.localdate()
    if Enrollment.objects.filter(student=student, training_class=training_class, end_date__isnull=True).exists():
        raise ValidationError(f"{student.full_name} is already in {training_class.name}.")
    with audit_context(actor, "Enrolled"):
        return Enrollment.objects.create(
            student=student,
            training_class=training_class,
            team=team or training_class.team,
            coach=coach,
            start_date=start_date,
        )


@transaction.atomic
def end_enrollment(enrollment, end_date=None, reason="", actor=None):
    require(actor, Cap.STUDENTS_MANAGE)
    end_date = end_date or timezone.localdate()
    if end_date < enrollment.start_date:
        raise ValidationError("End date cannot be before start date.")
    with audit_context(actor, reason or "Enrollment ended"):
        enrollment.end_date = end_date
        enrollment.end_reason = reason
        enrollment.save()
    return enrollment


@transaction.atomic
def transfer(enrollment, new_class, on_date=None, team=None, coach=None, actor=None, reason="Class transfer"):
    """Move a student to another class while keeping the old membership as history."""
    on_date = on_date or timezone.localdate()
    end_enrollment(enrollment, on_date - datetime.timedelta(days=1), reason, actor)
    return enroll(enrollment.student, new_class, on_date, team, coach, actor)


@transaction.atomic
def change_student_status(student, status, reason="", actor=None, effective_date=None):
    require(actor, Cap.STUDENTS_MANAGE)
    if student.status == status:
        return student
    with audit_context(actor, reason):
        student.status = status
        student.save()
        entry = student.status_history.first()
        if entry is not None:
            entry.reason = reason
            entry.effective_date = effective_date or timezone.localdate()
            entry.save()
        if status in (Student.Status.WITHDRAWN, Student.Status.GRADUATED):
            for enrollment in student.enrollments.filter(end_date__isnull=True):
                end_enrollment(enrollment, effective_date, f"Student {status.lower()}", actor)
    return student


def next_student_no(join_date=None):
    year = (join_date or timezone.localdate()).year
    prefix = f"SR{year}"
    last = (
        Student.objects.filter(student_no__startswith=prefix)
        .order_by("-student_no")
        .values_list("student_no", flat=True)
        .first()
    )
    number = int(last[len(prefix):]) + 1 if last and last[len(prefix):].isdigit() else 1
    return f"{prefix}{number:04d}"

