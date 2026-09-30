import calendar
import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.academy.models import TrainingClass, TrainingSession
from apps.accounts.models import Coach
from apps.audit.models import AuditCategory, AuditedModel

money = {"max_digits": 10, "decimal_places": 2}


class CoachRate(AuditedModel):
    """A coach's per-session pay rate, valid for a date range.

    All coach fees are per session (optionally per class). There is no monthly
    salary and no hourly pay. Rate resolution for each paid session is in
    ``apps.payroll.services.resolve_rate``:

    * regular session: class per-session rate, else the coach's general
      per-session rate;
    * substitute session: class substitute rate, else general substitute rate,
      else (no substitute rate configured) the class, then general, per-session rate;
    * nothing found: the line is flagged MISSING_RATE and payroll cannot be finalized.

    HOURLY, MONTHLY and SUBSTITUTE_HOURLY rows from before Phase 4 are kept as
    history but are never used and cannot be created.
    """

    audit_category = AuditCategory.PAYROLL

    class RateType(models.TextChoices):
        PER_SESSION = "PER_SESSION", "Per session"
        SUBSTITUTE_PER_SESSION = "SUBSTITUTE_PER_SESSION", "Substitute – per session"
        HOURLY = "HOURLY", "Hourly (legacy – not used)"
        MONTHLY = "MONTHLY", "Monthly (legacy – not used)"
        SUBSTITUTE_HOURLY = "SUBSTITUTE_HOURLY", "Substitute – hourly (legacy – not used)"

    ACTIVE_TYPES = (RateType.PER_SESSION, RateType.SUBSTITUTE_PER_SESSION)
    PRICING_FIELDS = ("coach_id", "rate_type", "amount", "training_class_id", "effective_from")

    coach = models.ForeignKey(Coach, on_delete=models.PROTECT, related_name="rates")
    rate_type = models.CharField(max_length=25, choices=RateType.choices)
    amount = models.DecimalField(**money, validators=[MinValueValidator(Decimal("0"))])
    training_class = models.ForeignKey(
        TrainingClass, null=True, blank=True, on_delete=models.PROTECT,
        help_text="Leave empty for the coach's general rate.",
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["coach", "rate_type", "-effective_from"]
        constraints = [models.CheckConstraint(condition=Q(amount__gte=0), name="coach_rate_not_negative")]

    def __str__(self):
        scope = f" [{self.training_class.name}]" if self.training_class_id else ""
        return f"{self.coach}: {self.get_rate_type_display()} RM {self.amount}{scope}"

    def clean(self):
        old = CoachRate.objects.filter(pk=self.pk).first() if self.pk else None
        pricing_changed = old is None or any(getattr(old, f) != getattr(self, f) for f in self.PRICING_FIELDS)
        if self.effective_to and self.effective_from and self.effective_to < self.effective_from:
            raise ValidationError("Effective-to cannot be before effective-from.")
        if old is not None and old.rate_type not in self.ACTIVE_TYPES and pricing_changed:
            raise ValidationError("A legacy rate is kept as history only; add a per-session rate instead.")
        if not pricing_changed:
            return
        if self.rate_type not in self.ACTIVE_TYPES:
            raise ValidationError("Coaches are paid per session: use a per-session or substitute per-session rate.")
        if old is not None and old.paid_lines().filter(payslip__run__status=PayrollRun.Status.FINALIZED).exists():
            raise ValidationError("This rate was used in a finalized payroll; end it (effective-to) and add a new rate.")
        overlapping = CoachRate.objects.filter(
            coach_id=self.coach_id, rate_type=self.rate_type, training_class_id=self.training_class_id,
            effective_from__lte=self.effective_to or datetime.date.max,
        ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from)).exclude(pk=self.pk)
        if overlapping.exists():
            raise ValidationError("Another rate of this type for this coach and class covers part of these dates; "
                                  "end it first so the rate for every session is unambiguous.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def paid_lines(self):
        return PayslipLine.objects.filter(rate_source=self)

    @classmethod
    def effective_on(cls, date):
        return cls.objects.filter(effective_from__lte=date).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=date))


class PayrollRun(AuditedModel):
    """One payroll period (a calendar month).

    DRAFT ──calculate──▶ READY ──finalize (SUPER_ADMIN)──▶ FINALIZED (final)
      ▲                    │
      └──── calculated with unresolved issues (e.g. a missing rate) stays DRAFT

    FINANCE_ADMIN prepares and calculates; only SUPER_ADMIN finalizes. A
    finalized run, its payslips and lines can never change (model guards and a
    PostgreSQL trigger)."""

    audit_category = AuditCategory.PAYROLL

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        READY = "READY", "Calculated – ready for approval"
        FINALIZED = "FINALIZED", "Finalized"

    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    calculated_at = models.DateTimeField(null=True, blank=True)
    calculated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+")
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    excluded = models.JSONField(default=list, blank=True,
                                help_text="Coach sessions in the period that were not paid, and why.")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(fields=["year", "month"], name="unique_payroll_month"),
            models.CheckConstraint(condition=Q(month__gte=1, month__lte=12), name="payroll_month_valid"),
            models.CheckConstraint(condition=~Q(status="FINALIZED") | Q(finalized_at__isnull=False),
                                   name="finalized_payroll_has_timestamp"),
        ]

    def __str__(self):
        return f"Payroll {self.year}-{self.month:02d} ({self.get_status_display()})"

    @property
    def is_locked(self):
        return self.status == self.Status.FINALIZED

    @property
    def period_start(self):
        return datetime.date(self.year, self.month, 1)

    @property
    def period_end(self):
        return datetime.date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    @property
    def issue_count(self):
        return PayslipLine.objects.filter(payslip__run=self).exclude(issue="").count()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            old = PayrollRun.objects.filter(pk=self.pk).first()
            if old is not None:
                if (old.year, old.month) != (self.year, self.month):
                    raise ValidationError("A payroll period cannot be moved to another month.")
                if old.is_locked and any(getattr(old, f.attname) != getattr(self, f.attname)
                                         for f in self._meta.concrete_fields if f.name != "notes"):
                    raise PermissionDenied("A finalized payroll cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.is_locked:
            raise PermissionDenied("A finalized payroll cannot be deleted.")
        super().delete(*args, **kwargs)


def _run_locked(year, month):
    return PayrollRun.objects.filter(year=year, month=month, status=PayrollRun.Status.FINALIZED).exists()


class PayrollAdjustment(AuditedModel):
    """Allowances, bonuses and deductions for a coach in a given month."""

    audit_category = AuditCategory.PAYROLL

    class Kind(models.TextChoices):
        ALLOWANCE = "ALLOWANCE", "Allowance"
        BONUS = "BONUS", "Bonus"
        DEDUCTION = "DEDUCTION", "Deduction"

    coach = models.ForeignKey(Coach, on_delete=models.PROTECT, related_name="payroll_adjustments")
    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    kind = models.CharField(max_length=10, choices=Kind.choices)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(**money, validators=[MinValueValidator(Decimal("0.01"))],
                                 help_text="Always positive; deductions are subtracted.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year", "-month", "coach"]

    def __str__(self):
        return f"{self.coach} {self.year}-{self.month:02d} {self.get_kind_display()} RM {self.amount}"

    def save(self, *args, **kwargs):
        if _run_locked(self.year, self.month):
            raise ValidationError("Payroll for this month is finalized; add the adjustment to a later month.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if _run_locked(self.year, self.month):
            raise PermissionDenied("Payroll for this month is finalized.")
        super().delete(*args, **kwargs)


class PayrollHistoryQuerySet(models.QuerySet):
    """Bulk writes skip the finalized-payroll guards."""

    def update(self, **kwargs):
        raise PermissionDenied("Payslips change only by recalculating a draft payroll.")

    def bulk_create(self, *args, **kwargs):
        raise PermissionDenied("Payslips are created only by the payroll calculation.")

    def bulk_update(self, *args, **kwargs):
        raise PermissionDenied("Payslips change only by recalculating a draft payroll.")


class Payslip(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    coach = models.ForeignKey(Coach, on_delete=models.PROTECT, related_name="payslips")
    gross_pay = models.DecimalField(**money, default=Decimal("0"))
    total_deductions = models.DecimalField(**money, default=Decimal("0"))
    net_pay = models.DecimalField(**money, default=Decimal("0"))
    regular_sessions = models.PositiveIntegerField(default=0)
    substitute_sessions = models.PositiveIntegerField(default=0)
    hours = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"),
                                help_text="For information only: pay is per session.")

    objects = PayrollHistoryQuerySet.as_manager()

    class Meta:
        ordering = ["run", "coach__full_name"]
        constraints = [models.UniqueConstraint(fields=["run", "coach"], name="unique_payslip")]

    def __str__(self):
        return f"{self.coach} – {self.run.year}-{self.run.month:02d}: RM {self.net_pay}"

    def save(self, *args, **kwargs):
        if PayrollRun.objects.filter(pk=self.run_id, status=PayrollRun.Status.FINALIZED).exists():
            raise PermissionDenied("Payslips of a finalized payroll cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if PayrollRun.objects.filter(pk=self.run_id, status=PayrollRun.Status.FINALIZED).exists():
            raise PermissionDenied("Payslips of a finalized payroll cannot be deleted.")
        super().delete(*args, **kwargs)


class PayslipLine(models.Model):
    """One paid session (or adjustment). A session line records the assignment
    that was paid (``slot``: the coach, the session, regular or substitute, and
    for a substitute the original coach), the rate row used, the rule that chose
    it and the amount. ``issue`` flags a line that blocks finalization."""

    class Kind(models.TextChoices):
        REGULAR_SESSION = "REGULAR_SESSION", "Regular session"
        SUBSTITUTE_SESSION = "SUBSTITUTE_SESSION", "Substitute session"
        MONTHLY = "MONTHLY", "Monthly salary (legacy)"
        ALLOWANCE = "ALLOWANCE", "Allowance"
        BONUS = "BONUS", "Bonus"
        DEDUCTION = "DEDUCTION", "Deduction"

    class Issue(models.TextChoices):
        NONE = "", "–"
        MISSING_RATE = "MISSING_RATE", "No per-session rate for this coach and session"

    SESSION_KINDS = (Kind.REGULAR_SESSION, Kind.SUBSTITUTE_SESSION)

    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    description = models.CharField(max_length=255)
    session = models.ForeignKey(TrainingSession, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    slot = models.ForeignKey("academy.SessionCoach", null=True, blank=True, on_delete=models.PROTECT,
                             related_name="payslip_lines", help_text="The coach assignment that was paid.")
    rate_source = models.ForeignKey(CoachRate, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    rule = models.CharField(max_length=40, blank=True, help_text="Which rate rule priced this line.")
    issue = models.CharField(max_length=20, choices=Issue.choices, blank=True, default="")
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("1"))
    rate = models.DecimalField(**money, default=Decimal("0"))
    amount = models.DecimalField(**money, help_text="Negative for deductions.")

    objects = PayrollHistoryQuerySet.as_manager()

    class Meta:
        ordering = ["payslip", "kind", "id"]
        constraints = [
            # A coach assignment is paid at most once, in one payroll period.
            models.UniqueConstraint(fields=["slot"], condition=Q(slot__isnull=False), name="assignment_paid_once"),
            models.UniqueConstraint(fields=["payslip", "session"], condition=Q(session__isnull=False),
                                    name="one_line_per_session_per_payslip"),
        ]

    def __str__(self):
        return f"{self.description}: RM {self.amount}"

    def save(self, *args, **kwargs):
        if PayrollRun.objects.filter(payslips__pk=self.payslip_id, status=PayrollRun.Status.FINALIZED).exists():
            raise PermissionDenied("Payslips of a finalized payroll cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if PayrollRun.objects.filter(payslips__pk=self.payslip_id, status=PayrollRun.Status.FINALIZED).exists():
            raise PermissionDenied("Payslips of a finalized payroll cannot be deleted.")
        super().delete(*args, **kwargs)
