"""Phase 6D: the API as the Student Portal uses it.

Student A is Raj (class A, Parent Two's family) and Student B is Mei (class B,
Parent One's family); both have their own StudentAccount login. Ali (class A,
Parent One) trains in Raj's class. The clock is 20:00 today: session A
(17:00-19:00) has ended and session B (19:00-21:00) is in progress. Every scope
comes from the signed-in student's own account; ids in URLs and filters are
never trusted.
"""

import datetime
from decimal import Decimal

from rest_framework.test import APIClient

from apps.academy.models import StudentAccount, TrainingSession
from apps.academy.services import cancel_session
from apps.academy.tests.base import AcademyTestCase, make_user
from apps.accounts.capabilities import Role
from apps.attendance.models import AttendanceRecord
from apps.attendance.services import mark_attendance
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration
from apps.competitions.services import record_result, register
from apps.finance.models import AcademyPaymentInfo, PaymentProof
from apps.finance.services import add_charge, record_payment
from apps.finance.tests.helpers import issued_invoice

# Keys that must never reach a student, anywhere in a response.
PRIVATE_KEYS = {"ic_number", "address", "phone", "email", "medical_notes", "emergency_contacts", "guardians",
                "family", "family_name", "date_of_birth", "form_responses", "notes", "fee", "fee_status", "invoice",
                "remarks", "recorded_by_name", "registration_form", "access_starts_at", "access_ends_at"}


def keys_in(data):
    """Every dict key anywhere in a JSON structure."""
    if isinstance(data, dict):
        return set(data) | {k for v in data.values() for k in keys_in(v)}
    if isinstance(data, list):
        return {k for v in data for k in keys_in(v)}
    return set()


class StudentPortalTestCase(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.raj_user = make_user("raj", Role.STUDENT)
        StudentAccount.objects.create(user=cls.raj_user, student=cls.student_3)
        cls.mei_user = make_user("mei", Role.STUDENT)
        StudentAccount.objects.create(user=cls.mei_user, student=cls.student_2)
        for student in (cls.student_2, cls.student_3):
            student.ic_number, student.address, student.medical_notes = "140101-10-9999", "1 Jalan Rahsia", "Asthma"
            student.phone, student.email = "0199", "kid@example.test"
            student.save()

        def session(training_class, days, hour=17, **extra):
            s = TrainingSession.objects.create(training_class=training_class,
                                               date=cls.today + datetime.timedelta(days=days),
                                               start_time=datetime.time(hour), end_time=datetime.time(hour + 2),
                                               venue="Hall A", notes="Staff note: hall key at desk", **extra)
            s.coach_slots.create(coach=cls.coach_a if training_class == cls.class_a else cls.coach_b)
            return s

        cls.past_a = session(cls.class_a, -7)
        cls.unmarked_a = session(cls.class_a, -5)
        cls.cancelled_a = session(cls.class_a, -3)
        cls.future_a = session(cls.class_a, 1)
        cls.before_join_a = session(cls.class_a, -300)      # before Raj joined (200 days ago)
        cls.past_b = session(cls.class_b, -7)
        cancel_session(cls.cancelled_a, cls.admin_user, "Hall closed")
        mark_attendance(cls.session_a, cls.student_3, "PRESENT", cls.coach_a_user, remarks="Good footwork")
        cls.raj_absent = mark_attendance(cls.past_a, cls.student_3, "ABSENT", cls.admin_user, remarks="Coach note",
                                         reason="Backfill")
        mark_attendance(cls.unmarked_a, cls.student_1, "LATE", cls.admin_user, reason="Backfill")   # Ali marked, Raj not
        cls.mei_record = mark_attendance(cls.past_b, cls.student_2, "PRESENT", cls.admin_user, reason="Backfill")

        competition = Competition.objects.create(
            name="State Open", venue="Stadium", start_date=cls.today + datetime.timedelta(days=30),
            end_date=cls.today + datetime.timedelta(days=31), rules="Bring your own weapon.",
            registration_deadline=cls.today + datetime.timedelta(days=10), status="OPEN")
        cls.competition = competition
        cls.event = CompetitionEvent.objects.create(competition=competition, event_type="OTHER", name="Nanquan",
                                                    fee=Decimal("20.00"))
        cls.reg_raj = register(cls.student_3, cls.event, cls.admin_user, notes="Family note")
        CompetitionRegistration.objects.filter(pk=cls.reg_raj.pk).update(form_responses=[
            {"key": "shirt", "label": "T-shirt size", "type": "TEXT", "value": "M", "display": "M"}])
        record_payment([(cls.reg_raj.charge.active_invoice_item().invoice, "20.00")], "CASH", cls.admin_user)
        cls.reg_raj.refresh_from_db()
        cls.result_raj = record_result(cls.reg_raj, cls.admin_user, placing=2, medal="SILVER",
                                       score=Decimal("8.950"), remarks="Internal: judge 3 query")
        cls.reg_mei = register(cls.student_2, cls.event, cls.admin_user)
        cls.reg_ali = register(cls.student_1, cls.event, cls.admin_user)
        cls.draft = Competition.objects.create(
            name="Draft Cup", start_date=cls.today + datetime.timedelta(days=60),
            end_date=cls.today + datetime.timedelta(days=60),
            registration_deadline=cls.today + datetime.timedelta(days=40), status="DRAFT")

        cls.charge_mei = add_charge(cls.student_2, "UNIFORM", "Uniform", "80.00")
        cls.inv_mei = issued_invoice([cls.charge_mei])
        cls.pay_mei, cls.rec_mei = record_payment([(cls.inv_mei, "80.00")], "CASH", cls.admin_user)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    @staticmethod
    def rows(response):
        body = response.json()
        return body.get("results", body) if isinstance(body, dict) else body


class ProfileTests(StudentPortalTestCase):
    def test_own_profile_is_the_basic_training_profile_only(self):
        body = self.api(self.raj_user).get("/api/students/me/").json()
        self.assertEqual(set(body), {"id", "student_no", "full_name", "chinese_name", "gender", "age", "status",
                                     "join_date", "current_classes"})
        self.assertEqual((body["id"], body["student_no"], body["full_name"]), (self.student_3.pk, "S3", "Raj"))
        self.assertEqual(body["current_classes"][0]["class_name"], "Class A")
        self.assertFalse(keys_in(body) & PRIVATE_KEYS)
        self.assertNotIn("140101", str(body))

    def test_generic_student_endpoints_return_the_same_safe_profile(self):
        client = self.api(self.raj_user)
        detail = client.get(f"/api/students/{self.student_3.pk}/").json()
        self.assertEqual(detail, client.get("/api/students/me/").json())
        self.assertEqual([r["id"] for r in self.rows(client.get("/api/students/"))], [self.student_3.pk])
        self.assertFalse(keys_in(self.rows(client.get("/api/students/"))) & PRIVATE_KEYS)

    def test_profile_is_read_only(self):
        client = self.api(self.raj_user)
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                self.assertEqual(getattr(client, method)("/api/students/me/", {"full_name": "X"}).status_code, 405)
        self.assertEqual(client.patch(f"/api/students/{self.student_3.pk}/", {"full_name": "X"}).status_code, 403)
        self.assertEqual(client.post(f"/api/students/{self.student_3.pk}/guardians/", {}).status_code, 403)
        self.assertEqual(client.get(f"/api/students/{self.student_3.pk}/history/").status_code, 403)

    def test_parent_and_staff_views_are_unchanged(self):
        own = self.api(self.parent_2_user).get(f"/api/students/{self.student_3.pk}/").json()
        self.assertEqual((own["ic_number"], own["medical_notes"]), ("140101-10-9999", "Asthma"))
        self.assertIn("guardians", own)
        full = self.api(self.admin_user).get(f"/api/students/{self.student_3.pk}/").json()
        self.assertEqual(full["address"], "1 Jalan Rahsia")

    def test_accounts_without_a_linked_student_or_role_get_nothing(self):
        unlinked = make_user("unlinked", Role.STUDENT)
        self.assertEqual(self.api(unlinked).get("/api/students/me/").status_code, 404)
        linked_no_role = make_user("linked")
        StudentAccount.objects.create(user=linked_no_role, student=self.student_1)
        for path in ("/api/students/me/", "/api/students/me/sessions/", "/api/students/me/attendance/",
                     "/api/students/me/competitions/"):
            self.assertEqual(self.api(linked_no_role).get(path).status_code, 403, path)
        self.assertEqual(self.api(self.admin_user).get("/api/students/me/").status_code, 403)  # not a student
        self.assertEqual(self.api(self.super_user).get("/api/students/me/").status_code, 404)  # no linked record
        self.assertEqual(APIClient().get("/api/students/me/").status_code, 401)


class ScheduleTests(StudentPortalTestCase):
    def ids(self, user, **params):
        return [r["id"] for r in self.rows(self.api(user).get("/api/students/me/sessions/", params))]

    def test_only_sessions_the_student_was_expected_at(self):
        self.assertEqual(set(self.ids(self.raj_user)), {self.session_a.pk, self.past_a.pk, self.unmarked_a.pk,
                                                        self.cancelled_a.pk, self.future_a.pk})
        self.assertEqual(set(self.ids(self.mei_user)), {self.session_b.pk, self.past_b.pk})
        # The generic sessions endpoint uses the same scope (never before they joined).
        generic = {r["id"] for r in self.rows(self.api(self.raj_user).get("/api/sessions/"))}
        self.assertEqual(generic, set(self.ids(self.raj_user)))

    def test_filters(self):
        self.assertEqual(self.ids(self.raj_user, view="today"), [self.session_a.pk])
        self.assertEqual(self.ids(self.raj_user, view="upcoming"), [self.future_a.pk])
        self.assertEqual(self.ids(self.raj_user, view="past"), [self.session_a.pk, self.unmarked_a.pk, self.past_a.pk])
        self.assertEqual(self.ids(self.raj_user, view="cancelled"), [self.cancelled_a.pk])
        self.assertEqual(self.ids(self.mei_user, view="upcoming"), [self.session_b.pk])   # in progress
        self.assertEqual(self.api(self.raj_user).get("/api/students/me/sessions/", {"view": "all"}).status_code, 400)

    def test_session_detail_is_student_safe_with_own_attendance(self):
        body = self.api(self.raj_user).get(f"/api/students/me/sessions/{self.session_a.pk}/").json()
        self.assertEqual(set(body), {"id", "class_name", "date", "start_time", "end_time", "venue", "status", "phase",
                                     "coaches", "my_attendance"})
        self.assertEqual((body["class_name"], body["coaches"], body["my_attendance"], body["phase"]),
                         ("Class A", ["Coach A"], "PRESENT", "COMPLETED"))
        client = self.api(self.raj_user)
        self.assertEqual(client.get(f"/api/students/me/sessions/{self.unmarked_a.pk}/").json()["my_attendance"],
                         "UNMARKED")
        self.assertIsNone(client.get(f"/api/students/me/sessions/{self.future_a.pk}/").json()["my_attendance"])
        cancelled = client.get(f"/api/students/me/sessions/{self.cancelled_a.pk}/").json()
        self.assertEqual((cancelled["status"], cancelled["my_attendance"]), ("CANCELLED", None))

    def test_generic_session_endpoint_hides_staff_notes_and_substitute_details(self):
        body = self.api(self.raj_user).get(f"/api/sessions/{self.session_a.pk}/").json()
        self.assertNotIn("notes", body)
        self.assertFalse(keys_in(body) & PRIVATE_KEYS)
        self.assertNotIn("Staff note", str(self.api(self.raj_user).get("/api/sessions/").json()))
        # Parents and staff still see the full session.
        self.assertIn("notes", self.api(self.parent_2_user).get(f"/api/sessions/{self.past_a.pk}/").json())
        self.assertIn("notes", self.api(self.admin_user).get(f"/api/sessions/{self.past_a.pk}/").json())


class AttendanceTests(StudentPortalTestCase):
    def test_own_attendance_with_backend_summary(self):
        body = self.api(self.raj_user).get("/api/students/me/attendance/").json()
        rows = {r["session"]: r["status"] for r in body["sessions"]}
        # Held, non-cancelled sessions only: no future or cancelled rows, nothing before joining.
        self.assertEqual(rows, {self.session_a.pk: "PRESENT", self.unmarked_a.pk: "UNMARKED", self.past_a.pk: "ABSENT"})
        self.assertEqual([r["session"] for r in body["sessions"]],
                         [self.session_a.pk, self.unmarked_a.pk, self.past_a.pk])
        summary = body["summary"]
        # UNMARKED is reported separately and left out of the percentage: 1 of 2 marked sessions attended.
        self.assertEqual((summary["expected"], summary["marked"], summary["unmarked"], summary["percentage"]),
                         (3, 2, 1, "50.00"))
        self.assertEqual(set(body["sessions"][0]), {"session", "date", "start_time", "end_time", "class_name",
                                                    "status"})
        # Same numbers as the existing summary endpoint.
        existing = self.api(self.raj_user).get(f"/api/students/{self.student_3.pk}/attendance-summary/").json()
        self.assertEqual(existing["percentage"], "50.00")

    def test_generic_attendance_records_hide_remarks_and_recorder(self):
        rows = self.rows(self.api(self.raj_user).get("/api/attendance/"))
        self.assertEqual({r["student"] for r in rows}, {self.student_3.pk})
        for row in rows:
            self.assertNotIn("remarks", row)
            self.assertNotIn("recorded_by_name", row)
        self.assertNotIn("Good footwork", str(rows))
        # Parents still see remarks.
        parent = self.rows(self.api(self.parent_2_user).get("/api/attendance/"))
        self.assertIn("Good footwork", str(parent))

    def test_students_cannot_mark_change_or_correct_attendance(self):
        client = self.api(self.raj_user)
        entry = {"records": [{"student": self.student_3.pk, "status": "PRESENT"}], "reason": "x"}
        for session in (self.session_a, self.past_a, self.session_b):
            self.assertEqual(client.post(f"/api/sessions/{session.pk}/attendance/", entry, format="json").status_code,
                             403)
            self.assertEqual(client.get(f"/api/sessions/{session.pk}/attendance/").status_code, 403)
        for method in ("put", "patch", "delete"):
            response = getattr(client, method)(f"/api/attendance/{self.raj_absent.pk}/", {"status": "PRESENT"},
                                               format="json")
            self.assertIn(response.status_code, (403, 405), method)
        self.assertEqual(client.post("/api/attendance/", {"status": "PRESENT"}).status_code, 405)
        self.assertEqual(client.get(f"/api/attendance/{self.raj_absent.pk}/history/").status_code, 403)
        self.assertEqual(client.post("/api/students/me/attendance/", {}).status_code, 405)
        self.assertEqual(AttendanceRecord.objects.get(pk=self.raj_absent.pk).status, "ABSENT")


class CompetitionTests(StudentPortalTestCase):
    def test_own_entries_with_result_and_no_family_or_payment_details(self):
        entries = self.api(self.raj_user).get("/api/students/me/competitions/").json()
        self.assertEqual([e["id"] for e in entries], [self.reg_raj.pk])
        entry = entries[0]
        self.assertEqual(set(entry), {"id", "status", "registered_at", "competition", "event", "result"})
        self.assertEqual((entry["status"], entry["competition"]["name"], entry["competition"]["venue"],
                          entry["competition"]["rules"], entry["event"]["name"]),
                         ("CONFIRMED", "State Open", "Stadium", "Bring your own weapon.", "Nanquan"))
        self.assertEqual(entry["result"], {"placing": 2, "medal": "SILVER", "score": "8.950"})
        self.assertFalse(keys_in(entries) & PRIVATE_KEYS)
        for secret in ("Family note", "T-shirt", "Internal", "20.00"):
            self.assertNotIn(secret, str(entries))
        mei = self.api(self.mei_user).get("/api/students/me/competitions/").json()
        self.assertEqual([(e["id"], e["status"], e["result"]) for e in mei], [(self.reg_mei.pk, "PENDING", None)])

    def test_generic_competition_endpoints_are_student_safe(self):
        client = self.api(self.raj_user)
        regs = self.rows(client.get("/api/competition-registrations/"))
        self.assertEqual([r["id"] for r in regs], [self.reg_raj.pk])
        self.assertFalse(keys_in(regs) & PRIVATE_KEYS)
        detail = client.get(f"/api/competition-registrations/{self.reg_raj.pk}/").json()
        self.assertFalse(keys_in(detail) & PRIVATE_KEYS)
        results = self.rows(client.get("/api/competition-results/"))
        self.assertEqual([r["id"] for r in results], [self.result_raj.pk])
        self.assertNotIn("remarks", results[0])
        competition = client.get(f"/api/competitions/{self.competition.pk}/").json()
        self.assertNotIn("registration_form", competition)
        self.assertNotIn("fee", competition["events"][0])
        events = self.rows(client.get("/api/competition-events/", {"competition": self.competition.pk}))
        self.assertTrue(events)
        self.assertFalse(any("fee" in e for e in events))
        self.assertIn("fee", self.rows(self.api(self.parent_2_user).get("/api/competition-events/"))[0])
        self.assertEqual(client.get(f"/api/competitions/{self.draft.pk}/").status_code, 404)
        # The family and staff still see answers, fee and remarks.
        family = self.api(self.parent_2_user).get(f"/api/competition-registrations/{self.reg_raj.pk}/").json()
        self.assertEqual((family["notes"], family["fee"]), ("Family note", "20.00"))
        self.assertEqual(family["form_responses"][0]["value"], "M")
        self.assertEqual(family["result"]["remarks"], "Internal: judge 3 query")
        self.assertIn("registration_form", self.api(self.parent_2_user).get(
            f"/api/competitions/{self.competition.pk}/").json())

    def test_students_cannot_register_withdraw_or_edit(self):
        client = self.api(self.raj_user)
        other_event = CompetitionEvent.objects.create(competition=self.competition, event_type="OTHER", name="Taiji")
        attempts = [
            ("post", "/api/competition-registrations/", {"event": other_event.pk, "student": self.student_3.pk,
                                                         "responses": {}}),
            ("post", f"/api/competition-registrations/{self.reg_raj.pk}/withdraw/", {"reason": "x"}),
            ("post", f"/api/competition-registrations/{self.reg_raj.pk}/confirm/", {}),
            ("patch", f"/api/competition-results/{self.result_raj.pk}/", {"placing": 1}),
            ("post", "/api/competition-results/", {"registration": self.reg_raj.pk, "placing": 1}),
            ("post", "/api/competition-form-fields/", {"competition": self.competition.pk}),
            ("post", f"/api/competitions/{self.competition.pk}/publish-form/", {}),
        ]
        for method, path, data in attempts:
            with self.subTest(path=path):
                self.assertEqual(getattr(client, method)(path, data, format="json").status_code, 403)
        for method in ("patch", "put", "delete"):
            response = getattr(client, method)(f"/api/competition-registrations/{self.reg_raj.pk}/",
                                               {"event": other_event.pk}, format="json")
            self.assertIn(response.status_code, (403, 405))
        self.assertEqual(client.post("/api/students/me/competitions/", {}).status_code, 405)
        self.assertFalse(CompetitionRegistration.objects.filter(event=other_event).exists())
        self.assertEqual(CompetitionRegistration.objects.get(pk=self.reg_raj.pk).status, "CONFIRMED")


class OtherStudentIdorTests(StudentPortalTestCase):
    """Raj (Student A) with his own token against Mei (Student B, another family
    and class) and Ali (another family, same class)."""

    def test_direct_ids_are_not_found(self):
        client = self.api(self.raj_user)
        for path in (f"/api/students/{self.student_2.pk}/", f"/api/students/{self.student_1.pk}/",
                     f"/api/students/{self.student_2.pk}/attendance-summary/",
                     f"/api/students/me/sessions/{self.session_b.pk}/", f"/api/students/me/sessions/{self.past_b.pk}/",
                     f"/api/students/me/sessions/{self.before_join_a.pk}/", "/api/students/me/sessions/999999/",
                     f"/api/sessions/{self.session_b.pk}/", f"/api/sessions/{self.before_join_a.pk}/",
                     f"/api/attendance/{self.mei_record.pk}/",
                     f"/api/competition-registrations/{self.reg_mei.pk}/",
                     f"/api/competition-registrations/{self.reg_ali.pk}/"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 404)

    def test_malformed_ids_never_error(self):
        client = self.api(self.raj_user)
        for path in ("/api/students/me/sessions/abc/", "/api/students/me/sessions/-1/", "/api/students/abc/",
                     "/api/students/me/sessions/1.5/"):
            self.assertEqual(client.get(path).status_code, 404, path)
        self.assertEqual(client.get("/api/attendance/", {"student": "x"}).status_code, 400)
        self.assertEqual(client.get("/api/sessions/", {"class": "x"}).status_code, 400)
        self.assertEqual(client.get("/api/competition-registrations/", {"competition": "x"}).status_code, 400)

    def test_query_parameters_cannot_widen_the_scope(self):
        client = self.api(self.raj_user)
        for params in ({"student": self.student_2.pk}, {"student_id": self.student_2.pk},
                       {"class": self.class_b.pk}, {"family": self.student_2.family_id}):
            with self.subTest(params=params):
                self.assertEqual(self.rows(client.get("/api/students/me/competitions/", params)),
                                 client.get("/api/students/me/competitions/").json())
                me = client.get("/api/students/me/", params).json()
                self.assertEqual(me["id"], self.student_3.pk)
                mine = {r["id"] for r in self.rows(client.get("/api/students/me/sessions/", params))}
                self.assertNotIn(self.session_b.pk, mine)
                self.assertNotIn(self.past_b.pk, mine)
                attendance = client.get("/api/students/me/attendance/", params).json()
                self.assertNotIn(self.past_b.pk, {r["session"] for r in attendance["sessions"]})
        self.assertEqual(self.rows(client.get("/api/attendance/", {"student": self.student_2.pk})), [])
        self.assertEqual(self.rows(client.get("/api/attendance/", {"student": self.student_1.pk})), [])
        self.assertEqual({r["id"] for r in self.rows(client.get("/api/sessions/", {"class": self.class_b.pk}))},
                         set())
        self.assertEqual(self.rows(client.get("/api/competition-registrations/",
                                              {"competition": self.competition.pk})),
                         self.rows(client.get("/api/competition-registrations/")))
        self.assertEqual({r["id"] for r in self.rows(client.get("/api/competition-results/",
                                                                {"competition": self.competition.pk}))},
                         {self.result_raj.pk})

    def test_student_b_sees_only_their_own(self):
        client = self.api(self.mei_user)
        self.assertEqual(client.get("/api/students/me/").json()["id"], self.student_2.pk)
        self.assertEqual(client.get(f"/api/students/{self.student_3.pk}/").status_code, 404)
        self.assertEqual(client.get(f"/api/students/me/sessions/{self.session_a.pk}/").status_code, 404)
        self.assertEqual(client.get(f"/api/attendance/{self.raj_absent.pk}/").status_code, 404)
        self.assertEqual(client.get(f"/api/competition-registrations/{self.reg_raj.pk}/").status_code, 404)
        self.assertEqual(client.get(f"/api/competition-results/{self.result_raj.pk}/").status_code, 404)
        rows = client.get("/api/students/me/attendance/").json()["sessions"]
        self.assertEqual({r["session"] for r in rows}, {self.past_b.pk, self.session_b.pk})


class RoleIsolationTests(StudentPortalTestCase):
    def test_no_finance_access_read_or_write(self):
        client = self.api(self.mei_user)
        proof = PaymentProof.objects.create(invoice=self.inv_mei, family=self.inv_mei.family, amount_claimed="80.00",
                                            payment_date=self.today, uploaded_by=self.parent_1_user,
                                            file="proofs/x.png", original_name="x.png", sha256="0" * 64,
                                            content_type="image/png", size=10)
        AcademyPaymentInfo.objects.create(bank_name="Test Bank", account_name="Academy", account_number="123")
        for path in ("/api/charges/", f"/api/charges/{self.charge_mei.pk}/", "/api/invoices/",
                     f"/api/invoices/{self.inv_mei.pk}/", "/api/payments/", f"/api/payments/{self.pay_mei.pk}/",
                     "/api/receipts/", f"/api/receipts/{self.rec_mei.pk}/", "/api/refunds/", "/api/payment-proofs/",
                     f"/api/payment-proofs/{proof.pk}/", "/api/payment-info/", "/api/families/",
                     f"/api/families/{self.student_2.family_id}/", "/api/reports/", "/api/reports/fees/",
                     "/api/payroll-runs/", "/api/payslips/"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 403)
        for path, data in (("/api/charges/", {"student": self.student_2.pk}), ("/api/payments/", {}),
                           ("/api/payment-proofs/", {"invoice": self.inv_mei.pk}),
                           (f"/api/invoices/{self.inv_mei.pk}/issue/", {}), ("/api/refunds/", {}),
                           (f"/api/payments/{self.pay_mei.pk}/void/", {"reason": "x"}),
                           (f"/api/payments/{self.pay_mei.pk}/refund/", {"amount": "1.00", "reason": "x"}),
                           (f"/api/invoices/{self.inv_mei.pk}/void/", {"reason": "x"}),
                           (f"/api/payment-proofs/{proof.pk}/accept/", {}),
                           ("/api/payment-info/", {"bank_name": "X"})):
            with self.subTest(path=path):
                self.assertIn(client.post(path, data).status_code, (403, 405))

    def test_no_parent_family_coach_or_admin_access(self):
        client = self.api(self.raj_user)
        for path in ("/api/parents/", f"/api/parents/{self.parent_2.pk}/", "/api/users/", "/api/coaches/",
                     "/api/enrollments/", f"/api/families/{self.student_3.family_id}/",
                     "/api/sessions/coaching/", f"/api/sessions/{self.session_a.pk}/roster/",
                     f"/api/sessions/{self.session_a.pk}/attendance/", f"/api/classes/{self.class_a.pk}/students/",
                     "/api/competition-form-fields/"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 403)
        for path, data in ((f"/api/sessions/{self.future_a.pk}/assign-substitute/", {"substitute": self.coach_b.pk}),
                           (f"/api/sessions/{self.future_a.pk}/cancel/", {"reason": "x"}),
                           ("/api/students/", {"full_name": "X"})):
            with self.subTest(path=path):
                self.assertEqual(client.post(path, data).status_code, 403)
        me = client.get("/api/me/").json()
        self.assertEqual(set(me), {"id", "username", "name", "role", "roles", "capabilities", "student"})
        self.assertEqual(me["student"], {"id": self.student_3.pk, "student_no": "S3", "full_name": "Raj"})
