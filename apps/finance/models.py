import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.academy.models import DatedQuerySet, Enrollment, Family, Student, TrainingClass
from apps.audit.models import AuditCategory, AuditedModel

from .money import CURRENCY, ZERO, round_money

money = {"max_digits": 10, "decimal_places": 2}


class FinancialHistoryQuerySet(models.QuerySet):
    """Bulk deletes bypass ``Model.delete()``; financial history is never deleted."""

    def delete(self):
        raise PermissionDenied(f"{self.model._meta.verbose_name_plural} are financial history and cannot be deleted.")


class ImmutableQuerySet(FinancialHistoryQuerySet):
    """For records that never change once written (receipts, allocations, refunds)."""

    def update(self, **kwargs):
        raise PermissionDenied(f"{self.model._meta.verbose_name_plural} are immutable.")


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

    objects = FinancialHistoryQuerySet.as_manager()

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
        return round_money(self.quantity * self.unit_amount - self.discount)

    def active_invoice_item(self):
        return self.invoice_items.filter(is_active=True).select_related("invoice").first()

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
        if None in (self.quantity, self.unit_amount, self.discount):
            return  # field-level errors are reported by the form
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
            item = old.active_invoice_item() if old else None
            if item and item.invoice.status != Invoice.Status.DRAFT:
                locked = ("student_id", "fee_type", "description", "quantity", "unit_amount", "discount")
                if any(getattr(old, f) != getattr(self, f) for f in locked):
                    raise ValidationError(
                        f"This charge is on issued invoice {item.invoice.number}; void the invoice to change it."
                    )
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


class DocumentSequence(models.Model):
    """Per-type, per-year counters for official document numbers.

    Numbers come from a row locked with SELECT ... FOR UPDATE inside the
    caller's transaction, so they are unique and gapless under concurrency.
    """

    class DocType(models.TextChoices):
        INVOICE = "INVOICE", "Invoice"
        PAYMENT = "PAYMENT", "Payment"
        RECEIPT = "RECEIPT", "Receipt"
        REFUND = "REFUND", "Refund"

    doc_type = models.CharField(max_length=10, choices=DocType.choices)
    year = models.PositiveIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["doc_type", "year"], name="unique_document_sequence")]

    def __str__(self):
        return f"{self.doc_type} {self.year}: {self.last_number}"


class Invoice(AuditedModel):
    """A family's billing document: a snapshot of one or more charges, possibly
    for several siblings. There is no bill-to parent: the invoice is issued for
    the students of the family.

    Lifecycle (``TRANSITIONS``): DRAFT -> ISSUED -> PARTIALLY_PAID -> PAID, and
    DRAFT/ISSUED (unpaid) -> VOID. Payment-driven moves back towards ISSUED only
    happen when a payment is voided. Issued invoices are never edited or deleted.
    """

    audit_category = AuditCategory.FINANCE

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ISSUED = "ISSUED", "Issued (unpaid)"
        PARTIALLY_PAID = "PARTIALLY_PAID", "Partially paid"
        PAID = "PAID", "Paid"
        VOID = "VOID", "Void"

    class Kind(models.TextChoices):
        GENERAL = "GENERAL", "Fees"
        COMPETITION = "COMPETITION", "Competition registration"

    TRANSITIONS = {
        Status.DRAFT: {Status.ISSUED, Status.VOID},
        Status.ISSUED: {Status.PARTIALLY_PAID, Status.PAID, Status.VOID},
        Status.PARTIALLY_PAID: {Status.PAID, Status.ISSUED},     # back to ISSUED only by voiding payments
        Status.PAID: {Status.PARTIALLY_PAID, Status.ISSUED},     # only by voiding payments
        Status.VOID: set(),
    }
    OPEN_FOR_PAYMENT = (Status.ISSUED, Status.PARTIALLY_PAID)
    LOCKED_AFTER_ISSUE = ("number", "family_id", "family_name", "kind", "currency", "issue_date", "due_date",
                          "subtotal", "discount_total", "total", "issued_at", "issued_by_id", "created_by_id")

    number = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False,
                              help_text="Assigned when the invoice is issued.")
    family = models.ForeignKey(Family, on_delete=models.PROTECT, related_name="invoices")
    family_name = models.CharField(max_length=200, blank=True, editable=False, help_text="Snapshot at issue.")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.GENERAL)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.DRAFT, editable=False)
    currency = models.CharField(max_length=3, default=CURRENCY, editable=False)
    issue_date = models.DateField(null=True, blank=True, editable=False)
    due_date = models.DateField(null=True, blank=True)
    subtotal = models.DecimalField(**money, default=ZERO, editable=False)
    discount_total = models.DecimalField(**money, default=ZERO, editable=False)
    total = models.DecimalField(**money, default=ZERO, editable=False)
    amount_paid = models.DecimalField(**money, default=ZERO, editable=False)
    balance_due = models.DecimalField(**money, default=ZERO, editable=False)
    amount_refunded = models.DecimalField(**money, default=ZERO, editable=False,
                                          help_text="Exceptional refunds; does not reopen the invoice.")
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    issued_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    void_reason = models.TextField(blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FinancialHistoryQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(total__gte=0), name="invoice_total_not_negative"),
            models.CheckConstraint(condition=Q(total=F("subtotal") - F("discount_total")), name="invoice_total_consistent"),
            models.CheckConstraint(condition=Q(amount_paid__gte=0) & Q(amount_paid__lte=F("total")),
                                   name="invoice_paid_within_total"),
            models.CheckConstraint(condition=Q(balance_due=F("total") - F("amount_paid")), name="invoice_balance_consistent"),
            models.CheckConstraint(condition=Q(amount_refunded__gte=0) & Q(amount_refunded__lte=F("amount_paid")),
                                   name="invoice_refund_within_paid"),
            models.CheckConstraint(condition=Q(number__isnull=True) | ~Q(status="DRAFT"), name="draft_has_no_number"),
            models.CheckConstraint(condition=Q(number__isnull=False) | Q(status__in=["DRAFT", "VOID"]),
                                   name="issued_invoice_has_number"),
        ]

    def __str__(self):
        return self.number or f"Draft invoice #{self.pk}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            old = Invoice.objects.filter(pk=self.pk).first()
            if old is not None:
                if old.status != self.status and self.status not in self.TRANSITIONS[old.status]:
                    raise ValidationError(f"Invoice cannot go from {old.status} to {self.status}.")
                if old.status != self.Status.DRAFT and any(
                    getattr(old, f) != getattr(self, f) for f in self.LOCKED_AFTER_ISSUE
                ):
                    raise ValidationError("An issued invoice cannot be edited; void it and issue a new one.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Invoices cannot be deleted; void them instead.")

    def active_items(self):
        return self.items.filter(is_active=True)


class InvoiceItem(AuditedModel):
    """One invoice line: a frozen copy of a charge for one student. Changes to
    fees, students or families later never alter it."""

    audit_category = AuditCategory.FINANCE

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="items")
    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name="invoice_items")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="invoice_items")
    student_no = models.CharField(max_length=20)
    student_name = models.CharField(max_length=200)
    description = models.CharField(max_length=255)
    fee_type = models.CharField(max_length=12, choices=FeeType.choices)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=8, decimal_places=2)
    unit_amount = models.DecimalField(**money)
    discount = models.DecimalField(**money, default=ZERO)
    amount = models.DecimalField(**money)
    amount_paid = models.DecimalField(**money, default=ZERO, editable=False)
    is_active = models.BooleanField(default=True, editable=False,
                                    help_text="False once the invoice is voided, so the charge can be re-invoiced.")
    position = models.PositiveIntegerField(default=0)

    MUTABLE_AFTER_ISSUE = {"amount_paid", "is_active"}

    objects = FinancialHistoryQuerySet.as_manager()

    class Meta:
        ordering = ["invoice", "position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["charge"], condition=Q(is_active=True), name="one_active_invoice_per_charge"),
            models.CheckConstraint(condition=Q(amount__gte=0), name="invoice_item_amount_not_negative"),
            models.CheckConstraint(condition=Q(amount_paid__gte=0) & Q(amount_paid__lte=F("amount")),
                                   name="invoice_item_paid_within_amount"),
        ]

    def __str__(self):
        return f"{self.student_name}: {self.description} RM {self.amount}"

    @property
    def outstanding(self):
        return self.amount - self.amount_paid

    def save(self, *args, **kwargs):
        invoice_status = Invoice.objects.filter(pk=self.invoice_id).values_list("status", flat=True).first()
        if self.pk is None:
            if invoice_status != Invoice.Status.DRAFT:
                raise ValidationError("Lines can only be added to a draft invoice.")
        elif invoice_status != Invoice.Status.DRAFT:
            old = InvoiceItem.objects.get(pk=self.pk)
            changed = {f.attname for f in self._meta.concrete_fields if getattr(old, f.attname) != getattr(self, f.attname)}
            if changed - self.MUTABLE_AFTER_ISSUE:
                raise ValidationError("Lines of an issued invoice cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Invoice lines cannot be deleted; void the invoice instead.")


class Payment(AuditedModel):
    """Money actually received by the academy, for one family's invoices.

    Recording a payment is not a bank deposit: an admin may record cash taken at
    the desk and bank it later. The payment's amount, method and date are frozen
    once recorded; mistakes are corrected by voiding (the receipt is kept and
    marked VOID) or, exceptionally, by a Refund.
    """

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

    LOCKED = ("number", "family_id", "payer_name", "amount", "method", "reference", "received_at", "received_by_id",
              "idempotency_key")

    number = models.CharField(max_length=30, unique=True, editable=False)
    family = models.ForeignKey(Family, on_delete=models.PROTECT, related_name="payments")
    payer_name = models.CharField(max_length=200, blank=True,
                                  help_text="Optional reference: who handed over the money, as stated.")
    amount = models.DecimalField(**money, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(max_length=15, choices=Method.choices)
    reference = models.CharField(max_length=100, blank=True, help_text="Bank / transaction reference, cheque no.")
    received_at = models.DateTimeField(default=timezone.now)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.VALID, editable=False)
    notes = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False,
                                       help_text="Client-supplied key; a retried request returns the same payment.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FinancialHistoryQuerySet.as_manager()

    class Meta:
        ordering = ["-received_at", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive")]

    def __str__(self):
        return f"{self.number} RM {self.amount}"

    @property
    def received_on(self):
        return timezone.localtime(self.received_at).date()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            old = Payment.objects.get(pk=self.pk)
            if any(getattr(old, f) != getattr(self, f) for f in self.LOCKED):
                raise ValidationError("A recorded payment cannot be edited; void it instead.")
            if old.status == self.Status.VOIDED and self.status != self.Status.VOIDED:
                raise ValidationError("A voided payment cannot be reinstated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Payments cannot be deleted; void them instead.")


class PaymentAllocation(AuditedModel):
    """How much of a payment went to which invoice line (and so to which
    student's charge). Immutable once written."""

    audit_category = AuditCategory.FINANCE

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    invoice_item = models.ForeignKey(InvoiceItem, on_delete=models.PROTECT, related_name="allocations")
    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(**money, validators=[MinValueValidator(Decimal("0.01"))])

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        ordering = ["payment", "id"]
        constraints = [
            models.UniqueConstraint(fields=["payment", "invoice_item"], name="one_allocation_per_line_per_payment"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="allocation_amount_positive"),
        ]

    def __str__(self):
        return f"RM {self.amount} → {self.invoice_item}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Payment allocations cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Payment allocations cannot be deleted.")


class Receipt(models.Model):
    """Official receipt for a payment. Immutable once issued: the full content is
    frozen in ``content`` at issue time. Mistakes are handled by voiding (ReceiptVoid)."""

    number = models.CharField(max_length=30, unique=True)
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="receipt")
    issued_at = models.DateTimeField()
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    payer_name = models.CharField(max_length=200, blank=True)
    total = models.DecimalField(**money)
    content = models.JSONField(help_text="Frozen receipt content as issued.")

    objects = ImmutableQuerySet.as_manager()

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

    objects = ImmutableQuerySet.as_manager()

    def __str__(self):
        return f"VOID {self.receipt.number}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionDenied("Void records are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Void records cannot be deleted.")


class Refund(AuditedModel):
    """Exceptional money returned against one payment line (e.g. an authorized
    competition refund). Competition fees are normally non-refundable, so this
    is never created automatically. The original payment, allocation and
    receipt are left untouched; the refund is its own record."""

    audit_category = AuditCategory.FINANCE

    number = models.CharField(max_length=30, unique=True, editable=False)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    allocation = models.ForeignKey(PaymentAllocation, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(**money)
    method = models.CharField(max_length=15, choices=Payment.Method.choices)
    reference = models.CharField(max_length=100, blank=True)
    reason = models.TextField()
    refunded_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        ordering = ["-refunded_at", "-id"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="refund_amount_positive")]

    def __str__(self):
        return f"{self.number} RM {self.amount}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Refunds cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Refunds cannot be deleted.")


# --------------------------------------------------------------------------- payment proofs


def _private_storage():
    from .uploads import private_storage

    return private_storage()


class AcademyPaymentInfo(AuditedModel):
    """How families pay the academy (one record): the academy's bank account, QR
    code and instructions, shown to parents on unpaid invoices. Managed by
    finance (``finance.payment_info.manage``). Unrelated to coach payroll bank
    details, which live on the coach record."""

    audit_category = AuditCategory.FINANCE
    audit_exclude = ("updated_at", "qr_code")

    bank_name = models.CharField(max_length=100, blank=True)
    account_name = models.CharField(max_length=150, blank=True)
    account_number = models.CharField(max_length=40, blank=True)
    instructions = models.TextField(blank=True, help_text="How to pay (shown to parents).")
    reference_instructions = models.CharField(
        max_length=255, blank=True, default="Use the invoice number as the payment reference.")
    qr_code = models.FileField(storage=_private_storage, max_length=255, blank=True,
                               help_text="PNG or JPEG payment QR (e.g. DuitNow).")
    qr_content_type = models.CharField(max_length=20, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)

    class Meta:
        verbose_name = "academy payment information"
        verbose_name_plural = "academy payment information"

    def __str__(self):
        return "Academy payment information"

    @classmethod
    def current(cls):
        return cls.objects.order_by("pk").first()

    def save(self, *args, **kwargs):
        if self.pk is None and AcademyPaymentInfo.objects.exists():
            raise ValidationError("There is only one academy payment information record; edit it instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Clear the fields instead of deleting the payment information.")

    @property
    def is_configured(self):
        return bool(self.bank_name and self.account_number) or bool(self.qr_code)


class PaymentProof(AuditedModel):
    """Evidence a parent uploads to say "I have paid this invoice" (a transfer
    screenshot, bank confirmation, ...). It is NOT a payment and NOT a receipt:

    PENDING_REVIEW → staff ACCEPT (optionally linking the payment they recorded
    with the normal record-payment operation) or REJECT (reason required).
    Accepting never records money, changes an invoice or confirms a competition
    registration; only a recorded Payment does that, through the finance
    services. Proofs are evidence: never deleted, and only the review fields
    change, once.
    """

    audit_category = AuditCategory.FINANCE
    audit_exclude = ("file", "sha256")

    class Status(models.TextChoices):
        PENDING_REVIEW = "PENDING_REVIEW", "Pending review"
        ACCEPTED = "ACCEPTED", "Accepted"
        REJECTED = "REJECTED", "Rejected"

    LOCKED = ("family_id", "invoice_id", "uploaded_by_id", "uploaded_at", "file", "original_name", "content_type",
              "size", "sha256", "amount_claimed", "payment_date", "reference", "note")

    family = models.ForeignKey(Family, on_delete=models.PROTECT, related_name="payment_proofs")
    invoice = models.ForeignKey("Invoice", on_delete=models.PROTECT, related_name="payment_proofs")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", editable=False)
    uploaded_at = models.DateTimeField(default=timezone.now, editable=False)
    file = models.FileField(storage=_private_storage, max_length=255, editable=False)
    original_name = models.CharField(max_length=120, editable=False)
    content_type = models.CharField(max_length=40, editable=False)
    size = models.PositiveIntegerField(editable=False)
    sha256 = models.CharField(max_length=64, editable=False)
    amount_claimed = models.DecimalField(**money, null=True, blank=True,
                                         validators=[MinValueValidator(Decimal("0.01"))])
    payment_date = models.DateField(null=True, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING_REVIEW, editable=False)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", editable=False)
    reviewed_at = models.DateTimeField(null=True, blank=True, editable=False)
    review_note = models.TextField(blank=True, editable=False)
    payment = models.ForeignKey("Payment", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="payment_proofs", editable=False,
                                help_text="The payment staff recorded for this proof, if linked.")

    objects = FinancialHistoryQuerySet.as_manager()

    class Meta:
        ordering = ["-uploaded_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(status="PENDING_REVIEW", reviewed_at__isnull=True)
                | Q(status__in=["ACCEPTED", "REJECTED"], reviewed_at__isnull=False),
                name="proof_review_matches_status"),
            models.CheckConstraint(condition=~Q(status="REJECTED") | ~Q(review_note=""),
                                   name="proof_rejection_has_reason"),
        ]

    def __str__(self):
        return f"Payment proof for {self.invoice_id} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            previous = PaymentProof.objects.filter(pk=self.pk).first()
            if previous is not None:
                if previous.status != self.Status.PENDING_REVIEW:
                    raise PermissionDenied("A reviewed payment proof cannot be changed.")
                changed = [f for f in self.LOCKED if getattr(previous, f) != getattr(self, f)]
                if changed:
                    raise PermissionDenied("An uploaded payment proof cannot be changed; upload a new one.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Payment proofs are evidence and cannot be deleted.")
