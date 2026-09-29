"""Table-driven permission test: every role against the existing API actions.

Integration tests (HTTP through the DRF stack on the real database). Each case
runs inside its own savepoint that is rolled back, so cases cannot affect each
other. Expected statuses encode both layers:
  403 = the role lacks the capability (RBAC)
  404 = the role may use the action, but this record is outside its scope (record access)
"""

import datetime
from decimal import Decimal

from django.db import transaction
from rest_framework.test import APIClient

from apps.academy.models import StudentAccount
from apps.academy.tests.base import AcademyTestCase, make_user
from apps.accounts.capabilities import Role
from apps.competitions.models import Competition, CompetitionEvent
from apps.competitions.services import register
from apps.finance.services import add_charge, record_payment

S, A, F, C, P, T = Role.SUPER_ADMIN, Role.ADMIN, Role.FINANCE_ADMIN, Role.COACH, Role.PARENT, Role.STUDENT
ALL = (S, A, F, C, P, T)


def only(status_by_role, default=403):
    return {role: status_by_role.get(role, default) for role in ALL}


class RbacMatrixTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # STUDENT: Raj (student_3, class A, child of parent_2) has his own login.
        cls.student_user = make_user("raj", Role.STUDENT)
        StudentAccount.objects.create(user=cls.student_user, student=cls.student_3)
        cls.actors = {S: cls.super_user, A: cls.admin_user, F: cls.finance_user,
                      C: cls.coach_a_user, P: cls.parent_1_user, T: cls.student_user}

        cls.charge = add_charge(cls.student_1, "UNIFORM", "Uniform", "80.00")
        cls.open_charge = add_charge(cls.student_1, "OTHER", "Misc", "30.00")
        cls.payment, _ = record_payment("Parent One", "80.00", "CASH", [(cls.charge, "80.00")], parent=cls.parent_1)
        cls.competition = Competition.objects.create(
            name="State Open", start_date=cls.today + datetime.timedelta(days=30),
            end_date=cls.today + datetime.timedelta(days=30),
            registration_deadline=cls.today + datetime.timedelta(days=10), status="OPEN")
        cls.event = CompetitionEvent.objects.create(competition=cls.competition, event_type="CHANGQUAN",
                                                    name="Changquan Open", fee=Decimal("40.00"))
        cls.other_event = CompetitionEvent.objects.create(competition=cls.competition, event_type="JIANSHU",
                                                          name="Jianshu Open")
        cls.registration = register(cls.student_1, cls.other_event, cls.admin_user)

    def cases(self):
        today = self.today
        return [
            # (description, method, path, data, expected statuses by role)
            ("list users", "get", "/api/users/", None, only({S: 200})),
            ("change roles", "post", f"/api/users/{self.parent_2_user.pk}/roles/",
             {"roles": ["PARENT", "COACH"], "reason": "test"}, only({S: 200})),
            ("list parents", "get", "/api/parents/", None, only({S: 200, A: 200, F: 200})),
            ("create parent", "post", "/api/parents/", {"full_name": "New Parent", "phone": "019"},
             only({S: 201, A: 201})),
            ("list coaches", "get", "/api/coaches/", None, only({S: 200, A: 200, F: 200})),
            ("list students", "get", "/api/students/", None, only({r: 200 for r in ALL})),
            ("view another family's child", "get", f"/api/students/{self.student_2.pk}/", None,
             only({S: 200, A: 200, F: 200, C: 404, P: 200, T: 404})),
            ("view a class-A student", "get", f"/api/students/{self.student_3.pk}/", None,
             only({S: 200, A: 200, F: 200, C: 200, P: 404, T: 200})),
            ("create student", "post", "/api/students/",
             {"student_no": "N1", "full_name": "New", "gender": "M", "date_of_birth": "2015-01-01"},
             only({S: 201, A: 201})),
            ("student history", "get", f"/api/students/{self.student_1.pk}/history/", None, only({S: 200, A: 200})),
            ("attendance summary", "get", f"/api/students/{self.student_3.pk}/attendance-summary/", None,
             only({S: 200, A: 200, C: 200, P: 404, T: 200})),
            ("list enrollments", "get", "/api/enrollments/", None, only({S: 200, A: 200})),
            ("list classes", "get", "/api/classes/", None, only({S: 200, A: 200, C: 200, P: 200, T: 200})),
            ("class A roster", "get", f"/api/classes/{self.class_a.pk}/students/", None,
             only({S: 200, A: 200, C: 200})),
            ("class B roster", "get", f"/api/classes/{self.class_b.pk}/students/", None,
             only({S: 200, A: 200, C: 404})),
            ("session A roster", "get", f"/api/sessions/{self.session_a.pk}/roster/", None,
             only({S: 200, A: 200, C: 200})),
            ("session B roster", "get", f"/api/sessions/{self.session_b.pk}/roster/", None,
             only({S: 200, A: 200, C: 404})),
            ("view session B", "get", f"/api/sessions/{self.session_b.pk}/", None,
             only({S: 200, A: 200, C: 404, P: 200, T: 404})),
            ("take attendance A", "post", f"/api/sessions/{self.session_a.pk}/attendance/",
             {"records": [{"student": self.student_1.pk, "status": "PRESENT"}]}, only({S: 200, A: 200, C: 200})),
            ("take attendance B", "post", f"/api/sessions/{self.session_b.pk}/attendance/",
             {"records": [{"student": self.student_2.pk, "status": "PRESENT"}]}, only({S: 200, A: 200, C: 404})),
            ("assign substitute", "post", f"/api/sessions/{self.session_b.pk}/assign-substitute/",
             {"substitute": self.coach_a.pk, "replaces": self.coach_b.pk}, only({S: 201, A: 201})),
            ("list attendance", "get", "/api/attendance/", None, only({S: 200, A: 200, C: 200, P: 200, T: 200})),
            ("list charges", "get", "/api/charges/", None, only({S: 200, A: 200, F: 200, P: 200})),
            ("add charge", "post", "/api/charges/",
             {"student": self.student_1.pk, "fee_type": "WEAPON", "description": "Jian", "unit_amount": "180.00"},
             only({S: 201, F: 201})),
            ("monthly billing", "post", "/api/charges/generate-monthly/", {"year": today.year, "month": today.month},
             only({S: 201, F: 201})),
            ("record payment", "post", "/api/payments/",
             {"parent": self.parent_1.pk, "payer_name": "Parent One", "amount": "30.00", "method": "CASH",
              "allocations": [{"charge": self.open_charge.pk, "amount": "30.00"}]}, only({S: 201, A: 201, F: 201})),
            ("void payment", "post", f"/api/payments/{self.payment.pk}/void/", {"reason": "entered twice"},
             only({S: 200, F: 200})),
            ("list receipts", "get", "/api/receipts/", None, only({S: 200, A: 200, F: 200, P: 200})),
            ("list competitions", "get", "/api/competitions/", None, only({r: 200 for r in ALL})),
            ("create competition", "post", "/api/competitions/",
             {"name": "New Cup", "start_date": "2026-12-01", "end_date": "2026-12-01",
              "registration_deadline": "2026-11-20"}, only({S: 201, A: 201})),
            ("list registrations", "get", "/api/competition-registrations/", None,
             only({S: 200, A: 200, C: 200, P: 200, T: 200})),
            ("register own child / any student", "post", "/api/competition-registrations/",
             {"event": self.event.pk, "student": self.student_1.pk}, only({S: 201, A: 201, P: 201})),
            ("enter result", "post", "/api/competition-results/",
             {"registration": self.registration.pk, "placing": 1, "medal": "GOLD"}, only({S: 201, A: 201})),
            ("list payslips", "get", "/api/payslips/", None, only({S: 200, F: 200, C: 200})),
            ("students report", "get", "/api/reports/students/", None, only({S: 200, A: 200})),
            ("attendance report", "get", "/api/reports/attendance/", None, only({S: 200, A: 200})),
            ("fees report", "get", "/api/reports/fees/", None, only({S: 200, F: 200})),
            ("payroll report", "get", "/api/reports/payroll/", None, only({S: 200, F: 200})),
            ("report index", "get", "/api/reports/", None, only({S: 200, A: 200, F: 200})),
            ("me", "get", "/api/me/", None, only({r: 200 for r in ALL})),
        ]

    def test_permission_matrix(self):
        for description, method, path, data, expected in self.cases():
            for role in ALL:
                with self.subTest(case=description, role=role), transaction.atomic():
                    client = APIClient()
                    client.force_authenticate(self.actors[role])
                    response = getattr(client, method)(path, data, format="json") if data is not None \
                        else getattr(client, method)(path)
                    self.assertEqual(response.status_code, expected[role],
                                     f"{role} {method.upper()} {path}: {response.content[:300]!r}")
                    transaction.set_rollback(True)

    def ids(self, role, path):
        client = APIClient()
        client.force_authenticate(self.actors[role])
        body = client.get(path).json()
        return {row["id"] for row in body.get("results", body)}

    def test_student_visibility_by_role(self):
        everyone = set(type(self.student_1).objects.values_list("id", flat=True))
        expected = {
            S: everyone, A: everyone, F: everyone,
            C: {self.student_1.pk, self.student_3.pk},  # class A roster
            P: {self.student_1.pk, self.student_2.pk},  # own children
            T: {self.student_3.pk},                     # self only
        }
        for role, ids in expected.items():
            self.assertEqual(self.ids(role, "/api/students/"), ids, role)

    def test_finance_directory_hides_personal_details(self):
        client = APIClient()
        client.force_authenticate(self.finance_user)
        body = client.get(f"/api/students/{self.student_1.pk}/").json()
        self.assertEqual(set(body), {"id", "student_no", "full_name", "chinese_name", "status", "guardians"})

    def test_coach_bank_details_only_for_finance(self):
        self.coach_a.bank_account_no = "7890123456"
        self.coach_a.save()
        for role, visible in ((S, True), (F, True), (A, False)):
            client = APIClient()
            client.force_authenticate(self.actors[role])
            body = client.get(f"/api/coaches/{self.coach_a.pk}/").json()
            self.assertEqual("bank_account_no" in body, visible, role)
        # Finance may correct bank details but not the rest of the coach record.
        client = APIClient()
        client.force_authenticate(self.finance_user)
        client.patch(f"/api/coaches/{self.coach_a.pk}/", {"bank_account_no": "111", "full_name": "Renamed"},
                     format="json")
        self.coach_a.refresh_from_db()
        self.assertEqual((self.coach_a.bank_account_no, self.coach_a.full_name), ("111", "Coach A"))
