import datetime

from django.core.exceptions import PermissionDenied

from apps.academy.models import Student, TrainingClass
from apps.academy.services import change_student_status, generate_sessions, transfer
from apps.audit.utils import history_for

from .base import AcademyTestCase


class HistoryTests(AcademyTestCase):
    def test_students_cannot_be_deleted(self):
        with self.assertRaises(PermissionDenied):
            self.student_1.delete()

    def test_personal_info_changes_are_kept(self):
        self.student_1.school = "SJK(C) Kuen Cheng"
        self.student_1.save()
        change = history_for(self.student_1).filter(action="UPDATE").first()
        self.assertEqual(change.changes["school"], {"from": "", "to": "SJK(C) Kuen Cheng"})

    def test_transfer_keeps_old_membership(self):
        enrollment = self.student_1.enrollments.get()
        new = transfer(enrollment, self.class_b, on_date=self.today, actor=self.admin_user)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.end_date, self.today - datetime.timedelta(days=1))
        self.assertEqual(self.student_1.enrollments.count(), 2)
        self.assertEqual(list(self.student_1.current_enrollments()), [new])
        # Roster of a past session in class A still includes the student.
        past = self.class_a.sessions.create(date=self.today - datetime.timedelta(days=10),
                                            start_time=datetime.time(17), end_time=datetime.time(19))
        self.assertIn(self.student_1, past.roster())
        self.assertNotIn(self.student_1, self.session_a.roster())

    def test_status_history(self):
        change_student_status(self.student_1, Student.Status.WITHDRAWN, "Moved to Penang", self.admin_user)
        latest = self.student_1.status_history.first()
        self.assertEqual((latest.previous_status, latest.status, latest.reason), ("ACTIVE", "WITHDRAWN", "Moved to Penang"))
        # Membership ends on the effective date (inclusive) and is kept as history.
        self.assertFalse(self.student_1.enrollments.filter(end_date__isnull=True).exists())
        self.assertFalse(self.student_1.current_enrollments(self.today + datetime.timedelta(days=1)).exists())

    def test_generate_sessions_from_timetable(self):
        cls = TrainingClass.objects.get(pk=self.class_a.pk)
        monday = self.today - datetime.timedelta(days=self.today.weekday())
        cls.schedules.create(weekday=0, start_time=datetime.time(16), end_time=datetime.time(18), effective_from=monday)
        cls.schedules.create(weekday=3, start_time=datetime.time(16), end_time=datetime.time(18), effective_from=monday)
        created = generate_sessions(cls, monday, monday + datetime.timedelta(days=13))
        self.assertEqual(len(created), 4)
        self.assertTrue(all(s.coach_slots.filter(coach=self.coach_a).exists() for s in created))
        self.assertEqual(generate_sessions(cls, monday, monday + datetime.timedelta(days=13)), [])
