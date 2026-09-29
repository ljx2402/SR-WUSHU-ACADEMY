import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError

from apps.academy.services import assign_substitute
from apps.academy.tests.base import AcademyTestCase
from apps.attendance.models import AttendanceRecord
from apps.attendance.services import mark_attendance, student_summary
from apps.audit.utils import history_for


class AttendanceTests(AcademyTestCase):
    def test_coach_marks_and_changes_are_audited(self):
        record = mark_attendance(self.session_a, self.student_1, "ABSENT", self.coach_a_user)
        with self.assertRaises(ValidationError):
            mark_attendance(self.session_a, self.student_1, "LATE", self.coach_a_user)  # no reason
        mark_attendance(self.session_a, self.student_1, "LATE", self.coach_a_user, reason="Arrived 17:20")
        entries = list(history_for(record).order_by("timestamp", "id"))
        self.assertEqual([e.action for e in entries], ["CREATE", "UPDATE"])
        self.assertEqual(entries[1].changes["status"], {"from": "ABSENT", "to": "LATE"})
        self.assertEqual(entries[1].reason, "Arrived 17:20")
        self.assertEqual(entries[1].actor, self.coach_a_user)

    def test_attendance_cannot_be_deleted(self):
        record = mark_attendance(self.session_a, self.student_1, "PRESENT", self.coach_a_user)
        with self.assertRaises(PermissionDenied):
            record.delete()

    def test_other_coach_cannot_mark(self):
        with self.assertRaises(PermissionDenied):
            mark_attendance(self.session_a, self.student_1, "PRESENT", self.coach_b_user)

    def test_parent_cannot_mark(self):
        with self.assertRaises(PermissionDenied):
            mark_attendance(self.session_a, self.student_1, "PRESENT", self.parent_1_user)

    def test_student_must_be_on_roster(self):
        with self.assertRaises(ValidationError):
            mark_attendance(self.session_a, self.student_2, "PRESENT", self.admin_user)

    def test_substitute_can_mark_their_session(self):
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user)
        mark_attendance(self.session_b, self.student_2, "PRESENT", self.coach_a_user)
        self.assertTrue(AttendanceRecord.objects.filter(session=self.session_b, student=self.student_2).exists())

    def test_percentage(self):
        statuses = ["PRESENT", "PRESENT", "LATE", "ABSENT", "EXCUSED"]
        for i, status in enumerate(statuses):
            session = self.class_a.sessions.create(date=self.today - datetime.timedelta(days=i + 1),
                                                   start_time=datetime.time(17), end_time=datetime.time(19))
            mark_attendance(session, self.student_1, status, self.admin_user)
        summary = student_summary(self.student_1)
        # (2 present + 1 late) / (5 - 1 excused) = 75%
        self.assertEqual(summary["percentage"], Decimal("75.00"))
        self.assertEqual((summary["present"], summary["late"], summary["absent"], summary["excused"]), (2, 1, 1, 1))

    def test_percentage_none_without_records(self):
        self.assertIsNone(student_summary(self.student_1)["percentage"])
