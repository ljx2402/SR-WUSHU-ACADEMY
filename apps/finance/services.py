import calendar
import datetime
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.academy.models import Enrollment, Student
from apps.attendance.models import AttendanceRecord, AttendanceStatus
from apps.audit.context import audit_context
from apps.audit.models import AuditCategory, AuditLog
from apps.audit.utils import record as audit_record

from .models import (
    ZERO,
    BillingCycle,
    Charge,
    ClassFee,
    FeeType,
    Payment,
    PaymentAllocation,
    Receipt,
    ReceiptSequence,
    ReceiptVoid,
    StudentFeePlan,
)


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
            discount = (gross * discount_percent / Decimal(100)).quantize(Decimal("0.01"))
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
               due_date=None, charge_item=None, notes=""):
    """One-off charge: registration, uniform, weapons, competition, other."""
    with audit_context(actor, "Charge added"):
        return Charge.objects.create(
            student=student,
            fee_type=fee_type,
            description=description,
            unit_amount=Decimal(unit_amount),
            quantity=Decimal(quantity),
            discount=Decimal(discount),
            due_date=due_date,
            charge_item=charge_item,
            notes=notes,
            created_by=actor,
        )


@transaction.atomic
def cancel_charge(charge, reason, actor=None, waive=False):
    if not reason:
        raise ValidationError("A reason is required.")
    if charge.valid_allocations().exists():
        raise ValidationError("This charge has payments; void the payment(s) first.")
    with audit_context(actor, reason):
        charge.status = Charge.Status.WAIVED if waive else Charge.Status.CANCELLED
        charge.save()
    return charge


def _next_receipt_number(year):
    sequence, _ = ReceiptSequence.objects.select_for_update().get_or_create(year=year)
    sequence.last_number += 1
    sequence.save()
    prefix = settings.ACADEMY.get("RECEIPT_PREFIX", "SRWA")
    return f"{prefix}-{year}-{sequence.last_number:06d}"


def _receipt_content(payment, number, issued_at, actor):
    academy = settings.ACADEMY
    lines = []
    for allocation in payment.allocations.select_related("charge__student").order_by("id"):
        charge = allocation.charge
        lines.append(
            {
                "student_no": charge.student.student_no,
                "student_name": charge.student.full_name,
                "description": charge.description,
                "fee_type": charge.get_fee_type_display(),
                "period_start": charge.period_start.isoformat() if charge.period_start else None,
                "period_end": charge.period_end.isoformat() if charge.period_end else None,
                "charge_amount": str(charge.amount),
                "amount_paid": str(allocation.amount),
            }
        )
    return {
        "academy": {
            "name": academy["NAME"],
            "registration_no": academy.get("REGISTRATION_NO", ""),
            "address": academy.get("ADDRESS", ""),
            "phone": academy.get("PHONE", ""),
            "email": academy.get("EMAIL", ""),
        },
        "number": number,
        "issued_at": timezone.localtime(issued_at).isoformat(),
        "currency": academy["CURRENCY"],
        "payer_name": payment.payer_name,
        "payment_date": payment.received_on.isoformat(),
        "payment_method": payment.get_method_display(),
        "reference": payment.reference,
        "lines": lines,
        "total": str(payment.amount),
        "issued_by": (actor.get_full_name() or actor.username) if actor else "",
    }


@transaction.atomic
def record_payment(payer_name, amount, method, allocations, actor=None, parent=None, reference="",
                   received_on=None, notes=""):
    """Record a payment, allocate it to charges and issue the official receipt.

    ``allocations`` is a list of (charge, amount). The allocations must add up
    to the payment amount and may not exceed each charge's outstanding balance.
    """
    amount = Decimal(amount)
    if not allocations:
        raise ValidationError("Allocate the payment to at least one charge.")
    total = sum((Decimal(a) for _, a in allocations), ZERO)
    if total != amount:
        raise ValidationError(f"Allocated RM {total} does not match the payment amount RM {amount}.")
    for charge, allocated in allocations:
        charge.refresh_from_db()
        if charge.status in (Charge.Status.CANCELLED, Charge.Status.WAIVED):
            raise ValidationError(f"Charge '{charge.description}' is {charge.get_status_display().lower()}.")
        if Decimal(allocated) > charge.balance:
            raise ValidationError(f"RM {allocated} exceeds the balance RM {charge.balance} of '{charge.description}'.")
        if parent is not None and not charge.student.guardianships.filter(parent=parent).exists():
            raise ValidationError(f"{charge.student.full_name} is not a child of {parent.full_name}.")

    with audit_context(actor, "Payment received"):
        payment = Payment.objects.create(
            parent=parent,
            payer_name=payer_name,
            amount=amount,
            method=method,
            reference=reference,
            received_on=received_on or timezone.localdate(),
            received_by=actor,
            notes=notes,
        )
        for charge, allocated in allocations:
            PaymentAllocation.objects.create(payment=payment, charge=charge, amount=Decimal(allocated))
            charge.refresh_status()
        receipt = issue_receipt(payment, actor)
    return payment, receipt


@transaction.atomic
def issue_receipt(payment, actor=None):
    if hasattr(payment, "receipt"):
        return payment.receipt
    issued_at = timezone.now()
    number = _next_receipt_number(timezone.localtime(issued_at).year)
    receipt = Receipt.objects.create(
        number=number,
        payment=payment,
        issued_at=issued_at,
        issued_by=actor,
        payer_name=payment.payer_name,
        total=payment.amount,
        content=_receipt_content(payment, number, issued_at, actor),
    )
    audit_record(receipt, AuditLog.Action.EVENT, {"issued": {"from": None, "to": number}},
                 reason="Receipt issued", category=AuditCategory.FINANCE, actor=actor)
    return receipt


@transaction.atomic
def void_payment(payment, reason, actor=None):
    """Void a payment and its receipt. The receipt itself is never altered;
    a ReceiptVoid record is attached and the charges become payable again."""
    if not reason:
        raise ValidationError("A reason is required to void a payment.")
    if payment.status == Payment.Status.VOIDED:
        raise ValidationError("Payment is already voided.")
    with audit_context(actor, reason):
        payment.status = Payment.Status.VOIDED
        payment.save()
        if hasattr(payment, "receipt"):
            ReceiptVoid.objects.create(receipt=payment.receipt, voided_by=actor, reason=reason)
            audit_record(payment.receipt, AuditLog.Action.EVENT, {"voided": {"from": False, "to": True}},
                         reason=reason, category=AuditCategory.FINANCE, actor=actor)
        for allocation in payment.allocations.select_related("charge"):
            allocation.charge.refresh_status()
    return payment


def outstanding_for_students(students):
    return Charge.objects.filter(student__in=students, status__in=[Charge.Status.UNPAID, Charge.Status.PARTIAL])
