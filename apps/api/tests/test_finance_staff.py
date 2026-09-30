"""Phase 6F: the API as the Finance Staff Portal uses it.

Fixtures (from the Parent Portal / payment-proof tests): Family A's invoice
(RM 550, RM 300 paid: partially paid), Family B's invoice (paid), Family B's
open uniform invoice (RM 80) and Dina's (Family B) competition entry with its
competition invoice (RM 50, awaiting payment).

Capabilities decide everything (``apps/accounts/capabilities.py``):
ADMIN: view all finance, record payments, review proofs, exceptional refunds.
FINANCE_ADMIN: those plus invoices, voids, charges and payment information.
SUPER_ADMIN: everything. COACH / STUDENT: nothing. PARENT: own families only.
"""

from decimal import Decimal

from django.test import override_settings

from apps.audit.utils import history_for
from apps.competitions.models import CompetitionRegistration
from apps.finance.models import AcademyPaymentInfo, Invoice, Payment, PaymentAllocation, PaymentProof, Receipt, Refund
from apps.finance.services import add_charge, create_invoice

from .test_payment_proofs import JPG, PDF, PNG, PRIVATE, PaymentProofTestCase, upload

SECRET_KEYS = {"sha256", "file", "path", "storage", "bank_account_no", "ic_number", "epf_no", "socso_no"}


def keys_in(data):
    if isinstance(data, dict):
        return set(data) | {k for v in data.values() for k in keys_in(v)}
    if isinstance(data, list):
        return {k for v in data for k in keys_in(v)}
    return set()


@override_settings(STORAGES=PRIVATE)
class FinanceStaffTestCase(PaymentProofTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.competition_invoice = cls.reg_b1.charge.active_invoice_item().invoice

    def rows(self, response):
        body = response.json()
        return body.get("results", body) if isinstance(body, dict) else body

    def pay(self, user, invoice, amount, method="BANK_TRANSFER", reference="REF-1", key=None):
        headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
        return self.api(user).post("/api/payments/", {
            "amount": amount, "method": method, "reference": reference,
            "allocations": [{"invoice": invoice.pk, "amount": amount}]}, format="json", **headers)


class DashboardTests(FinanceStaffTestCase):
    def test_totals_come_from_the_finance_records(self):
        body = self.api(self.finance_user).get("/api/finance/dashboard/").json()
        inv = body["invoices"]
        open_invoices = Invoice.objects.filter(status__in=Invoice.OPEN_FOR_PAYMENT)
        self.assertEqual((inv["unpaid"], inv["partially_paid"]),
                         (open_invoices.filter(status="ISSUED").count(), open_invoices.filter(status="PARTIALLY_PAID").count()))
        self.assertEqual(Decimal(inv["outstanding_total"]), sum(i.balance_due for i in open_invoices))
        self.assertEqual((inv["competition_open"], inv["competition_outstanding"]), (1, "50.00"))
        self.assertEqual(body["payments"]["today_count"], 2)
        self.assertEqual({p["number"] for p in body["payments"]["recent"]}, {self.payment_a.number, self.payment_b.number})
        self.assertEqual({r["number"] for r in body["receipts"]}, {self.receipt_a.number, self.receipt_b.number})
        self.assertEqual(body["proofs"]["pending"], 0)
        self.proof_for_b()
        self.assertEqual(self.api(self.admin_user).get("/api/finance/dashboard/").json()["proofs"]["pending"], 1)
        self.assertFalse(keys_in(body) & SECRET_KEYS)
        self.assertFalse({"account_number", "bank_name", "account_name"} & keys_in(body))

    def test_only_finance_viewers(self):
        for user in (self.admin_user, self.finance_user, self.super_user):
            self.assertEqual(self.api(user).get("/api/finance/dashboard/").status_code, 200, user.username)
        for user in (self.coach_user, self.parent_a_user, self.student_user):
            self.assertEqual(self.api(user).get("/api/finance/dashboard/").status_code, 403, user.username)


class InvoiceTests(FinanceStaffTestCase):
    def test_search_and_filters(self):
        client = self.api(self.admin_user)
        ids = lambda **q: {r["id"] for r in self.rows(client.get("/api/invoices/", q))}  # noqa: E731
        self.assertEqual(ids(search=self.invoice_a.number), {self.invoice_a.pk})
        self.assertEqual(ids(search="Chen"), {self.invoice_a.pk})              # student name on a line
        self.assertEqual(ids(search="B2"), {self.invoice_b.pk, self.invoice_b_open.pk})   # student no.
        self.assertEqual(ids(search="Family B"), {self.invoice_b.pk, self.invoice_b_open.pk, self.competition_invoice.pk})
        self.assertEqual(ids(student=self.a2.pk), {self.invoice_a.pk})
        self.assertEqual(ids(kind="COMPETITION"), {self.competition_invoice.pk})
        self.assertEqual(ids(status="PARTIALLY_PAID"), {self.invoice_a.pk})
        self.assertEqual(ids(outstanding=1), {self.invoice_a.pk, self.invoice_b_open.pk, self.competition_invoice.pk})
        self.assertEqual(ids(start=self.today.isoformat(), end=self.today.isoformat()),
                         set(Invoice.objects.exclude(status="DRAFT").values_list("id", flat=True)))
        self.assertEqual(client.get("/api/invoices/", {"start": "yesterday"}).status_code, 400)
        self.assertEqual(client.get("/api/invoices/", {"student": "x"}).status_code, 400)

    def test_detail_lines_and_competition_reference_for_staff_only(self):
        body = self.api(self.finance_user).get(f"/api/invoices/{self.competition_invoice.pk}/").json()
        line = body["items"][0]
        self.assertEqual(line["competition_registration"], {"id": self.reg_b1.pk, "competition_name": "State Open",
                                                           "event_name": "Open set", "status": "PENDING"})
        family = self.api(self.parent_b_user).get(f"/api/invoices/{self.competition_invoice.pk}/").json()
        self.assertNotIn("competition_registration", family["items"][0])
        self.assertNotIn("notes", family)
        multi = self.api(self.admin_user).get(f"/api/invoices/{self.invoice_a.pk}/").json()
        self.assertEqual([i["student_name"] for i in multi["items"]], ["Aaron", "Beth", "Chen"])
        self.assertFalse({"bill_to", "billing_contact", "billing_parent"} & keys_in(multi))

    def test_issued_invoices_are_not_editable(self):
        client = self.api(self.super_user)
        url = f"/api/invoices/{self.invoice_a.pk}/"
        for method in ("put", "patch", "delete"):
            self.assertEqual(getattr(client, method)(url, {"total": "1.00"}, format="json").status_code, 405, method)
        self.assertEqual(client.post(url + "issue/", {}).status_code, 400)          # already issued
        void = self.api(self.finance_user).post(f"/api/invoices/{self.invoice_b.pk}/void/", {"reason": "x"}, format="json")
        self.assertEqual(void.status_code, 400)                                    # has payments
        self.assertEqual(Invoice.objects.get(pk=self.invoice_a.pk).total, Decimal("550.00"))

    def test_invoice_operations_follow_the_capabilities(self):
        draft = create_invoice(self.family_a, [add_charge(self.a1, "UNIFORM", "Uniform", "60.00")], self.finance_user)
        self.assertEqual(self.api(self.admin_user).post(f"/api/invoices/{draft.pk}/issue/", {}).status_code, 403)
        self.assertEqual(self.api(self.admin_user).post(f"/api/invoices/{draft.pk}/void/", {"reason": "x"}).status_code, 403)
        self.assertEqual(self.api(self.finance_user).post(f"/api/invoices/{draft.pk}/void/", {}).status_code, 400)
        issued = self.api(self.finance_user).post(f"/api/invoices/{draft.pk}/issue/", {}, format="json")
        self.assertEqual(issued.json()["status"], "ISSUED")
        voided = self.api(self.finance_user).post(f"/api/invoices/{draft.pk}/void/", {"reason": "Duplicate"}, format="json")
        self.assertEqual(voided.json()["status"], "VOID")
        self.assertEqual(self.api(self.finance_user).post(f"/api/invoices/{draft.pk}/issue/", {}).status_code, 400)

    def test_non_finance_roles_and_other_families(self):
        for user in (self.coach_user, self.student_user):
            self.assertEqual(self.api(user).get("/api/invoices/").status_code, 403)
            self.assertEqual(self.api(user).get(f"/api/invoices/{self.invoice_a.pk}/").status_code, 403)
        self.assertEqual(self.api(self.parent_a_user).get(f"/api/invoices/{self.invoice_b.pk}/").status_code, 404)
        self.assertEqual({r["id"] for r in self.rows(self.api(self.parent_a_user).get("/api/invoices/", {"search": "Family B"}))},
                         set())


class ProofReviewTests(FinanceStaffTestCase):
    def test_list_filters_and_detail_never_expose_storage_details(self):
        proof_b = self.proof_for_b()
        proof_a = PaymentProof.objects.get(pk=self.post_proof(self.parent_a_user, self.invoice_a, amount_claimed="250.00",
                                                              reference="MBB-777").json()["id"])
        client = self.api(self.admin_user)
        ids = lambda **q: [r["id"] for r in self.rows(client.get("/api/payment-proofs/", q))]  # noqa: E731
        self.assertEqual(set(ids(status="PENDING_REVIEW")), {proof_a.pk, proof_b.pk})
        self.assertEqual(ids(invoice=self.invoice_a.pk), [proof_a.pk])
        self.assertEqual(ids(family=self.family_b.pk), [proof_b.pk])
        self.assertEqual(ids(student=self.a3.pk), [proof_a.pk])
        self.assertEqual(ids(search="MBB-777"), [proof_a.pk])
        self.assertEqual(ids(search="Eli"), [proof_b.pk])
        self.assertEqual(set(ids(start=self.today.isoformat())), {proof_a.pk, proof_b.pk})
        detail = client.get(f"/api/payment-proofs/{proof_a.pk}/").json()
        self.assertFalse(keys_in(detail) & SECRET_KEYS)
        self.assertNotIn(proof_a.file.name, str(detail))
        self.assertEqual((detail["amount_claimed"], detail["reference"], detail["status"]), ("250.00", "MBB-777", "PENDING_REVIEW"))

    def test_secure_download(self):
        proof = self.proof_for_b()
        response = self.api(self.finance_user).get(f"/api/payment-proofs/{proof.pk}/file/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual((response["Cache-Control"], response["X-Content-Type-Options"]), ("private, no-store", "nosniff"))
        self.assertIn("sandbox", response["Content-Security-Policy"])
        self.assertEqual(b"".join(response.streaming_content), PNG)
        for user in (self.coach_user, self.student_user):
            self.assertEqual(self.api(user).get(f"/api/payment-proofs/{proof.pk}/file/").status_code, 403)
        self.assertEqual(self.api(self.parent_a_user).get(f"/api/payment-proofs/{proof.pk}/file/").status_code, 404)

    def test_accepting_records_no_money(self):
        proof = self.proof_for_b()
        before = Invoice.objects.get(pk=self.invoice_b_open.pk)
        counts = (Payment.objects.count(), Receipt.objects.count(), PaymentAllocation.objects.count())
        accepted = self.api(self.admin_user).post(f"/api/payment-proofs/{proof.pk}/accept/", {"note": "Matches bank"},
                                                 format="json")
        self.assertEqual((accepted.status_code, accepted.json()["status"]), (200, "ACCEPTED"))
        self.assertIsNone(accepted.json()["payment"])
        after = Invoice.objects.get(pk=self.invoice_b_open.pk)
        self.assertEqual((after.status, after.balance_due), (before.status, before.balance_due))
        self.assertEqual((Payment.objects.count(), Receipt.objects.count(), PaymentAllocation.objects.count()), counts)
        self.assertEqual(self.api(self.admin_user).post(f"/api/payment-proofs/{proof.pk}/reject/", {"reason": "x"}).status_code, 400)
        entry = history_for(PaymentProof.objects.get(pk=proof.pk)).first()
        self.assertEqual((entry.actor, entry.reason), (self.admin_user, "Payment proof accepted: Matches bank"))

    def test_rejection_needs_a_reason_and_the_parent_sees_it(self):
        proof = self.proof_for_b()
        self.assertEqual(self.api(self.finance_user).post(f"/api/payment-proofs/{proof.pk}/reject/", {"reason": "  "},
                                                          format="json").status_code, 400)
        rejected = self.api(self.finance_user).post(f"/api/payment-proofs/{proof.pk}/reject/",
                                                    {"reason": "Amount does not match"}, format="json")
        self.assertEqual(rejected.json()["status"], "REJECTED")
        parent_view = self.api(self.parent_b_user).get(f"/api/payment-proofs/{proof.pk}/").json()
        self.assertEqual((parent_view["status"], parent_view["review_note"]), ("REJECTED", "Amount does not match"))
        self.assertNotIn("reviewed_by_name", parent_view)
        self.assertEqual(Invoice.objects.get(pk=self.invoice_b_open.pk).status, "ISSUED")

    def test_accept_may_link_only_a_valid_payment_of_the_same_family(self):
        proof = self.proof_for_b()
        wrong = self.api(self.finance_user).post(f"/api/payment-proofs/{proof.pk}/accept/", {"payment": self.payment_a.pk},
                                                 format="json")
        self.assertEqual(wrong.status_code, 400)
        paid = self.pay(self.finance_user, self.invoice_b_open, "80.00").json()
        linked = self.api(self.finance_user).post(f"/api/payment-proofs/{proof.pk}/accept/", {"payment": paid["id"]},
                                                  format="json")
        self.assertEqual((linked.json()["status"], linked.json()["payment_number"]), ("ACCEPTED", paid["number"]))

    def test_only_reviewers_can_review_and_proofs_are_never_edited(self):
        proof = self.proof_for_b()
        for user in (self.coach_user, self.student_user, self.parent_b_user, self.parent_a_user):
            client = self.api(user)
            self.assertIn(client.post(f"/api/payment-proofs/{proof.pk}/accept/", {}).status_code, (403, 404), user.username)
            self.assertIn(client.post(f"/api/payment-proofs/{proof.pk}/reject/", {"reason": "x"}).status_code, (403, 404))
        for method in ("put", "patch", "delete"):
            self.assertEqual(getattr(self.api(self.super_user), method)(f"/api/payment-proofs/{proof.pk}/",
                                                                        {"status": "ACCEPTED"}, format="json").status_code, 405)
        self.assertEqual(PaymentProof.objects.get(pk=proof.pk).status, "PENDING_REVIEW")


class PaymentTests(FinanceStaffTestCase):
    def test_methods_are_the_backends(self):
        methods = self.api(self.admin_user).get("/api/payments/methods/").json()
        self.assertEqual([m["value"] for m in methods], list(Payment.Method.values))
        self.assertEqual(self.api(self.parent_a_user).get("/api/payments/methods/").status_code, 403)

    def test_partial_then_full_payment_with_allocation_and_receipts(self):
        partial = self.pay(self.admin_user, self.invoice_b_open, "30.00", reference="PART-1")
        self.assertEqual(partial.status_code, 201, partial.content)
        invoice = Invoice.objects.get(pk=self.invoice_b_open.pk)
        self.assertEqual((invoice.status, invoice.amount_paid, invoice.balance_due),
                         ("PARTIALLY_PAID", Decimal("30.00"), Decimal("50.00")))
        self.assertEqual(sum(a.amount for a in PaymentAllocation.objects.filter(payment_id=partial.json()["id"])),
                         Decimal("30.00"))
        self.assertTrue(partial.json()["receipt_number"])
        too_much = self.pay(self.admin_user, self.invoice_b_open, "60.00")
        self.assertEqual(too_much.status_code, 400)
        full = self.pay(self.finance_user, self.invoice_b_open, "50.00", reference="PART-2")
        self.assertEqual(Invoice.objects.get(pk=self.invoice_b_open.pk).status, "PAID")
        self.assertTrue(Receipt.objects.filter(payment_id=full.json()["id"]).exists())
        self.assertEqual(self.pay(self.admin_user, self.invoice_b_open, "1.00").status_code, 400)   # paid: closed
        entry = history_for(Payment.objects.get(pk=full.json()["id"])).first()
        self.assertEqual((entry.actor, entry.reason), (self.finance_user, "Payment received"))
        by_ref = self.rows(self.api(self.admin_user).get("/api/payments/", {"search": "PART-2"}))
        self.assertEqual([p["id"] for p in by_ref], [full.json()["id"]])
        by_invoice = self.rows(self.api(self.admin_user).get("/api/payments/", {"invoice": self.invoice_b_open.pk}))
        self.assertEqual({p["id"] for p in by_invoice}, {partial.json()["id"], full.json()["id"]})
        by_receipt = self.rows(self.api(self.admin_user).get("/api/receipts/", {"search": full.json()["receipt_number"]}))
        self.assertEqual([r["payment"] for r in by_receipt], [full.json()["id"]])

    def test_idempotent_and_no_payment_on_a_draft(self):
        first = self.pay(self.admin_user, self.invoice_b_open, "10.00", key="k-1")
        again = self.pay(self.admin_user, self.invoice_b_open, "10.00", key="k-1")
        self.assertEqual(first.json()["id"], again.json()["id"])
        draft = create_invoice(self.family_a, [add_charge(self.a1, "UNIFORM", "Uniform", "60.00")], self.finance_user)
        self.assertEqual(self.pay(self.admin_user, draft, "60.00").status_code, 400)

    def test_void_and_refund_follow_the_capabilities(self):
        payment = self.pay(self.admin_user, self.invoice_b_open, "80.00").json()
        self.assertEqual(self.api(self.admin_user).post(f"/api/payments/{payment['id']}/void/", {"reason": "x"}).status_code, 403)
        allocation = payment["allocations"][0]["id"]
        url = f"/api/payments/{payment['id']}/refund/"
        self.assertEqual(self.api(self.admin_user).post(url, {"allocation": allocation, "amount": "10.00"}, format="json").status_code, 400)
        refund = self.api(self.admin_user).post(url, {"allocation": allocation, "amount": "10.00",
                                                      "reason": "Exceptional: duplicate uniform"}, format="json")
        self.assertEqual(refund.status_code, 201, refund.content)
        original = Payment.objects.get(pk=payment["id"])
        self.assertEqual((original.status, original.amount), ("VALID", Decimal("80.00")))
        self.assertFalse(Receipt.objects.get(payment=original).is_void)
        self.assertEqual(history_for(Refund.objects.get(pk=refund.json()["id"])).first().reason, "Exceptional: duplicate uniform")
        self.assertEqual(self.api(self.admin_user).post(url, {"allocation": allocation, "amount": "100.00", "reason": "x"},
                                                        format="json").status_code, 400)
        for user in (self.coach_user, self.parent_b_user, self.student_user):
            self.assertIn(self.api(user).post(url, {"allocation": allocation, "amount": "1.00", "reason": "x"},
                                              format="json").status_code, (403, 404))
        other = self.pay(self.admin_user, self.invoice_a, "10.00").json()
        cross = self.api(self.admin_user).post(url, {"allocation": other["allocations"][0]["id"], "amount": "1.00",
                                                     "reason": "x"}, format="json")
        self.assertEqual(cross.status_code, 400)                        # line of another payment
        voided = self.api(self.finance_user).post(f"/api/payments/{other['id']}/void/", {"reason": "Keyed twice"}, format="json")
        self.assertEqual(voided.json()["status"], "VOIDED")
        self.assertTrue(Receipt.objects.get(payment_id=other["id"]).is_void)

    def test_payments_cannot_be_created_or_edited_by_others_and_allocations_are_immutable(self):
        for user in (self.coach_user, self.parent_a_user, self.student_user):
            self.assertEqual(self.pay(user, self.invoice_a, "10.00").status_code, 403, user.username)
        for method in ("put", "patch", "delete"):
            self.assertEqual(getattr(self.api(self.super_user), method)(f"/api/payments/{self.payment_a.pk}/",
                                                                        {"amount": "1.00"}, format="json").status_code, 405)
        self.assertEqual(self.api(self.parent_a_user).get(f"/api/payments/{self.payment_b.pk}/").status_code, 404)
        self.assertEqual(self.api(self.parent_a_user).get(f"/api/receipts/{self.receipt_b.pk}/").status_code, 404)
        self.assertEqual(self.api(self.coach_user).get(f"/api/receipts/{self.receipt_b.pk}/").status_code, 403)
        self.assertEqual(self.api(self.student_user).get("/api/receipts/").status_code, 403)


class CompetitionLifecycleTests(FinanceStaffTestCase):
    """Registration → invoice → proof → review → payment → receipt → confirmation."""

    def test_rejected_or_accepted_proof_alone_changes_nothing_payment_confirms(self):
        invoice = self.competition_invoice
        rejected = self.post_proof(self.parent_b_user, invoice, file=upload("slip.pdf", PDF, "application/pdf")).json()
        self.api(self.finance_user).post(f"/api/payment-proofs/{rejected['id']}/reject/", {"reason": "Unreadable"},
                                         format="json")
        self.assertEqual((Invoice.objects.get(pk=invoice.pk).status, CompetitionRegistration.objects.get(pk=self.reg_b1.pk).status),
                         ("ISSUED", "PENDING"))
        accepted = self.post_proof(self.parent_b_user, invoice, file=upload("slip.jpg", JPG, "image/jpeg"),
                                   amount_claimed="50.00").json()
        self.api(self.finance_user).post(f"/api/payment-proofs/{accepted['id']}/accept/", {}, format="json")
        self.assertEqual((Invoice.objects.get(pk=invoice.pk).status, CompetitionRegistration.objects.get(pk=self.reg_b1.pk).status),
                         ("ISSUED", "PENDING"))
        self.assertFalse(Payment.objects.filter(allocations__invoice=invoice).exists())
        partial = self.pay(self.finance_user, invoice, "20.00")
        self.assertEqual((Invoice.objects.get(pk=invoice.pk).status, CompetitionRegistration.objects.get(pk=self.reg_b1.pk).status),
                         ("PARTIALLY_PAID", "PENDING"))
        full = self.pay(self.finance_user, invoice, "30.00").json()
        self.assertEqual((Invoice.objects.get(pk=invoice.pk).status, CompetitionRegistration.objects.get(pk=self.reg_b1.pk).status),
                         ("PAID", "CONFIRMED"))
        self.assertTrue(full["receipt_number"])
        self.assertEqual(partial.status_code, 201)
        line = self.api(self.finance_user).get(f"/api/invoices/{invoice.pk}/").json()["items"][0]
        self.assertEqual(line["competition_registration"]["status"], "CONFIRMED")


class PaymentInfoTests(FinanceStaffTestCase):
    def test_read_and_manage_follow_the_capabilities(self):
        self.assertEqual(self.api(self.finance_user).patch("/api/payment-info/", {"bank_name": "Test Bank",
            "account_name": "SR Academy", "account_number": "5140 1234 5678"}, format="json").status_code, 200)
        info = AcademyPaymentInfo.current()
        self.assertEqual(history_for(info).first().reason, "Academy payment information updated")
        admin_view = self.api(self.admin_user).get("/api/payment-info/")
        self.assertEqual(admin_view.json()["bank_name"], "Test Bank")
        self.assertNotIn("updated_by_name", admin_view.json())
        self.assertEqual(self.api(self.admin_user).patch("/api/payment-info/", {"bank_name": "X"}, format="json").status_code, 403)
        self.assertEqual(self.api(self.super_user).patch("/api/payment-info/", {"bank_name": "Bank 2"}, format="json").status_code, 200)
        for user in (self.coach_user, self.student_user):
            self.assertEqual(self.api(user).get("/api/payment-info/").status_code, 403)
            self.assertEqual(self.api(user).patch("/api/payment-info/", {"bank_name": "X"}, format="json").status_code, 403)
        self.assertEqual(self.api(self.parent_a_user).patch("/api/payment-info/", {"bank_name": "X"}, format="json").status_code, 403)
        self.assertEqual(AcademyPaymentInfo.current().bank_name, "Bank 2")

    def test_qr_upload_is_validated(self):
        client = self.api(self.finance_user)
        bad = client.patch("/api/payment-info/", {"qr_code": upload("qr.pdf", PDF, "application/pdf")}, format="multipart")
        self.assertEqual(bad.status_code, 400)
        fake = client.patch("/api/payment-info/", {"qr_code": upload("qr.png", b"not an image", "image/png")}, format="multipart")
        self.assertEqual(fake.status_code, 400)
        good = client.patch("/api/payment-info/", {"qr_code": upload("qr.png", PNG, "image/png")}, format="multipart")
        self.assertEqual(good.status_code, 200)
        self.assertTrue(good.json()["qr_code"].startswith("data:image/png;base64,"))
        self.assertNotIn("payment-qr", str(good.json()))           # no storage name


class SensitiveDataTests(FinanceStaffTestCase):
    def test_finance_responses_carry_no_storage_bank_or_identity_details(self):
        self.proof_for_b()
        client = self.api(self.admin_user)
        for path in ("/api/finance/dashboard/", "/api/invoices/", f"/api/invoices/{self.invoice_a.pk}/", "/api/payments/",
                     f"/api/payments/{self.payment_a.pk}/", "/api/receipts/", f"/api/receipts/{self.receipt_a.pk}/",
                     "/api/payment-proofs/", "/api/refunds/"):
            with self.subTest(path=path):
                self.assertFalse(keys_in(client.get(path).json()) & SECRET_KEYS)
