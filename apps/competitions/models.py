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
    rules = models.TextField(blank=True, help_text="Competition rules shown to parents (e.g. categories, equipment).")
    allow_parent_withdrawal = models.BooleanField(
        default=True, help_text="Parents may withdraw their own entries while registration is open. Refunds are "
                                "never automatic; exceptional refunds are a finance action.")

    # Registration form (see apps.competitions.registration_forms). Staff edit the
    # working copy (RegistrationFormField rows); publishing freezes it into
    # ``published_form`` with a new version. Parents only ever see and submit the
    # published version; registrations keep a snapshot of what they answered.
    class FormStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft (parents cannot register)"
        PUBLISHED = "PUBLISHED", "Published"

    form_status = models.CharField(max_length=10, choices=FormStatus.choices, default=FormStatus.PUBLISHED,
                                   editable=False)
    form_version = models.PositiveIntegerField(default=1, editable=False)
    form_published_at = models.DateTimeField(null=True, blank=True, editable=False)
    published_form = models.JSONField(default=list, blank=True, editable=False)
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
    # What was asked and answered, frozen at registration: [{key, label, type, value, display}].
    form_version = models.PositiveIntegerField(null=True, blank=True, editable=False)
    form_responses = models.JSONField(default=list, blank=True, editable=False)

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

    def save(self, *args, **kwargs):
        if self.pk is not None:
            previous = CompetitionRegistration.objects.filter(pk=self.pk).values(
                "form_responses", "form_version").first()
            if previous and (previous["form_responses"] != self.form_responses
                             or previous["form_version"] != self.form_version):
                raise PermissionDenied("Submitted registration form answers cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Competition registrations cannot be deleted; withdraw or reject them instead.")


class RegistrationFormField(AuditedModel):
    """One custom question in a competition's registration form (working copy).

    System fields (student, event, fee, status, payment, invoice, dates) are
    not form fields and cannot be defined here. Editing these rows never
    changes the published form or past answers: publishing freezes a copy.
    """

    audit_category = AuditCategory.COMPETITION

    class FieldType(models.TextChoices):
        TEXT = "TEXT", "Short text"
        LONG_TEXT = "LONG_TEXT", "Long text"
        NUMBER = "NUMBER", "Number"
        DATE = "DATE", "Date"
        SINGLE_SELECT = "SINGLE_SELECT", "Single choice"
        MULTI_SELECT = "MULTI_SELECT", "Multiple choice"
        YES_NO = "YES_NO", "Yes / No"
        EMAIL = "EMAIL", "Email"
        PHONE = "PHONE", "Phone"

    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name="form_fields")
    key = models.CharField(max_length=40, help_text="Stable identifier, e.g. shirt_size (lowercase, digits, _).")
    label = models.CharField(max_length=120)
    field_type = models.CharField(max_length=15, choices=FieldType.choices)
    required = models.BooleanField(default=False)
    help_text = models.CharField(max_length=255, blank=True)
    placeholder = models.CharField(max_length=100, blank=True)
    options = models.JSONField(default=list, blank=True, help_text="Choices for single / multiple choice fields.")
    max_length = models.PositiveIntegerField(null=True, blank=True, help_text="Text fields only.")
    min_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["competition", "order", "id"]
        constraints = [models.UniqueConstraint(fields=["competition", "key"], name="unique_form_field_key")]

    def __str__(self):
        return f"{self.competition.name}: {self.label}"

    def clean(self):
        from .registration_forms import validate_field_definition

        validate_field_definition(self)


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
