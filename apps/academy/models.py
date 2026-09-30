import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import Coach, Parent
from apps.audit.models import AuditCategory, AuditedModel


class Program(models.Model):
    """Discipline taught at the academy: Wushu Taolu, Sanda, Taiji, Changquan, ..."""

    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Team(models.Model):
    name = models.CharField(max_length=100, unique=True)
    program = models.ForeignKey(Program, null=True, blank=True, on_delete=models.PROTECT)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TrainingClass(AuditedModel):
    audit_category = AuditCategory.CLASS

    class Category(models.TextChoices):
        SCHOOL = "SCHOOL", "School"
        ADDITIONAL = "ADDITIONAL", "Additional"
        ELITE = "ELITE", "Elite"

    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=12, choices=Category.choices)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="classes")
    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.PROTECT, related_name="classes")
    venue = models.CharField(max_length=200, blank=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    coaches = models.ManyToManyField(Coach, through="ClassCoach", related_name="classes")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "class"
        verbose_name_plural = "classes"

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"

    def coaches_on(self, date):
        return Coach.objects.filter(
            class_assignments__in=ClassCoach.objects.active_on(date).filter(training_class=self)
        ).distinct()


class ClassSchedule(models.Model):
    """Weekly timetable slot. A class may train several times per week."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0
        TUESDAY = 1
        WEDNESDAY = 2
        THURSDAY = 3
        FRIDAY = 4
        SATURDAY = 5
        SUNDAY = 6

    training_class = models.ForeignKey(TrainingClass, on_delete=models.CASCADE, related_name="schedules")
    weekday = models.IntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    venue = models.CharField(max_length=200, blank=True, help_text="Leave empty to use the class venue.")
    effective_from = models.DateField(default=datetime.date.today)
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["training_class", "weekday", "start_time"]

    def __str__(self):
        return f"{self.training_class.name} {self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"

    def clean(self):
        if self.end_time and self.start_time and self.end_time <= self.start_time:
            raise ValidationError("End time must be after start time.")

    def applies_on(self, date):
        return (
            date.weekday() == self.weekday
            and self.effective_from <= date
            and (self.effective_to is None or date <= self.effective_to)
        )


class DatedQuerySet(models.QuerySet):
    """Rows with start_date / end_date (end_date empty = still current)."""

    def active_on(self, date=None):
        date = date or timezone.localdate()
        return self.filter(start_date__lte=date).filter(Q(end_date__isnull=True) | Q(end_date__gte=date))

    def overlapping(self, start, end):
        return self.filter(start_date__lte=end).filter(Q(end_date__isnull=True) | Q(end_date__gte=start))


class ClassCoach(AuditedModel):
    """Coach-to-class assignment. Ended assignments are kept for history."""

    audit_category = AuditCategory.CLASS

    class Role(models.TextChoices):
        HEAD = "HEAD", "Head coach"
        ASSISTANT = "ASSISTANT", "Assistant coach"

    training_class = models.ForeignKey(TrainingClass, on_delete=models.PROTECT, related_name="coach_assignments")
    coach = models.ForeignKey(Coach, on_delete=models.PROTECT, related_name="class_assignments")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.HEAD)
    start_date = models.DateField(default=datetime.date.today)
    end_date = models.DateField(null=True, blank=True)

    objects = DatedQuerySet.as_manager()

    class Meta:
        ordering = ["training_class", "-start_date"]
        verbose_name = "class coach"
        constraints = [
            models.UniqueConstraint(
                fields=["training_class", "coach"],
                condition=Q(end_date__isnull=True),
                name="one_open_assignment_per_coach_class",
            )
        ]

    def __str__(self):
        return f"{self.coach} → {self.training_class.name}"

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("End date cannot be before start date.")


class Family(AuditedModel):
    """A household of students (siblings) that is invoiced together.

    Family membership is assigned explicitly by staff (``Student.family``). It is
    never inferred from shared parents: two children of the same parent may live
    in different households. There is deliberately no "bill-to" or billing-parent
    concept: invoices are academy documents for the students of a family.
    """

    audit_category = AuditCategory.STUDENT

    name = models.CharField(max_length=200, help_text='Display name, e.g. "Tan family (Ali & Mei)".')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]
        verbose_name_plural = "families"

    def __str__(self):
        return f"{self.name} (#{self.pk})"

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Families cannot be deleted; move the students to another family instead.")


class Student(AuditedModel):
    """Student record. Records are never deleted; set a status instead.

    Every change to personal information is kept in the audit log, and status
    changes are additionally kept in StudentStatusHistory.
    """

    audit_category = AuditCategory.STUDENT

    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ON_LEAVE = "ON_LEAVE", "On leave"
        SUSPENDED = "SUSPENDED", "Suspended"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        GRADUATED = "GRADUATED", "Graduated"

    student_no = models.CharField("student no.", max_length=20, unique=True)
    full_name = models.CharField(max_length=200)
    chinese_name = models.CharField(max_length=100, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices)
    date_of_birth = models.DateField()
    ic_number = models.CharField("IC / MyKid / passport no.", max_length=30, blank=True)
    nationality = models.CharField(max_length=60, default="Malaysian")
    school = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    medical_notes = models.TextField(blank=True, help_text="Allergies, injuries, conditions coaches must know.")
    join_date = models.DateField(default=datetime.date.today)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    family = models.ForeignKey(
        Family, on_delete=models.PROTECT, related_name="students", blank=True,
        help_text="Siblings who are invoiced together share a family. Leave empty to create a new family.",
    )
    parents = models.ManyToManyField(Parent, through="Guardianship", related_name="children")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} [{self.student_no}]"

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Students cannot be deleted; change their status to Withdrawn instead.")

    def age_on(self, date):
        dob = self.date_of_birth
        return date.year - dob.year - ((date.month, date.day) < (dob.month, dob.day))

    def save(self, *args, **kwargs):
        if self.family_id is None:
            # Safe default: a student is their own household until staff group siblings.
            self.family = Family.objects.create(name=f"{self.full_name} family")
        previous_status = None
        if self.pk is not None:
            previous_status = Student.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        super().save(*args, **kwargs)
        if previous_status != self.status:
            StudentStatusHistory.objects.create(
                student=self,
                status=self.status,
                previous_status=previous_status or "",
                effective_date=timezone.localdate(),
            )

    def current_enrollments(self, date=None):
        return self.enrollments.active_on(date).select_related("training_class", "team", "coach")


class StudentStatusHistory(models.Model):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="status_history")
    previous_status = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=10, choices=Student.Status.choices)
    effective_date = models.DateField()
    reason = models.TextField(blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        verbose_name_plural = "student status history"

    def __str__(self):
        return f"{self.student}: {self.previous_status or '-'} → {self.status}"


class StudentAccount(AuditedModel):
    """Links a login to one student (for older students, e.g. Elite / senior).

    One user has at most one student account and one student has at most one
    login. Linking alone grants nothing: the user must also hold the STUDENT
    role (assigned by a super admin) to see their own record.
    """

    audit_category = AuditCategory.STUDENT

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="student_account")
    student = models.OneToOneField(Student, on_delete=models.PROTECT, related_name="account")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "student login"

    def __str__(self):
        return f"{self.user} → {self.student}"


class Guardianship(AuditedModel):
    """Links parents and students (many-to-many)."""

    audit_category = AuditCategory.STUDENT

    class Relationship(models.TextChoices):
        FATHER = "FATHER", "Father"
        MOTHER = "MOTHER", "Mother"
        GUARDIAN = "GUARDIAN", "Guardian"
        GRANDPARENT = "GRANDPARENT", "Grandparent"
        OTHER = "OTHER", "Other"

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="guardianships")
    parent = models.ForeignKey(Parent, on_delete=models.PROTECT, related_name="guardianships")
    relationship = models.CharField(max_length=12, choices=Relationship.choices)
    is_primary_contact = models.BooleanField(default=False)
    is_emergency_contact = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["student", "parent"], name="unique_guardianship")]

    def __str__(self):
        return f"{self.parent.full_name} ({self.get_relationship_display()}) of {self.student.full_name}"


class Enrollment(AuditedModel):
    """Class membership. Ending a membership sets end_date; rows are never removed."""

    audit_category = AuditCategory.CLASS

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="enrollments")
    training_class = models.ForeignKey(TrainingClass, on_delete=models.PROTECT, related_name="enrollments")
    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.PROTECT, related_name="enrollments")
    coach = models.ForeignKey(
        Coach, null=True, blank=True, on_delete=models.PROTECT, related_name="personal_students",
        help_text="Optional personal/primary coach for this student in this class.",
    )
    start_date = models.DateField(default=datetime.date.today)
    end_date = models.DateField(null=True, blank=True)
    end_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = DatedQuerySet.as_manager()

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "training_class"],
                condition=Q(end_date__isnull=True),
                name="one_open_enrollment_per_class",
            )
        ]

    def __str__(self):
        return f"{self.student.full_name} in {self.training_class.name}"

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError("End date cannot be before start date.")

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Class membership history cannot be deleted; set an end date instead.")


class TrainingSession(AuditedModel):
    """A single training occurrence of a class on a given date."""

    audit_category = AuditCategory.CLASS

    class Status(models.TextChoices):
        SCHEDULED = "SCHEDULED", "Scheduled"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    training_class = models.ForeignKey(TrainingClass, on_delete=models.PROTECT, related_name="sessions")
    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    venue = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCHEDULED)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-start_time"]
        constraints = [
            models.UniqueConstraint(fields=["training_class", "date", "start_time"], name="unique_session_slot")
        ]

    def __str__(self):
        return f"{self.training_class.name} {self.date:%Y-%m-%d} {self.start_time:%H:%M}"

    def clean(self):
        if self.end_time and self.start_time and self.end_time <= self.start_time:
            raise ValidationError("End time must be after start time.")

    # Always the academy's time zone (settings.TIME_ZONE), never the server's or
    # a per-request override: access windows and edit deadlines must not move.
    @property
    def starts_at(self):
        return timezone.make_aware(datetime.datetime.combine(self.date, self.start_time),
                                   timezone.get_default_timezone())

    @property
    def ends_at(self):
        return timezone.make_aware(datetime.datetime.combine(self.date, self.end_time),
                                   timezone.get_default_timezone())

    @property
    def duration_hours(self):
        seconds = (self.ends_at - self.starts_at).total_seconds()
        return (Decimal(seconds) / Decimal(3600)).quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        with transaction.atomic():
            previous = None
            if self.pk is not None:
                previous = TrainingSession.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            super().save(*args, **kwargs)
            if self.status == self.Status.CANCELLED and previous != self.Status.CANCELLED:
                # Whichever path cancelled the session (API, admin, service), no
                # substitute authorization may stay active on it.
                SessionCoach.cancel_substitutes_for(self)

    def roster(self):
        """Students who were members of the class on the session date."""
        return Student.objects.filter(
            enrollments__in=Enrollment.objects.active_on(self.date).filter(training_class=self.training_class)
        ).distinct().order_by("full_name")


class SessionCoachQuerySet(models.QuerySet):
    def delete(self):
        if self.filter(role=SessionCoach.Role.SUBSTITUTE).exists():
            raise PermissionDenied("Substitute authorizations are history and cannot be deleted; revoke them instead.")
        return super().delete()

    def substitutes(self):
        return self.filter(role=SessionCoach.Role.SUBSTITUTE)

    def active_substitutes(self):
        return self.filter(role=SessionCoach.Role.SUBSTITUTE, status=SessionCoach.Status.ASSIGNED)


class SessionCoach(AuditedModel):
    """Who coaches a particular session.

    Regular coaches are copied from the class when sessions are generated.

    A SUBSTITUTE row is a temporary authorization: the substitute coach may see
    this one session, its roster and its attendance, only while the row is
    ASSIGNED and only between access_starts_at and access_ends_at. It never
    grants the class. Lifecycle (``apps.academy.services``)::

        ASSIGNED ──revoke_substitute──▶ REVOKED     (final)
            └──────session cancelled──▶ CANCELLED   (final)

    Rows are never deleted or reactivated; authorizing the same coach again
    creates a new row, so every authorization keeps its own history.
    """

    audit_category = AuditCategory.ACCESS

    class Role(models.TextChoices):
        REGULAR = "REGULAR", "Regular coach"
        SUBSTITUTE = "SUBSTITUTE", "Substitute coach"

    class Status(models.TextChoices):
        ASSIGNED = "ASSIGNED", "Assigned"  # a substitute's active authorization
        REPLACED = "REPLACED", "Replaced by substitute"  # regular coach only
        ABSENT = "ABSENT", "Did not attend"  # regular coach only
        REVOKED = "REVOKED", "Substitute authorization revoked"
        CANCELLED = "CANCELLED", "Session cancelled"

    ENDED = (Status.REVOKED, Status.CANCELLED)
    REGULAR_STATUSES = (Status.ASSIGNED, Status.REPLACED, Status.ABSENT)
    # Fixed once a substitute is authorized (who, which session, for whom, when, why).
    SUBSTITUTE_FROZEN = ("session_id", "coach_id", "role", "replaces_id", "access_starts_at", "access_ends_at",
                         "authorized_at", "reason")
    ENDED_FROZEN = ("status", "revoked_at", "revocation_reason")

    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name="coach_slots")
    coach = models.ForeignKey(Coach, on_delete=models.PROTECT, related_name="session_slots")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.REGULAR)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ASSIGNED)
    replaces = models.ForeignKey(
        Coach, null=True, blank=True, on_delete=models.PROTECT, related_name="replaced_in_slots",
        help_text="The original coach this substitute covers for.",
    )
    access_starts_at = models.DateTimeField(null=True, blank=True)
    access_ends_at = models.DateTimeField(null=True, blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Who authorized the substitute (or generated the regular slot).",
    )
    authorized_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True, help_text="Why the substitute was needed.")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    revocation_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SessionCoachQuerySet.as_manager()

    class Meta:
        ordering = ["session", "role", "id"]
        constraints = [
            # One live slot per coach per session; revoked/cancelled rows are history.
            models.UniqueConstraint(
                fields=["session", "coach"], condition=~Q(status__in=["REVOKED", "CANCELLED"]),
                name="unique_live_coach_per_session",
            ),
            # One-substitute-per-session rule, enforced by the database.
            models.UniqueConstraint(
                fields=["session"], condition=Q(role="SUBSTITUTE", status="ASSIGNED"),
                name="one_active_substitute_per_session",
            ),
            models.CheckConstraint(
                condition=Q(role="SUBSTITUTE") | Q(status__in=["ASSIGNED", "REPLACED", "ABSENT"]),
                name="regular_slot_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(role="REGULAR") | Q(status__in=["ASSIGNED", "REVOKED", "CANCELLED"]),
                name="substitute_status_valid",
            ),
            models.CheckConstraint(
                condition=~Q(status__in=["REVOKED", "CANCELLED"]) | Q(revoked_at__isnull=False),
                name="ended_substitute_has_revoked_at",
            ),
            models.CheckConstraint(
                condition=Q(access_starts_at__isnull=True) | Q(access_ends_at__isnull=True)
                | Q(access_starts_at__lt=models.F("access_ends_at")),
                name="substitute_window_valid",
            ),
        ]

    def __str__(self):
        return f"{self.coach} – {self.session} ({self.get_role_display()})"

    @property
    def is_substitute(self):
        return self.role == self.Role.SUBSTITUTE

    @property
    def is_active_substitute(self):
        return self.is_substitute and self.status == self.Status.ASSIGNED

    @property
    def is_paid(self):
        return self.status == self.Status.ASSIGNED and self.session.status != TrainingSession.Status.CANCELLED

    def access_open(self, at=None):
        at = at or timezone.now()
        if self.status != self.Status.ASSIGNED or self.session.status == TrainingSession.Status.CANCELLED:
            return False
        return (self.access_starts_at is None or self.access_starts_at <= at) and (
            self.access_ends_at is None or at <= self.access_ends_at
        )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            old = SessionCoach.objects.filter(pk=self.pk).first()
            if old is not None:
                self._check_transition(old)
        super().save(*args, **kwargs)

    def _check_transition(self, old):
        """Model-level guard; the same rules are enforced by a PostgreSQL trigger."""
        if old.role != self.role:
            raise ValidationError("A coach slot cannot change between regular and substitute.")
        if old.role != self.Role.SUBSTITUTE:
            return
        if any(getattr(old, f) != getattr(self, f) for f in self.SUBSTITUTE_FROZEN):
            raise ValidationError("A substitute authorization cannot be edited; revoke it and authorize again.")
        if old.status in self.ENDED:
            if any(getattr(old, f) != getattr(self, f) for f in self.ENDED_FROZEN):
                raise ValidationError("A revoked or cancelled substitute authorization is final.")
        elif self.status not in (self.Status.ASSIGNED, *self.ENDED):
            raise ValidationError(f"Invalid substitute status: {self.status}")

    def delete(self, *args, **kwargs):
        if self.is_substitute:
            raise PermissionDenied("Substitute authorizations are history and cannot be deleted; revoke them instead.")
        return super().delete(*args, **kwargs)

    @classmethod
    def cancel_substitutes_for(cls, session):
        """End every active substitute authorization of a cancelled session and
        give the replaced coaches their slots back. History is kept."""
        from apps.audit.context import get_actor

        actor = get_actor()
        now = timezone.now()
        for slot in cls.objects.active_substitutes().filter(session=session).select_for_update():
            slot.status = cls.Status.CANCELLED
            slot.revoked_at = now
            slot.revoked_by = actor
            slot.revocation_reason = "Session cancelled"
            slot.save()
            slot.restore_replaced_coach()

    def restore_replaced_coach(self):
        if not self.replaces_id:
            return
        original = SessionCoach.objects.filter(
            session_id=self.session_id, coach_id=self.replaces_id, role=self.Role.REGULAR,
            status=self.Status.REPLACED,
        ).first()
        if original is not None:
            original.status = self.Status.ASSIGNED
            original.save()
