"""Coach payroll: per-session pay for a calendar-month payroll period.

Eligibility. A coach assignment (``SessionCoach``) in the period is paid when:
* its status is ASSIGNED: a regular coach who was not replaced, or an
  authorized substitute whose authorization was neither revoked nor cancelled.
  The authorization is the only source of truth; attendance records are
  never used to infer who coached;
* the session is not cancelled;
* the session has ended (future and in-progress sessions are not paid);
* the session date is inside the period;
* the assignment was not already paid in another payroll run.
Everything else in the period is listed in ``PayrollRun.excluded`` with its
reason.

Rates: see ``resolve_rate``. A missing rate is never paid as RM 0 silently:
the line is flagged MISSING_RATE, the run stays DRAFT and cannot be finalized.

Lifecycle: FINANCE_ADMIN (``payroll.prepare``) calculates, as often as needed,
while the run is not finalized. SUPER_ADMIN (``payroll.finalize``) finalizes a
READY run after the period has ended, and only if nothing changed since it was
calculated. Both lock the run row, so simultaneous calculations or
finalizations happen one after the other.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.academy.models import SessionCoach, TrainingSession
from apps.accounts.capabilities import Cap, require
from apps.accounts.models import Coach
from apps.audit.context import audit_context

from .models import CoachRate, PayrollAdjustment, PayrollRun, Payslip, PayslipLine

ZERO = Decimal("0.00")
R = CoachRate.RateType


class Rule:
    CLASS_REGULAR = "CLASS_REGULAR"
    GENERAL_REGULAR = "GENERAL_REGULAR"
    CLASS_SUBSTITUTE = "CLASS_SUBSTITUTE"
    GENERAL_SUBSTITUTE = "GENERAL_SUBSTITUTE"
    SUBSTITUTE_USES_CLASS_REGULAR = "SUBSTITUTE_USES_CLASS_REGULAR"
    SUBSTITUTE_USES_GENERAL_REGULAR = "SUBSTITUTE_USES_GENERAL_REGULAR"
    MISSING = "MISSING_RATE"

    LABELS = {
        CLASS_REGULAR: "class per-session rate",
        GENERAL_REGULAR: "general per-session rate",
        CLASS_SUBSTITUTE: "class substitute rate",
        GENERAL_SUBSTITUTE: "general substitute rate",
        SUBSTITUTE_USES_CLASS_REGULAR: "no substitute rate set: class per-session rate",
        SUBSTITUTE_USES_GENERAL_REGULAR: "no substitute rate set: general per-session rate",
        MISSING: "NO RATE SET",
    }


REGULAR_ORDER = [(R.PER_SESSION, True, Rule.CLASS_REGULAR), (R.PER_SESSION, False, Rule.GENERAL_REGULAR)]
SUBSTITUTE_ORDER = [
    (R.SUBSTITUTE_PER_SESSION, True, Rule.CLASS_SUBSTITUTE),
    (R.SUBSTITUTE_PER_SESSION, False, Rule.GENERAL_SUBSTITUTE),
    (R.PER_SESSION, True, Rule.SUBSTITUTE_USES_CLASS_REGULAR),
    (R.PER_SESSION, False, Rule.SUBSTITUTE_USES_GENERAL_REGULAR),
]


class RateBook:
    """A coach's per-session rates, looked up by type, class and date."""

    def __init__(self, coach):
        self.rates = list(CoachRate.objects.filter(coach=coach, rate_type__in=CoachRate.ACTIVE_TYPES))

    def find(self, rate_type, date, training_class_id=None):
        candidates = [
            r for r in self.rates
            if r.rate_type == rate_type
            and r.training_class_id == training_class_id
            and r.effective_from <= date
            and (r.effective_to is None or date <= r.effective_to)
        ]
        return max(candidates, key=lambda r: (r.effective_from, r.pk)) if candidates else None


def resolve_rate(book, slot):
    """Return (CoachRate or None, rule) for one coach assignment."""
    session = slot.session
    order = SUBSTITUTE_ORDER if slot.is_substitute else REGULAR_ORDER
    for rate_type, class_specific, rule in order:
        rate = book.find(rate_type, session.date, session.training_class_id if class_specific else None)
        if rate is not None:
            return rate, rule
    return None, Rule.MISSING


@dataclass(frozen=True)
class LineSpec:
    coach_id: int
    kind: str
    description: str
    amount: Decimal
    rate: Decimal = ZERO
    session_id: int | None = None
    slot_id: int | None = None
    rate_source_id: int | None = None
    rule: str = ""
    issue: str = ""

    def signature(self):
        return _signature(self.coach_id, self.kind, self.slot_id, self.session_id, self.rate_source_id, self.rate,
                          self.amount, self.rule, self.issue, self.description)


def _signature(coach_id, kind, slot_id, session_id, rate_source_id, rate, amount, rule, issue, description):
    """Comparable, plain-value fingerprint of one payslip line."""
    return (coach_id, str(kind), slot_id, session_id, rate_source_id, str(rate), str(amount), str(rule), str(issue),
            "" if slot_id else str(description))


def _excluded_reason(slot, run, at):
    session = slot.session
    if slot.status != SessionCoach.Status.ASSIGNED:
        return {
            SessionCoach.Status.REPLACED: "Replaced by an authorized substitute",
            SessionCoach.Status.ABSENT: "Did not attend",
            SessionCoach.Status.REVOKED: "Substitute authorization revoked",
            SessionCoach.Status.CANCELLED: "Substitute authorization cancelled",
        }.get(slot.status, slot.get_status_display())
    if session.status == TrainingSession.Status.CANCELLED:
        return "Session cancelled"
    if session.ends_at > at:
        return "Session not completed yet"
    paid_elsewhere = slot.payslip_lines.exclude(payslip__run=run).select_related("payslip__run").first()
    if paid_elsewhere is not None:
        other = paid_elsewhere.payslip.run
        return f"Already paid in payroll {other.year}-{other.month:02d}"
    return None


def plan(run, at=None):
    """What calculating ``run`` would produce now: (line specs, excluded rows).
    Pure: writes nothing."""
    at = at or timezone.now()
    slots = (
        SessionCoach.objects.filter(session__date__range=(run.period_start, run.period_end))
        .select_related("session__training_class", "coach", "replaces")
        .order_by("coach__full_name", "session__date", "session__start_time", "id")
    )
    books = {}
    specs, excluded = [], []
    for slot in slots:
        session = slot.session
        reason = _excluded_reason(slot, run, at)
        if reason:
            excluded.append({"slot": slot.pk, "session": session.pk, "coach": slot.coach.full_name,
                             "date": session.date.isoformat(), "class": session.training_class.name,
                             "role": slot.role, "reason": reason})
            continue
        book = books.setdefault(slot.coach_id, RateBook(slot.coach))
        rate, rule = resolve_rate(book, slot)
        amount = rate.amount if rate else ZERO
        who = f"Substitute for {slot.replaces.full_name}: " if slot.is_substitute and slot.replaces else (
            "Substitute: " if slot.is_substitute else "")
        price = f"RM {rate.amount} per session" if rate else "NO RATE SET"
        specs.append(LineSpec(
            coach_id=slot.coach_id,
            kind=PayslipLine.Kind.SUBSTITUTE_SESSION if slot.is_substitute else PayslipLine.Kind.REGULAR_SESSION,
            description=(f"{who}{session.training_class.name} {session.date:%d/%m} {session.start_time:%H:%M} "
                         f"({price}; {Rule.LABELS[rule]})")[:255],
            amount=amount, rate=amount, session_id=session.pk, slot_id=slot.pk,
            rate_source_id=rate.pk if rate else None, rule=rule,
            issue=PayslipLine.Issue.MISSING_RATE if rate is None else "",
        ))
    for adj in PayrollAdjustment.objects.filter(year=run.year, month=run.month).order_by("id"):
        amount = -adj.amount if adj.kind == PayrollAdjustment.Kind.DEDUCTION else adj.amount
        specs.append(LineSpec(coach_id=adj.coach_id, kind=adj.kind, description=adj.description,
                              amount=amount, rate=adj.amount))
    return specs, excluded


def _stored_signatures(run):
    lines = PayslipLine.objects.filter(payslip__run=run).select_related("payslip")
    return sorted((_signature(line.payslip.coach_id, line.kind, line.slot_id, line.session_id, line.rate_source_id,
                             line.rate, line.amount, line.rule, line.issue, line.description) for line in lines), key=repr)


def _get_run_for_update(year, month):
    if not (1 <= month <= 12) or year < 2000:
        raise ValidationError("Invalid payroll period.")
    try:
        with transaction.atomic():
            PayrollRun.objects.get_or_create(year=year, month=month)
    except IntegrityError:
        pass  # created by a simultaneous calculation; lock it below
    return PayrollRun.objects.select_for_update().get(year=year, month=month)


@transaction.atomic
def calculate_run(year, month, actor=None):
    """(Re)calculate a payroll period. Never touches a finalized run."""
    require(actor, Cap.PAYROLL_PREPARE)
    run = _get_run_for_update(year, month)
    if run.is_locked:
        raise ValidationError("This payroll is finalized and cannot be recalculated.")
    specs, excluded = plan(run)
    by_coach = defaultdict(list)
    for spec in specs:
        by_coach[spec.coach_id].append(spec)
    issues = sum(1 for spec in specs if spec.issue)
    with audit_context(actor, f"Payroll calculated: {len(specs)} lines, {len(excluded)} excluded, {issues} issues"):
        for payslip in run.payslips.all():
            payslip.delete()
        sessions = {s.pk: s for s in TrainingSession.objects.filter(
            pk__in=[spec.session_id for spec in specs if spec.session_id])}
        for coach in Coach.objects.filter(pk__in=by_coach).order_by("full_name"):
            _write_payslip(run, coach, by_coach[coach.pk], sessions)
        run.excluded = excluded
        run.calculated_at = timezone.now()
        run.calculated_by = actor
        run.status = PayrollRun.Status.DRAFT if issues else PayrollRun.Status.READY
        run.save()
    return run


def _write_payslip(run, coach, specs, sessions):
    payslip = Payslip.objects.create(run=run, coach=coach)
    gross = deductions = ZERO
    hours = ZERO
    counts = {PayslipLine.Kind.REGULAR_SESSION: 0, PayslipLine.Kind.SUBSTITUTE_SESSION: 0}
    for spec in specs:
        PayslipLine.objects.create(
            payslip=payslip, kind=spec.kind, description=spec.description, session_id=spec.session_id,
            slot_id=spec.slot_id, rate_source_id=spec.rate_source_id, rule=spec.rule, issue=spec.issue,
            rate=spec.rate, amount=spec.amount,
        )
        if spec.amount < 0:
            deductions += spec.amount
        else:
            gross += spec.amount
        if spec.kind in counts:
            counts[spec.kind] += 1
            hours += sessions[spec.session_id].duration_hours
    payslip.gross_pay = gross
    payslip.total_deductions = -deductions
    payslip.net_pay = gross + deductions
    payslip.regular_sessions = counts[PayslipLine.Kind.REGULAR_SESSION]
    payslip.substitute_sessions = counts[PayslipLine.Kind.SUBSTITUTE_SESSION]
    payslip.hours = hours
    payslip.save()
    return payslip


def period_over(run, at=None):
    """A period can be finalized only once it has ended (academy time zone)."""
    end = timezone.make_aware(datetime.datetime.combine(run.period_end + datetime.timedelta(days=1), datetime.time()),
                              timezone.get_default_timezone())
    return (at or timezone.now()) >= end


@transaction.atomic
def finalize_run(run, actor=None):
    """Lock a payroll period. SUPER_ADMIN only (``payroll.finalize``)."""
    require(actor, Cap.PAYROLL_FINALIZE)
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.is_locked:
        raise ValidationError("Already finalized.")
    if run.calculated_at is None:
        raise ValidationError("Calculate the payroll before finalizing it.")
    if run.status != PayrollRun.Status.READY:
        raise ValidationError(f"The payroll has {run.issue_count} unresolved issue(s), such as missing rates; "
                              "resolve them and recalculate.")
    if not period_over(run):
        raise ValidationError(f"The payroll period ends {run.period_end:%Y-%m-%d}; it can be finalized after that.")
    specs, _ = plan(run)
    if sorted((spec.signature() for spec in specs), key=repr) != _stored_signatures(run):
        raise ValidationError("Sessions, substitutes, rates or adjustments changed since this payroll was "
                              "calculated; recalculate and review it before finalizing.")
    with audit_context(actor, "Payroll finalized"):
        run.status = PayrollRun.Status.FINALIZED
        run.finalized_at = timezone.now()
        run.finalized_by = actor
        run.save()
    return run
