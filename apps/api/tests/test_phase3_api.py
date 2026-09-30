"""Phase 3 through the API: substitute authorization and attendance, including
crafted requests, and the COACH + PARENT combination with and without a
substitute authorization."""

import datetime
from unittest import mock

from rest_framework.test import APIClient

from apps.academy.models import SessionCoach, TrainingSession
from apps.academy.services import assign_substitute, revoke_substitute
from apps.academy.tests.base import AcademyTestCase
from apps.accounts.capabilities import Role
from apps.accounts.models import Parent
from apps.accounts.services import set_roles
from apps.attendance.models import AttendanceRecord
from apps.attendance.services import coach_edit_deadline, mark_attendance
from apps.finance.services import add_charge


class Phase3ApiTestCase(AcademyTestCase):
    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def rows(self, response):
        body = response.json()
        return body.get("results", body)

    def ids(self, response):
        return {row["id"] for row in self.rows(response)}


class SubstituteApiTests(Phase3ApiTestCase):
    def assign(self, user, **data):
        payload = {"substitute": self.coach_a.pk, "replaces": self.coach_b.pk, "reason": "Coach B sick", **data}
        return self.client_for(user).post(f"/api/sessions/{self.session_b.pk}/assign-substitute/", payload)

    def revoke(self, user, session=None, **data):
        payload = {"substitute": self.coach_a.pk, "reason": "Coach B recovered", **data}
        return self.client_for(user).post(f"/api/sessions/{(session or self.session_b).pk}/revoke-substitute/", payload)

    def test_only_substitute_assigners_can_authorize(self):
        for user in (self.coach_a_user, self.coach_b_user, self.parent_1_user, self.finance_user):
            with self.subTest(user=user.username):
                self.assertEqual(self.assign(user).status_code, 403)
        self.assertFalse(SessionCoach.objects.substitutes().exists())
        response = self.assign(self.admin_user)
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["role"], body["status"], body["replaces"]), ("SUBSTITUTE", "ASSIGNED", self.coach_b.pk))
        self.assertIsNotNone(body["authorized_at"])

    def test_conflicting_or_invalid_authorizations_are_refused(self):
        self.assertEqual(self.assign(self.admin_user).status_code, 201)
        self.assertEqual(self.assign(self.admin_user).status_code, 400)                        # same again
        coach_c = self.coach_b.__class__.objects.create(full_name="Coach C", phone="013")
        self.assertEqual(self.assign(self.admin_user, substitute=coach_c.pk, replaces="").status_code, 400)  # second
        self.assertEqual(self.assign(self.admin_user, substitute=999999).status_code, 404)       # crafted id
        self.assertEqual(self.assign(self.admin_user, substitute="1 OR 1=1").status_code, 400)   # crafted value
        self.assertEqual(self.revoke(self.admin_user, substitute="abc").status_code, 400)
        self.assertEqual(SessionCoach.objects.active_substitutes().count(), 1)

    def test_cancelled_session_refuses_substitute(self):
        self.session_b.status = TrainingSession.Status.CANCELLED
        self.session_b.save()
        response = self.assign(self.admin_user)
        self.assertEqual(response.status_code, 400)
        self.assertIn("cancelled", str(response.json()))

    def test_revocation_requires_capability_and_reason_and_happens_once(self):
        self.assign(self.admin_user)
        for user in (self.coach_a_user, self.coach_b_user, self.finance_user, self.parent_1_user):
            with self.subTest(user=user.username):
                self.assertEqual(self.revoke(user).status_code, 403)
        self.assertEqual(self.revoke(self.admin_user, reason="").status_code, 400)
        response = self.revoke(self.admin_user)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "REVOKED")
        self.assertEqual(self.revoke(self.admin_user).status_code, 400)                            # already revoked
        self.assertEqual(self.revoke(self.admin_user, substitute=self.coach_b.pk).status_code, 404)  # no such
        self.assertEqual(SessionCoach.objects.substitutes().count(), 1)                          # history kept

    def test_crafted_revoke_through_another_session_is_refused(self):
        self.assign(self.admin_user)
        self.assertEqual(self.revoke(self.admin_user, session=self.session_a).status_code, 404)
        self.assertTrue(SessionCoach.objects.active_substitutes().filter(session=self.session_b).exists())

    def test_revoked_substitute_loses_every_path(self):
        self.assign(self.admin_user)
        sub = self.client_for(self.coach_a_user)
        base = f"/api/sessions/{self.session_b.pk}"
        self.assertEqual(sub.get(f"{base}/roster/").status_code, 200)
        self.revoke(self.admin_user)
        self.assertEqual(sub.get(f"{base}/").status_code, 404)
        self.assertEqual(sub.get(f"{base}/roster/").status_code, 404)
        self.assertEqual(sub.get(f"{base}/attendance/").status_code, 404)
        post = sub.post(f"{base}/attendance/", {"records": [{"student": self.student_2.pk, "status": "PRESENT"}]},
                        format="json")
        self.assertEqual(post.status_code, 404)
        self.assertEqual(sub.get(f"/api/students/{self.student_2.pk}/").status_code, 404)
        self.assertEqual(sub.get("/api/me/").json()["substitute_sessions"], [])
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_cancelling_the_session_through_the_api_ends_the_authorization(self):
        self.assign(self.admin_user)
        response = self.client_for(self.admin_user).patch(f"/api/sessions/{self.session_b.pk}/",
                                                          {"status": "CANCELLED"})
        self.assertEqual(response.status_code, 200, response.content)
        slot = SessionCoach.objects.substitutes().get(session=self.session_b)
        self.assertEqual(slot.status, SessionCoach.Status.CANCELLED)
        self.assertEqual(self.client_for(self.coach_a_user).get(f"/api/sessions/{self.session_b.pk}/roster/").status_code,
                         404)
        # Cancelled authorizations cannot be revoked (nothing is active) and are not reused.
        self.assertEqual(self.revoke(self.admin_user).status_code, 400)


class AttendanceApiTests(Phase3ApiTestCase):
    url = None

    def setUp(self):
        self.url = f"/api/sessions/{self.session_a.pk}/attendance/"

    def post(self, user, records, reason=""):
        return self.client_for(user).post(self.url, {"records": records, "reason": reason}, format="json")

    def test_sheet_shows_expected_marked_and_unmarked(self):
        self.post(self.coach_a_user, [{"student": self.student_1.pk, "status": "PRESENT"}])
        body = self.client_for(self.coach_a_user).get(self.url).json()
        self.assertEqual({(r["student"], r["status"]) for r in body["sheet"]},
                         {(self.student_1.pk, "PRESENT"), (self.student_3.pk, "UNMARKED")})
        summary = body["summary"]
        self.assertEqual((summary["expected"], summary["marked"], summary["unmarked"], summary["percentage"]),
                         (2, 1, 1, "100.00"))
        self.assertEqual(body["state"], "OPEN")
        self.assertIsNotNone(body["coach_edit_deadline"])

    def test_non_roster_student_is_refused_and_nothing_is_saved(self):
        response = self.post(self.coach_a_user, [{"student": self.student_1.pk, "status": "PRESENT"},
                                                 {"student": self.student_2.pk, "status": "PRESENT"}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AttendanceRecord.objects.exists())
        self.assertEqual(self.post(self.admin_user, [{"student": 999999, "status": "PRESENT"}]).status_code, 400)

    def test_duplicate_entries_are_refused(self):
        response = self.post(self.coach_a_user, [{"student": self.student_1.pk, "status": "PRESENT"},
                                                 {"student": self.student_1.pk, "status": "ABSENT"}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_unauthorized_users(self):
        entry = [{"student": self.student_1.pk, "status": "PRESENT"}]
        self.assertEqual(self.post(self.coach_b_user, entry).status_code, 404)   # not their session
        self.assertEqual(self.post(self.finance_user, entry).status_code, 403)   # no attendance capability
        self.assertEqual(self.post(self.parent_1_user, entry).status_code, 403)  # parent of student 1
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_change_without_reason_is_refused(self):
        self.post(self.coach_a_user, [{"student": self.student_1.pk, "status": "PRESENT"}])
        self.assertEqual(self.post(self.coach_a_user, [{"student": self.student_1.pk, "status": "ABSENT"}]).status_code,
                         400)
        self.assertEqual(AttendanceRecord.objects.get().status, "PRESENT")

    def test_after_the_window_coach_is_refused_and_admin_must_give_a_reason(self):
        entry = [{"student": self.student_1.pk, "status": "PRESENT"}]
        after = coach_edit_deadline(self.session_a) + datetime.timedelta(seconds=1)
        with mock.patch("django.utils.timezone.now", return_value=after):
            self.assertEqual(self.post(self.coach_a_user, entry, reason="late").status_code, 403)
            self.assertEqual(self.post(self.admin_user, entry).status_code, 400)
            self.assertEqual(self.client_for(self.admin_user).get(self.url).json()["state"], "LOCKED")
            self.assertEqual(self.post(self.admin_user, entry, reason="Paper register").status_code, 200)
        self.assertEqual(AttendanceRecord.objects.get().status, "PRESENT")

    def test_attendance_records_cannot_be_written_directly(self):
        record = mark_attendance(self.session_a, self.student_1, "ABSENT", self.coach_a_user)
        client = self.client_for(self.super_user)
        for method in ("put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(client, method)(f"/api/attendance/{record.pk}/", {"status": "PRESENT"})
                self.assertIn(response.status_code, (403, 405))
        self.assertIn(client.post("/api/attendance/", {"session": self.session_a.pk, "student": self.student_3.pk,
                                                       "status": "PRESENT"}).status_code, (403, 405))
        self.assertEqual(AttendanceRecord.objects.get().status, "ABSENT")


class CoachParentSameClassTests(Phase3ApiTestCase):
    """Coach A (COACH + PARENT) has a child in class A, which they coach, and a
    child in class B, which they do not coach."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        set_roles(cls.coach_a_user, [Role.COACH, Role.PARENT], cls.super_user, "Coach A's children train here")
        cls.as_parent = Parent.objects.create(user=cls.coach_a_user, full_name="Coach A (parent)", phone="011")
        cls.kid_a = cls.make_student("KA", "Kid A", cls.as_parent, cls.class_a)
        cls.kid_b = cls.make_student("KB", "Kid B", cls.as_parent, cls.class_b)
        for student in (cls.kid_a, cls.kid_b, cls.student_1, cls.student_2):
            student.ic_number = f"IC-{student.student_no}"
            student.save()

    def setUp(self):
        self.client = self.client_for(self.coach_a_user)

    def detail(self, student):
        return self.client.get(f"/api/students/{student.pk}/")

    def test_parent_and_coach_contexts_stay_separate(self):
        self.assertEqual(self.ids(self.client.get("/api/students/")),
                         {self.kid_a.pk, self.kid_b.pk, self.student_1.pk, self.student_3.pk})
        self.assertEqual(self.detail(self.kid_a).json()["ic_number"], "IC-KA")   # own child: parent view
        self.assertNotIn("ic_number", self.detail(self.student_1).json())        # coached: roster view
        self.assertEqual(self.detail(self.student_2).status_code, 404)           # kid B's classmate
        # Parent role never opens kid B's class roster.
        self.assertEqual(self.client.get(f"/api/classes/{self.class_b.pk}/students/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/sessions/{self.session_b.pk}/roster/").status_code, 404)

    def test_coaching_never_exposes_family_finance(self):
        mine = add_charge(self.kid_a, "UNIFORM", "Uniform", "80.00")
        coached = add_charge(self.student_1, "UNIFORM", "Uniform", "80.00")
        ids = self.ids(self.client.get("/api/charges/"))
        self.assertIn(mine.pk, ids)
        self.assertNotIn(coached.pk, ids)
        self.assertEqual(self.client.get(f"/api/charges/{coached.pk}/").status_code, 404)

    def test_substitute_authorization_stays_limited_to_the_session(self):
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user,
                                 reason="Coach B at a course")
        other_b = self.class_b.sessions.create(date=self.today + datetime.timedelta(days=7),
                                               start_time=datetime.time(19), end_time=datetime.time(21))
        roster = {r["id"] for r in self.client.get(f"/api/sessions/{self.session_b.pk}/roster/").json()}
        self.assertEqual(roster, {self.kid_b.pk, self.student_2.pk})
        self.assertEqual(self.client.get(f"/api/classes/{self.class_b.pk}/students/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/sessions/{other_b.pk}/roster/").status_code, 404)
        self.assertNotIn("ic_number", self.detail(self.student_2).json())   # roster view only
        self.assertEqual(self.detail(self.kid_b).json()["ic_number"], "IC-KB")
        charge = add_charge(self.student_2, "UNIFORM", "Uniform", "80.00")
        self.assertNotIn(charge.pk, self.ids(self.client.get("/api/charges/")))
        marked = self.client.post(f"/api/sessions/{self.session_b.pk}/attendance/",
                                  {"records": [{"student": self.student_2.pk, "status": "PRESENT"},
                                               {"student": self.kid_b.pk, "status": "PRESENT"}]}, format="json")
        self.assertEqual(marked.status_code, 200, marked.content)

        revoke_substitute(slot, self.admin_user, "Coach B is back")
        self.assertEqual(self.client.get(f"/api/sessions/{self.session_b.pk}/roster/").status_code, 404)
        self.assertEqual(self.detail(self.student_2).status_code, 404)
        self.assertEqual(self.detail(self.kid_b).json()["ic_number"], "IC-KB")  # still their child
        attendance = self.ids(self.client.get("/api/attendance/"))
        own = AttendanceRecord.objects.get(student=self.kid_b)
        other = AttendanceRecord.objects.get(student=self.student_2)
        self.assertIn(own.pk, attendance)        # parent context: own child's record
        self.assertNotIn(other.pk, attendance)   # coach context ended with the authorization
        response = self.client.post(f"/api/sessions/{self.session_b.pk}/attendance/",
                                    {"records": [{"student": self.kid_b.pk, "status": "LATE"}], "reason": "x"},
                                    format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(own.status, AttendanceRecord.objects.get(pk=own.pk).status)
