"""Phase 3: attendance roster, UNMARKED, percentage, 48-hour coach window,
administrator corrections, reasons, audit and bulk-bypass protection."""

import datetime
from contextlib import contextmanager
from decimal import Decimal
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.academy.models import Enrollment, Student, TrainingClass, TrainingSession
from apps.academy.services import assign_substitute, revoke_substitute
from apps.academy.tests.base import AcademyTestCase
from apps.attendance import services
from apps.attendance.models import AttendanceRecord, AttendanceStatus
from apps.attendance.services import (
    SessionState,
    coach_edit_deadline,
    mark_attendance,
    record_session_attendance,
    session_summary,
    student_summary,
)
from apps.audit.utils import history_for

KL = datetime.timezone(datetime.timedelta(hours=8))
SECOND = datetime.timedelta(seconds=1)


@contextmanager
def frozen(moment):
    with mock.patch("django.utils.timezone.now", return_value=moment):
        yield


class AttendanceRulesTestCase(AcademyTestCase):
    def past_session(self, days_ago=3, start=datetime.time(18), end=datetime.time(20), training_class=None):
        session = (training_class or self.class_a).sessions.create(
            date=self.today - datetime.timedelta(days=days_ago), start_time=start, end_time=end)
        session.coach_slots.create(coach=self.coach_a)
        return session


class RosterTests(AttendanceRulesTestCase):
    def test_sheet_lists_every_expected_student_unmarked_until_marked(self):
        sheet = services.session_sheet(self.session_a)
        self.assertEqual([(r["student"], r["status"]) for r in sheet],
                         [(self.student_1, "UNMARKED"), (self.student_3, "UNMARKED")])
        self.assertFalse(AttendanceRecord.objects.exists())  # no rows are invented
        mark_attendance(self.session_a, self.student_1, "PRESENT", self.coach_a_user)
        sheet = services.session_sheet(self.session_a)
        self.assertEqual([r["status"] for r in sheet], ["PRESENT", "UNMARKED"])

    def test_roster_follows_class_membership_on_the_session_date(self):
        Enrollment.objects.filter(student=self.student_3).update(end_date=self.today - datetime.timedelta(days=10))
        late_joiner = Student.objects.create(student_no="S9", full_name="Zed", gender="M",
                                             date_of_birth=datetime.date(2014, 1, 1))
        Enrollment.objects.create(student=late_joiner, training_class=self.class_a, start_date=self.today)
        old = self.past_session(days_ago=20)
        self.assertEqual(list(old.roster()), [self.student_1, self.student_3])
        self.assertEqual(list(self.session_a.roster()), [self.student_1, late_joiner])
        with self.assertRaisesMessage(ValidationError, "not on the roster"):
            mark_attendance(self.session_a, self.student_3, "PRESENT", self.admin_user)
        with self.assertRaisesMessage(ValidationError, "not on the roster"):
            mark_attendance(old, late_joiner, "PRESENT", self.admin_user, reason="x")

    def test_non_roster_student_is_refused_for_every_role(self):
        for user in (self.coach_a_user, self.admin_user, self.super_user):
            with self.subTest(user=user.username), self.assertRaisesMessage(ValidationError, "not on the roster"):
                mark_attendance(self.session_a, self.student_2, "PRESENT", user)
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_model_refuses_an_off_roster_record_created_directly(self):
        with self.assertRaisesMessage(ValidationError, "not on the roster"):
            AttendanceRecord.objects.create(session=self.session_a, student=self.student_2, status="PRESENT")

    def test_one_record_per_session_and_student(self):
        mark_attendance(self.session_a, self.student_1, "PRESENT", self.coach_a_user)
        mark_attendance(self.session_a, self.student_1, "PRESENT", self.coach_a_user)  # same again: no-op
        self.assertEqual(AttendanceRecord.objects.filter(session=self.session_a, student=self.student_1).count(), 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceRecord.objects.create(session=self.session_a, student=self.student_1, status="ABSENT")

    def test_duplicate_student_in_one_submission_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "more than once"):
            record_session_attendance(self.session_a, [(self.student_1, "PRESENT", ""), (self.student_1, "ABSENT", "")],
                                      self.coach_a_user)
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_submission_is_all_or_nothing(self):
        with self.assertRaises(ValidationError):
            record_session_attendance(self.session_a, [(self.student_1, "PRESENT", ""), (self.student_2, "PRESENT", "")],
                                      self.coach_a_user)
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_every_status_and_unknown_status(self):
        for student, status in ((self.student_1, "LATE"), (self.student_3, "EXCUSED")):
            self.assertEqual(mark_attendance(self.session_a, student, status, self.coach_a_user).status, status)
        with self.assertRaisesMessage(ValidationError, "Unknown attendance status"):
            mark_attendance(self.session_a, self.student_1, "MAYBE", self.coach_a_user, reason="x")
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE attendance_attendancerecord SET status = 'MAYBE'")

    def test_unmarked_for_unrecorded_student_creates_nothing(self):
        self.assertIsNone(mark_attendance(self.session_a, self.student_1, "UNMARKED", self.coach_a_user))
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_cancelled_session_takes_no_attendance(self):
        self.session_a.status = TrainingSession.Status.CANCELLED
        self.session_a.save()
        with self.assertRaisesMessage(ValidationError, "cancelled"):
            mark_attendance(self.session_a, self.student_1, "PRESENT", self.coach_a_user)
        with self.assertRaises(PermissionDenied):
            mark_attendance(self.session_a, self.student_1, "PRESENT", self.coach_b_user)


class PercentageTests(AttendanceRulesTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.class_c = TrainingClass.objects.create(code="c", name="Class C", category="ADDITIONAL",
                                                   program=cls.program)
        cls.class_c.coach_assignments.create(coach=cls.coach_a, start_date=cls.today - datetime.timedelta(days=365))
        cls.ten = []
        for i in range(10):
            student = Student.objects.create(student_no=f"C{i:02d}", full_name=f"Student {i:02d}", gender="F",
                                             date_of_birth=datetime.date(2013, 1, 1))
            Enrollment.objects.create(student=student, training_class=cls.class_c,
                                      start_date=cls.today - datetime.timedelta(days=100))
            cls.ten.append(student)
        cls.session_c = TrainingSession.objects.create(training_class=cls.class_c, date=cls.today,
                                                       start_time=datetime.time(8), end_time=datetime.time(9))

    def mark(self, statuses, session=None):
        entries = [(student, status, "") for student, status in zip(self.ten, statuses)]
        record_session_attendance(session or self.session_c, entries, self.coach_a_user)

    def test_unmarked_is_excluded_from_the_percentage_and_shown_separately(self):
        self.mark(["PRESENT"] * 7 + ["ABSENT"] + ["UNMARKED"] * 2)
        summary = session_summary(self.session_c)
        self.assertEqual(summary["percentage"], Decimal("87.50"))  # 7 / 8, not 7 / 10
        self.assertEqual((summary["expected"], summary["marked"], summary["unmarked"]), (10, 8, 2))
        self.assertEqual(services.session_state(self.session_c, summary), SessionState.OPEN)

    def test_all_unmarked(self):
        summary = session_summary(self.session_c)
        self.assertIsNone(summary["percentage"])
        self.assertEqual((summary["expected"], summary["marked"], summary["unmarked"], summary["absent"]),
                         (10, 0, 10, 0))

    def test_zero_denominator_when_everyone_is_excused(self):
        self.mark(["EXCUSED"] * 10)
        summary = session_summary(self.session_c)
        self.assertIsNone(summary["percentage"])
        self.assertEqual((summary["marked"], summary["unmarked"]), (10, 0))

    def test_no_expected_students(self):
        empty = TrainingSession.objects.create(training_class=self.class_c, date=self.today - datetime.timedelta(
            days=150), start_time=datetime.time(8), end_time=datetime.time(9))
        summary = session_summary(empty)
        self.assertEqual((summary["expected"], summary["unmarked"], summary["percentage"]), (0, 0, None))

    def test_fully_marked_session_is_complete(self):
        self.mark(["PRESENT"] * 5 + ["LATE"] * 2 + ["ABSENT"] * 2 + ["EXCUSED"])
        summary = session_summary(self.session_c)
        self.assertEqual(summary["percentage"], Decimal("77.78"))  # (5 + 2 late) / 9, half-up
        self.assertEqual(summary["unmarked"], 0)
        self.assertEqual(services.session_state(self.session_c, summary), SessionState.COMPLETE)

    def test_resetting_to_unmarked_takes_the_mark_out_of_the_percentage(self):
        self.mark(["PRESENT", "ABSENT"])
        mark_attendance(self.session_c, self.ten[1], "UNMARKED", self.coach_a_user, reason="Marked the wrong child")
        summary = session_summary(self.session_c)
        self.assertEqual((summary["percentage"], summary["marked"], summary["unmarked"]), (Decimal("100.00"), 1, 9))

    def test_student_summary_over_multiple_sessions(self):
        student = self.ten[0]
        sessions = [TrainingSession.objects.create(training_class=self.class_c,
                                                   date=self.today - datetime.timedelta(days=d),
                                                   start_time=datetime.time(8), end_time=datetime.time(9))
                    for d in (1, 2, 3, 4)]
        mark_attendance(sessions[0], student, "PRESENT", self.admin_user, reason="Register")
        mark_attendance(sessions[1], student, "ABSENT", self.admin_user, reason="Register")
        # sessions[2] and [3] were never marked; today's session_c (08:00-09:00) is unmarked once it has started.
        after_today = timezone.make_aware(datetime.datetime.combine(self.today, datetime.time(10)),
                                          timezone.get_default_timezone())
        with frozen(after_today):
            summary = student_summary(student)
        self.assertEqual(summary["percentage"], Decimal("50.00"))
        self.assertEqual((summary["expected"], summary["marked"], summary["unmarked"]), (5, 2, 3))
        # A session that has not started yet is not "unmarked".
        TrainingSession.objects.create(training_class=self.class_c, date=self.today + datetime.timedelta(days=2),
                                       start_time=datetime.time(8), end_time=datetime.time(9))
        with frozen(after_today):
            self.assertEqual(student_summary(student)["expected"], 5)

    def test_cancelled_sessions_are_not_expected(self):
        cancelled = TrainingSession.objects.create(training_class=self.class_c,
                                                   date=self.today - datetime.timedelta(days=1),
                                                   start_time=datetime.time(8), end_time=datetime.time(9),
                                                   status=TrainingSession.Status.CANCELLED)
        summary = student_summary(self.ten[0], start=cancelled.date, end=cancelled.date)
        self.assertEqual((summary["expected"], summary["unmarked"]), (0, 0))


class EditWindowTests(AttendanceRulesTestCase):
    def setUp(self):
        self.session = self.past_session(days_ago=3)
        self.deadline = coach_edit_deadline(self.session)

    def test_deadline_is_48_hours_after_the_session_ends_in_academy_time(self):
        end = datetime.datetime.combine(self.session.date, datetime.time(20), tzinfo=KL)
        self.assertEqual(self.deadline, end + datetime.timedelta(hours=48))

    def test_coach_can_record_until_just_before_48_hours(self):
        with frozen(self.deadline - SECOND):
            mark_attendance(self.session, self.student_1, "PRESENT", self.coach_a_user)
            mark_attendance(self.session, self.student_1, "LATE", self.coach_a_user, reason="Came at 18:20")
            self.assertEqual(services.session_state(self.session), SessionState.OPEN)

    def test_window_is_closed_at_exactly_48_hours_and_after(self):
        for moment in (self.deadline, self.deadline + SECOND, self.deadline + datetime.timedelta(days=30)):
            with self.subTest(moment=moment), frozen(moment):
                with self.assertRaisesMessage(PermissionDenied, "edit window"):
                    mark_attendance(self.session, self.student_1, "PRESENT", self.coach_a_user)
                with self.assertRaisesMessage(PermissionDenied, "edit window"):
                    mark_attendance(self.session, self.student_1, "PRESENT", self.coach_a_user, reason="please")
                self.assertEqual(services.session_state(self.session), SessionState.LOCKED)
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_existing_marks_are_locked_for_coaches_after_the_window(self):
        with frozen(self.deadline - datetime.timedelta(hours=1)):
            mark_attendance(self.session, self.student_1, "ABSENT", self.coach_a_user)
        with frozen(self.deadline + SECOND), self.assertRaises(PermissionDenied):
            mark_attendance(self.session, self.student_1, "PRESENT", self.coach_a_user, reason="Forgot")
        self.assertEqual(AttendanceRecord.objects.get().status, "ABSENT")

    def test_window_does_not_depend_on_the_active_time_zone(self):
        # Session 22:00-23:30 KL ends at 15:30 UTC; the deadline is 15:30 UTC two days later,
        # whatever time zone the request runs in.
        late = self.past_session(days_ago=4, start=datetime.time(22), end=datetime.time(23, 30))
        deadline = datetime.datetime.combine(late.date + datetime.timedelta(days=2), datetime.time(15, 30),
                                             tzinfo=datetime.timezone.utc)
        for zone in ("UTC", "America/New_York", "Asia/Kuala_Lumpur"):
            with self.subTest(zone=zone), timezone.override(zone):
                self.assertEqual(coach_edit_deadline(late), deadline)
                self.assertTrue(services.coach_window_open(late, deadline - SECOND))
                self.assertFalse(services.coach_window_open(late, deadline))
        # Treating the session time as UTC would wrongly keep it open 8 more hours.
        with frozen(deadline + datetime.timedelta(hours=7)), timezone.override("UTC"):
            with self.assertRaises(PermissionDenied):
                mark_attendance(late, self.student_1, "PRESENT", self.coach_a_user)

    def test_deadline_crossing_midnight(self):
        late = self.past_session(days_ago=4, start=datetime.time(22), end=datetime.time(23, 30))
        second_day = late.date + datetime.timedelta(days=2)
        before_midnight = datetime.datetime.combine(second_day, datetime.time(23, 29, 59), tzinfo=KL)
        after_midnight = datetime.datetime.combine(second_day + datetime.timedelta(days=1), datetime.time(0, 0, 1),
                                                   tzinfo=KL)
        with frozen(before_midnight):
            mark_attendance(late, self.student_1, "PRESENT", self.coach_a_user)
        with frozen(after_midnight), self.assertRaises(PermissionDenied):
            mark_attendance(late, self.student_3, "PRESENT", self.coach_a_user)

    def test_substitute_is_also_limited_by_their_access_window(self):
        with frozen(self.session.starts_at - datetime.timedelta(hours=2)):
            slot = assign_substitute(self.session, self.coach_b, replaces=self.coach_a, actor=self.admin_user,
                                     reason="Coach A away")
        with frozen(self.session.ends_at + datetime.timedelta(hours=1)):
            mark_attendance(self.session, self.student_1, "PRESENT", self.coach_b_user)
        # 30 h after the session: inside the 48-hour window, outside the 24-hour substitute access.
        with frozen(self.session.ends_at + datetime.timedelta(hours=30)), self.assertRaises(PermissionDenied):
            mark_attendance(self.session, self.student_3, "PRESENT", self.coach_b_user)
        self.assertEqual(slot.access_ends_at, self.session.ends_at + datetime.timedelta(hours=24))


class CorrectionTests(AttendanceRulesTestCase):
    def setUp(self):
        self.session = self.past_session(days_ago=5)
        with frozen(self.session.ends_at + datetime.timedelta(hours=1)):
            mark_attendance(self.session, self.student_1, "ABSENT", self.coach_a_user)

    def test_admin_corrects_after_the_window_with_a_reason_and_it_is_audited(self):
        record = mark_attendance(self.session, self.student_1, "EXCUSED", self.admin_user,
                                 reason="Medical certificate received")
        self.assertEqual(record.status, "EXCUSED")
        entry = history_for(record).filter(action="UPDATE").get()
        self.assertEqual(entry.changes["status"], {"from": "ABSENT", "to": "EXCUSED"})
        self.assertEqual(entry.actor, self.admin_user)
        self.assertIn("Administrative correction after the 48-hour coach edit window", entry.reason)
        self.assertIn("Medical certificate received", entry.reason)
        self.assertIsNotNone(entry.timestamp)

    def test_late_first_entry_after_the_window_is_also_a_correction(self):
        with self.assertRaisesMessage(ValidationError, "reason is required"):
            mark_attendance(self.session, self.student_3, "PRESENT", self.admin_user)
        record = mark_attendance(self.session, self.student_3, "PRESENT", self.admin_user, reason="Paper register")
        self.assertIn("Administrative correction", history_for(record).get().reason)

    def test_correction_needs_a_reason(self):
        for reason in ("", "   "):
            with self.subTest(reason=reason), self.assertRaisesMessage(ValidationError, "reason is required"):
                mark_attendance(self.session, self.student_1, "PRESENT", self.admin_user, reason=reason)
        self.assertEqual(AttendanceRecord.objects.get(student=self.student_1).status, "ABSENT")

    def test_only_attendance_correct_holders_may_correct(self):
        for user in (self.coach_a_user, self.coach_b_user, self.finance_user, self.parent_1_user):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                mark_attendance(self.session, self.student_1, "PRESENT", user, reason="x")
        mark_attendance(self.session, self.student_1, "PRESENT", self.super_user, reason="Checked CCTV")

    def test_change_inside_the_window_also_needs_a_reason(self):
        mark_attendance(self.session_a, self.student_1, "PRESENT", self.coach_a_user)
        with self.assertRaisesMessage(ValidationError, "reason is required"):
            mark_attendance(self.session_a, self.student_1, "ABSENT", self.coach_a_user)


class AccessTests(AttendanceRulesTestCase):
    def test_who_may_record_inside_the_window(self):
        allowed = (self.coach_a_user, self.admin_user, self.super_user)
        refused = (self.coach_b_user, self.finance_user, self.parent_1_user)
        for user in allowed:
            with self.subTest(user=user.username):
                mark_attendance(self.session_a, self.student_1, "PRESENT", user)
        for user in refused:
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                mark_attendance(self.session_a, self.student_3, "PRESENT", user)

    def test_revoked_substitute_cannot_record(self):
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user,
                                 reason="Coach B away")
        mark_attendance(self.session_b, self.student_2, "PRESENT", self.coach_a_user)
        revoke_substitute(slot, self.admin_user, "Coach B came")
        with self.assertRaises(PermissionDenied):
            mark_attendance(self.session_b, self.student_2, "LATE", self.coach_a_user, reason="x")
        # The replaced-then-restored original coach can.
        mark_attendance(self.session_b, self.student_2, "LATE", self.coach_b_user, reason="Arrived late")

    def test_substitute_cannot_record_for_other_sessions_of_the_class(self):
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user, reason="x")
        other = self.class_b.sessions.create(date=self.today - datetime.timedelta(days=1),
                                             start_time=datetime.time(19), end_time=datetime.time(21))
        with self.assertRaises(PermissionDenied):
            mark_attendance(other, self.student_2, "PRESENT", self.coach_a_user)


class BulkBypassTests(AttendanceRulesTestCase):
    def setUp(self):
        self.record = mark_attendance(self.session_a, self.student_1, "ABSENT", self.coach_a_user)

    def test_queryset_bulk_writes_are_refused(self):
        qs = AttendanceRecord.objects.filter(pk=self.record.pk)
        with self.assertRaises(PermissionDenied):
            qs.update(status="PRESENT")
        with self.assertRaises(PermissionDenied):
            qs.delete()
        with self.assertRaises(PermissionDenied):
            AttendanceRecord.objects.bulk_create([AttendanceRecord(session=self.session_a, student=self.student_3,
                                                                   status="PRESENT")])
        self.record.status = "PRESENT"
        with self.assertRaises(PermissionDenied):
            AttendanceRecord.objects.bulk_update([self.record], ["status"])
        with self.assertRaises(PermissionDenied):
            self.record.delete()
        self.assertEqual(AttendanceRecord.objects.get().status, "ABSENT")

    def test_raw_sql_cannot_delete_attendance_history(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL trigger")
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM attendance_attendancerecord WHERE id = %s", [self.record.pk])
        self.assertTrue(AttendanceRecord.objects.filter(pk=self.record.pk).exists())


class AttendanceReportTests(AttendanceRulesTestCase):
    def test_report_lists_unmarked_separately_from_absent(self):
        from apps.reports.reports import attendance_report

        session = self.past_session(days_ago=1)
        mark_attendance(session, self.student_1, "PRESENT", self.coach_a_user)
        after = session.ends_at + datetime.timedelta(hours=1)
        with frozen(after):
            columns, rows = attendance_report({"start": session.date.isoformat(), "end": session.date.isoformat(),
                                               "class": str(self.class_a.pk)})
        table = {row[0]: dict(zip(columns, row)) for row in rows}
        self.assertEqual((table["S1"]["present"], table["S1"]["unmarked"], table["S1"]["attendance_pct"]),
                         (1, 0, "100.00"))
        # Raj was expected but never marked: unmarked, not absent, no percentage.
        self.assertEqual((table["S3"]["absent"], table["S3"]["expected"], table["S3"]["unmarked"],
                          table["S3"]["attendance_pct"]), (0, 1, 1, ""))
