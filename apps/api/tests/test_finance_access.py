"""Finance API: capability (403) and record scope (404) for all six roles."""

from django.db import transaction
from rest_framework.test import APIClient

from apps.academy.models import Guardianship, StudentAccount
from apps.academy.tests.base import AcademyTestCase, make_user
from apps.accounts.capabilities import Role
from apps.accounts.models import Parent
from apps.accounts.services import set_roles
from apps.finance.services import add_charge, create_invoice, record_payment
from apps.finance.tests.helpers import issued_invoice, make_family

S, A, F, C, P, T = Role.SUPER_ADMIN, Role.ADMIN, Role.FINANCE_ADMIN, Role.COACH, Role.PARENT, Role.STUDENT
ALL = (S, A, F, C, P, T)


def only(status_by_role, default=403):
    return {role: status_by_role.get(role, default) for role in ALL}


class FinanceAccessTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Family 1: Ali and Mei (parent_1's children) plus step-sibling Zara, whose only guardian is someone else.
        cls.step_parent = Parent.objects.create(full_name="Zara's father", phone="0199")
        cls.zara = cls.make_student("S5", "Zara", cls.step_parent, cls.class_b)
        cls.family_1 = make_family(cls.student_1, cls.student_2, cls.zara, name="Blended family")
        cls.family_2 = cls.student_3.family  # Raj, parent_2
        cls.raj_user = make_user("raj", Role.STUDENT)
        StudentAccount.objects.create(user=cls.raj_user, student=cls.student_3)
        cls.actors = {S: cls.super_user, A: cls.admin_user, F: cls.finance_user,
                      C: cls.coach_a_user, P: cls.parent_1_user, T: cls.raj_user}

        cls.inv_1 = issued_invoice([add_charge(cls.student_1, "TUITION", "Fee Ali", "220.00"),
                                    add_charge(cls.zara, "TUITION", "Fee Zara", "280.00")])
        cls.inv_1_open = issued_invoice([add_charge(cls.student_2, "UNIFORM", "Uniform Mei", "80.00")])
        cls.draft_1 = create_invoice(cls.family_1, [add_charge(cls.student_1, "OTHER", "Draft Ali", "10.00")])
        cls.inv_2 = issued_invoice([add_charge(cls.student_3, "TUITION", "Fee Raj", "220.00")])
        cls.pay_1, cls.receipt_1 = record_payment([(cls.inv_1, "500.00")], "CASH")
        cls.pay_2, cls.receipt_2 = record_payment([(cls.inv_2, "220.00")], "CASH")
        cls.free_charge = add_charge(cls.student_1, "OTHER", "Uninvoiced", "15.00")

    def client_for(self, role):
        client = APIClient()
        client.force_authenticate(self.actors[role])
        return client

    def ids(self, role, path):
        body = self.client_for(role).get(path).json()
        return {row["id"] for row in body.get("results", body)}

    def cases(self):
        alloc = self.pay_1.allocations.first()
        return [
            ("list invoices", "get", "/api/invoices/", None, only({S: 200, A: 200, F: 200, P: 200})),
            ("other family's invoice", "get", f"/api/invoices/{self.inv_2.pk}/", None,
             only({S: 200, A: 200, F: 200, P: 404})),
            ("draft invoice", "get", f"/api/invoices/{self.draft_1.pk}/", None, only({S: 200, A: 200, F: 200, P: 404})),
            ("own family invoice", "get", f"/api/invoices/{self.inv_1.pk}/", None,
             only({S: 200, A: 200, F: 200, P: 200})),
            ("draft invoice from charges", "post", "/api/invoices/",
             {"family": self.family_1.pk, "charges": [self.free_charge.pk]}, only({S: 201, F: 201})),
            ("issue draft", "post", f"/api/invoices/{self.draft_1.pk}/issue/", {}, only({S: 200, F: 200})),
            ("void unpaid invoice", "post", f"/api/invoices/{self.inv_1_open.pk}/void/", {"reason": "wrong"},
             only({S: 200, F: 200})),
            ("generate drafts", "post", "/api/invoices/generate-drafts/", {}, only({S: 201, F: 201})),
            ("list payments", "get", "/api/payments/", None, only({S: 200, A: 200, F: 200, P: 200})),
            ("record payment", "post", "/api/payments/",
             {"amount": "80.00", "method": "CASH", "allocations": [{"invoice": self.inv_1_open.pk, "amount": "80.00"}]},
             only({S: 201, A: 201, F: 201})),
            ("void payment", "post", f"/api/payments/{self.pay_1.pk}/void/", {"reason": "error"},
             only({S: 200, F: 200})),
            ("exceptional refund", "post", f"/api/payments/{self.pay_1.pk}/refund/",
             {"allocation": alloc.pk, "amount": "10.00", "reason": "Approved exception"}, only({S: 201, A: 201, F: 201})),
            ("list receipts", "get", "/api/receipts/", None, only({S: 200, A: 200, F: 200, P: 200})),
            ("other family's receipt", "get", f"/api/receipts/{self.receipt_2.pk}/", None,
             only({S: 200, A: 200, F: 200, P: 404})),
            ("list refunds", "get", "/api/refunds/", None, only({S: 200, A: 200, F: 200, P: 200})),
            ("list families", "get", "/api/families/", None, only({S: 200, A: 200, F: 200, P: 200})),
            ("other family", "get", f"/api/families/{self.family_2.pk}/", None, only({S: 200, A: 200, F: 200, P: 404})),
            ("invoices report", "get", "/api/reports/invoices/", None, only({S: 200, F: 200})),
            ("refunds report", "get", "/api/reports/refunds/", None, only({S: 200, F: 200})),
        ]

    def test_finance_permission_matrix(self):
        for description, method, path, data, expected in self.cases():
            for role in ALL:
                with self.subTest(case=description, role=role), transaction.atomic():
                    client = self.client_for(role)
                    response = getattr(client, method)(path, data, format="json") if data is not None \
                        else getattr(client, method)(path)
                    self.assertEqual(response.status_code, expected[role],
                                     f"{role} {method.upper()} {path}: {response.content[:300]!r}")
                    transaction.set_rollback(True)

    def test_parent_sees_own_families_whole_invoice_only(self):
        self.assertEqual(self.ids(P, "/api/invoices/"), {self.inv_1.pk, self.inv_1_open.pk})
        body = self.client_for(P).get(f"/api/invoices/{self.inv_1.pk}/").json()
        self.assertEqual({i["student_name"] for i in body["items"]}, {"Ali", "Zara"})  # whole family document
        self.assertNotIn("bill_to", body)
        self.assertEqual(self.ids(P, "/api/receipts/"), {self.receipt_1.pk})
        other_parent = APIClient()
        other_parent.force_authenticate(self.parent_2_user)
        self.assertEqual({r["id"] for r in other_parent.get("/api/invoices/").json()["results"]}, {self.inv_2.pk})

    def test_parent_charges_are_own_children_only(self):
        ids = self.ids(P, "/api/charges/")
        self.assertFalse(ids & set(self.zara.charges.values_list("id", flat=True)))  # step-sibling's charges
        self.assertTrue(set(self.student_1.charges.values_list("id", flat=True)) <= ids)

    def test_student_has_no_finance_access(self):
        client = self.client_for(T)
        for path in ("/api/invoices/", "/api/payments/", "/api/receipts/", "/api/refunds/", "/api/charges/",
                     f"/api/invoices/{self.inv_2.pk}/", "/api/families/"):
            self.assertEqual(client.get(path).status_code, 403, path)

    def test_coach_parent_does_not_leak_coached_students_fees(self):
        set_roles(self.coach_a_user, [Role.COACH, Role.PARENT], self.super_user, "Coach is also a parent")
        coach_parent = Parent.objects.create(user=self.coach_a_user, full_name="Coach A (parent)", phone="011")
        kid = self.make_student("K1", "Kid", coach_parent, self.class_b)
        kid_invoice = issued_invoice([add_charge(kid, "TUITION", "Fee Kid", "100.00")])
        client = APIClient()
        client.force_authenticate(self.coach_a_user)
        # Coach A coaches Ali (family 1) and Raj (family 2), but only sees Kid's family's finances.
        self.assertEqual({r["id"] for r in client.get("/api/invoices/").json()["results"]}, {kid_invoice.pk})
        self.assertEqual(client.get(f"/api/invoices/{self.inv_1.pk}/").status_code, 404)
        self.assertEqual(client.get(f"/api/invoices/{self.inv_2.pk}/").status_code, 404)
        self.assertEqual(client.get(f"/api/receipts/{self.receipt_1.pk}/").status_code, 404)
        self.assertEqual({r["student"] for r in client.get("/api/charges/").json()["results"]}, {kid.pk})
        self.assertEqual(client.get("/api/families/").json()["results"][0]["id"], kid.family_id)

    def test_print_pages_respect_access(self):
        self.client.force_login(self.parent_1_user)
        self.assertEqual(self.client.get(f"/invoices/{self.inv_1.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/invoices/{self.inv_2.pk}/").status_code, 403)
        self.assertEqual(self.client.get(f"/invoices/{self.draft_1.pk}/").status_code, 403)
        self.assertEqual(self.client.get(f"/receipts/{self.receipt_1.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/receipts/{self.receipt_2.pk}/").status_code, 403)
        self.client.force_login(self.coach_a_user)
        self.assertEqual(self.client.get(f"/invoices/{self.inv_1.pk}/").status_code, 403)

    def test_idempotency_key_over_api(self):
        client = self.client_for(A)
        body = {"amount": "80.00", "method": "CASH", "allocations": [{"invoice": self.inv_1_open.pk, "amount": "80.00"}]}
        first = client.post("/api/payments/", body, format="json", HTTP_IDEMPOTENCY_KEY="abc-123")
        second = client.post("/api/payments/", body, format="json", HTTP_IDEMPOTENCY_KEY="abc-123")
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual(first.json()["id"], second.json()["id"])

    def test_validation_errors_are_400(self):
        client = self.client_for(A)
        over = client.post("/api/payments/", {"amount": "81.00", "method": "CASH",
                                              "allocations": [{"invoice": self.inv_1_open.pk, "amount": "81.00"}]},
                           format="json")
        self.assertEqual(over.status_code, 400)
        sub_sen = client.post("/api/payments/", {"amount": "1.005", "method": "CASH",
                                                 "allocations": [{"invoice": self.inv_1_open.pk, "amount": "1.005"}]},
                              format="json")
        self.assertEqual(sub_sen.status_code, 400)
        self.assertFalse(Guardianship._meta.get_fields() and any(
            f.name == "is_billing_contact" for f in Guardianship._meta.get_fields()))
