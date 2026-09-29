import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError

from apps.academy.services import assign_substitute
from apps.academy.tests.base import AcademyTestCase
from apps.payroll.models import CoachRate, PayrollAdjustment, Payslip
from apps.payroll.services import calculate_run, finalize_run


class PayrollTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        since = cls.today - datetime.timedelta(days=400)
        CoachRate.objects.create(coach=cls.coach_a, rate_type="HOURLY", amount=Decimal("40"), effective_from=since)
        CoachRate.objects.create(coach=cls.coach_a, rate_type="SUBSTITUTE_PER_SESSION", amount=Decimal("100"),
                                 effective_from=since)
        CoachRate.objects.create(coach=cls.coach_b, rate_type="MONTHLY", amount=Decimal("3000"), effective_from=since)

    def run_payroll(self):
        return calculate_run(self.today.year, self.today.month, self.admin_user)

    def test_hourly_and_monthly(self):
        run = self.run_payroll()
        a = run.payslips.get(coach=self.coach_a)
        b = run.payslips.get(coach=self.coach_b)
        self.assertEqual(a.gross_pay, Decimal("80.00"))  # 2h × RM40
        self.assertEqual(b.gross_pay, Decimal("3000.00"))  # session covered by monthly salary
        self.assertEqual(b.regular_sessions, 1)

    def test_substitute_rate_and_replaced_coach_not_paid(self):
        CoachRate.objects.create(coach=self.coach_b, rate_type="PER_SESSION", amount=Decimal("70"),
                                 training_class=self.class_a, effective_from=self.today - datetime.timedelta(days=1))
        assign_substitute(self.session_a, self.coach_b, replaces=self.coach_a, actor=self.admin_user)
        run = self.run_payroll()
        b = run.payslips.get(coach=self.coach_b)
        self.assertFalse(run.payslips.filter(coach=self.coach_a).exists())  # replaced: no paid work
        # Coach B has no substitute rate: falls back to the class per-session rate, not the monthly salary.
        self.assertEqual(b.gross_pay, Decimal("3070.00"))
        self.assertEqual(b.substitute_sessions, 1)

    def test_substitute_rate_used(self):
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user)
        a = self.run_payroll().payslips.get(coach=self.coach_a)
        self.assertEqual(a.gross_pay, Decimal("180.00"))  # 80 regular + 100 substitute

    def test_adjustments(self):
        for kind, amount in (("ALLOWANCE", "50"), ("BONUS", "200"), ("DEDUCTION", "30")):
            PayrollAdjustment.objects.create(coach=self.coach_a, year=self.today.year, month=self.today.month,
                                             kind=kind, description=kind.title(), amount=Decimal(amount))
        a = self.run_payroll().payslips.get(coach=self.coach_a)
        self.assertEqual((a.gross_pay, a.total_deductions, a.net_pay),
                         (Decimal("330.00"), Decimal("30.00"), Decimal("300.00")))

    def test_finalized_payroll_is_locked(self):
        run = self.run_payroll()
        finalize_run(run, self.admin_user)
        payslip = Payslip.objects.get(run=run, coach=self.coach_a)
        with self.assertRaises(PermissionDenied):
            payslip.save()
        with self.assertRaises(ValidationError):
            self.run_payroll()
        with self.assertRaises(ValidationError):
            PayrollAdjustment.objects.create(coach=self.coach_a, year=run.year, month=run.month, kind="BONUS",
                                             description="Late bonus", amount=Decimal("10"))
