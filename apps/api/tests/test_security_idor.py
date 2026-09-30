"""Phase 5 attack tests: IDOR / horizontal escalation, vertical escalation,
mass assignment and immutable-record tampering through direct HTTP requests.

Semantics: 404 where record scoping hides a record the caller may not see
(existence is not revealed); 403 where the caller lacks the capability for the
action altogether; 401 without credentials."""

import datetime
from decimal import Decimal

from rest_framework.test import APIClient

from apps.academy.models import StudentAccount
from apps.academy.services import assign_substitute
from apps.academy.tests.base import AcademyTestCase, make_user
from apps.accounts.capabilities import Role
from apps.attendance.models import AttendanceRecord
from apps.attendance.services import mark_attendance
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration
from apps.competitions.services import register
from apps.finance.models import Invoice, Payment, Receipt
from apps.finance.services import add_charge, record_exceptional_refund, record_payment
from apps.finance.tests.helpers import issued_invoice


class SecurityTestCase(AcademyTestCase):
    """Family 1: Parent One's child Ali (class A). Family 3: Parent Two's child Raj
    (class A). Mei (Parent One, class B) is a third family. Raj has a student login."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.raj_user = make_user("raj", Role.STUDENT)
        StudentAccount.objects.create(user=cls.raj_user, student=cls.student_3)
        cls.ali_user = make_user("ali", Role.STUDENT)
        StudentAccount.objects.create(user=cls.ali_user, student=cls.student_1)
        cls.charge_1 = add_charge(cls.student_1, "UNIFORM", "Uniform", "80.00")
        cls.charge_3 = add_charge(cls.student_3, "UNIFORM", "Uniform", "80.00")
        cls.inv_1 = issued_invoice([cls.charge_1])
        cls.inv_3 = issued_invoice([cls.charge_3])
        cls.pay_1, cls.rec_1 = record_payment([(cls.inv_1, "80.00")], "CASH", cls.admin_user)
        cls.pay_3, cls.rec_3 = record_payment([(cls.inv_3, "80.00")], "CASH", cls.admin_user)
        cls.refund_3 = record_exceptional_refund(cls.pay_3.allocations.get(), "10.00", "Overcharged", cls.admin_user)
        competition = Competition.objects.create(
            name="Open", start_date=cls.today + datetime.timedelta(days=30),
            end_date=cls.today + datetime.timedelta(days=30),
            registration_deadline=cls.today + datetime.timedelta(days=10), status="OPEN")
        cls.event = CompetitionEvent.objects.create(competition=competition, event_type="OTHER", name="E",
                                                    fee=Decimal("20.00"))
        cls.reg_1 = register(cls.student_1, cls.event, cls.admin_user)
        cls.reg_3 = register(cls.student_3, cls.event, cls.admin_user)
        cls.att_1 = mark_attendance(cls.session_a, cls.student_1, "PRESENT", cls.admin_user)
        cls.att_3 = mark_attendance(cls.session_a, cls.student_3, "ABSENT", cls.admin_user)
        cls.att_2 = mark_attendance(cls.session_b, cls.student_2, "PRESENT", cls.admin_user)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def ids(self, response):
        body = response.json()
        return {row["id"] for row in body.get("results", body)}

    def assert_statuses(self, client, cases):
        for method, url, data, expected in cases:
            with self.subTest(method=method, url=url):
                call = getattr(client, method)
                response = call(url, data, format="json") if data is not None else call(url)
                self.assertEqual(response.status_code, expected, f"{method.upper()} {url}: {response.content[:200]!r}")


class HorizontalEscalationTests(SecurityTestCase):
    def test_parent_cannot_read_another_familys_records(self):
        other = [
            f"/api/students/{self.student_3.pk}/", f"/api/families/{self.student_3.family_id}/",
            f"/api/invoices/{self.inv_3.pk}/", f"/api/payments/{self.pay_3.pk}/", f"/api/receipts/{self.rec_3.pk}/",
            f"/api/refunds/{self.refund_3.pk}/", f"/api/charges/{self.charge_3.pk}/",
            f"/api/competition-registrations/{self.reg_3.pk}/", f"/api/attendance/{self.att_3.pk}/",
            f"/api/students/{self.student_3.pk}/attendance-summary/", f"/invoices/{self.inv_3.pk}/",
            f"/receipts/{self.rec_3.pk}/",
        ]
        client = self.api(self.parent_1_user)
        self.client.force_login(self.parent_1_user)
        for url in other:
            with self.subTest(url=url):
                getter = self.client.get if url.startswith(("/invoices/", "/receipts/")) else client.get
                self.assertEqual(getter(url).status_code, 404)
        # Swapping ids in filters returns nothing of theirs.
        self.assertEqual(self.ids(client.get(f"/api/charges/?student={self.student_3.pk}")), set())
        self.assertEqual(self.ids(client.get(f"/api/invoices/?family={self.student_3.family_id}")), set())
        self.assertEqual(self.ids(client.get(f"/api/attendance/?student={self.student_3.pk}")), set())
        # Their own records are reachable.
        self.assertEqual(client.get(f"/api/invoices/{self.inv_1.pk}/").status_code, 200)
        self.assertEqual(client.get(f"/api/receipts/{self.rec_1.pk}/").status_code, 200)

    def test_parent_cannot_act_on_another_familys_records(self):
        self.assert_statuses(self.api(self.parent_1_user), [
            ("post", "/api/competition-registrations/", {"event": self.event.pk, "student": self.student_3.pk}, 403),
            ("post", f"/api/competition-registrations/{self.reg_3.pk}/withdraw/", {"reason": "x"}, 404),
            ("post", f"/api/competition-registrations/{self.reg_3.pk}/confirm/", {}, 403),
            ("post", f"/api/payments/{self.pay_3.pk}/refund/", {"allocation": 1, "amount": "1.00", "reason": "x"}, 403),
            ("post", f"/api/invoices/{self.inv_3.pk}/void/", {"reason": "x"}, 403),
        ])
        self.assertEqual(CompetitionRegistration.objects.get(pk=self.reg_3.pk).status, "PENDING")

    def test_coach_cannot_reach_another_coachs_class(self):
        self.assert_statuses(self.api(self.coach_b_user), [
            ("get", f"/api/sessions/{self.session_a.pk}/roster/", None, 404),
            ("get", f"/api/sessions/{self.session_a.pk}/attendance/", None, 404),
            ("post", f"/api/sessions/{self.session_a.pk}/attendance/",
             {"records": [{"student": self.student_1.pk, "status": "ABSENT"}], "reason": "x"}, 404),
            ("get", f"/api/attendance/{self.att_1.pk}/", None, 404),
            ("get", f"/api/attendance/{self.att_1.pk}/history/", None, 404),
            ("get", f"/api/students/{self.student_1.pk}/", None, 404),
            ("get", f"/api/students/{self.student_1.pk}/attendance-summary/", None, 404),
            ("get", f"/api/classes/{self.class_a.pk}/students/", None, 404),
            ("get", f"/api/charges/{self.charge_1.pk}/", None, 403),     # no finance at all
        ])
        self.assertEqual(AttendanceRecord.objects.get(pk=self.att_1.pk).status, "PRESENT")

    def test_student_sees_only_themselves(self):
        client = self.api(self.raj_user)
        self.assert_statuses(client, [
            ("get", f"/api/students/{self.student_1.pk}/", None, 404),
            ("get", f"/api/students/{self.student_3.pk}/", None, 200),
            ("get", f"/api/attendance/{self.att_1.pk}/", None, 404),
            ("get", f"/api/competition-registrations/{self.reg_1.pk}/", None, 404),
            ("get", f"/api/sessions/{self.session_b.pk}/", None, 404),
            ("get", f"/api/sessions/{self.session_a.pk}/roster/", None, 403),
            ("get", "/api/charges/", None, 403),
            ("get", "/api/invoices/", None, 403),
            ("get", "/api/payslips/", None, 403),
        ])
        self.assertEqual(self.ids(client.get("/api/attendance/")), {self.att_3.pk})
        self.assertEqual(self.ids(client.get("/api/students/")), {self.student_3.pk})
        body = client.get(f"/api/students/{self.student_3.pk}/").json()
        self.assertNotIn("guardians", [k for k, v in body.items() if isinstance(v, list) and v and "ic_number" in v[0]])

    def test_substitute_cannot_reach_other_sessions(self):
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user, reason="x")
        other = self.class_b.sessions.create(date=self.today + datetime.timedelta(days=7),
                                             start_time=datetime.time(19), end_time=datetime.time(21))
        self.assert_statuses(self.api(self.coach_a_user), [
            ("get", f"/api/sessions/{self.session_b.pk}/roster/", None, 200),
            ("get", f"/api/sessions/{other.pk}/roster/", None, 404),
            ("post", f"/api/sessions/{other.pk}/attendance/",
             {"records": [{"student": self.student_2.pk, "status": "ABSENT"}]}, 404),
            ("get", f"/api/classes/{self.class_b.pk}/students/", None, 404),
            ("get", f"/api/families/{self.student_2.family_id}/", None, 403),
        ])


class VerticalEscalationTests(SecurityTestCase):
    STAFF_ONLY = None

    def cases(self):
        return [
            ("post", "/api/students/", {"student_no": "X9", "full_name": "X", "gender": "M",
                                        "date_of_birth": "2015-01-01"}),
            ("post", "/api/charges/", {"student": self.student_1.pk, "fee_type": "OTHER", "description": "x",
                                       "unit_amount": "1.00"}),
            ("post", "/api/payments/", {"amount": "1.00", "method": "CASH",
                                        "allocations": [{"invoice": self.inv_1.pk, "amount": "1.00"}]}),
            ("post", f"/api/payments/{self.pay_1.pk}/void/", {"reason": "x"}),
            ("post", f"/api/users/{self.parent_1_user.pk}/roles/", {"roles": ["SUPER_ADMIN"], "reason": "x"}),
            ("post", "/api/payroll-runs/calculate/", {"year": self.today.year, "month": self.today.month}),
            ("post", f"/api/sessions/{self.session_a.pk}/cancel/", {"reason": "x"}),
            ("post", "/api/competition-results/", {"registration": self.reg_1.pk, "placing": 1}),
            ("get", "/api/reports/fees/", None),
            ("get", "/api/users/", None),
        ]

    def test_parent_student_and_coach_get_403_for_staff_actions(self):
        for user in (self.parent_1_user, self.raj_user, self.coach_a_user):
            client = self.api(user)
            for method, url, data in self.cases():
                with self.subTest(user=user.username, url=url):
                    call = getattr(client, method)
                    response = call(url, data, format="json") if data is not None else call(url)
                    self.assertEqual(response.status_code, 403)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertFalse(self.parent_1_user.groups.filter(name="SUPER_ADMIN").exists())

    def test_staff_roles_stay_in_their_lane(self):
        self.assert_statuses(self.api(self.finance_user), [
            ("post", f"/api/users/{self.parent_1_user.pk}/roles/", {"roles": ["ADMIN"], "reason": "x"}, 403),
            ("post", f"/api/sessions/{self.session_a.pk}/cancel/", {"reason": "x"}, 403),
            ("post", f"/api/sessions/{self.session_a.pk}/attendance/",
             {"records": [{"student": self.student_1.pk, "status": "ABSENT"}], "reason": "x"}, 403),
            ("get", f"/api/students/{self.student_1.pk}/attendance-summary/", None, 403),   # no attendance capability
            ("post", "/api/competition-results/", {"registration": self.reg_1.pk, "placing": 1}, 403),
        ])
        self.assert_statuses(self.api(self.admin_user), [
            ("post", f"/api/payments/{self.pay_1.pk}/void/", {"reason": "x"}, 403),
            ("post", f"/api/users/{self.parent_1_user.pk}/roles/", {"roles": ["ADMIN"], "reason": "x"}, 403),
            ("post", "/api/payroll-runs/calculate/", {"year": self.today.year, "month": self.today.month}, 403),
            ("get", "/api/payslips/", None, 403),
            ("get", "/api/reports/payroll/", None, 403),
        ])
        body = self.api(self.admin_user).get(f"/api/coaches/{self.coach_a.pk}/").json()
        self.assertNotIn("bank_account_no", body)

    def test_mixed_roles_do_not_add_up_to_more(self):
        from apps.accounts.services import set_roles

        set_roles(self.coach_b_user, [Role.COACH, Role.PARENT], self.super_user, "Coach B is also a parent")
        self.assert_statuses(self.api(self.coach_b_user), [
            ("get", f"/api/invoices/{self.inv_1.pk}/", None, 404),   # parent role: no child in family 1
            ("get", f"/api/sessions/{self.session_a.pk}/roster/", None, 404),
            ("post", "/api/charges/", {"student": self.student_2.pk, "fee_type": "OTHER", "description": "x",
                                       "unit_amount": "1.00"}, 403),
        ])


class ImmutableRecordTamperingTests(SecurityTestCase):
    def test_direct_writes_to_protected_records_are_refused_even_for_super_admin(self):
        client = self.api(self.super_user)
        targets = [f"/api/invoices/{self.inv_1.pk}/", f"/api/payments/{self.pay_1.pk}/",
                   f"/api/receipts/{self.rec_1.pk}/", f"/api/refunds/{self.refund_3.pk}/",
                   f"/api/attendance/{self.att_1.pk}/", f"/api/competition-registrations/{self.reg_1.pk}/"]
        for url in targets:
            for method in ("put", "patch", "delete"):
                with self.subTest(url=url, method=method):
                    response = getattr(client, method)(url, {"amount": "1.00", "status": "PAID", "total": "0.00"},
                                                       format="json")
                    self.assertIn(response.status_code, (403, 405))
        self.assertIn(client.post("/api/receipts/", {"payment": self.pay_1.pk}).status_code, (403, 405))
        self.assertEqual(Invoice.objects.get(pk=self.inv_1.pk).total, Decimal("80.00"))
        self.assertEqual(Payment.objects.get(pk=self.pay_1.pk).amount, Decimal("80.00"))
        self.assertEqual(Receipt.objects.get(pk=self.rec_1.pk).total, Decimal("80.00"))

    def test_mass_assignment_of_read_only_fields_is_ignored(self):
        admin = self.api(self.admin_user)
        admin.patch(f"/api/students/{self.student_1.pk}/", {"status": "WITHDRAWN", "created_at": "2000-01-01T00:00Z"},
                    format="json")
        self.student_1.refresh_from_db()
        self.assertEqual(self.student_1.status, "ACTIVE")
        response = self.api(self.parent_1_user).post(
            "/api/competition-registrations/", {"event": self.event.pk, "student": self.student_2.pk,
                                                "status": "CONFIRMED"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["status"], "PENDING")
        self.api(self.finance_user).patch(f"/api/coaches/{self.coach_a.pk}/", {"full_name": "Renamed"}, format="json")
        self.coach_a.refresh_from_db()
        self.assertEqual(self.coach_a.full_name, "Coach A")

    def test_payment_allocation_tampering(self):
        admin = self.api(self.admin_user)
        other_family = issued_invoice([add_charge(self.student_3, "OTHER", "Extra", "30.00")])
        mine = issued_invoice([add_charge(self.student_1, "OTHER", "Extra", "30.00")])
        cases = [
            {"amount": "60.00", "method": "CASH",
             "allocations": [{"invoice": mine.pk, "amount": "30.00"}, {"invoice": other_family.pk, "amount": "30.00"}]},
            {"amount": "40.00", "method": "CASH", "allocations": [{"invoice": mine.pk, "amount": "40.00"}]},
            {"amount": "-5.00", "method": "CASH", "allocations": [{"invoice": mine.pk, "amount": "-5.00"}]},
            {"amount": "30.00", "method": "CASH", "allocations": [{"invoice": self.inv_1.pk, "amount": "30.00"}]},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertEqual(admin.post("/api/payments/", payload, format="json").status_code, 400)
        self.assertEqual(Payment.objects.count(), 2)


class SensitiveFieldTests(SecurityTestCase):
    IC = "140501-10-1234"

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for obj in (cls.student_1, cls.parent_1):
            obj.ic_number = cls.IC
            obj.save()
        cls.student_1.medical_notes = "Asthma"
        cls.student_1.address = "1 Jalan Rahsia"
        cls.student_1.save()

    def test_who_sees_identity_numbers_medical_notes_and_addresses(self):
        student_url = f"/api/students/{self.student_1.pk}/"
        full = self.api(self.admin_user).get(student_url).json()
        self.assertEqual((full["ic_number"], full["medical_notes"]), (self.IC, "Asthma"))
        own = self.api(self.parent_1_user).get(student_url).json()           # own child
        self.assertEqual((own["ic_number"], own["medical_notes"]), (self.IC, "Asthma"))
        coach = self.api(self.coach_a_user).get(student_url).json()           # roster: training info only
        for field in ("ic_number", "address", "phone", "email", "guardians", "medical_notes", "emergency_contacts"):
            self.assertNotIn(field, coach)
        directory = self.api(self.finance_user).get(student_url).json()       # finance directory
        for field in ("ic_number", "medical_notes", "address", "date_of_birth"):
            self.assertNotIn(field, directory)
        self.assertEqual(self.api(self.parent_2_user).get(student_url).status_code, 404)
        self.assertEqual(self.api(self.coach_b_user).get(student_url).status_code, 404)

    def test_parent_ic_is_masked_for_viewers_who_do_not_manage_parents(self):
        url = f"/api/parents/{self.parent_1.pk}/"
        self.assertEqual(self.api(self.admin_user).get(url).json()["ic_number"], self.IC)
        self.assertEqual(self.api(self.finance_user).get(url).json()["ic_number"], "**********1234")
        me = self.api(self.parent_1_user).get("/api/me/").json()
        self.assertEqual(me["parent"]["ic_number"], self.IC)
        self.assertEqual(self.api(self.parent_1_user).get(url).status_code, 403)


class DomainAttackTests(SecurityTestCase):
    """Direct requests that try to bend attendance, payroll, competition and finance rules."""

    def test_attendance_attacks(self):
        from unittest import mock

        from apps.academy.services import revoke_substitute
        from apps.attendance.services import coach_edit_deadline

        coach = self.api(self.coach_a_user)
        for method in ("patch", "put", "delete"):
            with self.subTest(method=method):
                self.assertIn(getattr(coach, method)(f"/api/attendance/{self.att_1.pk}/", {"status": "ABSENT"},
                                                     format="json").status_code, (403, 405))
        future = self.class_a.sessions.create(date=self.today + datetime.timedelta(days=2),
                                              start_time=datetime.time(17), end_time=datetime.time(19))
        future.coach_slots.create(coach=self.coach_a)
        response = coach.post(f"/api/sessions/{future.pk}/attendance/",
                              {"records": [{"student": self.student_1.pk, "status": "PRESENT"}]}, format="json")
        self.assertEqual(response.status_code, 400)                                    # before the session starts
        after = coach_edit_deadline(self.session_a) + datetime.timedelta(seconds=1)
        with mock.patch("django.utils.timezone.now", return_value=after):
            response = coach.post(f"/api/sessions/{self.session_a.pk}/attendance/",
                                  {"records": [{"student": self.student_1.pk, "status": "ABSENT"}], "reason": "x"},
                                  format="json")
        self.assertEqual(response.status_code, 403)                                    # 48-hour window closed
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user,
                                 reason="x")
        revoke_substitute(slot, self.admin_user, "Coach B is back")
        response = coach.post(f"/api/sessions/{self.session_b.pk}/attendance/",
                              {"records": [{"student": self.student_2.pk, "status": "ABSENT"}], "reason": "x"},
                              format="json")
        self.assertEqual(response.status_code, 404)                                    # revoked substitute
        self.assertEqual(AttendanceRecord.objects.get(pk=self.att_1.pk).status, "PRESENT")
        self.assertEqual(AttendanceRecord.objects.get(pk=self.att_2.pk).status, "PRESENT")

    def test_payroll_attacks(self):
        from unittest import mock

        from apps.academy.tests.base import academy_time
        from apps.payroll.models import CoachRate, PayrollRun, Payslip
        from apps.payroll.services import calculate_run

        for coach in (self.coach_a, self.coach_b):
            CoachRate.objects.create(coach=coach, rate_type="PER_SESSION", amount=Decimal("80"),
                                     effective_from=self.today - datetime.timedelta(days=30))
        first_next = (self.today.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        with mock.patch("django.utils.timezone.now", return_value=academy_time(first_next, 9)):
            run = calculate_run(self.today.year, self.today.month, self.finance_user)
            self.assertEqual(self.api(self.finance_user).post(f"/api/payroll-runs/{run.pk}/finalize/").status_code,
                             403)
        payslip_b = Payslip.objects.get(run=run, coach=self.coach_b)
        for method in ("patch", "put", "delete"):
            with self.subTest(method=method):
                self.assertIn(getattr(self.api(self.super_user), method)(
                    f"/api/payslips/{payslip_b.pk}/", {"net_pay": "9999.00"}, format="json").status_code, (403, 405))
                self.assertIn(getattr(self.api(self.super_user), method)(
                    f"/api/payroll-runs/{run.pk}/", {"status": "FINALIZED"}, format="json").status_code, (403, 405))
        self.assertEqual(self.api(self.coach_a_user).get(f"/api/payslips/{payslip_b.pk}/").status_code, 404)
        self.assertEqual(PayrollRun.objects.get(pk=run.pk).status, PayrollRun.Status.READY)
        self.assertEqual(Payslip.objects.get(pk=payslip_b.pk).net_pay, Decimal("80.00"))

    def test_competition_attacks(self):
        parent = self.api(self.parent_1_user)
        self.assertEqual(parent.post(f"/api/competition-registrations/{self.reg_1.pk}/confirm/").status_code, 403)
        self.assertIn(parent.patch(f"/api/competition-registrations/{self.reg_1.pk}/", {"status": "CONFIRMED"},
                                   format="json").status_code, (403, 405))
        self.assertEqual(parent.post("/api/competition-results/", {"registration": self.reg_1.pk, "placing": 1})
                         .status_code, 403)
        admin = self.api(self.admin_user)
        self.assertEqual(admin.post(f"/api/competition-registrations/{self.reg_1.pk}/confirm/").status_code, 400)
        self.assertEqual(admin.post("/api/competition-results/", {"registration": self.reg_1.pk, "placing": 1})
                         .status_code, 400)
        self.assertEqual(CompetitionRegistration.objects.get(pk=self.reg_1.pk).status, "PENDING")

    def test_finance_attacks(self):
        self.assertEqual(self.api(self.admin_user).post(f"/api/invoices/{self.inv_1.pk}/void/", {"reason": "x"})
                         .status_code, 403)                                            # ADMIN cannot void
        self.assertEqual(self.api(self.finance_user).post(f"/api/invoices/{self.inv_1.pk}/void/", {"reason": "x"})
                         .status_code, 400)                                            # paid: cannot void
        refund = self.api(self.parent_1_user).post(f"/api/payments/{self.pay_1.pk}/refund/",
                                                   {"allocation": self.pay_1.allocations.get().pk,
                                                    "amount": "10.00", "reason": "please"})
        self.assertEqual(refund.status_code, 403)
        too_much = self.api(self.finance_user).post(f"/api/payments/{self.pay_1.pk}/refund/",
                                                    {"allocation": self.pay_1.allocations.get().pk,
                                                     "amount": "500.00", "reason": "x"})
        self.assertEqual(too_much.status_code, 400)
        foreign = self.api(self.finance_user).post(f"/api/payments/{self.pay_1.pk}/refund/",
                                                   {"allocation": self.pay_3.allocations.get().pk,
                                                    "amount": "1.00", "reason": "x"})
        self.assertIn(foreign.status_code, (400, 404))                                 # allocation of another payment
        self.assertEqual(Invoice.objects.get(pk=self.inv_1.pk).status, "PAID")
