"""Users holding more than one role (integration tests through the API).

Coach A also becomes a parent of "Kid", who trains in class B, which Coach A
does NOT coach. Coach A must see Kid as a parent and class-A students as a
coach, and must never get coach access to class B or parent access to class-A
families.
"""

import datetime

from rest_framework.test import APIClient

from apps.academy.models import Enrollment, Guardianship
from apps.academy.services import assign_substitute
from apps.academy.tests.base import AcademyTestCase, make_user
from apps.accounts.capabilities import Role
from apps.accounts.models import Parent
from apps.accounts.services import set_roles
from apps.attendance.services import mark_attendance
from apps.competitions.models import Competition, CompetitionEvent
from apps.finance.services import add_charge


class CoachParentTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        set_roles(cls.coach_a_user, [Role.COACH, Role.PARENT], cls.super_user, "Coach A's child trains in class B")
        cls.coach_parent = Parent.objects.create(user=cls.coach_a_user, full_name="Coach A (parent)", phone="011")
        cls.kid = cls.make_student("K1", "Kid", cls.coach_parent, cls.class_b)
        cls.student_1.medical_notes = "Asthma"
        cls.student_1.ic_number = "140501-10-1111"
        cls.student_1.save()
        cls.kid.ic_number = "150101-10-2222"
        cls.kid.save()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.coach_a_user)

    def rows(self, path):
        body = self.client.get(path).json()
        return body.get("results", body)

    def test_students_are_the_union_with_context_specific_detail(self):
        rows = {r["id"]: r for r in self.rows("/api/students/")}
        self.assertEqual(set(rows), {self.student_1.pk, self.student_3.pk, self.kid.pk})
        own = rows[self.kid.pk]  # parent view of own child
        self.assertEqual(own["ic_number"], "150101-10-2222")
        self.assertEqual(own["guardians"], [{"name": "Coach A (parent)", "relationship": "MOTHER", "phone": "011"}])
        coached = rows[self.student_1.pk]  # coach view of a roster student
        self.assertNotIn("ic_number", coached)
        self.assertNotIn("address", coached)
        self.assertNotIn("guardians", coached)
        self.assertNotIn("medical_notes", coached)       # not shown to coaches
        self.assertNotIn("emergency_contacts", coached)
        self.assertIn("medical_notes", own)               # the parent's own-child view is unchanged

    def test_parent_role_never_opens_coach_access_to_childs_class(self):
        self.assertEqual(self.client.get(f"/api/sessions/{self.session_b.pk}/").status_code, 200)  # timetable
        self.assertEqual(self.client.get(f"/api/sessions/{self.session_b.pk}/roster/").status_code, 404)
        self.assertEqual(self.client.get(f"/api/classes/{self.class_b.pk}/students/").status_code, 404)
        response = self.client.post(f"/api/sessions/{self.session_b.pk}/attendance/",
                                    {"records": [{"student": self.kid.pk, "status": "PRESENT"}]}, format="json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(f"/api/students/{self.student_2.pk}/").status_code, 404)

    def test_coach_role_never_opens_family_finance_of_roster(self):
        kid_charge = add_charge(self.kid, "UNIFORM", "Uniform", "80.00")
        roster_charge = add_charge(self.student_1, "UNIFORM", "Uniform", "80.00")
        ids = {r["id"] for r in self.rows("/api/charges/")}
        self.assertIn(kid_charge.pk, ids)
        self.assertNotIn(roster_charge.pk, ids)

    def test_attendance_records_union(self):
        mark_attendance(self.session_b, self.kid, "PRESENT", self.admin_user)
        mark_attendance(self.session_b, self.student_2, "ABSENT", self.admin_user)
        mark_attendance(self.session_a, self.student_3, "LATE", self.admin_user)
        seen = {(r["student"], r["session"]) for r in self.rows("/api/attendance/")}
        self.assertIn((self.kid.pk, self.session_b.pk), seen)            # own child
        self.assertIn((self.student_3.pk, self.session_a.pk), seen)      # coached class
        self.assertNotIn((self.student_2.pk, self.session_b.pk), seen)   # classmate of own child

    def test_competition_registration_contexts(self):
        competition = Competition.objects.create(name="Open", start_date=self.today + datetime.timedelta(days=30),
                                                 end_date=self.today + datetime.timedelta(days=30),
                                                 registration_deadline=self.today + datetime.timedelta(days=10),
                                                 status="OPEN")
        event = CompetitionEvent.objects.create(competition=competition, event_type="NANQUAN", name="Nanquan Open")
        own = self.client.post("/api/competition-registrations/", {"event": event.pk, "student": self.kid.pk})
        self.assertEqual(own.status_code, 201, own.content)
        coached = self.client.post("/api/competition-registrations/", {"event": event.pk, "student": self.student_1.pk})
        self.assertEqual(coached.status_code, 403)

    def test_substitute_session_still_session_specific(self):
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user)
        # Covering session B opens its roster, but never the class.
        self.assertEqual(self.client.get(f"/api/sessions/{self.session_b.pk}/roster/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/classes/{self.class_b.pk}/students/").status_code, 404)


class OtherRoleCombinationTests(AcademyTestCase):
    def test_admin_plus_coach_keeps_full_access(self):
        set_roles(self.coach_b_user, [Role.ADMIN, Role.COACH], self.super_user, "Head coach also runs the desk")
        client = APIClient()
        client.force_authenticate(self.coach_b_user)
        body = client.get(f"/api/students/{self.student_1.pk}/").json()
        self.assertIn("ic_number", body)  # full staff view, not the coach roster view
        self.assertEqual(client.get(f"/api/sessions/{self.session_a.pk}/roster/").status_code, 200)
        self.assertEqual(client.get("/api/reports/payroll/").status_code, 403)

    def test_finance_plus_coach_gets_roster_view_only_for_own_athletes(self):
        user = make_user("fincoach")
        set_roles(user, [Role.FINANCE_ADMIN, Role.COACH], self.super_user, "Treasurer also coaches class A")
        self.coach_a.user = None
        self.coach_a.save()
        self.coach_a.user = user
        self.coach_a.save()
        client = APIClient()
        client.force_authenticate(user)
        own = client.get(f"/api/students/{self.student_1.pk}/").json()
        other = client.get(f"/api/students/{self.student_2.pk}/").json()
        self.assertEqual(own["full_name"], self.student_1.full_name)  # roster view: coaches class A
        self.assertNotIn("medical_notes", own)
        self.assertNotIn("medical_notes", other)  # finance directory view
        self.assertEqual(client.get(f"/api/sessions/{self.session_b.pk}/roster/").status_code, 404)

    def test_enrollment_history_still_governs_coach_scope(self):
        # Record-level rules from access.py are unchanged: ending a membership removes the student.
        enrollment = Enrollment.objects.get(student=self.student_3)
        enrollment.end_date = self.today - datetime.timedelta(days=1)
        enrollment.save()
        client = APIClient()
        client.force_authenticate(self.coach_a_user)
        self.assertEqual(client.get(f"/api/students/{self.student_3.pk}/").status_code, 404)
        self.assertFalse(Guardianship.objects.filter(parent__user=self.coach_a_user).exists())
