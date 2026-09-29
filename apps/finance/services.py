"""Finance services: the only supported way to change financial records.

Charge (amount owed by a student) -> Invoice (family document, snapshot of
charges) -> Payment (money received for a family's invoices) -> PaymentAllocation
(which invoice line each sen went to) -> Receipt (immutable proof of payment).

Everything that moves money runs in a transaction that locks the invoice rows
(``SELECT ... FOR UPDATE``, always in ascending id order) before reading
balances, so concurrent payments cannot over-allocate. Database check
constraints on the stored balances are the last line of defence.
"""

import calendar
import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.academy.models import Enrollment, Family, Student
from apps.accounts.capabilities import Cap, require
from apps.attendance.models import AttendanceRecord, AttendanceStatus
from apps.audit.context import audit_context
from apps.audit.models import AuditCategory, AuditLog
from apps.audit.utils import record as audit_record

from . import signals
from .models import (
    BillingCycle,
    Charge,
    ClassFee,
    DocumentSequence,
    FeeType,
    Invoice,
    InvoiceItem,
    Payment,
    PaymentAllocation,
    Receipt,
    ReceiptVoid,
    Refund,
    StudentFeePlan,
)
from .money import ZERO, round_money, to_money


def month_bounds(year, month):
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    return first, last


def fee_for_enrollment(enrollment, on_date):
    """Return (class_fee, unit_amount, discount_percent, note) for this student, or None."""
    plan = StudentFeePlan.objects.active_on(on_date).filter(enrollment=enrollment).select_related("class_fee").first()
    fee = None
    if plan and plan.class_fee_id:
        fee = plan.class_fee
    if fee is None:
        fee = (
            ClassFee.effective_on(on_date)
            .filter(training_class_id=enrollment.training_class_id, is_default=True, fee_type=FeeType.TUITION)
            .filter(billing_cycle__in=[BillingCycle.MONTHLY, BillingCycle.PER_SESSION])
            .order_by("-effective_from")
            .first()
        )
    if fee is None:
        return None
    amount = plan.custom_amount if plan and plan.custom_amount is not None else fee.amount
    discount_percent = plan.discount_percent if plan else ZERO
    note = plan.reason if plan else ""
    return fee, amount, discount_percent, note


@transaction.atomic
def generate_tuition_charges(year, month, actor=None, due_date=None):
    """Bill monthly and per-session class fees for one month. Safe to re-run:
    a period already billed for an enrollment is skipped."""
    require(actor, Cap.FINANCE_CHARGES_MANAGE)
    first, last = month_bounds(year, month)
    due_day = settings.ACADEMY.get("DEFAULT_TUITION_DUE_DAY", 7)
    due_date = due_date or datetime.date(year, month, min(due_day, last.day))
    created = []
    enrollments = (
        Enrollment.objects.overlapping(first, last)
        .filter(student__status=Student.Status.ACTIVE)
        .select_related("student", "training_class")
    )
    with audit_context(actor, f"Monthly billing {year}-{month:02d}"):
        for enrollment in enrollments:
            billing_date = max(first, enrollment.start_date)
            found = fee_for_enrollment(enrollment, billing_date)
            if found is None:
                continue
            fee, unit_amount, discount_percent, note = found
            if Charge.objects.filter(enrollment=enrollment, class_fee=fee, period_start=first).exclude(
                status=Charge.Status.CANCELLED
            ).exists():
                continue
            if fee.billing_cycle == BillingCycle.MONTHLY:
                quantity = Decimal("1")
                description = f"{fee.name} – {enrollment.training_class.name} ({first:%b %Y})"
            elif fee.billing_cycle == BillingCycle.PER_SESSION:
                quantity = Decimal(
                    AttendanceRecord.objects.filter(
                        student=enrollment.student,
                        session__training_class=enrollment.training_class,
                        session__date__range=(first, last),
                        status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE],
                    ).count()
                )
                if quantity == 0:
                    continue
                description = f"{fee.name} – {enrollment.training_class.name} ({first:%b %Y}, {quantity:.0f} sessions)"
            else:
                continue
            gross = quantity * unit_amount
            discount = round_money(gross * discount_percent / Decimal(100))
            created.append(
                Charge.objects.create(
                    student=enrollment.student,
                    fee_type=fee.fee_type,
                    description=description,
                    class_fee=fee,
                    enrollment=enrollment,
                    period_start=first,
                    period_end=last,
                    quantity=quantity,
                    unit_amount=unit_amount,
                    discount=discount,
                    due_date=due_date,
                    notes=note,
                    created_by=actor,
                )
            )
    return created


@transaction.atomic
def add_charge(student, fee_type, description, unit_amount, actor=None, quantity=1, discount=ZERO,
               due_date=None, charge_item=None, notes="", period_start=None, period_end=None):
    """One-off charge: registration, uniform, weapons, competition, other."""
    require(actor, Cap.FINANCE_CHARGES_MANAGE)
    return _create_charge(student, fee_type, description, unit_amount, actor, quantity, discount, due_date,
                          charge_item, notes, period_start, period_end)


def _create_charge(student, fee_type, description, unit_amount, actor=None, quantity=1, discount=ZERO,
                   due_date=None, charge_item=None, notes="", period_start=None, period_end=None):
    """Create a charge without a capability check; callers are responsible
    (e.g. competition registration, which has its own capability)."""
    if isinstance(quantity, float):
        raise ValidationError("quantity: floating-point values are not accepted.")
    quantity = Decimal(str(quantity))
    if quantity <= 0:
        raise ValidationError("quantity: must be more than zero.")
    with audit_context(actor, "Charge added"):
        return Charge.objects.create(
            student=student,
            fee_type=fee_type,
            description=description,
            unit_amount=to_money(unit_amount, field="unit amount"),
            quantity=quantity,
            discount=to_money(discount, field="discount"),
            due_date=due_date,
            charge_item=charge_item,
            notes=notes,
            period_start=period_start,
            period_end=period_end,
            created_by=actor,
        )


@transaction.atomic
def cancel_charge(charge, reason, actor=None, waive=False):
    require(actor, Cap.FINANCE_CHARGES_MANAGE)
    if not reason:
        raise ValidationError("A reason is required.")
    if charge.valid_allocations().exists():
        raise ValidationError("This charge has payments; void the payment(s) first.")
    item = charge.active_invoice_item()
    if item is not None:
        raise ValidationError(f"This charge is on invoice {item.invoice}; void that invoice first.")
    with audit_context(actor, reason):
        charge.status = Charge.Status.WAIVED if waive else Charge.Status.CANCELLED
        charge.save()
    return charge


# --------------------------------------------------------------------------- document numbers


def next_document_number(doc_type, year=None):
    """Next official number, e.g. INV-2026-000123. Must run inside a transaction:
    the sequence row stays locked until the caller commits, so numbers are
    unique and gapless even when several staff record at the same moment."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Document numbers must be taken inside a transaction.")
    year = year or timezone.localdate().year
    sequence = DocumentSequence.objects.select_for_update().filter(doc_type=doc_type, year=year).first()
    if sequence is None:
        try:
            with transaction.atomic():
                DocumentSequence.objects.create(doc_type=doc_type, year=year)
        except IntegrityError:
            pass  # another transaction created it first; lock theirs below
        sequence = DocumentSequence.objects.select_for_update().get(doc_type=doc_type, year=year)
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    prefix = settings.ACADEMY["DOCUMENT_PREFIXES"][doc_type]
    return f"{prefix}-{year}-{sequence.last_number:06d}"


# --------------------------------------------------------------------------- invoices


def _lock_invoices(invoices):
    ids = sorted({inv.pk for inv in invoices})
    locked = {inv.pk: inv for inv in Invoice.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    if len(locked) != len(ids):
        raise ValidationError("Invoice not found.")
    return locked


def _item_from_charge(invoice, charge, position):
    student = charge.student
    return InvoiceItem.objects.create(
        invoice=invoice, charge=charge, student=student, student_no=student.student_no, student_name=student.full_name,
        description=charge.description, fee_type=charge.fee_type, period_start=charge.period_start,
        period_end=charge.period_end, quantity=charge.quantity, unit_amount=charge.unit_amount,
        discount=charge.discount, amount=charge.amount, position=position,
    )


def _set_totals(invoice):
    items = list(invoice.active_items())
    subtotal = sum((round_money(i.quantity * i.unit_amount) for i in items), ZERO)
    # Line amounts are the authority; the discount total absorbs any sen rounding.
    total = sum((i.amount for i in items), ZERO)
    invoice.subtotal, invoice.discount_total, invoice.total = subtotal, subtotal - total, total
    invoice.balance_due = total - invoice.amount_paid


def _create_invoice(family, charges, actor, *, kind=Invoice.Kind.GENERAL, due_date=None, notes=""):
    """Create a DRAFT invoice from charges (no capability check; see create_invoice)."""
    charges = list(charges)
    if not charges:
        raise ValidationError("An invoice needs at least one charge.")
    locked = list(Charge.objects.select_for_update().filter(pk__in=[c.pk for c in charges]).order_by("pk")
                  .select_related("student"))
    for charge in locked:
        if charge.student.family_id != family.pk:
            raise ValidationError(f"{charge.student.full_name} is not in {family.name}.")
        if charge.status != Charge.Status.UNPAID:
            raise ValidationError(f"'{charge.description}' is {charge.get_status_display().lower()} and cannot be invoiced.")
        if charge.invoice_items.filter(is_active=True).exists():
            raise ValidationError(f"'{charge.description}' is already on an invoice.")
    with audit_context(actor, "Invoice drafted"):
        invoice = Invoice.objects.create(family=family, kind=kind, due_date=due_date, notes=notes, created_by=actor)
        for position, charge in enumerate(sorted(locked, key=lambda c: (c.student.full_name, c.pk)), start=1):
            _item_from_charge(invoice, charge, position)
        _set_totals(invoice)
        invoice.save()
    return invoice


@transaction.atomic
def create_invoice(family, charges, actor=None, due_date=None, notes=""):
    """Draft one family invoice covering the given charges (one or more students)."""
    require(actor, Cap.FINANCE_INVOICES_MANAGE)
    return _create_invoice(family, charges, actor, due_date=due_date, notes=notes)


def uninvoiced_charges():
    return Charge.objects.filter(status=Charge.Status.UNPAID).exclude(invoice_items__is_active=True)


@transaction.atomic
def generate_draft_invoices(actor=None, families=None, due_date=None):
    """One DRAFT invoice per family for all of its unpaid, not-yet-invoiced charges."""
    require(actor, Cap.FINANCE_INVOICES_MANAGE)
    pending = uninvoiced_charges().select_related("student")
    if families is not None:
        pending = pending.filter(student__family__in=families)
    by_family = {}
    for charge in pending:
        by_family.setdefault(charge.student.family_id, []).append(charge)
    created = []
    for family in Family.objects.filter(pk__in=by_family).order_by("pk"):
        created.append(_create_invoice(family, by_family[family.pk], actor, due_date=due_date))
    return created


def _issue_invoice(invoice, actor, due_date=None):
    invoice = _lock_invoices([invoice])[invoice.pk]
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError(f"Only draft invoices can be issued (this one is {invoice.get_status_display()}).")
    items = list(invoice.active_items().select_related("charge", "student"))
    if not items:
        raise ValidationError("An invoice needs at least one line before it is issued.")
    for item in items:
        if item.student.family_id != invoice.family_id:
            raise ValidationError(f"{item.student_name} is no longer in this family; void and re-create the invoice.")
        if item.charge.status != Charge.Status.UNPAID or item.charge.amount != item.amount:
            raise ValidationError(f"'{item.description}' changed since the draft; void and re-create the invoice.")
    with audit_context(actor, "Invoice issued"):
        invoice.number = next_document_number(DocumentSequence.DocType.INVOICE)
        invoice.status = Invoice.Status.ISSUED
        invoice.issue_date = timezone.localdate()
        invoice.issued_at = timezone.now()
        invoice.issued_by = actor
        invoice.family_name = invoice.family.name
        if due_date is not None:
            invoice.due_date = due_date
        _set_totals(invoice)
        invoice.save()
    return invoice


@transaction.atomic
def issue_invoice(invoice, actor=None, due_date=None):
    """DRAFT -> ISSUED: assigns the invoice number and freezes the document."""
    require(actor, Cap.FINANCE_INVOICES_MANAGE)
    return _issue_invoice(invoice, actor, due_date)


def _void_invoice(invoice, reason, actor):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required to void an invoice.")
    invoice = _lock_invoices([invoice])[invoice.pk]
    if invoice.status not in (Invoice.Status.DRAFT, Invoice.Status.ISSUED) or invoice.amount_paid > ZERO:
        raise ValidationError("Only unpaid invoices can be voided; void the payments first.")
    with audit_context(actor, reason):
        for item in invoice.active_items():
            item.is_active = False
            item.save()
        invoice.status = Invoice.Status.VOID
        invoice.voided_at = timezone.now()
        invoice.voided_by = actor
        invoice.void_reason = reason
        invoice.save()
    return invoice


@transaction.atomic
def void_invoice(invoice, reason, actor=None):
    """DRAFT/ISSUED (unpaid) -> VOID. The document stays on record; its charges
    become free to be invoiced again."""
    require(actor, Cap.FINANCE_INVOICES_MANAGE)
    return _void_invoice(invoice, reason, actor)


@transaction.atomic
def create_competition_invoice(charge, actor):
    """Issue a single-line COMPETITION invoice for a registration fee, due today
    (competition fees are paid at registration). The competition service
    enforces its own capability before calling this."""
    invoice = _create_invoice(charge.student.family, [charge], actor, kind=Invoice.Kind.COMPETITION,
                              due_date=timezone.localdate(), notes="Competition registration fee")
    return _issue_invoice(invoice, actor)


@transaction.atomic
def cancel_unpaid_competition_charge(charge, reason, actor):
    """On withdrawal before payment: void the unpaid competition invoice and
    cancel the charge. Paid fees are left untouched (non-refundable)."""
    charge = Charge.objects.select_for_update().get(pk=charge.pk)
    if charge.valid_allocations().exists():
        return False
    item = charge.active_invoice_item()
    if item is not None:
        _void_invoice(item.invoice, reason, actor)
    with audit_context(actor, reason):
        charge.status = Charge.Status.CANCELLED
        charge.save()
    return True


def calculate_invoice_balance(invoice):
    """Recompute paid/balance/status from the valid allocations (lock held by caller)."""
    items = list(invoice.items.all())
    for item in items:
        paid = (item.allocations.filter(payment__status=Payment.Status.VALID).aggregate(t=Sum("amount"))["t"] or ZERO)
        if paid != item.amount_paid:
            item.amount_paid = paid
            item.save()
    invoice.amount_paid = sum((i.amount_paid for i in items if i.is_active), ZERO)
    invoice.balance_due = invoice.total - invoice.amount_paid
    if invoice.status in Invoice.OPEN_FOR_PAYMENT + (Invoice.Status.PAID,):
        if invoice.amount_paid <= ZERO:
            invoice.status = Invoice.Status.ISSUED
        elif invoice.amount_paid < invoice.total:
            invoice.status = Invoice.Status.PARTIALLY_PAID
        else:
            invoice.status = Invoice.Status.PAID
    invoice.save()
    return invoice


# --------------------------------------------------------------------------- payments and receipts


def _receipt_content(payment, number, issued_at, actor):
    academy = settings.ACADEMY
    lines = []
    for allocation in payment.allocations.select_related("invoice", "invoice_item").order_by("id"):
        item = allocation.invoice_item
        lines.append({
            "invoice_number": allocation.invoice.number,
            "student_no": item.student_no,
            "student_name": item.student_name,
            "description": item.description,
            "fee_type": item.get_fee_type_display(),
            "period_start": item.period_start.isoformat() if item.period_start else None,
            "period_end": item.period_end.isoformat() if item.period_end else None,
            "line_amount": str(item.amount),
            "amount_paid": str(allocation.amount),
        })
    return {
        "academy": {
            "name": academy["NAME"],
            "registration_no": academy.get("REGISTRATION_NO", ""),
            "address": academy.get("ADDRESS", ""),
            "phone": academy.get("PHONE", ""),
            "email": academy.get("EMAIL", ""),
        },
        "number": number,
        "payment_number": payment.number,
        "issued_at": timezone.localtime(issued_at).isoformat(),
        "currency": academy["CURRENCY"],
        "students": sorted({f"{line['student_name']} ({line['student_no']})" for line in lines}),
        "payer_reference": payment.payer_name,
        "payment_date": timezone.localtime(payment.received_at).isoformat(),
        "payment_method": payment.get_method_display(),
        "reference": payment.reference,
        "lines": lines,
        "total": str(payment.amount),
        "issued_by": (actor.get_full_name() or actor.username) if actor else "",
    }


def _issue_receipt(payment, actor):
    if hasattr(payment, "receipt"):
        return payment.receipt
    issued_at = timezone.now()
    number = next_document_number(DocumentSequence.DocType.RECEIPT, timezone.localtime(issued_at).year)
    receipt = Receipt.objects.create(
        number=number, payment=payment, issued_at=issued_at, issued_by=actor, payer_name=payment.payer_name,
        total=payment.amount, content=_receipt_content(payment, number, issued_at, actor),
    )
    audit_record(receipt, AuditLog.Action.EVENT, {"issued": {"from": None, "to": number}},
                 reason="Receipt issued", category=AuditCategory.FINANCE, actor=actor)
    return receipt


def _distribute(invoice, amount):
    """Spread an amount over an invoice's open lines in line order."""
    plan, remaining = [], amount
    for item in invoice.active_items().select_for_update().order_by("position", "pk"):
        if remaining <= ZERO:
            break
        share = min(item.outstanding, remaining)
        if share > ZERO:
            plan.append((item, share))
            remaining -= share
    if remaining > ZERO:
        raise ValidationError(f"RM {amount} exceeds the balance due on {invoice.number}.")
    return plan


@transaction.atomic
def record_payment(allocations, method, actor=None, *, payer_name="", reference="", received_at=None, notes="",
                   amount=None, idempotency_key=None):
    """Record money received and apply it to one or more ISSUED invoices of one family.

    ``allocations`` is a list of ``(invoice, amount)``. Each amount is spread over
    that invoice's open lines (and so over the students on it). The payment must
    be fully allocated: overpayment / unallocated credit is not supported and is
    rejected, as is anything above an invoice's balance. Issues the receipt.
    Returns ``(payment, receipt)``.
    """
    require(actor, Cap.FINANCE_PAYMENTS_RECORD)
    if method not in Payment.Method.values:
        raise ValidationError(f"Unknown payment method: {method}")
    if idempotency_key:
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing, existing.receipt
    if not allocations:
        raise ValidationError("Apply the payment to at least one invoice.")
    requested = [(inv, to_money(amt, field="allocation", allow_zero=False)) for inv, amt in allocations]
    if len({inv.pk for inv, _ in requested}) != len(requested):
        raise ValidationError("Each invoice may appear only once in a payment.")
    total = sum((amt for _, amt in requested), ZERO)
    if amount is not None and to_money(amount, allow_zero=False) != total:
        raise ValidationError(f"Allocated RM {total} does not match the payment amount RM {to_money(amount)}; "
                              "overpayment and unallocated credit are not supported.")
    received_at = received_at or timezone.now()
    if timezone.is_naive(received_at):
        raise ValidationError("received_at must be timezone-aware.")

    # Lock the invoices first (ascending id), then re-read balances.
    locked = _lock_invoices([inv for inv, _ in requested])
    if idempotency_key:
        # A concurrent retry may have committed while we waited for the lock.
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing, existing.receipt
    families = {inv.family_id for inv in locked.values()}
    if len(families) != 1:
        raise ValidationError("A payment can only cover invoices of one family.")
    plan = []
    for inv, amt in requested:
        invoice = locked[inv.pk]
        if invoice.status not in Invoice.OPEN_FOR_PAYMENT:
            raise ValidationError(f"{invoice} is {invoice.get_status_display().lower()} and cannot take payments.")
        if amt > invoice.balance_due:
            raise ValidationError(f"RM {amt} exceeds the balance due RM {invoice.balance_due} on {invoice.number}.")
        plan.append((invoice, _distribute(invoice, amt)))

    with audit_context(actor, "Payment received"):
        payment = Payment.objects.create(
            number=next_document_number(DocumentSequence.DocType.PAYMENT),
            family_id=families.pop(), payer_name=payer_name, amount=total, method=method, reference=reference,
            received_at=received_at, received_by=actor, notes=notes, idempotency_key=idempotency_key or None,
        )
        settled = []
        for invoice, shares in plan:
            for item, share in shares:
                PaymentAllocation.objects.create(payment=payment, invoice=invoice, invoice_item=item,
                                                 charge_id=item.charge_id, amount=share)
            calculate_invoice_balance(invoice)
            for item, _ in shares:
                charge = Charge.objects.select_for_update().get(pk=item.charge_id)
                charge.refresh_status()
                if charge.status == Charge.Status.PAID:
                    settled.append(charge)
        receipt = _issue_receipt(payment, actor)
        for charge in settled:
            signals.charge_settled.send(sender=Charge, charge=charge, actor=actor)
    return payment, receipt


@transaction.atomic
def void_payment(payment, reason, actor=None):
    """Void a payment and its receipt (e.g. a bounced cheque or a keying error).
    Nothing is edited: the payment is marked VOIDED, the receipt gets a void
    record, and the invoices re-open by the amount that was applied."""
    require(actor, Cap.FINANCE_PAYMENTS_VOID)
    if not (reason or "").strip():
        raise ValidationError("A reason is required to void a payment.")
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.status == Payment.Status.VOIDED:
        raise ValidationError("Payment is already voided.")
    if payment.refunds.exists():
        raise ValidationError("Money was already refunded against this payment; it cannot be voided.")
    invoices = Invoice.objects.filter(allocations__payment=payment).distinct()
    locked = _lock_invoices(invoices)
    with audit_context(actor, reason):
        payment.status = Payment.Status.VOIDED
        payment.save()
        if hasattr(payment, "receipt"):
            ReceiptVoid.objects.create(receipt=payment.receipt, voided_by=actor, reason=reason)
            audit_record(payment.receipt, AuditLog.Action.EVENT, {"voided": {"from": False, "to": True}},
                         reason=reason, category=AuditCategory.FINANCE, actor=actor)
        for invoice in locked.values():
            calculate_invoice_balance(invoice)
        for allocation in payment.allocations.all():
            charge = Charge.objects.select_for_update().get(pk=allocation.charge_id)
            was_paid = charge.status == Charge.Status.PAID
            charge.refresh_status()
            if was_paid and charge.status != Charge.Status.PAID:
                signals.charge_unsettled.send(sender=Charge, charge=charge, actor=actor)
    return payment


@transaction.atomic
def record_exceptional_refund(allocation, amount, reason, actor=None, *, method=Payment.Method.BANK_TRANSFER,
                              reference="", refunded_at=None):
    """Record money returned against one payment line, as an authorized exception.

    Competition fees are normally non-refundable; this is never triggered
    automatically (withdrawal or status changes do not refund). The payment,
    allocation, invoice lines and receipt stay exactly as they were; the refund
    is its own numbered record and the invoice's ``amount_refunded`` goes up.
    """
    require(actor, Cap.FINANCE_REFUNDS_RECORD)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required for a refund.")
    amount = to_money(amount, allow_zero=False)
    allocation = PaymentAllocation.objects.select_related("payment", "invoice").get(pk=allocation.pk)
    invoice = _lock_invoices([allocation.invoice])[allocation.invoice_id]
    payment = Payment.objects.select_for_update().get(pk=allocation.payment_id)
    if payment.status != Payment.Status.VALID:
        raise ValidationError("Refunds can only be made against a valid payment.")
    already = allocation.refunds.aggregate(t=Sum("amount"))["t"] or ZERO
    if amount > allocation.amount - already:
        raise ValidationError(f"RM {amount} exceeds the refundable RM {allocation.amount - already} on this line.")
    with audit_context(actor, reason):
        refund = Refund.objects.create(
            number=next_document_number(DocumentSequence.DocType.REFUND), payment=payment, allocation=allocation,
            amount=amount, method=method, reference=reference, reason=reason,
            refunded_at=refunded_at or timezone.now(), recorded_by=actor,
        )
        invoice.amount_refunded += amount
        invoice.save()
    return refund
