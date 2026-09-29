import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum

from apps.academy.models import DatedQuerySet, Enrollment, Student, TrainingClass
from apps.accounts.models import Parent
from apps.audit.models import AuditCategory, AuditedModel

ZERO = Decimal("0.00")
money = {"max_digits": 10, "decimal_places": 2}


class FeeType(models.TextChoices):
    TUITION = "TUITION", "Tuition / class fee"
    REGISTRATION = "REGISTRATION", "Registration fee"
    UNIFORM = "UNIFORM", "Uniform fee"
    WEAPON = "WEAPON", "Weapon charges"
    COMPETITION = "COMPETITION", "Competition fee"
    OTHER = "OTHER", "Other charges"


class BillingCycle(models.TextChoices):
    MONTHLY = "MONTHLY", "Per month"
    PER_SESSION = "PER_SESSION", "Per session attended"
    TERM = "TERM", "Per term"
    YEARLY = "YEARLY", "Per year"
    ONE_TIME = "ONE_TIME", "One time"


class ClassFee(AuditedModel):
    """Fee rate for a class, valid from effective_from until effective_to.

    To change a fee, end the current row and add a new one: once a rate has
    been billed its amount is locked, so historical fees are preserved.
    """

    audit_category = AuditCategory.FINANCE

    training_class = models.ForeignKey(TrainingClass, on_delete=models.PROTECT, related_name="fees")
    name = models.CharField(max_length=100, help_text='e.g. "Monthly fee", "Monthly fee (3x/week)"')
    fee_type = models.CharField(max_length=12, choices=FeeType.choices, default=FeeType.TUITION)
    billing_cycle = models.CharField(max_length=12, choices=BillingCycle.choices, default=BillingCycle.MONTHLY)
    amount = models.DecimalField(**money, validators=[MinValueValidator(ZERO)])
    is_default = models.BooleanField(
        default=True, help_text="Used for students in this class who have no specific fee plan."
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["training_class", "name", "-effective_from"]

    def __str__(self):
        return f"{self.training_class.name} – {self.name}: RM {self.amount} ({self.get_billing_cycle_display()})"

    def clean(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValidationError("Effective-to cannot be before effective-from.")
        overlapping = ClassFee.objects.filter(
            training_class_id=self.training_class_id, name=self.name, effective_from__lte=self.effective_to or datetime.date.max
        ).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=self.effective_from)).exclude(pk=self.pk)
        if self.training_class_id and overlapping.exists():
            raise ValidationError(
                "Another fee with the same name overlaps these dates. End the old fee before adding a new one."
            )

    def save(self, *args, **kwargs):
        if self.pk is not None:
            old = ClassFee.objects.filter(pk=self.pk).first()
            locked_fields = ("amount", "billing_cycle", "fee_type", "training_class_id", "effective_from")
            if old and self.charges.exists() and any(getattr(old, f) != getattr(self, f) for f in locked_fields):
                raise ValidationError(
                    "This fee has already been billed, so it cannot be changed. "
                    "Set an effective-to date and add a new fee instead."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.charges.exists():
            raise PermissionDenied("This fee has been billed and cannot be deleted.")
        super().delete(*args, **kwargs)

    @classmethod
    def effective_on(cls, date):
        return cls.objects.filter(effective_from__lte=date).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=date))


class StudentFeePlan(AuditedModel):
    """Per-student fee arrangement in a class: a specific fee option, a custom
    amount, or a discount (sibling discount, scholarship, ...)."""

    audit_category = AuditCategory.FINANCE

    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="fee_plans")
    class_fee = models.ForeignKey(
        ClassFee, null=True, blank=True, on_delete=models.PROTECT,
        help_text="Pick one of the class's fee options. Leave empty to use the class default.",
    )
    custom_amount = models.DecimalField(**money, null=True, blank=True, help_text="Overrides the fee amount.")
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO), MaxValueValidator(100)]
    )
    reason = models.CharField(max_length=255, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)

    objects = DatedQuerySet.as_manager()

    class Meta:
        ordering = ["enrollment", "-start_date"]

    def __str__(self):
        return f"Fee plan for {self.enrollment}"

    def clean(self):
        if self.class_fee_id and self.enrollment_id and self.class_fee.training_class_id != self.enrollment.training_class_id:
            raise ValidationError("The selected fee belongs to a different class.")


class ChargeItem(models.Model):
    """Price list for one-off charges: registration, uniforms, weapons, ..."""

    name = models.CharField(max_length=150, unique=True)
    fee_type = models.CharField(max_length=12, choices=FeeType.choices)
    default_amount = models.DecimalField(**money, validators=[MinValueValidator(ZERO)])
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["fee_type", "name"]

    def __str__(self):
        return f"{self.name} (RM {self.default_amount})"


class Charge(AuditedModel):
    """An amount billed to a student. The amount is a snapshot, so later fee
    changes never alter it. Charges are cancelled, never deleted."""

    audit_category = AuditCategory.FINANCE

    class Status(models.TextChoices):
        UNPAID = "UNPAID", "Unpaid"
        PARTIAL = "PARTIAL", "Partially paid"
        PAID = "PAID", "Paid"
        WAIVED = "WAIVED", "Waived"
        CANCELLED = "CANCELLED", "Cancelled"

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="charges")
    fee_type = models.CharField(max_length=12, choices=FeeType.choices)
    description = models.CharField(max_length=255)
    class_fee = models.ForeignKey(ClassFee, null=True, blank=True, on_delete=models.PROTECT, related_name="charges")
    charge_item = models.ForeignKey(ChargeItem, null=True, blank=True, on_delete=models.PROTECT, related_name="charges")
    enrollment = models.ForeignKey(Enrollment, null=True, blank=True, on_delete=models.PROTECT, related_name="charges")
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("1"))
    unit_amount = models.DecimalField(**money, validators=[MinValueValidator(ZERO)])
    discount = models.DecimalField(**money, default=ZERO, validators=[MinValueValidator(ZERO)])
    amount = models.DecimalField(**money, editable=False, help_text="quantity × unit amount − discount")
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNPAID)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["enrollment", "class_fee", "period_start"],
                condition=~Q(status="CANCELLED"),
                name="unique_active_period_charge",
            )
        ]

    def __str__(self):
        return f"{self.student.full_name}: {self.description} RM {self.amount}"

    def compute_amount(self):
        return (self.quantity * self.unit_amount - self.discount).quantize(Decimal("0.01"))

    def valid_allocations(self):
        return self.allocations.filter(payment__status=Payment.Status.VALID)

    @property
    def amount_paid(self):
        if self.pk is None:
            return ZERO
        return self.valid_allocations().aggregate(total=Sum("amount"))["total"] or ZERO

    @property
    def balance(self):
        if self.status in (self.Status.CANCELLED, self.Status.WAIVED):
            return ZERO
        return self.amount - self.amount_paid

    def clean(self):
        if self.compute_amount() < ZERO:
            raise ValidationError("Discount cannot exceed the charge amount.")

    def save(self, *args, **kwargs):
        new_amount = self.compute_amount()
        if new_amount < ZERO:
            raise ValidationError("Discount cannot exceed the charge amount.")
        if self.pk is not None:
            old = Charge.objects.filter(pk=self.pk).first()
            if old and old.amount != new_amount and old.valid_allocations().exists():
                raise ValidationError("A charge that has payments against it cannot change amount.")
        self.amount = new_amount
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Charges cannot be deleted; cancel them instead.")

    def refresh_status(self):
        if self.status in (self.Status.CANCELLED, self.Status.WAIVED):
            return
        paid = self.amount_paid
        if paid <= ZERO:
            status = self.Status.UNPAID
        elif paid < self.amount:
            status = self.Status.PARTIAL
        else:
            status = self.Status.PAID
        if status != self.status:
            self.status = status
            self.save()


class Payment(AuditedModel):
    audit_category = AuditCategory.FINANCE

    class Method(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank transfer"
        DUITNOW = "DUITNOW", "DuitNow / QR"
        FPX = "FPX", "FPX online banking"
        CARD = "CARD", "Debit / credit card"
        CHEQUE = "CHEQUE", "Cheque"
        EWALLET = "EWALLET", "E-wallet (TNG, GrabPay, ...)"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        VALID = "VALID", "Valid"
        VOIDED = "VOIDED", "Voided"

    parent = models.ForeignKey(Parent, null=True, blank=True, on_delete=models.PROTECT, related_name="payments")
    payer_name = models.CharField(max_length=200)
    amount = models.DecimalField(**money, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(max_length=15, choices=Method.choices)
    reference = models.CharField(max_length=100, blank=True, help_text="Bank / transaction reference, cheque no.")
    received_on = models.DateField(default=datetime.date.today)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.VALID)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_on", "-id"]

    def __str__(self):
        return f"Payment #{self.pk} RM {self.amount} from {self.payer_name}"

    def save(self, *args, **kwargs):
        if self.pk is not None and hasattr(self, "receipt"):
            old = Payment.objects.get(pk=self.pk)
            for field in ("amount", "parent_id", "payer_name", "method", "reference", "received_on"):
                if getattr(old, field) != getattr(self, field):
                    raise ValidationError("A receipt has been issued for this payment; it can only be voided.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Payments cannot be deleted; void them instead.")


class PaymentAllocation(AuditedModel):
    audit_category = AuditCategory.FINANCE

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(**money, validators=[MinValueValidator(Decimal("0.01"))])

    def __str__(self):
        return f"RM {self.amount} → {self.charge}"

    def save(self, *args, **kwargs):
        if self.payment_id and hasattr(self.payment, "receipt"):
            raise ValidationError("A receipt has been issued for this payment; allocations are locked.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Payment allocations cannot be deleted.")


class ReceiptSequence(models.Model):
    year = models.PositiveIntegerField(primary_key=True)
    last_number = models.PositiveIntegerField(default=0)


class Receipt(models.Model):
    """Official receipt. Immutable once issued: the full content is frozen in
    ``content`` at issue time. Mistakes are handled by voiding (ReceiptVoid)."""

    number = models.CharField(max_length=30, unique=True)
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="receipt")
    issued_at = models.DateTimeField()
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    payer_name = models.CharField(max_length=200)
    total = models.DecimalField(**money)
    content = models.JSONField(help_text="Frozen receipt content as issued.")

    class Meta:
        ordering = ["-issued_at", "-id"]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionDenied("Receipts are immutable once issued.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Receipts cannot be deleted; void them instead.")

    @property
    def is_void(self):
        return hasattr(self, "void_record")


class ReceiptVoid(models.Model):
    receipt = models.OneToOneField(Receipt, on_delete=models.PROTECT, related_name="void_record")
    voided_at = models.DateTimeField(auto_now_add=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.TextField()

    def __str__(self):
        return f"VOID {self.receipt.number}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionDenied("Void records are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Void records cannot be deleted.")
