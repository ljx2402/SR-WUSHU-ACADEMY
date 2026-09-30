"""Phase 4 end-to-end flows across sessions, attendance, substitutes and payroll,
through the API where a user would use it."""

import datetime
from contextlib import contextmanager
from decimal import Decimal
from unittest import mock

from rest_framework.test import APIClient

from apps.academy.models import TrainingSession
from apps.academy.services import generate_sessions
from apps.academy.tests.base import AcademyTestCase, academy_time
from apps.attendance.services import session_summary
from apps.payroll.models import CoachRate, PayrollRun, PayslipLine


@contextmanager
def at(moment):
    with mock.patch("django.utils.timezone.now", return_value=moment):
        yield


class EndToEndTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        since = cls.today - datetime.timedelta(days=400)
        CoachRate.objects.create(coach=cls.coach_a, rate_type="PER_SESSION", amount=Decimal("80"),
                                 effective_from=since)
        CoachRate.objects.create(coach=cls.coach_b, rate_type="PER_SESSION", amount=Decimal("70"),
                                 effective_from=since)
        CoachRate.objects.create(coach=cls.coach_b, rate_type="SUBSTITUTE_PER_SESSION", amount=Decimal("120"),
                                 training_class=cls.class_a, effective_from=since)
        cls.class_a.schedules.create(weekday=cls.today.weekday(), start_time=datetime.time(6),
                                     end_time=datetime.time(7), effective_from=since)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def payroll(self):
        first_next = (self.today.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        after = academy_time(first_next, 9)
        with at(after):
            response = self.api(self.finance_user).post("/api/payroll-runs/calculate/",
                                                        {"year": self.today.year, "month": self.today.month})
            self.assertEqual(response.status_code, 200, response.content)
            run_id = response.json()["id"]
            self.assertEqual(self.api(self.finance_user).post(f"/api/payroll-runs/{run_id}/finalize/").status_code,
                             403)
            self.assertEqual(self.api(self.super_user).post(f"/api/payroll-runs/{run_id}/finalize/").status_code,
                             200)
        return PayrollRun.objects.get(pk=run_id)

    def morning_session(self):
        created = generate_sessions(self.class_a, self.today, self.today, self.admin_user)
        self.assertEqual(len(created), 1)
        return created[0]

    def test_session_coach_attendance_payroll(self):
        morning = self.morning_session()                      # 06:00-07:00 today, generated from the timetable
        self.assertEqual(list(morning.coach_slots.values_list("coach", flat=True)), [self.coach_a.pk])
        coach = self.api(self.coach_a_user)
        url = f"/api/sessions/{morning.pk}/attendance/"
        response = coach.post(url, {"records": [{"student": self.student_1.pk, "status": "PRESENT"}]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(session_summary(morning)["unmarked"], 1)
        run = self.payroll()
        a_lines = PayslipLine.objects.filter(payslip__run=run, payslip__coach=self.coach_a).order_by("session__start_time")
        self.assertEqual([(line.session, line.kind, line.amount) for line in a_lines],
                         [(morning, "REGULAR_SESSION", Decimal("80.00")),
                          (self.session_a, "REGULAR_SESSION", Decimal("80.00"))])
        self.assertEqual(run.payslips.get(coach=self.coach_a).net_pay, Decimal("160.00"))

    def test_session_substitute_attendance_substitute_payroll(self):
        with at(academy_time(self.today, 5)):                 # before the 06:00 session
            morning = self.morning_session()
            admin = self.api(self.admin_user)
            response = admin.post(f"/api/sessions/{morning.pk}/assign-substitute/",
                                  {"substitute": self.coach_b.pk, "replaces": self.coach_a.pk,
                                   "reason": "Coach A at a referee course"})
            self.assertEqual(response.status_code, 201, response.content)
        with at(academy_time(self.today, 6, 30)):               # during the session
            substitute = self.api(self.coach_b_user)
            response = substitute.post(f"/api/sessions/{morning.pk}/attendance/",
                                       {"records": [{"student": self.student_1.pk, "status": "PRESENT"},
                                                    {"student": self.student_3.pk, "status": "ABSENT"}]},
                                       format="json")
            self.assertEqual(response.status_code, 200, response.content)
        run = self.payroll()
        sub_line = PayslipLine.objects.get(payslip__run=run, session=morning)
        self.assertEqual((sub_line.payslip.coach, sub_line.kind, sub_line.slot.replaces, sub_line.amount),
                         (self.coach_b, "SUBSTITUTE_SESSION", self.coach_a, Decimal("120.00")))
        # The original coach keeps their identity on the session and is paid only for session A.
        self.assertEqual(set(PayslipLine.objects.filter(payslip__run=run, payslip__coach=self.coach_a)
                             .values_list("session", flat=True)), {self.session_a.pk})
        self.assertIn({"session": morning.pk, "coach": "Coach A", "reason": "Replaced by an authorized substitute"},
                      [{k: r[k] for k in ("session", "coach", "reason")} for r in run.excluded])
        # A finalized month is closed history.
        with self.assertRaises(Exception):
            TrainingSession.objects.create(training_class=self.class_a, date=self.today,
                                           start_time=datetime.time(22), end_time=datetime.time(23))
