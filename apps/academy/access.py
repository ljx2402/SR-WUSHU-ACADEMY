"""Record-level access: which specific records a user may see.

RBAC (``apps.accounts.capabilities``) decides whether a user may use a kind of
action at all. This module decides *which records*, based on capabilities
plus relationships:

* ``*_all`` capabilities: every record.
* Coach (COACH role + coach profile): classes they are currently assigned to,
  those classes' sessions and current members. A substitute coach only gets
  the specific session they cover while its access window is open; never the
  class.
* Parent (PARENT role + parent profile): their own children only.
* Student (STUDENT role + student account): their own record only.

A user may hold several roles (e.g. COACH + PARENT). Visibility is the union of
each relationship, but the *level of detail* depends on how the user relates
to each record; see ``StudentScope.level_for`` and the context-specific
querysets (``roster_sessions_for``, ``children_for``) used for rosters and
finance, which never widen through another role.
"""

from dataclasses import dataclass, field

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.accounts.capabilities import Cap, can

from .models import ClassCoach, Enrollment, SessionCoach, Student, TrainingClass, TrainingSession

# --------------------------------------------------------------------------- profiles


def _active(user):
    return bool(user and user.is_authenticated and user.is_active)


def coach_of(user):
    return user.coach if _active(user) else None


def parent_of(user):
    return user.parent if _active(user) else None


def student_of(user):
    return user.student if _active(user) else None


# --------------------------------------------------------------------------- building blocks


def open_substitute_slots(coach, at=None):
    """Substitute authorizations that grant access right now: ASSIGNED (not
    revoked or cancelled), on a session that is not cancelled, inside the
    access window. Presence at a session never grants access by itself."""
    at = at or timezone.now()
    return SessionCoach.objects.filter(
        coach=coach,
        role=SessionCoach.Role.SUBSTITUTE,
        status=SessionCoach.Status.ASSIGNED,
    ).exclude(session__status=TrainingSession.Status.CANCELLED).filter(
        Q(access_starts_at__isnull=True) | Q(access_starts_at__lte=at),
        Q(access_ends_at__isnull=True) | Q(access_ends_at__gte=at),
    )


def regular_class_ids(coach, date=None):
    return ClassCoach.objects.active_on(date or timezone.localdate()).filter(coach=coach).values("training_class_id")


def _coach_for(user, capability):
    coach = coach_of(user)
    return coach if coach and can(user, capability) else None


def _parent_for(user, capability):
    parent = parent_of(user)
    return parent if parent and can(user, capability) else None


def _student_for(user, capability):
    student = student_of(user)
    return student if student and can(user, capability) else None


def student_view(user, broader):
    """True when the user sees a kind of data only as a student viewing their
    own record: they have a student account and none of the ``broader``
    capabilities (staff, coach or parent). Serializers use this to return the
    student-safe representation (no staff notes, family answers or finance)."""
    return student_of(user) is not None and not can(user, broader)


def student_sessions(student):
    """Sessions the student is (or was) expected at: sessions of a class they
    were a member of on the session date (the roster rule), plus any session
    where they have an attendance record."""
    member = Enrollment.objects.filter(
        student=student, training_class=OuterRef("training_class"), start_date__lte=OuterRef("date"),
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=OuterRef("date")))
    return TrainingSession.objects.filter(Q(Exists(member)) | Q(attendance__student=student)).distinct()


def children_for(user):
    """The user's own children. Used for parent-only data (e.g. finance), so it
    never widens to students the user coaches."""
    parent = parent_of(user)
    if not parent:
        return Student.objects.none()
    return Student.objects.filter(guardianships__parent=parent).distinct()


def is_parent_of(user, student):
    parent = parent_of(user)
    return bool(parent and student.guardianships.filter(parent=parent).exists())


def coach_roster_student_ids(coach, at=None):
    today = timezone.localdate()
    ids = set(
        Enrollment.objects.active_on(today)
        .filter(training_class_id__in=regular_class_ids(coach))
        .values_list("student_id", flat=True)
    )
    for slot in open_substitute_slots(coach, at).select_related("session"):
        ids.update(slot.session.roster().values_list("id", flat=True))
    return ids


# --------------------------------------------------------------------------- classes and sessions


def classes_for(user):
    """Classes whose details / timetable the user may see."""
    if can(user, Cap.CLASSES_VIEW_ALL):
        return TrainingClass.objects.all()
    q = Q(pk__in=[])
    coach = _coach_for(user, Cap.CLASSES_VIEW_ASSIGNED)
    if coach:
        # Substitute access never exposes the class itself, only the session.
        q |= Q(id__in=regular_class_ids(coach))
    parent = _parent_for(user, Cap.CLASSES_VIEW_OWN_CHILDREN)
    if parent:
        q |= Q(enrollments__student__guardianships__parent=parent)
    student = _student_for(user, Cap.CLASSES_VIEW_SELF)
    if student:
        q |= Q(enrollments__student=student)
    return TrainingClass.objects.filter(q).distinct()


def roster_classes_for(user):
    """Classes whose member list the user may see (never via a parent/student role)."""
    if can(user, Cap.ROSTER_VIEW_ALL):
        return TrainingClass.objects.all()
    coach = _coach_for(user, Cap.ROSTER_VIEW_ASSIGNED)
    if coach:
        return TrainingClass.objects.filter(id__in=regular_class_ids(coach))
    return TrainingClass.objects.none()


def sessions_for(user, at=None):
    """Sessions the user may see as a schedule entry."""
    if can(user, Cap.SESSIONS_VIEW_ALL):
        return TrainingSession.objects.all()
    q = Q(pk__in=[])
    coach = _coach_for(user, Cap.SESSIONS_VIEW_ASSIGNED)
    if coach:
        q |= Q(training_class_id__in=regular_class_ids(coach)) | Q(id__in=open_substitute_slots(coach, at).values("session_id"))
    parent = _parent_for(user, Cap.SESSIONS_VIEW_OWN_CHILDREN)
    if parent:
        q |= Q(training_class__enrollments__student__guardianships__parent=parent)
    student = _student_for(user, Cap.SESSIONS_VIEW_SELF)
    if student:
        # Only the sessions the student was expected at (never the class's
        # sessions from before they joined or after they left).
        q |= Q(id__in=student_sessions(student).values("id"))
    return TrainingSession.objects.filter(q).distinct()


def roster_sessions_for(user, at=None):
    """Sessions whose roster and attendance the user may work with as staff or
    coach. Being a parent in the class never grants this."""
    if can(user, (Cap.ROSTER_VIEW_ALL, Cap.ATTENDANCE_VIEW_ALL)):
        return TrainingSession.objects.all()
    coach = _coach_for(user, (Cap.ROSTER_VIEW_ASSIGNED, Cap.ATTENDANCE_VIEW_ASSIGNED))
    if coach:
        return TrainingSession.objects.filter(
            Q(training_class_id__in=regular_class_ids(coach)) | Q(id__in=open_substitute_slots(coach, at).values("session_id"))
        )
    return TrainingSession.objects.none()


def can_view_session(user, session, at=None):
    return sessions_for(user, at).filter(pk=session.pk).exists()


def can_take_attendance(user, session, at=None):
    """Who may record attendance for this session at all (the 48-hour edit window
    and reasons are enforced by ``apps.attendance.services``): staff with
    attendance.take_any; the class's regular coaches on the session date; an
    authorized substitute only inside their access window."""
    if can(user, Cap.ATTENDANCE_TAKE_ANY):
        return True
    coach = _coach_for(user, Cap.ATTENDANCE_TAKE_ASSIGNED)
    if not coach:
        return False
    if ClassCoach.objects.active_on(session.date).filter(coach=coach, training_class=session.training_class).exists():
        return True
    return open_substitute_slots(coach, at).filter(session=session).exists()


# --------------------------------------------------------------------------- students

FULL = "FULL"            # complete record (academy staff)
OWN = "OWN"              # own child: personal details, guardians as contacts only
SELF = "SELF"            # a student's own record: basic training profile only (no IC, contacts, family, medical)
ROSTER = "ROSTER"        # coach view: training info only (medical notes / contacts for staff only)
DIRECTORY = "DIRECTORY"  # finance view: names and guardian contacts


@dataclass
class StudentScope:
    """Which students a user may see, and at what level of detail.

    Built once per request. When several relationships apply, the most
    appropriate one wins: staff > own child > self > coach roster > directory.
    """

    full: bool = False
    directory: bool = False
    child_ids: set = field(default_factory=set)
    self_id: int | None = None
    roster_ids: set = field(default_factory=set)

    def queryset(self):
        if self.full or self.directory:
            return Student.objects.all()
        ids = self.child_ids | self.roster_ids | ({self.self_id} if self.self_id else set())
        return Student.objects.filter(id__in=ids)

    def level_for(self, student_id):
        if self.full:
            return FULL
        if student_id in self.child_ids:
            return OWN
        if student_id == self.self_id:
            return SELF
        if student_id in self.roster_ids:
            return ROSTER
        if self.directory:
            return DIRECTORY
        return None


def student_scope(user, at=None):
    scope = StudentScope(full=can(user, Cap.STUDENTS_VIEW_ALL), directory=can(user, Cap.STUDENTS_VIEW_DIRECTORY))
    if scope.full:
        return scope
    parent = _parent_for(user, Cap.STUDENTS_VIEW_OWN_CHILDREN)
    if parent:
        scope.child_ids = set(Student.objects.filter(guardianships__parent=parent).values_list("id", flat=True))
    student = _student_for(user, Cap.STUDENTS_VIEW_SELF)
    if student:
        scope.self_id = student.id
    coach = _coach_for(user, Cap.STUDENTS_VIEW_ASSIGNED)
    if coach:
        scope.roster_ids = coach_roster_student_ids(coach, at)
    return scope


def students_for(user, at=None):
    return student_scope(user, at).queryset()


def own_student_ids(user):
    """The user's children plus their own student record (for parent/student-facing data)."""
    ids = set(children_for(user).values_list("id", flat=True))
    student = student_of(user)
    if student:
        ids.add(student.id)
    return ids
