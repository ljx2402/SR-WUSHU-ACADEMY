import datetime
from decimal import Decimal

from rest_framework.test import APIClient

from apps.academy.services import assign_substitute
from apps.academy.tests.base import AcademyTestCase
from apps.competitions.models import Competition, CompetitionEvent
from apps.finance.models import Receipt
from apps.finance.services import add_charge, record_payment
from apps.payroll.models import CoachRate
from apps.payroll.services import calculate_run, finalize_run


class ApiTestCase(AcademyTestCase):
    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def ids(self, response):
        data = response.json()
        return {row["id"] for row in data.get("results", data)}


class ParentApiTests(ApiTestCase):
    def test_parent_lists_only_own_children(self):
        response = self.client_for(self.parent_1_user).get("/api/students/")
        self.assertEqual(self.ids(response), {self.student_1.id, self.student_2.id})
        self.assertEqual(self.client_for(self.parent_1_user).get(f"/api/students/{self.student_3.id}/").status_code, 404)

    def test_parent_cannot_edit_or_see_rosters(self):
        client = self.client_for(self.parent_1_user)
        self.assertEqual(client.patch(f"/api/students/{self.student_1.id}/", {"school": "x"}).status_code, 403)
        self.assertEqual(client.get(f"/api/sessions/{self.session_a.id}/roster/").status_code, 403)

    def test_parent_sees_own_charges_and_receipts_only(self):
        mine = add_charge(self.student_1, "UNIFORM", "Uniform", "80.00")
        other = add_charge(self.student_3, "UNIFORM", "Uniform", "80.00")
        _, my_receipt = record_payment("P1", "80.00", "CASH", [(mine, "80.00")], actor=self.admin_user)
        _, other_receipt = record_payment("P2", "80.00", "CASH", [(other, "80.00")], actor=self.admin_user)
        client = self.client_for(self.parent_1_user)
        self.assertEqual(self.ids(client.get("/api/charges/")), {mine.id})
        self.assertEqual(self.ids(client.get("/api/receipts/")), {my_receipt.id})
        client.force_login(self.parent_1_user)
        self.assertEqual(client.get(f"/receipts/{my_receipt.id}/").status_code, 200)
        self.assertEqual(client.get(f"/receipts/{other_receipt.id}/").status_code, 403)

    def test_parent_registers_child_for_competition(self):
        competition = Competition.objects.create(
            name="State Championship", start_date=self.today + datetime.timedelta(days=20),
            end_date=self.today + datetime.timedelta(days=20),
            registration_deadline=self.today + datetime.timedelta(days=5), status="OPEN")
        event = CompetitionEvent.objects.create(competition=competition, event_type="NANQUAN", name="Nanquan Open",
                                                fee=Decimal("35.00"))
        client = self.client_for(self.parent_1_user)
        ok = client.post("/api/competition-registrations/", {"event": event.id, "student": self.student_2.id})
        self.assertEqual(ok.status_code, 201, ok.content)
        self.assertEqual(ok.json()["fee"], "35.00")
        denied = client.post("/api/competition-registrations/", {"event": event.id, "student": self.student_3.id})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.ids(client.get("/api/competition-registrations/")), {ok.json()["id"]})


class CoachApiTests(ApiTestCase):
    def test_coach_has_no_finance_access(self):
        client = self.client_for(self.coach_a_user)
        for url in ("/api/charges/", "/api/payments/", "/api/receipts/", "/api/reports/fees/"):
            self.assertEqual(client.get(url).status_code, 403, url)

    def test_coach_student_view_hides_personal_and_parent_details(self):
        response = self.client_for(self.coach_a_user).get(f"/api/students/{self.student_1.id}/")
        body = response.json()
        self.assertNotIn("ic_number", body)
        self.assertNotIn("address", body)
        self.assertEqual(body["emergency_contacts"][0], {"name": "Parent One", "relationship": "MOTHER", "phone": "0123"})
        self.assertEqual(self.client_for(self.coach_a_user).get(f"/api/students/{self.student_2.id}/").status_code, 404)

    def test_coach_takes_attendance_and_change_needs_reason(self):
        client = self.client_for(self.coach_a_user)
        url = f"/api/sessions/{self.session_a.id}/attendance/"
        first = client.post(url, {"records": [{"student": self.student_1.id, "status": "PRESENT"},
                                              {"student": self.student_3.id, "status": "ABSENT"}]}, format="json")
        self.assertEqual(first.status_code, 200, first.content)
        change = client.post(url, {"records": [{"student": self.student_3.id, "status": "EXCUSED"}]}, format="json")
        self.assertEqual(change.status_code, 400)
        change = client.post(url, {"records": [{"student": self.student_3.id, "status": "EXCUSED"}],
                                   "reason": "Medical certificate"}, format="json")
        self.assertEqual(change.status_code, 200)
        record_id = change.json()[0]["id"]
        history = client.get(f"/api/attendance/{record_id}/history/").json()
        self.assertEqual(history[0]["reason"], "Medical certificate")
        self.assertEqual(history[0]["actor"], self.coach_a_user.id)
        summary = client.get(url).json()["summary"]
        self.assertEqual(summary["percentage"], "100.00")

    def test_coach_cannot_touch_other_class(self):
        client = self.client_for(self.coach_a_user)
        self.assertEqual(client.get(f"/api/sessions/{self.session_b.id}/roster/").status_code, 404)
        response = client.post(f"/api/sessions/{self.session_b.id}/attendance/",
                               {"records": [{"student": self.student_2.id, "status": "PRESENT"}]}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_substitute_flow(self):
        client = self.client_for(self.coach_a_user)
        admin = self.client_for(self.admin_user)
        response = admin.post(f"/api/sessions/{self.session_b.id}/assign-substitute/",
                              {"substitute": self.coach_a.id, "replaces": self.coach_b.id, "reason": "Coach B sick"})
        self.assertEqual(response.status_code, 201, response.content)
        roster = client.get(f"/api/sessions/{self.session_b.id}/roster/")
        self.assertEqual({s["id"] for s in roster.json()}, {self.student_2.id})
        marked = client.post(f"/api/sessions/{self.session_b.id}/attendance/",
                             {"records": [{"student": self.student_2.id, "status": "LATE"}]}, format="json")
        self.assertEqual(marked.status_code, 200)
        # Still no access to class B itself, its other sessions, or finance.
        self.assertEqual(client.get(f"/api/classes/{self.class_b.id}/").status_code, 404)
        self.assertEqual(client.get(f"/api/classes/{self.class_b.id}/students/").status_code, 404)
        self.assertEqual(client.get("/api/charges/").status_code, 403)
        self.assertEqual(client.get("/api/reports/payroll/").status_code, 403)
        self.assertEqual(client.get("/api/me/").json()["substitute_sessions"][0]["session"], self.session_b.id)

    def test_substitute_cannot_assign_substitutes(self):
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user)
        response = self.client_for(self.coach_a_user).post(
            f"/api/sessions/{self.session_b.id}/assign-substitute/", {"substitute": self.coach_a.id})
        self.assertEqual(response.status_code, 403)

    def test_coach_sees_only_own_finalized_payslips(self):
        CoachRate.objects.create(coach=self.coach_a, rate_type="HOURLY", amount=Decimal("40"),
                                 effective_from=self.today - datetime.timedelta(days=30))
        CoachRate.objects.create(coach=self.coach_b, rate_type="HOURLY", amount=Decimal("40"),
                                 effective_from=self.today - datetime.timedelta(days=30))
        run = calculate_run(self.today.year, self.today.month, self.admin_user)
        client = self.client_for(self.coach_a_user)
        self.assertEqual(self.ids(client.get("/api/payslips/")), set())
        finalize_run(run, self.admin_user)
        mine = run.payslips.get(coach=self.coach_a)
        self.assertEqual(self.ids(client.get("/api/payslips/")), {mine.id})


class AdminApiTests(ApiTestCase):
    def test_payment_via_api_issues_receipt(self):
        charge = add_charge(self.student_1, "REGISTRATION", "Registration", "50.00")
        response = self.client_for(self.admin_user).post("/api/payments/", {
            "parent": self.parent_1.id, "payer_name": "Parent One", "amount": "50.00", "method": "DUITNOW",
            "reference": "DN123", "allocations": [{"charge": charge.id, "amount": "50.00"}],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(Receipt.objects.filter(number=response.json()["receipt_number"]).exists())
        void = self.client_for(self.admin_user).post(f"/api/payments/{response.json()['id']}/void/", {"reason": "Duplicate"})
        self.assertEqual(void.json()["status"], "VOIDED")

    def test_reports(self):
        client = self.client_for(self.admin_user)
        for name in ("students", "attendance", "fees", "payments", "receipts", "competitions", "results", "payroll"):
            response = client.get(f"/api/reports/{name}/")
            self.assertEqual(response.status_code, 200, name)
        students = client.get("/api/reports/students/").json()
        self.assertEqual(students["count"], 3)
        csv_response = client.get("/api/reports/students/?export=csv")
        self.assertEqual(csv_response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("student_no", csv_response.content.decode("utf-8-sig").splitlines()[0])

    def test_students_cannot_be_deleted_via_api(self):
        response = self.client_for(self.admin_user).delete(f"/api/students/{self.student_1.id}/")
        self.assertEqual(response.status_code, 405)


class ParentPrivacyTests(ApiTestCase):
    def test_parent_sees_co_guardian_as_contact_only(self):
        self.parent_2.ic_number = "800101-14-5555"
        self.parent_2.address = "Somewhere"
        self.parent_2.save()
        self.student_1.guardianships.create(parent=self.parent_2, relationship="FATHER")
        body = self.client_for(self.parent_1_user).get(f"/api/students/{self.student_1.id}/").json()
        self.assertNotIn("800101-14-5555", str(body))
        self.assertIn({"name": "Parent Two", "relationship": "FATHER", "phone": "0124"}, body["guardians"])
