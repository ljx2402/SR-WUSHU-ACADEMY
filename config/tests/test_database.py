"""Database behaviour the business rules rely on.

Run against PostgreSQL 16 in CI (the reference database). A few tests need
real PostgreSQL behaviour and are skipped, with the reason shown, on SQLite.
Set REQUIRE_POSTGRES=1 to fail loudly if the suite is not really on PostgreSQL.
"""

import datetime
import os
import threading
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import DatabaseError, IntegrityError, connection, connections, transaction
from django.db.models import ProtectedError, Sum
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from apps.academy.models import ClassCoach, Enrollment, Program, SessionCoach, Student, TrainingClass
from apps.academy.tests.base import AcademyTestCase
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration
from apps.finance.models import Charge, ClassFee
from apps.finance.services import add_charge

KL = ZoneInfo("Asia/Kuala_Lumpur")
ON_POSTGRES = connection.vendor == "postgresql"
POSTGRES_ONLY = "requires PostgreSQL (SQLite does not enforce this)"


class BackendTests(TestCase):
    def test_required_backend(self):
        if os.environ.get("REQUIRE_POSTGRES") == "1":
            self.assertEqual(connection.vendor, "postgresql")
            self.assertGreaterEqual(connection.pg_version, 160000)

    def test_timezone_settings(self):
        from django.conf import settings

        self.assertEqual(settings.TIME_ZONE, "Asia/Kuala_Lumpur")
        self.assertTrue(settings.USE_TZ)


class MoneyPrecisionTests(AcademyTestCase):
    def test_amounts_are_exact_decimals(self):
        charge = add_charge(self.student_1, "OTHER", "Ten sen x3", "0.10", quantity=3)
        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal("0.30"))
        self.assertIsInstance(charge.amount, Decimal)

    def test_sums_are_exact(self):
        for _ in range(33):
            add_charge(self.student_1, "OTHER", "Ten sen", "0.10")
        total = Charge.objects.aggregate(total=Sum("amount"))["total"]
        self.assertIsInstance(total, Decimal)
        self.assertEqual(total, Decimal("3.30"))  # a float sum would give 3.3000000000000003

    def test_largest_amount_round_trips(self):
        charge = add_charge(self.student_1, "OTHER", "Max", "99999999.99")
        charge.refresh_from_db()
        self.assertEqual(charge.amount, Decimal("99999999.99"))

    def test_overflow_is_rejected(self):
        if not ON_POSTGRES:
            self.skipTest(POSTGRES_ONLY)
        with self.assertRaises(DatabaseError), transaction.atomic():
            ClassFee.objects.create(training_class=self.class_a, name="Too big", amount=Decimal("100000000.00"),
                                    effective_from=self.today)


class KualaLumpurTimeTests(AcademyTestCase):
    """Stored in UTC, filtered and displayed in Asia/Kuala_Lumpur (UTC+8)."""

    def test_datetimes_near_midnight_keep_their_local_date(self):
        late = datetime.datetime(2026, 9, 29, 23, 30, tzinfo=KL)    # 15:30 UTC, same day
        early = datetime.datetime(2026, 9, 30, 0, 30, tzinfo=KL)    # 16:30 UTC on the 29th!
        slot = SessionCoach.objects.create(session=self.session_a, coach=self.coach_b, role="SUBSTITUTE",
                                           access_starts_at=late, access_ends_at=early)
        slot.refresh_from_db()
        self.assertEqual(slot.access_starts_at, late)
        self.assertEqual(timezone.localtime(slot.access_ends_at).date(), datetime.date(2026, 9, 30))
        self.assertTrue(SessionCoach.objects.filter(pk=slot.pk, access_starts_at__date=datetime.date(2026, 9, 29)).exists())
        self.assertTrue(SessionCoach.objects.filter(pk=slot.pk, access_ends_at__date=datetime.date(2026, 9, 30)).exists())

    def test_session_times_are_local(self):
        self.session_a.date = datetime.date(2026, 9, 29)
        self.session_a.start_time = datetime.time(19, 30)
        self.session_a.end_time = datetime.time(21, 30)
        self.assertEqual(self.session_a.starts_at, datetime.datetime(2026, 9, 29, 11, 30, tzinfo=datetime.timezone.utc))
        self.assertEqual(self.session_a.duration_hours, Decimal("2.00"))


class ConstraintTests(AcademyTestCase):
    def assertRejected(self, create):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create()

    def test_one_open_enrollment_per_class(self):
        self.assertRejected(lambda: Enrollment.objects.create(student=self.student_1, training_class=self.class_a))
        enrollment = Enrollment.objects.get(student=self.student_1, training_class=self.class_a)
        enrollment.end_date = self.today
        enrollment.save()
        Enrollment.objects.create(student=self.student_1, training_class=self.class_a,
                                  start_date=self.today + datetime.timedelta(days=1))

    def test_one_open_coach_assignment(self):
        self.assertRejected(lambda: ClassCoach.objects.create(training_class=self.class_a, coach=self.coach_a))

    def test_one_active_charge_per_billing_period(self):
        enrollment = self.student_1.enrollments.get()
        fee = ClassFee.objects.create(training_class=self.class_a, name="Monthly", amount=Decimal("120"),
                                      effective_from=self.today)
        first = self.today.replace(day=1)
        make = lambda: Charge.objects.create(student=self.student_1, fee_type="TUITION", description="x",  # noqa: E731
                                             class_fee=fee, enrollment=enrollment, period_start=first,
                                             unit_amount=Decimal("120"))
        charge = make()
        self.assertRejected(make)
        charge.status = Charge.Status.CANCELLED
        charge.save()
        make()  # allowed once the earlier one is cancelled (partial unique index)

    def test_one_active_competition_registration(self):
        competition = Competition.objects.create(name="C", start_date=self.today, end_date=self.today,
                                                 registration_deadline=self.today, status="OPEN")
        event = CompetitionEvent.objects.create(competition=competition, event_type="OTHER", name="E")
        first = CompetitionRegistration.objects.create(event=event, student=self.student_1)
        self.assertRejected(lambda: CompetitionRegistration.objects.create(event=event, student=self.student_1))
        first.status = "WITHDRAWN"
        first.save()
        CompetitionRegistration.objects.create(event=event, student=self.student_1)

    def test_protected_foreign_keys(self):
        with self.assertRaises(ProtectedError):
            Program.objects.get(pk=self.program.pk).delete()

    def test_foreign_keys_enforced_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {Enrollment._meta.db_table} (student_id, training_class_id, start_date, end_reason, created_at) "
                    "VALUES (%s, %s, %s, '', %s)", [999999, self.class_a.pk, self.today, timezone.now()])
            connection.check_constraints()


class RowLockingTests(TransactionTestCase):
    """Foundation for the payment-locking phase (P8): row locks must really block."""

    @skipUnlessDBFeature("has_select_for_update_nowait")
    def test_select_for_update_blocks_a_second_connection(self):
        program = Program.objects.create(code="p", name="P")
        cls = TrainingClass.objects.create(code="c", name="C", category="SCHOOL", program=program)
        student = Student.objects.create(student_no="L1", full_name="Lock", gender="M",
                                         date_of_birth=datetime.date(2010, 1, 1))
        outcome = {}

        def contender():
            other = connections.create_connection("default")
            try:
                with other.cursor() as cursor:
                    cursor.execute("BEGIN")
                    try:
                        cursor.execute(f"SELECT id FROM {Student._meta.db_table} WHERE id = %s FOR UPDATE NOWAIT",
                                       [student.pk])
                        outcome["locked_out"] = False
                    except Exception as exc:  # psycopg LockNotAvailable
                        outcome["locked_out"] = "could not obtain lock" in str(exc)
                    cursor.execute("ROLLBACK")
            finally:
                other.close()

        with transaction.atomic():
            Student.objects.select_for_update().get(pk=student.pk)
            thread = threading.Thread(target=contender)
            thread.start()
            thread.join(timeout=30)
        self.assertTrue(outcome.get("locked_out"), outcome)
        self.assertTrue(cls.pk)
