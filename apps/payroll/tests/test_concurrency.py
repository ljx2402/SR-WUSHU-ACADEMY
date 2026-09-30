"""Phase 4: simultaneous payroll calculations and finalizations, and a result
entry racing a withdrawal, as real concurrent PostgreSQL transactions
(skipped on SQLite)."""

import datetime
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.academy.models import ClassCoach, Enrollment, Program, Student, TrainingClass, TrainingSession
from apps.academy.tests.base import make_user
from apps.accounts.capabilities import Role
from apps.accounts.models import Coach
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration, CompetitionResult
from apps.competitions.services import record_result, register, withdraw
from apps.finance.tests.test_concurrency import POSTGRES_ONLY, run_concurrently
from apps.payroll.models import CoachRate, PayrollRun, Payslip, PayslipLine
from apps.payroll.services import calculate_run, finalize_run


class Phase4ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest(POSTGRES_ONLY)
        self.today = timezone.localdate()
        first_next = (self.today.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        moment = timezone.make_aware(datetime.datetime.combine(first_next, datetime.time(9)),
                                     timezone.get_default_timezone())
        patcher = mock.patch("django.utils.timezone.now", return_value=moment)   # the month is over
        patcher.start()
        self.addCleanup(patcher.stop)
        self.finance_1 = make_user("fin1", Role.FINANCE_ADMIN)
        self.finance_2 = make_user("fin2", Role.FINANCE_ADMIN)
        self.super_1 = make_user("sup1", Role.SUPER_ADMIN)
        self.super_2 = make_user("sup2", Role.SUPER_ADMIN)
        self.admin = make_user("desk", Role.ADMIN)
        program = Program.objects.create(code="p", name="P")
        self.klass = TrainingClass.objects.create(code="c", name="C", category="SCHOOL", program=program)
        since = self.today - datetime.timedelta(days=60)
        for i in range(3):
            coach = Coach.objects.create(user=make_user(f"coach{i}", Role.COACH), full_name=f"Coach {i}", phone="1")
            ClassCoach.objects.create(training_class=self.klass, coach=coach, start_date=since)
            CoachRate.objects.create(coach=coach, rate_type="PER_SESSION", amount=Decimal("50"), effective_from=since)
        session = TrainingSession.objects.create(training_class=self.klass, date=self.today.replace(day=1),
                                                 start_time=datetime.time(17), end_time=datetime.time(19))
        for assignment in ClassCoach.objects.all():
            session.coach_slots.create(coach=assignment.coach)

    def test_simultaneous_calculations_produce_one_consistent_run(self):
        results = run_concurrently(
            lambda: calculate_run(self.today.year, self.today.month, self.finance_1),
            lambda: calculate_run(self.today.year, self.today.month, self.finance_2),
        )
        self.assertTrue(all(isinstance(r, PayrollRun) for r in results), results)
        self.assertEqual(PayrollRun.objects.count(), 1)
        self.assertEqual(Payslip.objects.count(), 3)
        self.assertEqual(PayslipLine.objects.count(), 3)   # each assignment paid once

    def test_simultaneous_finalizations_finalize_once(self):
        run = calculate_run(self.today.year, self.today.month, self.finance_1)
        results = run_concurrently(lambda: finalize_run(run, self.super_1), lambda: finalize_run(run, self.super_2))
        self.assertEqual(sum(isinstance(r, PayrollRun) for r in results), 1, results)
        self.assertEqual(sum(isinstance(r, ValidationError) for r in results), 1, results)
        run.refresh_from_db()
        self.assertEqual(run.status, PayrollRun.Status.FINALIZED)

    def test_recalculation_racing_finalization_never_changes_a_finalized_run(self):
        run = calculate_run(self.today.year, self.today.month, self.finance_1)
        before = sorted(PayslipLine.objects.values_list("slot_id", "amount"))
        results = run_concurrently(
            lambda: finalize_run(run, self.super_1),
            lambda: calculate_run(self.today.year, self.today.month, self.finance_2),
        )
        run.refresh_from_db()
        if run.status == PayrollRun.Status.FINALIZED and isinstance(results[1], ValidationError):
            self.assertIn("finalized", str(results[1]))
        self.assertEqual(sorted(PayslipLine.objects.values_list("slot_id", "amount")), before)

    def test_result_entry_racing_withdrawal(self):
        competition = Competition.objects.create(
            name="Open", start_date=self.today + datetime.timedelta(days=30),
            end_date=self.today + datetime.timedelta(days=30),
            registration_deadline=self.today + datetime.timedelta(days=10), status="OPEN")
        event = CompetitionEvent.objects.create(competition=competition, event_type="OTHER", name="Free demo",
                                                fee=Decimal("0"))
        student = Student.objects.create(student_no="C1", full_name="Racer", gender="M",
                                         date_of_birth=datetime.date(2012, 1, 1))
        Enrollment.objects.create(student=student, training_class=self.klass, start_date=self.today)
        registration = register(student, event, self.admin)   # free event: confirmed at once
        results = run_concurrently(
            lambda: record_result(registration, self.admin, placing=1, medal="GOLD"),
            lambda: withdraw(registration, self.admin, "Injured"),
        )
        registration.refresh_from_db()
        has_result = CompetitionResult.objects.filter(registration=registration).exists()
        # Exactly one wins: either a result on a confirmed registration, or a withdrawal without a result.
        self.assertNotEqual(has_result, registration.status == CompetitionRegistration.Status.WITHDRAWN, results)
        self.assertEqual(sum(isinstance(r, ValidationError) for r in results), 1, results)
