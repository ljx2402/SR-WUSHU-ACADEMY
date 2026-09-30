"""Phase 3 through the Django admin: attendance and substitute rules hold for
normal use, direct URLs and crafted POSTs."""

import datetime
from unittest import mock

from django.urls import reverse

from apps.academy.models import SessionCoach, TrainingSession
from apps.academy.services import assign_substitute
from apps.academy.tests.base import AcademyTestCase
from apps.accounts.models import Coach
from apps.attendance.models import AttendanceRecord
from apps.attendance.services import coach_edit_deadline, mark_attendance
from apps.audit.utils import history_for
from apps.finance.tests.admin_helpers import form_data


class AdminTestCase(AcademyTestCase):
    def login(self, user):
        self.client.force_login(user)


class AttendanceSheetAdminTests(AdminTestCase):
    def sheet_url(self, session=None):
        return reverse("admin:academy_trainingsession_attendance", args=[(session or self.session_a).pk])

    def submit(self, extra=None, reason=""):
        data = form_data(self.client.get(self.sheet_url()))
        data.update({f"status_{self.student_1.pk}": "PRESENT", "reason": reason, **(extra or {})})
        return self.client.post(self.sheet_url(), data)

    def test_sheet_access_by_role(self):
        for user, status in ((self.super_user, 200), (self.admin_user, 200), (self.finance_user, 403)):
            with self.subTest(user=user.username):
                self.login(user)
                self.assertEqual(self.client.get(self.sheet_url()).status_code, status)
        for user in (self.coach_a_user, self.parent_1_user):
            with self.subTest(user=user.username):
                self.login(user)
                response = self.client.post(self.sheet_url(), {f"status_{self.student_1.pk}": "PRESENT"})
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("admin:login"), response["Location"])
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_sheet_shows_unmarked_students_and_saves_through_the_service(self):
        self.login(self.admin_user)
        page = self.client.get(self.sheet_url()).content.decode()
        self.assertIn("Unmarked: 2", page)
        response = self.submit()
        self.assertEqual(response.status_code, 302, response.content[:500])
        record = AttendanceRecord.objects.get()
        self.assertEqual((record.student, record.status, record.recorded_by),
                         (self.student_1, "PRESENT", self.admin_user))
        self.assertFalse(AttendanceRecord.objects.filter(student=self.student_3).exists())  # left UNMARKED
        self.assertIn("Unmarked: 1", self.client.get(self.sheet_url()).content.decode())

    def test_crafted_field_for_a_student_not_on_the_roster_is_refused(self):
        self.login(self.admin_user)
        response = self.submit({f"status_{self.student_2.pk}": "PRESENT"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not on the roster")
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_change_and_late_correction_need_a_reason(self):
        self.login(self.admin_user)
        self.submit()
        response = self.submit({f"status_{self.student_1.pk}": "ABSENT"})
        self.assertContains(response, "reason is required")
        self.assertEqual(AttendanceRecord.objects.get().status, "PRESENT")
        after = coach_edit_deadline(self.session_a) + datetime.timedelta(minutes=1)
        with mock.patch("django.utils.timezone.now", return_value=after):
            self.login(self.admin_user)  # sessions last 12 hours (Phase 5): sign in at the simulated time
            response = self.submit({f"status_{self.student_3.pk}": "ABSENT"})
            self.assertContains(response, "reason is required")
            response = self.submit({f"status_{self.student_3.pk}": "ABSENT"}, reason="From the paper register")
            self.assertEqual(response.status_code, 302)
        record = AttendanceRecord.objects.get(student=self.student_3)
        self.assertIn("Administrative correction", history_for(record).get().reason)

    def test_cancelled_session_sheet_refuses_marks(self):
        self.session_a.status = TrainingSession.Status.CANCELLED
        self.session_a.save()
        self.login(self.admin_user)
        self.assertContains(self.submit(), "cancelled session")
        self.assertFalse(AttendanceRecord.objects.exists())


class AttendanceRecordAdminTests(AdminTestCase):
    def setUp(self):
        self.record = mark_attendance(self.session_a, self.student_1, "ABSENT", self.coach_a_user)

    def url(self, name, *args):
        return reverse(f"admin:attendance_attendancerecord_{name}", args=args)

    def test_records_cannot_be_added_edited_or_deleted_directly(self):
        self.login(self.super_user)
        self.assertEqual(self.client.get(self.url("add")).status_code, 403)
        self.assertEqual(self.client.post(self.url("add"), {"session": self.session_a.pk, "student": self.student_3.pk,
                                                            "status": "PRESENT"}).status_code, 403)
        self.assertEqual(self.client.get(self.url("change", self.record.pk)).status_code, 200)  # read-only view
        self.assertEqual(self.client.post(self.url("change", self.record.pk),
                                          {"status": "PRESENT", "session": self.session_a.pk,
                                           "student": self.student_1.pk}).status_code, 403)
        self.assertEqual(self.client.post(self.url("delete", self.record.pk), {"post": "yes"}).status_code, 403)
        self.client.post(self.url("changelist"), {"action": "delete_selected", "_selected_action": [self.record.pk],
                                                  "post": "yes"})
        self.assertEqual(list(AttendanceRecord.objects.values_list("status", flat=True)), ["ABSENT"])

    def test_correction_goes_through_the_service_with_a_reason(self):
        self.login(self.admin_user)
        url = self.url("correct", self.record.pk)
        self.assertEqual(self.client.post(url, {"status": "EXCUSED", "reason": ""}).status_code, 200)
        self.assertEqual(AttendanceRecord.objects.get().status, "ABSENT")
        response = self.client.post(url, {"status": "EXCUSED", "reason": "Doctor's note"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceRecord.objects.get().status, "EXCUSED")
        entry = history_for(self.record).filter(action="UPDATE").get()
        self.assertEqual((entry.actor, entry.reason), (self.admin_user, "Doctor's note"))

    def test_correction_view_is_capability_checked_and_ids_are_checked(self):
        self.login(self.finance_user)
        self.assertEqual(self.client.post(self.url("correct", self.record.pk),
                                          {"status": "PRESENT", "reason": "x"}).status_code, 403)
        self.login(self.admin_user)
        self.assertEqual(self.client.post(self.url("correct", 999999), {"status": "PRESENT", "reason": "x"}).status_code,
                         404)
        self.assertEqual(AttendanceRecord.objects.get().status, "ABSENT")


class SubstituteAdminTests(AdminTestCase):
    def assign_url(self, session=None):
        return reverse("admin:academy_trainingsession_substitute", args=[(session or self.session_b).pk])

    def test_assign_view_refuses_a_second_substitute(self):
        self.login(self.admin_user)
        data = {"substitute": self.coach_a.pk, "replaces": self.coach_b.pk, "reason": "Coach B sick"}
        self.assertEqual(self.client.post(self.assign_url(), data).status_code, 302)
        coach_c = Coach.objects.create(full_name="Coach C", phone="013")
        response = self.client.post(self.assign_url(), {"substitute": coach_c.pk, "reason": "extra"}, follow=True)
        self.assertContains(response, "already the authorized substitute")
        self.assertEqual(SessionCoach.objects.active_substitutes().count(), 1)

    def test_assign_view_capability_and_ids(self):
        self.login(self.finance_user)
        self.assertEqual(self.client.post(self.assign_url(), {"substitute": self.coach_a.pk}).status_code, 403)
        self.login(self.admin_user)
        self.assertEqual(self.client.get(reverse("admin:academy_trainingsession_substitute", args=[999999])).status_code,
                         404)
        self.assertFalse(SessionCoach.objects.substitutes().exists())

    def test_authorizations_are_read_only_and_revoked_through_the_service(self):
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user,
                                 reason="Coach B sick")
        self.login(self.super_user)
        change = reverse("admin:academy_sessioncoach_change", args=[slot.pk])
        self.assertContains(self.client.get(change), "Revoke substitute")
        self.assertEqual(self.client.post(change, {"status": "REVOKED", "access_ends_at_0": "2099-01-01"}).status_code,
                         403)
        self.assertEqual(self.client.get(reverse("admin:academy_sessioncoach_add")).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:academy_sessioncoach_delete", args=[slot.pk]),
                                          {"post": "yes"}).status_code, 403)
        revoke = reverse("admin:academy_sessioncoach_revoke", args=[slot.pk])
        self.login(self.finance_user)
        self.assertEqual(self.client.post(revoke, {"reason": "x"}).status_code, 403)
        self.login(self.admin_user)
        self.assertEqual(self.client.post(revoke, {"reason": ""}).status_code, 200)  # reason required
        self.assertEqual(SessionCoach.objects.get(pk=slot.pk).status, SessionCoach.Status.ASSIGNED)
        self.assertEqual(self.client.post(revoke, {"reason": "Coach B recovered"}).status_code, 302)
        slot.refresh_from_db()
        self.assertEqual((slot.status, slot.revoked_by, slot.revocation_reason),
                         (SessionCoach.Status.REVOKED, self.admin_user, "Coach B recovered"))
        self.assertContains(self.client.post(revoke, {"reason": "again"}), "already revoked")
        self.assertNotContains(self.client.get(change), "Revoke substitute")

    def test_cancelling_a_session_in_the_admin_ends_the_substitute(self):
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user,
                                 reason="Coach B sick")
        self.login(self.admin_user)
        url = reverse("admin:academy_trainingsession_change", args=[self.session_b.pk])
        data = form_data(self.client.get(url))
        data["status"] = "CANCELLED"
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, response.content[:1000])
        slot.refresh_from_db()
        self.assertEqual(slot.status, SessionCoach.Status.CANCELLED)
        self.assertEqual(slot.revoked_by, self.admin_user)
