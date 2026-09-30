"""Report builders. Each returns (columns, rows) for JSON or CSV export."""

import datetime
from collections import defaultdict

from django.db.models import Prefetch
from django.utils import timezone

from apps.academy.models import ClassCoach, Enrollment, Guardianship, Student, TrainingClass
from apps.attendance.models import MARKED_STATUSES, AttendanceRecord
from apps.attendance.services import expected_pairs, held_sessions, summarize
from apps.competitions.models import CompetitionRegistration, CompetitionResult
from apps.finance.models import Charge, Invoice, Payment, Receipt, Refund
from apps.payroll.models import Payslip


def _date(value, default=None):
    if not value:
        return default
    return datetime.date.fromisoformat(value)


def students_report(params):
    today = timezone.localdate()
    qs = Student.objects.prefetch_related(
        Prefetch("enrollments", queryset=Enrollment.objects.active_on(today).select_related("training_class", "team", "coach")),
        Prefetch("guardianships", queryset=Guardianship.objects.select_related("parent")),
    )
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("class"):
        qs = qs.filter(enrollments__in=Enrollment.objects.active_on(today).filter(training_class_id=params["class"]))
    class_coaches = {}
    for cc in ClassCoach.objects.active_on(today).select_related("coach"):
        class_coaches.setdefault(cc.training_class_id, []).append(cc.coach.full_name)
    columns = ["student_no", "full_name", "chinese_name", "gender", "date_of_birth", "age", "status", "join_date",
               "classes", "teams", "coaches", "parents"]
    rows = []
    for s in qs.distinct():
        enrollments = list(s.enrollments.all())
        coaches = set()
        for e in enrollments:
            coaches.update([e.coach.full_name] if e.coach else class_coaches.get(e.training_class_id, []))
        rows.append([
            s.student_no, s.full_name, s.chinese_name, s.gender, s.date_of_birth.isoformat(), s.age_on(today),
            s.status, s.join_date.isoformat(),
            "; ".join(e.training_class.name for e in enrollments),
            "; ".join(sorted({e.team.name for e in enrollments if e.team})),
            "; ".join(sorted(coaches)),
            "; ".join(f"{g.parent.full_name} ({g.get_relationship_display()}, {g.parent.phone})" for g in s.guardianships.all()),
        ])
    return columns, rows


def attendance_report(params):
    """Per student and class: counts, expected sessions held, unmarked, and the
    percentage. Unmarked sessions are listed separately and never counted as absent."""
    today = timezone.localdate()
    start = _date(params.get("start"), today.replace(day=1))
    end = _date(params.get("end"), today)
    records = AttendanceRecord.objects.filter(session__date__range=(start, end)).exclude(session__status="CANCELLED")
    training_class = None
    if params.get("class"):
        records = records.filter(session__training_class_id=params["class"])
        training_class = TrainingClass.objects.filter(pk=params["class"]).first()
    # An unknown class id matches nothing (never "all classes").
    sessions = [] if params.get("class") and training_class is None else held_sessions(start, end, training_class)
    session_class = {session.id: session.training_class_id for session in sessions}
    expected = defaultdict(set)
    for session_id, student_id in expected_pairs(sessions):
        expected[(student_id, session_class[session_id])].add(session_id)
    marked = defaultdict(set)
    for student_id, class_id, session_id in records.filter(status__in=MARKED_STATUSES).values_list(
            "student_id", "session__training_class_id", "session_id"):
        marked[(student_id, class_id)].add(session_id)
    pairs = set(records.values_list("student_id", "session__training_class_id").distinct()) | set(expected)
    students = {s.id: s for s in Student.objects.filter(id__in={p[0] for p in pairs})}
    classes = {c.id: c for c in TrainingClass.objects.filter(id__in={p[1] for p in pairs})}
    columns = ["student_no", "full_name", "class", "present", "late", "absent", "excused", "total", "expected",
               "unmarked", "attendance_pct", "start", "end"]
    rows = []
    for student_id, class_id in sorted(pairs, key=lambda p: (classes[p[1]].name, students[p[0]].full_name)):
        s = summarize(records.filter(student_id=student_id, session__training_class_id=class_id))
        pair_expected = expected.get((student_id, class_id), set())
        student = students[student_id]
        rows.append([student.student_no, student.full_name, classes[class_id].name, s["present"], s["late"],
                     s["absent"], s["excused"], s["total"], len(pair_expected),
                     len(pair_expected - marked.get((student_id, class_id), set())),
                     str(s["percentage"]) if s["percentage"] is not None else "", start.isoformat(), end.isoformat()])
    return columns, rows


def fees_report(params):
    qs = Charge.objects.select_related("student")
    if params.get("start"):
        qs = qs.filter(created_at__date__gte=_date(params["start"]))
    if params.get("end"):
        qs = qs.filter(created_at__date__lte=_date(params["end"]))
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("fee_type"):
        qs = qs.filter(fee_type=params["fee_type"])
    columns = ["charge_id", "student_no", "student", "fee_type", "description", "period_start", "period_end",
               "amount", "paid", "balance", "status", "due_date", "created_at"]
    rows = []
    for c in qs.order_by("created_at"):
        paid = c.amount_paid
        rows.append([c.pk, c.student.student_no, c.student.full_name, c.fee_type, c.description,
                     c.period_start.isoformat() if c.period_start else "", c.period_end.isoformat() if c.period_end else "",
                     str(c.amount), str(paid), str(c.balance), c.status, c.due_date.isoformat() if c.due_date else "",
                     timezone.localtime(c.created_at).strftime("%Y-%m-%d %H:%M")])
    return columns, rows


def payments_report(params):
    qs = Payment.objects.select_related("receipt", "received_by", "family")
    if params.get("start"):
        qs = qs.filter(received_at__date__gte=_date(params["start"]))
    if params.get("end"):
        qs = qs.filter(received_at__date__lte=_date(params["end"]))
    if params.get("method"):
        qs = qs.filter(method=params["method"])
    columns = ["payment_no", "received_at", "family", "amount", "method", "reference", "status", "receipt_no",
               "received_by"]
    rows = []
    for p in qs.order_by("received_at", "id"):
        receipt = getattr(p, "receipt", None)
        rows.append([p.number, timezone.localtime(p.received_at).strftime("%Y-%m-%d %H:%M"), p.family.name,
                     str(p.amount), p.method, p.reference, p.status, receipt.number if receipt else "",
                     p.received_by.username if p.received_by else ""])
    return columns, rows


def invoices_report(params):
    qs = Invoice.objects.exclude(status=Invoice.Status.DRAFT).select_related("family")
    if params.get("start"):
        qs = qs.filter(issue_date__gte=_date(params["start"]))
    if params.get("end"):
        qs = qs.filter(issue_date__lte=_date(params["end"]))
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    columns = ["invoice_no", "issue_date", "due_date", "family", "kind", "status", "total", "amount_paid",
               "balance_due", "amount_refunded", "students"]
    rows = []
    for inv in qs.order_by("issue_date", "number").prefetch_related("items"):
        students = sorted({i.student_name for i in inv.items.all() if i.is_active or inv.status == Invoice.Status.VOID})
        rows.append([inv.number, inv.issue_date.isoformat() if inv.issue_date else "",
                     inv.due_date.isoformat() if inv.due_date else "", inv.family_name, inv.kind, inv.status,
                     str(inv.total), str(inv.amount_paid), str(inv.balance_due), str(inv.amount_refunded),
                     "; ".join(students)])
    return columns, rows


def refunds_report(params):
    qs = Refund.objects.select_related("payment", "allocation__invoice_item", "recorded_by")
    if params.get("start"):
        qs = qs.filter(refunded_at__date__gte=_date(params["start"]))
    if params.get("end"):
        qs = qs.filter(refunded_at__date__lte=_date(params["end"]))
    columns = ["refund_no", "refunded_at", "payment_no", "student", "description", "amount", "method", "reason",
               "recorded_by"]
    rows = [[r.number, timezone.localtime(r.refunded_at).strftime("%Y-%m-%d %H:%M"), r.payment.number,
             r.allocation.invoice_item.student_name, r.allocation.invoice_item.description, str(r.amount), r.method,
             r.reason, r.recorded_by.username if r.recorded_by else ""] for r in qs.order_by("refunded_at", "id")]
    return columns, rows


def receipts_report(params):
    qs = Receipt.objects.select_related("payment")
    if params.get("start"):
        qs = qs.filter(issued_at__date__gte=_date(params["start"]))
    if params.get("end"):
        qs = qs.filter(issued_at__date__lte=_date(params["end"]))
    columns = ["receipt_no", "issued_at", "payment_no", "students", "total", "method", "void", "void_reason"]
    rows = []
    for r in qs.order_by("issued_at"):
        void = getattr(r, "void_record", None)
        rows.append([r.number, timezone.localtime(r.issued_at).strftime("%Y-%m-%d %H:%M"), r.payment.number,
                     "; ".join(r.content.get("students", [])), str(r.total),
                     r.payment.method, "YES" if void else "", void.reason if void else ""])
    return columns, rows


def competitions_report(params):
    qs = CompetitionRegistration.objects.select_related("event__competition", "student", "charge")
    if params.get("competition"):
        qs = qs.filter(event__competition_id=params["competition"])
    columns = ["competition", "event", "event_type", "student_no", "student", "gender", "age", "status", "fee",
               "fee_status"]
    rows = []
    for r in qs.order_by("event__competition__start_date", "event__name", "student__full_name"):
        comp = r.event.competition
        rows.append([comp.name, r.event.name, r.event.event_type, r.student.student_no, r.student.full_name,
                     r.student.gender, r.student.age_on(comp.age_reference_date or comp.start_date), r.status,
                     str(r.charge.amount) if r.charge else "0.00", r.charge.status if r.charge else ""])
    return columns, rows


def results_report(params):
    qs = CompetitionResult.objects.select_related("registration__event__competition", "registration__student")
    if params.get("competition"):
        qs = qs.filter(registration__event__competition_id=params["competition"])
    columns = ["competition", "date", "event", "student_no", "student", "placing", "medal", "score", "remarks"]
    rows = []
    for r in qs.order_by("registration__event__competition__start_date", "registration__event__name", "placing"):
        reg = r.registration
        rows.append([reg.event.competition.name, reg.event.competition.start_date.isoformat(), reg.event.name,
                     reg.student.student_no, reg.student.full_name, r.placing or "", r.medal,
                     str(r.score) if r.score is not None else "", r.remarks])
    return columns, rows


def payroll_report(params):
    qs = Payslip.objects.select_related("run", "coach")
    if params.get("year"):
        qs = qs.filter(run__year=int(params["year"]))
    if params.get("month"):
        qs = qs.filter(run__month=int(params["month"]))
    columns = ["year", "month", "run_status", "coach", "regular_sessions", "substitute_sessions", "hours", "gross_pay",
               "deductions", "net_pay", "bank_name", "bank_account_no"]
    rows = []
    for p in qs.order_by("run__year", "run__month", "coach__full_name"):
        rows.append([p.run.year, p.run.month, p.run.status, p.coach.full_name, p.regular_sessions, p.substitute_sessions,
                     str(p.hours), str(p.gross_pay), str(p.total_deductions), str(p.net_pay), p.coach.bank_name,
                     p.coach.bank_account_no])
    return columns, rows


REPORTS = {
    "students": students_report,
    "attendance": attendance_report,
    "fees": fees_report,
    "payments": payments_report,
    "invoices": invoices_report,
    "refunds": refunds_report,
    "receipts": receipts_report,
    "competitions": competitions_report,
    "results": results_report,
    "payroll": payroll_report,
}
