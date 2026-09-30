from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.academy.models import Student
from apps.audit.models import AuditCategory, AuditedModel


class Competition(AuditedModel):
    audit_category = AuditCategory.COMPETITION

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft (hidden from parents)"
        OPEN = "OPEN", "Open for registration"
        CLOSED = "CLOSED", "Registration closed"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    name = models.CharField(max_length=200)
    organiser = models.CharField(max_length=200, blank=True)
    venue = models.CharField(max_length=255, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    registration_deadline = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    allow_parent_registration = models.BooleanField(default=True)
    max_events_per_student = models.PositiveIntegerField(null=True, blank=True)
    age_reference_date = models.DateField(
        null=True, blank=True, help_text="Date used to calculate age for age groups (defaults to the start date)."
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.name} ({self.start_date:%Y})"

    def clean(self):
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError("End date cannot be before start date.")

    def is_open_for_registration(self, today=None):
        today = today or timezone.localdate()
        return self.status == self.Status.OPEN and today <= self.registration_deadline


class EventType(models.TextChoices):
    CHANGQUAN = "CHANGQUAN", "Changquan"
    NANQUAN = "NANQUAN", "Nanquan"
    TAIJIQUAN = "TAIJIQUAN", "Taijiquan"
    JIANSHU = "JIANSHU", "Jianshu"
    DAOSHU = "DAOSHU", "Daoshu"
    GUNSHU = "GUNSHU", "Gunshu"
    QIANGSHU = "QIANGSHU", "Qiangshu"
    NANDAO = "NANDAO", "Nandao"
    NANGUN = "NANGUN", "Nangun"
    TAIJIJIAN = "TAIJIJIAN", "Taijijian"
    SANDA = "SANDA", "Sanda"
    DUILIAN = "DUILIAN", "Duilian"
    GROUP = "GROUP", "Group set"
    OTHER = "OTHER", "Other"


class CompetitionEvent(AuditedModel):
    audit_category = AuditCategory.COMPETITION

    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        OPEN = "OPEN", "Open / mixed"

    competition = models.ForeignKey(Competition, on_delete=models.PROTECT, related_name="events")
    event_type = models.CharField(max_length=10, choices=EventType.choices)
    name = models.CharField(max_length=150, help_text='e.g. "Changquan – Boys U12"')
    gender = models.CharField(max_length=4, choices=Gender.choices, default=Gender.OPEN)
    min_age = models.PositiveIntegerField(null=True, blank=True)
    max_age = models.PositiveIntegerField(null=True, blank=True)
    weight_class = models.CharField(max_length=50, blank=True, help_text="For Sanda.")
    fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    max_entries = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["competition", "event_type", "name"]
        constraints = [models.UniqueConstraint(fields=["competition", "name"], name="unique_event_name")]

    def __str__(self):
        return f"{self.competition.name}: {self.name}"

    def active_registrations(self):
        return self.registrations.exclude(status__in=CompetitionRegistration.INACTIVE)

    def eligibility_errors(self, student):
        errors = []
        if self.gender != self.Gender.OPEN and student.gender != self.gender:
            errors.append(f"{self.name} is for {self.get_gender_display().lower()} athletes only.")
        age = student.age_on(self.competition.age_reference_date or self.competition.start_date)
        if self.min_age is not None and age < self.min_age:
            errors.append(f"{student.full_name} is too young for {self.name} (age {age}).")
        if self.max_age is not None and age > self.max_age:
            errors.append(f"{student.full_name} is too old for {self.name} (age {age}).")
        return errors


class RegistrationQuerySet(models.QuerySet):
    def delete(self):
        raise PermissionDenied("Competition registrations cannot be deleted; withdraw or reject them instead.")


class CompetitionRegistration(AuditedModel):
    audit_category = AuditCategory.COMPETITION

    objects = RegistrationQuerySet.as_manager()

    class Status(models.TextChoices):
        PENDING = "PENDING", "Awaiting payment"
        CONFIRMED = "CONFIRMED", "Confirmed"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        REJECTED = "REJECTED", "Rejected"

    INACTIVE = (Status.WITHDRAWN, Status.REJECTED)

    event = models.ForeignKey(CompetitionEvent, on_delete=models.PROTECT, related_name="registrations")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="competition_registrations")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    registered_at = models.DateTimeField(auto_now_add=True)
    charge = models.OneToOneField(
        "finance.Charge", null=True, blank=True, on_delete=models.PROTECT, related_name="competition_registration"
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["event", "student__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "student"],
                condition=~Q(status__in=["WITHDRAWN", "REJECTED"]),
                name="one_active_registration_per_event",
            )
        ]

    def __str__(self):
        return f"{self.student.full_name} – {self.event}"

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Competition registrations cannot be deleted; withdraw or reject them instead.")


class ResultQuerySet(models.QuerySet):
    """Bulk writes would skip the confirmed-registration rule."""

    def update(self, **kwargs):
        raise PermissionDenied("Competition results are recorded one at a time through the results service.")

    def bulk_create(self, *args, **kwargs):
        raise PermissionDenied("Competition results are recorded one at a time through the results service.")

    def bulk_update(self, *args, **kwargs):
        raise PermissionDenied("Competition results are recorded one at a time through the results service.")

    def delete(self):
        raise PermissionDenied("Competition results are history and cannot be deleted.")


class CompetitionResult(AuditedModel):
    """A result for one CONFIRMED (paid) registration. Enforced here, in the
    results service, the API and admin forms, and by a PostgreSQL trigger."""

    audit_category = AuditCategory.COMPETITION

    class Medal(models.TextChoices):
        GOLD = "GOLD", "Gold"
        SILVER = "SILVER", "Silver"
        BRONZE = "BRONZE", "Bronze"
        NONE = "NONE", "No medal"

    registration = models.OneToOneField(CompetitionRegistration, on_delete=models.PROTECT, related_name="result")
    placing = models.PositiveIntegerField(null=True, blank=True)
    medal = models.CharField(max_length=6, choices=Medal.choices, default=Medal.NONE)
    score = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    remarks = models.CharField(max_length=255, blank=True)
    recorded_at = models.DateTimeField(auto_now=True)

    objects = ResultQuerySet.as_manager()

    class Meta:
        ordering = ["registration__event", "placing"]

    def __str__(self):
        return f"{self.registration}: {self.get_medal_display()}"

    def clean(self):
        if self.pk is not None:
            old = CompetitionResult.objects.filter(pk=self.pk).values_list("registration_id", flat=True).first()
            if old is not None and old != self.registration_id:
                raise ValidationError("A result cannot be moved to another registration.")
        registration = CompetitionRegistration.objects.filter(pk=self.registration_id).first()
        if registration is None or registration.status != CompetitionRegistration.Status.CONFIRMED:
            status = registration.get_status_display().lower() if registration else "missing"
            raise ValidationError(f"Results can only be recorded for a confirmed (paid) registration; "
                                  f"this registration is {status}.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Competition results are history and cannot be deleted.")
