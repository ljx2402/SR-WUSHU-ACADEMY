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
    """Pay rate for a coach, valid for a date range.

    Rate precedence for a regular session:
      1. class-specific per-session rate, 2. class-specific hourly rate,
      3. monthly salary (session is covered, no extra pay),
      4. general per-session rate, 5. general hourly rate.
    Substitute sessions use the substitute rates first (class-specific, then
    general), then fall back to the per-session / hourly rates above
    (a monthly salary never covers substitute work).
    """

    audit_category = AuditCategory.PAYROLL

    class RateType(models.TextChoices):
        HOURLY = "HOURLY", "Hourly"
        PER_SESSION = "PER_SESSION", "Per session"
        MONTHLY = "MONTHLY", "Monthly"
        SUBSTITUTE_HOURLY = "SUBSTITUTE_HOURLY", "Substitute – hourly"
        SUBSTITUTE_PER_SESSION = "SUBSTITUTE_PER_SESSION", "Substitute – per session"

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

    def __str__(self):
        scope = f" [{self.training_class.name}]" if self.training_class_id else ""
        return f"{self.coach}: {self.get_rate_type_display()} RM {self.amount}{scope}"

    def clean(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError("Effective-to cannot be before effective-from.")
        if self.training_class_id and self.rate_type == self.RateType.MONTHLY:
            raise ValidationError("Monthly rates apply to the coach, not to a single class.")

    @classmethod
    def effective_on(cls, date):
        return cls.objects.filter(effective_from__lte=date).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=date))


class PayrollRun(AuditedModel):
    audit_category = AuditCategory.PAYROLL

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        FINALIZED = "FINALIZED", "Finalized"

    year = models.PositiveIntegerField()
    month = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    calculated_at = models.DateTimeField(null=True, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [models.UniqueConstraint(fields=["year", "month"], name="unique_payroll_month")]

    def __str__(self):
        return f"Payroll {self.year}-{self.month:02d} ({self.get_status_display()})"

    @property
    def is_locked(self):
        return self.status == self.Status.FINALIZED

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


class Payslip(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    coach = models.ForeignKey(Coach, on_delete=models.PROTECT, related_name="payslips")
    gross_pay = models.DecimalField(**money, default=Decimal("0"))
    total_deductions = models.DecimalField(**money, default=Decimal("0"))
    net_pay = models.DecimalField(**money, default=Decimal("0"))
    regular_sessions = models.PositiveIntegerField(default=0)
    substitute_sessions = models.PositiveIntegerField(default=0)
    hours = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"))

    class Meta:
        ordering = ["run", "coach__full_name"]
        constraints = [models.UniqueConstraint(fields=["run", "coach"], name="unique_payslip")]

    def __str__(self):
        return f"{self.coach} – {self.run.year}-{self.run.month:02d}: RM {self.net_pay}"

    def save(self, *args, **kwargs):
        if self.run.is_locked:
            raise PermissionDenied("Payslips of a finalized payroll cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.run.is_locked:
            raise PermissionDenied("Payslips of a finalized payroll cannot be deleted.")
        super().delete(*args, **kwargs)


class PayslipLine(models.Model):
    class Kind(models.TextChoices):
        REGULAR_SESSION = "REGULAR_SESSION", "Regular session"
        SUBSTITUTE_SESSION = "SUBSTITUTE_SESSION", "Substitute session"
        MONTHLY = "MONTHLY", "Monthly salary"
        ALLOWANCE = "ALLOWANCE", "Allowance"
        BONUS = "BONUS", "Bonus"
        DEDUCTION = "DEDUCTION", "Deduction"

    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    description = models.CharField(max_length=255)
    session = models.ForeignKey(TrainingSession, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("1"))
    rate = models.DecimalField(**money, default=Decimal("0"))
    amount = models.DecimalField(**money, help_text="Negative for deductions.")

    class Meta:
        ordering = ["payslip", "kind", "id"]

    def __str__(self):
        return f"{self.description}: RM {self.amount}"

    def save(self, *args, **kwargs):
        if self.payslip.run.is_locked:
            raise PermissionDenied("Payslips of a finalized payroll cannot be changed.")
        super().save(*args, **kwargs)
