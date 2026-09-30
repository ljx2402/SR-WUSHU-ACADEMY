"""Phase 3: genuinely simultaneous substitute authorizations and attendance
submissions on PostgreSQL (skipped on SQLite, which serialises all writes)."""

import datetime

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.academy.models import ClassCoach, Enrollment, Program, SessionCoach, Student, TrainingClass, TrainingSession
from apps.academy.services import assign_substitute
from apps.academy.tests.base import make_user
from apps.accounts.capabilities import Role
from apps.accounts.models import Coach
from apps.attendance.models import AttendanceRecord
from apps.attendance.services import mark_attendance
from apps.finance.tests.test_concurrency import POSTGRES_ONLY, run_concurrently


class Phase3ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest(POSTGRES_ONLY)
        today = timezone.localdate()
        self.admin_1 = make_user("desk1", Role.ADMIN)
        self.admin_2 = make_user("desk2", Role.ADMIN)
        self.coaches = []
        for name in ("regular", "sub1", "sub2"):
            user = make_user(name, Role.COACH)
            self.coaches.append(Coach.objects.create(user=user, full_name=name.title(), phone="01"))
        self.regular, self.sub_1, self.sub_2 = self.coaches
        program = Program.objects.create(code="p", name="P")
        self.klass = TrainingClass.objects.create(code="c", name="C", category="SCHOOL", program=program)
        ClassCoach.objects.create(training_class=self.klass, coach=self.regular,
                                  start_date=today - datetime.timedelta(days=30))
        self.student = Student.objects.create(student_no="C1", full_name="Racer", gender="M",
                                              date_of_birth="2012-01-01")
        Enrollment.objects.create(student=self.student, training_class=self.klass,
                                  start_date=today - datetime.timedelta(days=30))
        self.session = TrainingSession.objects.create(training_class=self.klass, date=today,
                                                      start_time=datetime.time(17), end_time=datetime.time(19))
        self.session.coach_slots.create(coach=self.regular)

    def test_two_admins_authorize_different_substitutes_at_once(self):
        results = run_concurrently(
            lambda: assign_substitute(self.session, self.sub_1, actor=self.admin_1, reason="cover"),
            lambda: assign_substitute(self.session, self.sub_2, actor=self.admin_2, reason="cover"),
        )
        self.assertEqual(sum(isinstance(r, SessionCoach) for r in results), 1, results)
        self.assertEqual(sum(isinstance(r, ValidationError) for r in results), 1, results)
        self.assertEqual(SessionCoach.objects.active_substitutes().filter(session=self.session).count(), 1)

    def test_the_same_substitute_submitted_twice_at_once(self):
        results = run_concurrently(
            *[lambda: assign_substitute(self.session, self.sub_1, replaces=self.regular, actor=self.admin_1,
                                        reason="double click")] * 2
        )
        self.assertEqual(sum(isinstance(r, SessionCoach) for r in results), 1, results)
        self.assertEqual(SessionCoach.objects.substitutes().filter(session=self.session).count(), 1)
        self.assertEqual(self.session.coach_slots.get(coach=self.regular).status, SessionCoach.Status.REPLACED)

    def test_simultaneous_identical_marks_create_one_record(self):
        results = run_concurrently(
            lambda: mark_attendance(self.session, self.student, "PRESENT", self.regular.user),
            lambda: mark_attendance(self.session, self.student, "PRESENT", self.admin_1),
        )
        self.assertTrue(all(isinstance(r, AttendanceRecord) for r in results), results)
        self.assertEqual(AttendanceRecord.objects.filter(session=self.session, student=self.student).count(), 1)

    def test_simultaneous_conflicting_marks_cannot_skip_the_reason_rule(self):
        results = run_concurrently(
            lambda: mark_attendance(self.session, self.student, "PRESENT", self.regular.user),
            lambda: mark_attendance(self.session, self.student, "ABSENT", self.admin_1),
        )
        self.assertEqual(sum(isinstance(r, AttendanceRecord) for r in results), 1, results)
        self.assertEqual(sum(isinstance(r, ValidationError) for r in results), 1, results)
        self.assertIn("reason is required", str(next(r for r in results if isinstance(r, ValidationError))))
        self.assertEqual(AttendanceRecord.objects.filter(session=self.session).count(), 1)


class AutocommitTests(TransactionTestCase):
    """Without a surrounding test transaction, as in a real request (no ATOMIC_REQUESTS)."""

    def test_cancelling_a_session_through_the_api_in_autocommit_mode(self):
        from rest_framework.test import APIClient

        today = timezone.localdate()
        admin = make_user("desk", Role.ADMIN)
        sub = Coach.objects.create(user=make_user("sub", Role.COACH), full_name="Sub", phone="01")
        program = Program.objects.create(code="p", name="P")
        klass = TrainingClass.objects.create(code="c", name="C", category="SCHOOL", program=program)
        session = TrainingSession.objects.create(training_class=klass, date=today, start_time=datetime.time(17),
                                                 end_time=datetime.time(19))
        slot = assign_substitute(session, sub, actor=admin, reason="cover")
        client = APIClient()
        client.force_authenticate(admin)
        response = client.patch(f"/api/sessions/{session.pk}/", {"status": "CANCELLED"})
        self.assertEqual(response.status_code, 200, response.content)
        slot.refresh_from_db()
        self.assertEqual(slot.status, SessionCoach.Status.CANCELLED)
