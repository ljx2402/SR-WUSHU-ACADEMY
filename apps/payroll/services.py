from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.academy.models import SessionCoach, TrainingSession
from apps.accounts.models import Coach
from apps.audit.context import audit_context
from apps.finance.services import month_bounds

from .models import CoachRate, PayrollAdjustment, PayrollRun, Payslip, PayslipLine

ZERO = Decimal("0.00")
R = CoachRate.RateType


def _q(value):
    return Decimal(value).quantize(Decimal("0.01"))


class RateBook:
    """All rates for one coach, looked up by type, class and date."""

    def __init__(self, coach):
        self.rates = list(CoachRate.objects.filter(coach=coach))

    def find(self, rate_type, date, training_class_id=None):
        candidates = [
            r for r in self.rates
            if r.rate_type == rate_type
            and r.training_class_id == training_class_id
            and r.effective_from <= date
            and (r.effective_to is None or date <= r.effective_to)
        ]
        return max(candidates, key=lambda r: r.effective_from) if candidates else None

    def monthly_for(self, first, last):
        candidates = [
            r for r in self.rates
            if r.rate_type == R.MONTHLY and r.effective_from <= last and (r.effective_to is None or r.effective_to >= first)
        ]
        return max(candidates, key=lambda r: r.effective_from) if candidates else None


def price_session(book, slot, has_monthly):
    """Return (rate, quantity, amount, note) for one coached session."""
    session = slot.session
    cls = session.training_class_id
    hours = session.duration_hours
    if slot.is_substitute:
        order = [(R.SUBSTITUTE_PER_SESSION, cls), (R.SUBSTITUTE_HOURLY, cls),
                 (R.SUBSTITUTE_PER_SESSION, None), (R.SUBSTITUTE_HOURLY, None),
                 (R.PER_SESSION, cls), (R.HOURLY, cls), (R.PER_SESSION, None), (R.HOURLY, None)]
    else:
        order = [(R.PER_SESSION, cls), (R.HOURLY, cls), ("MONTHLY_COVERS", None),
                 (R.PER_SESSION, None), (R.HOURLY, None)]
    for rate_type, class_id in order:
        if rate_type == "MONTHLY_COVERS":
            if has_monthly:
                return ZERO, Decimal("1"), ZERO, "covered by monthly salary"
            continue
        rate = book.find(rate_type, session.date, class_id)
        if rate is None:
            continue
        if rate_type in (R.HOURLY, R.SUBSTITUTE_HOURLY):
            return rate.amount, hours, _q(rate.amount * hours), f"{hours} h × RM {rate.amount}"
        return rate.amount, Decimal("1"), _q(rate.amount), f"per session RM {rate.amount}"
    return ZERO, Decimal("1"), ZERO, "NO RATE SET"


def calculate_payslip(run, coach):
    first, last = month_bounds(run.year, run.month)
    book = RateBook(coach)
    monthly = book.monthly_for(first, last)
    slots = (
        SessionCoach.objects.filter(coach=coach, status=SessionCoach.Status.ASSIGNED, session__date__range=(first, last))
        .exclude(session__status=TrainingSession.Status.CANCELLED)
        .select_related("session__training_class")
        .order_by("session__date", "session__start_time")
    )
    adjustments = PayrollAdjustment.objects.filter(coach=coach, year=run.year, month=run.month)
    if not slots.exists() and monthly is None and not adjustments.exists():
        return None

    payslip = Payslip.objects.create(run=run, coach=coach)
    lines = []
    totals = defaultdict(lambda: ZERO)
    hours = ZERO
    counts = {"regular": 0, "substitute": 0}
    if monthly is not None:
        lines.append(PayslipLine(payslip=payslip, kind=PayslipLine.Kind.MONTHLY, description="Monthly salary",
                                 rate=monthly.amount, amount=monthly.amount))
    for slot in slots:
        rate, quantity, amount, note = price_session(book, slot, monthly is not None)
        session = slot.session
        kind = PayslipLine.Kind.SUBSTITUTE_SESSION if slot.is_substitute else PayslipLine.Kind.REGULAR_SESSION
        counts["substitute" if slot.is_substitute else "regular"] += 1
        hours += session.duration_hours
        label = "Substitute: " if slot.is_substitute else ""
        lines.append(PayslipLine(
            payslip=payslip, kind=kind, session=session, quantity=quantity, rate=rate, amount=amount,
            description=f"{label}{session.training_class.name} {session.date:%d/%m} {session.start_time:%H:%M} ({note})",
        ))
    for adj in adjustments:
        kind = PayslipLine.Kind(adj.kind)
        amount = -adj.amount if adj.kind == PayrollAdjustment.Kind.DEDUCTION else adj.amount
        lines.append(PayslipLine(payslip=payslip, kind=kind, description=adj.description, rate=adj.amount, amount=amount))

    for line in lines:
        line.save()
        totals["deductions" if line.amount < 0 else "gross"] += line.amount
    payslip.gross_pay = _q(totals["gross"])
    payslip.total_deductions = _q(-totals["deductions"])
    payslip.net_pay = _q(totals["gross"] + totals["deductions"])
    payslip.regular_sessions = counts["regular"]
    payslip.substitute_sessions = counts["substitute"]
    payslip.hours = hours
    payslip.save()
    return payslip


@transaction.atomic
def calculate_run(year, month, actor=None):
    """(Re)calculate a draft payroll month from sessions, rates and adjustments."""
    run, _ = PayrollRun.objects.get_or_create(year=year, month=month)
    if run.is_locked:
        raise ValidationError("This payroll is finalized and cannot be recalculated.")
    with audit_context(actor, "Payroll calculated"):
        for payslip in run.payslips.all():
            payslip.delete()
        for coach in Coach.objects.all():
            calculate_payslip(run, coach)
        run.calculated_at = timezone.now()
        run.save()
    return run


@transaction.atomic
def finalize_run(run, actor=None):
    if run.is_locked:
        raise ValidationError("Already finalized.")
    if run.calculated_at is None:
        raise ValidationError("Calculate the payroll before finalizing it.")
    with audit_context(actor, "Payroll finalized"):
        run.status = PayrollRun.Status.FINALIZED
        run.finalized_at = timezone.now()
        run.finalized_by = actor
        run.save()
    return run
