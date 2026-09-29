"""Who may see what.

* Admin: everything.
* Parent: only their own children (via Guardianship).
* Coach: classes they are currently assigned to, those classes' sessions and
  current members.
* Substitute coach: only the specific session they were assigned to, and only
  while the access window is open. That gives them the session, its roster and
  its attendance, and nothing else (no other classes, finance, payroll admin
  or unrelated parent data).
"""

from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User

from .models import ClassCoach, Enrollment, SessionCoach, Student, TrainingClass, TrainingSession


def is_admin(user):
    return bool(user and user.is_authenticated and user.is_academy_admin)


def coach_of(user):
    return user.coach if user and user.is_authenticated and user.is_active else None


def parent_of(user):
    return user.parent if user and user.is_authenticated and user.is_active else None


def open_substitute_slots(coach, at=None):
    at = at or timezone.now()
    return SessionCoach.objects.filter(
        coach=coach,
        role=SessionCoach.Role.SUBSTITUTE,
        status=SessionCoach.Status.ASSIGNED,
    ).filter(
        Q(access_starts_at__isnull=True) | Q(access_starts_at__lte=at),
        Q(access_ends_at__isnull=True) | Q(access_ends_at__gte=at),
    )


def regular_class_ids(coach, date=None):
    return ClassCoach.objects.active_on(date or timezone.localdate()).filter(coach=coach).values("training_class_id")


def classes_for(user):
    if is_admin(user):
        return TrainingClass.objects.all()
    coach = coach_of(user)
    if coach:
        # Substitute access never exposes the class itself, only the session.
        return TrainingClass.objects.filter(id__in=regular_class_ids(coach))
    parent = parent_of(user)
    if parent:
        return TrainingClass.objects.filter(enrollments__student__guardianships__parent=parent).distinct()
    return TrainingClass.objects.none()


def sessions_for(user, at=None):
    if is_admin(user):
        return TrainingSession.objects.all()
    coach = coach_of(user)
    if coach:
        substitute_sessions = open_substitute_slots(coach, at).values("session_id")
        return TrainingSession.objects.filter(
            Q(training_class_id__in=regular_class_ids(coach)) | Q(id__in=substitute_sessions)
        )
    parent = parent_of(user)
    if parent:
        return TrainingSession.objects.filter(
            training_class__enrollments__student__guardianships__parent=parent
        ).distinct()
    return TrainingSession.objects.none()


def students_for(user, at=None):
    if is_admin(user):
        return Student.objects.all()
    coach = coach_of(user)
    if coach:
        today = timezone.localdate()
        regular = Enrollment.objects.active_on(today).filter(training_class_id__in=regular_class_ids(coach))
        ids = set(regular.values_list("student_id", flat=True))
        for slot in open_substitute_slots(coach, at).select_related("session"):
            ids.update(slot.session.roster().values_list("id", flat=True))
        return Student.objects.filter(id__in=ids)
    parent = parent_of(user)
    if parent:
        return Student.objects.filter(guardianships__parent=parent).distinct()
    return Student.objects.none()


def can_view_session(user, session, at=None):
    return sessions_for(user, at).filter(pk=session.pk).exists()


def can_take_attendance(user, session, at=None):
    """Admins always; regular class coaches; substitutes only inside their window."""
    if is_admin(user):
        return True
    coach = coach_of(user)
    if not coach:
        return False
    if ClassCoach.objects.active_on(session.date).filter(coach=coach, training_class=session.training_class).exists():
        return True
    return open_substitute_slots(coach, at).filter(session=session).exists()


def is_parent_of(user, student):
    parent = parent_of(user)
    return bool(parent and student.guardianships.filter(parent=parent).exists())


def role_of(user):
    if is_admin(user):
        return User.Role.ADMIN
    return getattr(user, "role", None)
