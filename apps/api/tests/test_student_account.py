"""StudentAccount: one login ↔ one student, access to own record only."""

import datetime

from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from apps.academy.models import StudentAccount
from apps.academy.tests.base import AcademyTestCase, make_user
from apps.accounts.capabilities import Role
from apps.attendance.services import mark_attendance
from apps.competitions.models import Competition, CompetitionEvent
from apps.competitions.services import register


class StudentAccountTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.raj_user = make_user("raj", Role.STUDENT)
        StudentAccount.objects.create(user=cls.raj_user, student=cls.student_3)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.raj_user)

    def rows(self, path):
        body = self.client.get(path).json()
        return body.get("results", body)

    def test_student_sees_self(self):
        self.assertEqual([r["id"] for r in self.rows("/api/students/")], [self.student_3.pk])
        body = self.client.get(f"/api/students/{self.student_3.pk}/").json()
        self.assertEqual(body["student_no"], "S3")
        # Guardians shown as contacts only: no parent IC / address.
        self.assertEqual(body["guardians"], [{"name": "Parent Two", "relationship": "MOTHER", "phone": "0124"}])
        me = self.client.get("/api/me/").json()
        self.assertEqual((me["roles"], me["student"]["id"]), (["STUDENT"], self.student_3.pk))

    def test_student_cannot_see_another_student(self):
        for other in (self.student_1, self.student_2):
            self.assertEqual(self.client.get(f"/api/students/{other.pk}/").status_code, 404)
            self.assertEqual(self.client.get(f"/api/students/{other.pk}/attendance-summary/").status_code, 404)

    def test_student_cannot_reach_parent_coach_finance_or_admin_endpoints(self):
        for path in ("/api/parents/", "/api/coaches/", "/api/users/", "/api/enrollments/", "/api/charges/",
                     "/api/payments/", "/api/receipts/", "/api/payslips/", "/api/reports/",
                     "/api/reports/students/", f"/api/sessions/{self.session_a.pk}/roster/",
                     f"/api/classes/{self.class_a.pk}/students/"):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        self.assertEqual(self.client.post("/api/students/", {}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/sessions/{self.session_a.pk}/attendance/",
                                          {"records": []}, format="json").status_code, 403)

    def test_student_timetable_attendance_and_competitions_are_own(self):
        self.assertEqual({r["id"] for r in self.rows("/api/sessions/")}, {self.session_a.pk})
        self.assertEqual(self.client.get(f"/api/sessions/{self.session_b.pk}/").status_code, 404)
        mark_attendance(self.session_a, self.student_3, "PRESENT", self.admin_user)
        mark_attendance(self.session_a, self.student_1, "ABSENT", self.admin_user)
        self.assertEqual({r["student"] for r in self.rows("/api/attendance/")}, {self.student_3.pk})
        summary = self.client.get(f"/api/students/{self.student_3.pk}/attendance-summary/").json()
        self.assertEqual(summary["percentage"], "100.00")
        competition = Competition.objects.create(name="Open", start_date=self.today + datetime.timedelta(days=30),
                                                 end_date=self.today + datetime.timedelta(days=30),
                                                 registration_deadline=self.today + datetime.timedelta(days=10),
                                                 status="OPEN")
        event = CompetitionEvent.objects.create(competition=competition, event_type="NANQUAN", name="Nanquan Open")
        mine = register(self.student_3, event, self.admin_user)
        register(self.student_1, event, self.admin_user)
        self.assertEqual({r["id"] for r in self.rows("/api/competition-registrations/")}, {mine.pk})
        self.assertEqual(self.client.post("/api/competition-registrations/",
                                          {"event": event.pk, "student": self.student_3.pk}).status_code, 403)

    def test_one_to_one_constraints(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StudentAccount.objects.create(user=self.raj_user, student=self.student_1)  # user already linked
        with self.assertRaises(IntegrityError), transaction.atomic():
            StudentAccount.objects.create(user=make_user("imposter", Role.STUDENT), student=self.student_3)

    def test_link_without_role_grants_nothing_and_role_without_link_sees_nothing(self):
        linked_no_role = make_user("linked")
        StudentAccount.objects.create(user=linked_no_role, student=self.student_1)
        client = APIClient()
        client.force_authenticate(linked_no_role)
        self.assertEqual(client.get("/api/students/").status_code, 403)
        role_no_link = make_user("unlinked", Role.STUDENT)
        client.force_authenticate(role_no_link)
        body = client.get("/api/students/").json()
        self.assertEqual(body["count"], 0)
