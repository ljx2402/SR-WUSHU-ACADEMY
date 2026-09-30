"""Phase 6B: the API as the Parent Portal uses it.

Family A has three children (one parent, one family invoice covering all
three); Family B has two. Parent A must reach everything of Family A through
the endpoints the portal calls, and nothing of Family B, whatever ids are put
in URLs or filters. Staff-only notes never reach a parent.
"""

import datetime
from decimal import Decimal

from rest_framework.test import APIClient

from apps.academy.models import Guardianship
from apps.academy.tests.base import AcademyTestCase, make_user
from apps.accounts.capabilities import Role
from apps.accounts.models import Parent
from apps.attendance.services import mark_attendance
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration
from apps.competitions.services import register
from apps.finance.models import Charge, Invoice
from apps.finance.services import add_charge, record_payment
from apps.finance.tests.helpers import issued_invoice, make_family


class ParentPortalTestCase(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.parent_a_user = make_user("parent_a", Role.PARENT)
        cls.parent_a = Parent.objects.create(user=cls.parent_a_user, full_name="Parent A", phone="0191")
        cls.parent_b_user = make_user("parent_b", Role.PARENT)
        cls.parent_b = Parent.objects.create(user=cls.parent_b_user, full_name="Parent B", phone="0192")
        cls.a1 = cls.make_student("A1", "Aaron", cls.parent_a, cls.class_a)
        cls.a2 = cls.make_student("A2", "Beth", cls.parent_a, cls.class_b, gender="F")
        cls.a3 = cls.make_student("A3", "Chen", cls.parent_a, cls.class_a)
        cls.b1 = cls.make_student("B1", "Dina", cls.parent_b, cls.class_a, gender="F")
        cls.b2 = cls.make_student("B2", "Eli", cls.parent_b, cls.class_b)
        cls.family_a = make_family(cls.a1, cls.a2, cls.a3, name="Family A")
        cls.family_b = make_family(cls.b1, cls.b2, name="Family B")
        cls.family_a.notes = "Staff: prefers WhatsApp"
        cls.family_a.save()

        # One family invoice covering all three of Family A's children.
        charges_a = [add_charge(st, "TUITION", "Monthly class fee", amount)
                     for st, amount in ((cls.a1, "220.00"), (cls.a2, "280.00"), (cls.a3, "50.00"))]
        Charge.objects.filter(pk=charges_a[0].pk).update(notes="Staff: checked with parent")
        cls.invoice_a = issued_invoice(charges_a)
        Invoice.objects.filter(pk=cls.invoice_a.pk).update(notes="Staff: internal invoice note")
        cls.charge_a1 = charges_a[0]
        charges_b = [add_charge(st, "TUITION", "Monthly class fee", "200.00") for st in (cls.b1, cls.b2)]
        cls.invoice_b = issued_invoice(charges_b)
        cls.charge_b1 = charges_b[0]

        cls.payment_a, cls.receipt_a = record_payment([(cls.invoice_a, "300.00")], "BANK_TRANSFER", cls.admin_user,
                                                      reference="MBB123", notes="Staff: matched bank statement")
        cls.payment_b, cls.receipt_b = record_payment([(cls.invoice_b, "400.00")], "CASH", cls.admin_user)

        cls.att_a1 = mark_attendance(cls.session_a, cls.a1, "PRESENT", cls.admin_user)
        cls.att_b1 = mark_attendance(cls.session_a, cls.b1, "ABSENT", cls.admin_user)

        cls.competition = Competition.objects.create(
            name="State Open", start_date=cls.today + datetime.timedelta(days=30),
            end_date=cls.today + datetime.timedelta(days=30),
            registration_deadline=cls.today + datetime.timedelta(days=10), status="OPEN",
            allow_parent_registration=True)
        cls.event = CompetitionEvent.objects.create(competition=cls.competition, event_type="OTHER", name="Open set",
                                                    fee=Decimal("50.00"))
        cls.event_2 = CompetitionEvent.objects.create(competition=cls.competition, event_type="OTHER",
                                                      name="Second set", fee=Decimal("30.00"))
        cls.reg_b1 = register(cls.b1, cls.event, cls.parent_b_user)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    @staticmethod
    def ids(response):
        body = response.json()
        return {row["id"] for row in body.get("results", body)}


class MultiChildFamilyTests(ParentPortalTestCase):
    def test_parent_sees_all_three_children_and_none_of_family_b(self):
        client = self.api(self.parent_a_user)
        me = client.get("/api/me/").json()
        self.assertEqual({c["id"] for c in me["children"]}, {self.a1.pk, self.a2.pk, self.a3.pk})
        self.assertEqual(self.ids(client.get("/api/students/")), {self.a1.pk, self.a2.pk, self.a3.pk})
        families = client.get("/api/families/").json()["results"]
        self.assertEqual([f["name"] for f in families], ["Family A"])
        self.assertEqual({s["id"] for s in families[0]["students"]}, {self.a1.pk, self.a2.pk, self.a3.pk})

    def test_one_family_invoice_lists_each_childs_line(self):
        client = self.api(self.parent_a_user)
        self.assertEqual(self.ids(client.get("/api/invoices/")), {self.invoice_a.pk})
        invoice = client.get(f"/api/invoices/{self.invoice_a.pk}/").json()
        lines = {(item["student_name"], item["amount"]) for item in invoice["items"]}
        self.assertEqual(lines, {("Aaron", "220.00"), ("Beth", "280.00"), ("Chen", "50.00")})
        self.assertEqual((invoice["total"], invoice["amount_paid"], invoice["balance_due"]),
                         ("550.00", "300.00", "250.00"))
        self.assertNotIn("billing_contact", invoice)

    def test_family_scoped_lists_contain_only_family_a(self):
        client = self.api(self.parent_a_user)
        self.assertEqual(self.ids(client.get("/api/payments/")), {self.payment_a.pk})
        self.assertEqual(self.ids(client.get("/api/receipts/")), {self.receipt_a.pk})
        self.assertEqual(self.ids(client.get("/api/charges/")), {c.pk for c in Charge.objects.filter(
            student__in=[self.a1, self.a2, self.a3])})
        self.assertEqual(self.ids(client.get("/api/attendance/")), {self.att_a1.pk})
        self.assertEqual(self.ids(client.get("/api/competition-registrations/")), set())

    def test_sessions_are_the_childrens_classes_only(self):
        other = self.class_b.sessions.create(date=self.today + datetime.timedelta(days=1),
                                             start_time=datetime.time(10), end_time=datetime.time(11))
        client = self.api(self.parent_b_user)
        # Family B has children in both classes; add a class nobody in Family B attends.
        from apps.academy.models import TrainingClass
        lonely = TrainingClass.objects.create(code="z", name="Class Z", category="SCHOOL", program=self.program)
        lonely_session = lonely.sessions.create(date=self.today, start_time=datetime.time(8), end_time=datetime.time(9))
        ids = self.ids(client.get("/api/sessions/", {"start": self.today.isoformat()}))
        self.assertIn(other.pk, ids)
        self.assertNotIn(lonely_session.pk, ids)
        self.assertEqual(client.get(f"/api/sessions/{lonely_session.pk}/").status_code, 404)


class ParentIdorTests(ParentPortalTestCase):
    def test_family_b_records_are_404_for_parent_a(self):
        client = self.api(self.parent_a_user)
        for url in [
            f"/api/students/{self.b1.pk}/", f"/api/students/{self.b2.pk}/",
            f"/api/students/{self.b1.pk}/attendance-summary/",
            f"/api/families/{self.family_b.pk}/",
            f"/api/attendance/{self.att_b1.pk}/",
            f"/api/invoices/{self.invoice_b.pk}/",
            f"/api/charges/{self.charge_b1.pk}/",
            f"/api/payments/{self.payment_b.pk}/",
            f"/api/receipts/{self.receipt_b.pk}/",
            f"/api/competition-registrations/{self.reg_b1.pk}/",
        ]:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 404)

    def test_family_a_records_are_reachable_by_parent_a(self):
        client = self.api(self.parent_a_user)
        for url in [
            f"/api/students/{self.a1.pk}/", f"/api/students/{self.a3.pk}/attendance-summary/",
            f"/api/families/{self.family_a.pk}/", f"/api/attendance/{self.att_a1.pk}/",
            f"/api/invoices/{self.invoice_a.pk}/", f"/api/charges/{self.charge_a1.pk}/",
            f"/api/payments/{self.payment_a.pk}/", f"/api/receipts/{self.receipt_a.pk}/",
        ]:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)

    def test_swapped_ids_in_filters_return_nothing(self):
        client = self.api(self.parent_a_user)
        self.assertEqual(self.ids(client.get("/api/attendance/", {"student": self.b1.pk})), set())
        self.assertEqual(self.ids(client.get("/api/charges/", {"student": self.b1.pk})), set())
        self.assertEqual(self.ids(client.get("/api/invoices/", {"family": self.family_b.pk})), set())
        self.assertEqual(self.ids(client.get("/api/competition-registrations/",
                                             {"competition": self.competition.pk})), set())

    def test_receipt_content_is_own_family_only(self):
        client = self.api(self.parent_a_user)
        receipt = client.get(f"/api/receipts/{self.receipt_a.pk}/").json()
        self.assertEqual(receipt["content"]["total"], "300.00")
        self.assertEqual({line["student_name"] for line in receipt["content"]["lines"]} <= {"Aaron", "Beth", "Chen"},
                         True)
        self.assertEqual(self.api(self.parent_b_user).get(f"/api/receipts/{self.receipt_a.pk}/").status_code, 404)
        # The Django print page needs a Django session and gives the same 404.
        self.client.force_login(self.parent_a_user)
        self.assertEqual(self.client.get(f"/receipts/{self.receipt_b.pk}/").status_code, 404)

    def test_parent_has_no_attendance_refund_or_confirm_mutations(self):
        client = self.api(self.parent_a_user)
        own_session_post = client.post(f"/api/sessions/{self.session_a.pk}/attendance/",
                                       {"records": [{"student": self.a1.pk, "status": "ABSENT"}]}, format="json")
        self.assertIn(own_session_post.status_code, (403, 404))
        self.assertEqual(client.post("/api/attendance/", {}, format="json").status_code, 405)
        self.assertEqual(client.post(f"/api/payments/{self.payment_a.pk}/refund/",
                                     {"allocation": self.payment_a.allocations.first().pk, "amount": "1.00",
                                      "reason": "x"}, format="json").status_code, 403)
        self.assertEqual(client.post(f"/api/competition-registrations/{self.reg_b1.pk}/confirm/", {}).status_code,
                         403)
        self.assertEqual(self.att_a1.__class__.objects.get(pk=self.att_a1.pk).status, "PRESENT")

    def test_registering_or_withdrawing_family_b_children_is_refused(self):
        client = self.api(self.parent_a_user)
        response = client.post("/api/competition-registrations/", {"student": self.b2.pk, "event": self.event.pk},
                               format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(client.post(f"/api/competition-registrations/{self.reg_b1.pk}/withdraw/",
                                     {"reason": "x"}).status_code, 404)
        self.assertEqual(CompetitionRegistration.objects.get(pk=self.reg_b1.pk).status, "PENDING")

    def test_forged_guardianship_needs_staff(self):
        # A parent cannot attach themselves to another family's child through the API.
        client = self.api(self.parent_a_user)
        response = client.post(f"/api/students/{self.b1.pk}/guardians/",
                               {"parent_id": self.parent_a.pk, "relationship": "FATHER"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Guardianship.objects.filter(student=self.b1, parent=self.parent_a).exists())


class StaffNotesTests(ParentPortalTestCase):
    def test_staff_notes_are_hidden_from_parents(self):
        client = self.api(self.parent_a_user)
        for url in [f"/api/charges/{self.charge_a1.pk}/", f"/api/invoices/{self.invoice_a.pk}/",
                    f"/api/payments/{self.payment_a.pk}/", f"/api/families/{self.family_a.pk}/"]:
            with self.subTest(url=url):
                self.assertNotIn("notes", client.get(url).json())
        for row in client.get("/api/payments/").json()["results"]:
            self.assertNotIn("notes", row)

    def test_staff_notes_remain_visible_to_staff(self):
        finance = self.api(self.finance_user)
        self.assertEqual(finance.get(f"/api/payments/{self.payment_a.pk}/").json()["notes"],
                         "Staff: matched bank statement")
        self.assertEqual(finance.get(f"/api/charges/{self.charge_a1.pk}/").json()["notes"],
                         "Staff: checked with parent")
        self.assertEqual(finance.get(f"/api/invoices/{self.invoice_a.pk}/").json()["notes"],
                         "Staff: internal invoice note")
        self.assertEqual(self.api(self.admin_user).get(f"/api/families/{self.family_a.pk}/").json()["notes"],
                         "Staff: prefers WhatsApp")


class CompetitionFlowTests(ParentPortalTestCase):
    def test_register_pay_confirm_withdraw_without_refund(self):
        client = self.api(self.parent_a_user)
        response = client.post("/api/competition-registrations/", {"student": self.a1.pk, "event": self.event.pk},
                               format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["fee"], body["fee_status"]), ("PENDING", "50.00", "UNPAID"))
        self.assertEqual(body["invoice"]["balance_due"], "50.00")
        # The parent cannot confirm it; payment is recorded by staff.
        self.assertEqual(client.post(f"/api/competition-registrations/{body['id']}/confirm/", {}).status_code, 403)
        invoice = Invoice.objects.get(pk=body["invoice"]["id"])
        self.assertEqual(invoice.status, "ISSUED")
        record_payment([(invoice, "50.00")], "CASH", self.admin_user)
        registration = client.get(f"/api/competition-registrations/{body['id']}/").json()
        self.assertEqual((registration["status"], registration["fee_status"]), ("CONFIRMED", "PAID"))
        # Withdrawing a paid entry changes its status only; nothing is refunded.
        withdrawn = client.post(f"/api/competition-registrations/{body['id']}/withdraw/", {"reason": "Sick"})
        self.assertEqual(withdrawn.json()["status"], "WITHDRAWN")
        self.assertEqual(Charge.objects.get(competition_registration__pk=body["id"]).status, "PAID")
        self.assertEqual(Invoice.objects.get(pk=invoice.pk).amount_refunded, Decimal("0.00"))

    def test_withdrawing_unpaid_entry_voids_its_invoice(self):
        client = self.api(self.parent_a_user)
        body = client.post("/api/competition-registrations/", {"student": self.a2.pk, "event": self.event_2.pk},
                           format="json").json()
        self.assertEqual(client.post(f"/api/competition-registrations/{body['id']}/withdraw/", {}).status_code, 200)
        withdrawn = client.get(f"/api/competition-registrations/{body['id']}/").json()
        self.assertEqual((withdrawn["status"], withdrawn["fee_status"]), ("WITHDRAWN", "CANCELLED"))
        self.assertEqual(Invoice.objects.get(pk=body["invoice"]["id"]).status, "VOID")

    def test_backend_eligibility_and_duplicates_are_reported(self):
        self.event.gender = "F"
        self.event.save()
        client = self.api(self.parent_a_user)
        response = client.post("/api/competition-registrations/", {"student": self.a1.pk, "event": self.event.pk},
                               format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("female", " ".join(response.json()["detail"]).lower())
        first = client.post("/api/competition-registrations/", {"student": self.a2.pk, "event": self.event.pk},
                            format="json")
        self.assertEqual(first.status_code, 201)
        again = client.post("/api/competition-registrations/", {"student": self.a2.pk, "event": self.event.pk},
                            format="json")
        self.assertEqual(again.status_code, 400)
        self.assertIn("already registered", " ".join(again.json()["detail"]))

    def test_draft_competitions_are_hidden_from_parents(self):
        draft = Competition.objects.create(
            name="Secret", start_date=self.today + datetime.timedelta(days=60),
            end_date=self.today + datetime.timedelta(days=60),
            registration_deadline=self.today + datetime.timedelta(days=40), status="DRAFT")
        client = self.api(self.parent_a_user)
        self.assertNotIn(draft.pk, self.ids(client.get("/api/competitions/")))
        self.assertEqual(client.get(f"/api/competitions/{draft.pk}/").status_code, 404)
