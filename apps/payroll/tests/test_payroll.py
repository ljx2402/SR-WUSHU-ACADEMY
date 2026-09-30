"""Coach payroll: per session, per class (Phase 4).

These replace the pre-Phase-4 tests that asserted hourly and monthly-salary
pay, which the final business decision (all coach fees are per session) rules
out. Their intent is kept: regular pay, substitute rate, replaced coach not
paid, adjustments, finalized payroll locked.
"""

import datetime
from contextlib import contextmanager
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction

from apps.academy.models import SessionCoach, TrainingSession
from apps.academy.services import (
    assign_substitute,
    cancel_session,
    reschedule_session,
    revoke_substitute,
)
from apps.academy.tests.base import AcademyTestCase, academy_time
from apps.audit.models import AuditLog
from apps.payroll.models import CoachRate, PayrollAdjustment, PayrollRun, Payslip, PayslipLine
from apps.payroll.services import Rule, calculate_run, finalize_run


@contextmanager
def at(moment):
    with mock.patch("django.utils.timezone.now", return_value=moment):
        yield


class PayrollTestCase(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.since = cls.today - datetime.timedelta(days=400)
        first_next = (cls.today.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        cls.after_month = academy_time(first_next, 9)   # the payroll month is over
        cls.a_regular = CoachRate.objects.create(coach=cls.coach_a, rate_type="PER_SESSION", amount=Decimal("80"),
                                                 effective_from=cls.since)
        cls.a_substitute = CoachRate.objects.create(coach=cls.coach_a, rate_type="SUBSTITUTE_PER_SESSION",
                                                    amount=Decimal("100"), effective_from=cls.since)
        cls.b_class = CoachRate.objects.create(coach=cls.coach_b, rate_type="PER_SESSION", amount=Decimal("70"),
                                               training_class=cls.class_b, effective_from=cls.since)

    def calculate(self, moment=None, actor=None):
        with at(moment or self.after_month):
            return calculate_run(self.today.year, self.today.month, actor or self.finance_user)

    def finalize(self, run, actor=None, moment=None):
        with at(moment or self.after_month):
            return finalize_run(run, actor or self.super_user)

    def lines(self, run, coach):
        return list(PayslipLine.objects.filter(payslip__run=run, payslip__coach=coach).order_by("id"))

    def later_session_today(self, training_class, coach, start=21, end=22):
        """A session later today (upcoming at the 20:00 test clock)."""
        session = training_class.sessions.create(date=self.today, start_time=datetime.time(start, 30),
                                                 end_time=datetime.time(end, 30))
        session.coach_slots.create(coach=coach)
        return session


class RatesAndEligibilityTests(PayrollTestCase):
    def test_regular_sessions_are_paid_per_session_with_the_rule_recorded(self):
        run = self.calculate()
        self.assertEqual(run.status, PayrollRun.Status.READY)
        a = run.payslips.get(coach=self.coach_a)
        b = run.payslips.get(coach=self.coach_b)
        self.assertEqual((a.gross_pay, a.regular_sessions), (Decimal("80.00"), 1))
        self.assertEqual((b.gross_pay, b.regular_sessions), (Decimal("70.00"), 1))
        line = self.lines(run, self.coach_b)[0]
        self.assertEqual((line.session, line.slot.coach, line.kind, line.rate, line.amount, line.rule, line.rate_source),
                         (self.session_b, self.coach_b, "REGULAR_SESSION", Decimal("70.00"), Decimal("70.00"),
                          Rule.CLASS_REGULAR, self.b_class))
        self.assertEqual(self.lines(run, self.coach_a)[0].rule, Rule.GENERAL_REGULAR)
        self.assertFalse(PayslipLine.objects.filter(kind="MONTHLY").exists())

    def test_class_rate_beats_general_rate(self):
        CoachRate.objects.create(coach=self.coach_a, rate_type="PER_SESSION", amount=Decimal("95"),
                                 training_class=self.class_a, effective_from=self.since)
        line = self.lines(self.calculate(), self.coach_a)[0]
        self.assertEqual((line.amount, line.rule), (Decimal("95.00"), Rule.CLASS_REGULAR))

    def test_substitute_gets_substitute_rate_and_replaced_coach_is_not_paid(self):
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user,
                          reason="Coach B at a course")
        run = self.calculate()
        a = run.payslips.get(coach=self.coach_a)
        self.assertEqual((a.gross_pay, a.regular_sessions, a.substitute_sessions), (Decimal("180.00"), 1, 1))
        sub_line = PayslipLine.objects.get(payslip=a, kind="SUBSTITUTE_SESSION")
        self.assertEqual((sub_line.session, sub_line.amount, sub_line.rule, sub_line.slot.replaces),
                         (self.session_b, Decimal("100.00"), Rule.GENERAL_SUBSTITUTE, self.coach_b))
        self.assertIn("Substitute for Coach B", sub_line.description)
        self.assertFalse(run.payslips.filter(coach=self.coach_b).exists())  # not paid twice for the session
        self.assertIn({"coach": "Coach B", "reason": "Replaced by an authorized substitute"},
                      [{"coach": r["coach"], "reason": r["reason"]} for r in run.excluded])

    def test_substitute_without_a_substitute_rate_uses_the_regular_rate(self):
        session = self.later_session_today(self.class_a, self.coach_a)
        assign_substitute(session, self.coach_b, replaces=self.coach_a, actor=self.admin_user, reason="swap")
        CoachRate.objects.create(coach=self.coach_b, rate_type="PER_SESSION", amount=Decimal("60"),
                                 effective_from=self.since)
        line = PayslipLine.objects.get(payslip__run=self.calculate(), slot__session=session)
        self.assertEqual((line.amount, line.rule), (Decimal("60.00"), Rule.SUBSTITUTE_USES_GENERAL_REGULAR))

    def test_missing_rate_is_flagged_never_silently_paid_and_blocks_finalization(self):
        self.b_class.effective_to = self.today - datetime.timedelta(days=30)
        self.b_class.save()
        run = self.calculate()
        self.assertEqual((run.status, run.issue_count), (PayrollRun.Status.DRAFT, 1))
        line = self.lines(run, self.coach_b)[0]
        self.assertEqual((line.issue, line.amount, line.rule), ("MISSING_RATE", Decimal("0.00"), Rule.MISSING))
        with self.assertRaisesMessage(ValidationError, "unresolved issue"):
            self.finalize(run)
        CoachRate.objects.create(coach=self.coach_b, rate_type="PER_SESSION", amount=Decimal("75"),
                                 effective_from=self.today - datetime.timedelta(days=29))
        run = self.calculate()
        self.assertEqual(run.status, PayrollRun.Status.READY)
        self.finalize(run)

    def test_legacy_hourly_and_monthly_rates_are_never_used(self):
        # Rows created before Phase 4 (bulk_create skips the model rule, as old data would).
        CoachRate.objects.filter(coach=self.coach_b).update(effective_to=self.since)
        CoachRate.objects.bulk_create([
            CoachRate(coach=self.coach_b, rate_type="MONTHLY", amount=Decimal("3000"), effective_from=self.since),
            CoachRate(coach=self.coach_b, rate_type="HOURLY", amount=Decimal("40"), effective_from=self.since),
        ])
        run = self.calculate()
        line = self.lines(run, self.coach_b)[0]
        self.assertEqual((line.issue, line.amount), ("MISSING_RATE", Decimal("0.00")))
        self.assertEqual(run.payslips.get(coach=self.coach_b).gross_pay, Decimal("0.00"))

    def test_rate_rules(self):
        for rate_type in ("HOURLY", "MONTHLY", "SUBSTITUTE_HOURLY"):
            with self.subTest(rate_type=rate_type), self.assertRaisesMessage(ValidationError, "per session"):
                CoachRate.objects.create(coach=self.coach_a, rate_type=rate_type, amount=Decimal("1"),
                                         effective_from=self.today)
        with self.assertRaisesMessage(ValidationError, "covers part of these dates"):
            CoachRate.objects.create(coach=self.coach_a, rate_type="PER_SESSION", amount=Decimal("90"),
                                     effective_from=self.today)

    def test_excluded_sessions_and_reasons(self):
        upcoming = self.later_session_today(self.class_a, self.coach_a)
        cancelled = self.class_a.sessions.create(date=self.today, start_time=datetime.time(8),
                                                 end_time=datetime.time(9))
        cancelled.coach_slots.create(coach=self.coach_a)
        cancel_session(cancelled, self.admin_user, "Rain")
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user,
                                 reason="x")
        revoke_substitute(slot, self.admin_user, "Coach B came after all")
        last_month = self.today.replace(day=1) - datetime.timedelta(days=1)
        old = TrainingSession.objects.create(training_class=self.class_a, date=last_month,
                                             start_time=datetime.time(17), end_time=datetime.time(19))
        old.coach_slots.create(coach=self.coach_a)
        run = self.calculate(moment=self.clock)  # 20:00 today: session B in progress, upcoming not started
        reasons = {(r["session"], r["coach"]): r["reason"] for r in run.excluded}
        self.assertEqual(reasons[(upcoming.pk, "Coach A")], "Session not completed yet")
        self.assertEqual(reasons[(self.session_b.pk, "Coach B")], "Session not completed yet")
        self.assertEqual(reasons[(cancelled.pk, "Coach A")], "Session cancelled")
        self.assertEqual(reasons[(self.session_b.pk, "Coach A")], "Substitute authorization revoked")
        self.assertNotIn(old.pk, {r["session"] for r in run.excluded})  # outside the period
        paid = set(PayslipLine.objects.filter(payslip__run=run, slot__isnull=False).values_list("session", flat=True))
        self.assertEqual(paid, {self.session_a.pk})
        # Once session B has ended, the restored original coach is paid for it.
        run = self.calculate()
        self.assertTrue(PayslipLine.objects.filter(payslip__run=run, session=self.session_b,
                                                   payslip__coach=self.coach_b).exists())

    def test_attendance_never_creates_pay(self):
        from apps.attendance.services import mark_attendance

        mark_attendance(self.session_b, self.student_2, "PRESENT", self.admin_user)  # recorded by an admin
        run = self.calculate()
        self.assertEqual(set(run.payslips.values_list("coach", flat=True)), {self.coach_a.pk, self.coach_b.pk})

    def test_adjustments(self):
        for kind, amount in (("ALLOWANCE", "50"), ("BONUS", "200"), ("DEDUCTION", "30")):
            PayrollAdjustment.objects.create(coach=self.coach_a, year=self.today.year, month=self.today.month,
                                             kind=kind, description=kind.title(), amount=Decimal(amount))
        a = self.calculate().payslips.get(coach=self.coach_a)
        self.assertEqual((a.gross_pay, a.total_deductions, a.net_pay),
                         (Decimal("330.00"), Decimal("30.00"), Decimal("300.00")))


class LifecycleTests(PayrollTestCase):
    def test_roles(self):
        for user in (self.admin_user, self.coach_a_user, self.parent_1_user):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                self.calculate(actor=user)
        run = self.calculate()
        self.assertEqual(run.calculated_by, self.finance_user)
        for user in (self.finance_user, self.admin_user, self.coach_a_user):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                self.finalize(run, actor=user)
        run = self.finalize(run)
        self.assertEqual((run.status, run.finalized_by), (PayrollRun.Status.FINALIZED, self.super_user))

    def test_calculation_and_finalization_are_audited(self):
        run = self.finalize(self.calculate())
        entries = AuditLog.objects.filter(object_id=str(run.pk), content_type__model="payrollrun")
        reasons = " | ".join(entries.values_list("reason", flat=True))
        self.assertIn("Payroll calculated", reasons)
        self.assertIn("Payroll finalized", reasons)

    def test_finalize_only_after_the_period_ends(self):
        run = self.calculate()
        last_minute = academy_time(run.period_end, 23, 59)
        with self.assertRaisesMessage(ValidationError, "can be finalized after"):
            self.finalize(run, moment=last_minute)
        self.finalize(run)

    def test_changes_after_calculation_block_finalization_until_recalculated(self):
        run = self.calculate()
        PayrollAdjustment.objects.create(coach=self.coach_a, year=run.year, month=run.month, kind="BONUS",
                                         description="Late bonus", amount=Decimal("10"))
        with self.assertRaisesMessage(ValidationError, "changed since"):
            self.finalize(run)
        self.finalize(self.calculate())

    def test_finalized_payroll_is_locked(self):
        run = self.finalize(self.calculate())
        payslip = Payslip.objects.get(run=run, coach=self.coach_a)
        with self.assertRaises(PermissionDenied):
            payslip.save()
        with self.assertRaises(PermissionDenied):
            payslip.lines.first().save()
        with self.assertRaises(PermissionDenied):
            Payslip.objects.filter(pk=payslip.pk).update(net_pay=Decimal("1"))
        with self.assertRaises(PermissionDenied):
            PayslipLine.objects.filter(payslip=payslip).update(amount=Decimal("1"))
        with self.assertRaises(PermissionDenied):
            run.delete()
        with self.assertRaisesMessage(ValidationError, "finalized"):
            self.calculate()
        with self.assertRaisesMessage(ValidationError, "finalized"):
            self.finalize(run)
        with self.assertRaises(ValidationError):
            PayrollAdjustment.objects.create(coach=self.coach_a, year=run.year, month=run.month, kind="BONUS",
                                             description="Late bonus", amount=Decimal("10"))
        run.status = PayrollRun.Status.DRAFT
        with self.assertRaises(PermissionDenied):
            run.save()
        # The rate used is history: it can be ended, not rewritten.
        self.a_regular.amount = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "finalized payroll"):
            self.a_regular.save()
        self.a_regular.refresh_from_db()
        self.a_regular.effective_to = self.after_month.date()
        self.a_regular.save()

    def test_sessions_of_a_finalized_month_are_fixed(self):
        self.finalize(self.calculate())
        with self.assertRaisesMessage(ValidationError, "finalized"):
            cancel_session(self.session_a, self.admin_user, "Too late")
        with self.assertRaisesMessage(ValidationError, "finalized"):
            TrainingSession.objects.create(training_class=self.class_a, date=self.today,
                                           start_time=datetime.time(6), end_time=datetime.time(7))
        with at(self.after_month), self.assertRaises(ValidationError):
            reschedule_session(self.session_a, self.admin_user, "move", date=self.after_month.date())
        self.assertEqual(TrainingSession.objects.get(pk=self.session_a.pk).status, "SCHEDULED")

    def test_revoking_a_paid_substitution_is_refused(self):
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user,
                                 reason="x")
        self.finalize(self.calculate())
        with self.assertRaisesMessage(ValidationError, "paid history"):
            revoke_substitute(slot, self.admin_user, "oops")

    def test_an_assignment_is_paid_at_most_once(self):
        run = self.calculate()
        line = self.lines(run, self.coach_a)[0]
        with self.assertRaises(IntegrityError), transaction.atomic():
            PayslipLine.objects.create(payslip=line.payslip, kind=line.kind, description="duplicate",
                                       slot=line.slot, amount=Decimal("80"))

    def test_recalculating_a_draft_replaces_its_payslips(self):
        self.calculate()
        run = self.calculate()
        self.assertEqual(Payslip.objects.filter(run=run).count(), 2)
        self.assertEqual(PayslipLine.objects.filter(payslip__run=run).count(), 2)


class DatabaseGuardTests(PayrollTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL triggers")
        self.run_ = self.finalize(self.calculate())

    def raw(self, sql, params):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, params)

    def test_raw_sql_cannot_rewrite_finalized_payroll_or_its_sessions(self):
        line = PayslipLine.objects.filter(payslip__run=self.run_).first()
        attempts = [
            ("UPDATE payroll_payslipline SET amount = 1 WHERE id = %s", [line.pk]),
            ("DELETE FROM payroll_payslipline WHERE id = %s", [line.pk]),
            ("UPDATE payroll_payslip SET net_pay = 1 WHERE run_id = %s", [self.run_.pk]),
            ("UPDATE payroll_payrollrun SET status = 'DRAFT' WHERE id = %s", [self.run_.pk]),
            ("DELETE FROM payroll_payrollrun WHERE id = %s", [self.run_.pk]),
            ("UPDATE academy_trainingsession SET status = 'CANCELLED' WHERE id = %s", [self.session_a.pk]),
            ("UPDATE academy_trainingsession SET date = date + 40 WHERE id = %s", [self.session_a.pk]),
        ]
        for sql, params in attempts:
            with self.subTest(sql=sql), self.assertRaises(IntegrityError):
                self.raw(sql, params)
        self.raw("UPDATE payroll_payrollrun SET notes = 'paid by transfer' WHERE id = %s", [self.run_.pk])
        self.raw("UPDATE academy_trainingsession SET notes = 'good session' WHERE id = %s", [self.session_a.pk])


class BankDetailTests(PayrollTestCase):
    def test_bank_details_only_for_finance_and_super_admin(self):
        from rest_framework.test import APIClient

        self.coach_a.bank_account_no = "1234567890"
        self.coach_a.save()
        for user, visible in ((self.super_user, True), (self.finance_user, True), (self.admin_user, False)):
            client = APIClient()
            client.force_authenticate(user)
            body = client.get(f"/api/coaches/{self.coach_a.pk}/").json()
            with self.subTest(user=user.username):
                self.assertEqual("bank_account_no" in body, visible)
        run = self.finalize(self.calculate())
        client = APIClient()
        client.force_authenticate(self.coach_a_user)
        self.assertEqual(client.get(f"/api/coaches/{self.coach_a.pk}/").status_code, 403)
        payslips = client.get("/api/payslips/").json()
        rows = payslips.get("results", payslips)
        self.assertEqual({row["id"] for row in rows}, {run.payslips.get(coach=self.coach_a).pk})
        self.assertNotIn("1234567890", str(payslips))
        for user in (self.parent_1_user, self.admin_user):
            client.force_authenticate(user)
            with self.subTest(user=user.username):
                self.assertEqual(client.get("/api/reports/payroll/").status_code, 403)
                self.assertEqual(client.get("/api/payroll-runs/").status_code, 403)
        client.force_authenticate(self.parent_1_user)
        self.assertEqual(client.get("/api/payslips/").status_code, 403)

    def test_admin_coach_page_hides_bank_fields_from_admin(self):
        self.client.force_login(self.admin_user)
        page = self.client.get(f"/admin/accounts/coach/{self.coach_a.pk}/change/").content.decode()
        self.assertNotIn("bank_account_no", page)
        self.client.force_login(self.finance_user)
        self.assertIn("bank_account_no", self.client.get(f"/admin/accounts/coach/{self.coach_a.pk}/change/")
                      .content.decode())


class PayrollApiAndAdminTests(PayrollTestCase):
    def api(self, user):
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_api_calculate_and_finalize_by_role(self):
        payload = {"year": self.today.year, "month": self.today.month}
        with at(self.after_month):
            self.assertEqual(self.api(self.admin_user).post("/api/payroll-runs/calculate/", payload).status_code, 403)
            response = self.api(self.finance_user).post("/api/payroll-runs/calculate/", payload)
            self.assertEqual(response.status_code, 200, response.content)
            run_id = response.json()["id"]
            self.assertEqual(response.json()["status"], "READY")
            url = f"/api/payroll-runs/{run_id}/finalize/"
            self.assertEqual(self.api(self.finance_user).post(url).status_code, 403)
            self.assertEqual(self.api(self.super_user).post(url).status_code, 200)
            self.assertEqual(self.api(self.super_user).post(url).status_code, 400)  # already finalized
        lines = self.api(self.finance_user).get("/api/payslips/").json()
        rows = lines.get("results", lines)
        self.assertTrue(all(line["rule"] for row in rows for line in row["lines"]))

    def test_admin_cannot_edit_amounts_or_status_or_finalize_as_finance(self):
        run = self.calculate()
        payslip = run.payslips.get(coach=self.coach_a)
        self.client.force_login(self.super_user)
        self.assertEqual(self.client.post(f"/admin/payroll/payslip/{payslip.pk}/change/",
                                          {"net_pay": "9999"}).status_code, 403)
        self.client.post(f"/admin/payroll/payrollrun/{run.pk}/change/",
                         {"year": run.year, "month": run.month, "status": "FINALIZED", "notes": "x"})
        self.assertEqual(PayrollRun.objects.get(pk=run.pk).status, PayrollRun.Status.READY)
        self.client.force_login(self.finance_user)
        with at(self.after_month):
            self.client.post("/admin/payroll/payrollrun/", {"action": "finalize", "_selected_action": [run.pk]})
        self.assertEqual(PayrollRun.objects.get(pk=run.pk).status, PayrollRun.Status.READY)
        self.client.force_login(self.super_user)
        with at(self.after_month):
            self.client.post("/admin/payroll/payrollrun/", {"action": "finalize", "_selected_action": [run.pk]})
        self.assertEqual(PayrollRun.objects.get(pk=run.pk).status, PayrollRun.Status.FINALIZED)
        self.assertEqual(self.client.post(f"/admin/payroll/payrollrun/{run.pk}/delete/", {"post": "yes"}).status_code,
                         403)
        self.assertEqual(Payslip.objects.get(pk=payslip.pk).net_pay, Decimal("80.00"))

    def test_admin_rate_form_offers_only_per_session_types(self):
        self.client.force_login(self.finance_user)
        page = self.client.get("/admin/payroll/coachrate/add/").content.decode()
        self.assertIn('value="PER_SESSION"', page)
        self.assertNotIn('value="MONTHLY"', page)
        self.assertNotIn('value="HOURLY"', page)
        response = self.client.post("/admin/payroll/coachrate/add/", {
            "coach": self.coach_b.pk, "rate_type": "MONTHLY", "amount": "3000", "effective_from": "2026-01-01"})
        self.assertEqual(response.status_code, 200)  # form error, nothing saved
        self.assertFalse(CoachRate.objects.filter(rate_type="MONTHLY").exists())

    def test_payroll_lines_report_explains_each_payment(self):
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user, reason="x")
        self.calculate()
        body = self.api(self.finance_user).get("/api/reports/payroll_lines/").json()
        rows = body["rows"]
        sub = next(r for r in rows if r["role"] == "SUBSTITUTE")
        self.assertEqual((sub["coach"], sub["original_coach"], sub["rate"], sub["rule"], sub["amount"]),
                         ("Coach A", "Coach B", "100.00", Rule.GENERAL_SUBSTITUTE, "100.00"))
        self.assertEqual(self.api(self.admin_user).get("/api/reports/payroll_lines/").status_code, 403)


class SessionCoachHistoryTests(PayrollTestCase):
    def test_substitute_never_overwrites_the_original_coach(self):
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user,
                                 reason="x")
        original = self.session_b.coach_slots.get(coach=self.coach_b)
        self.assertEqual((original.role, original.status), (SessionCoach.Role.REGULAR, SessionCoach.Status.REPLACED))
        self.assertEqual((slot.coach, slot.replaces), (self.coach_a, self.coach_b))
