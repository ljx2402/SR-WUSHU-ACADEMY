"""Payment proofs (parent evidence of a manual payment) and the academy's
payment information.

A proof is never a payment: uploading or accepting one records no money,
changes no invoice, issues no receipt and confirms no competition entry. Only
staff recording the payment does. Direct API requests are used throughout.
"""

import shutil
import tempfile
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.academy.models import StudentAccount
from apps.academy.tests.base import make_user
from apps.accounts.capabilities import Role
from apps.audit.models import AuditLog
from apps.competitions.models import CompetitionRegistration
from apps.finance.models import Invoice, Payment, PaymentProof, Receipt
from apps.finance.services import add_charge
from apps.finance.tests.helpers import issued_invoice

from .test_parent_portal import ParentPortalTestCase

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%fake but well-formed header\n"
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64

TEMP_ROOT = tempfile.mkdtemp(prefix="sr-proofs-")
PRIVATE = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    "private": {"BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": TEMP_ROOT, "base_url": None}},
}


def upload(name="transfer.png", content=PNG, content_type="image/png"):
    return SimpleUploadedFile(name, content, content_type=content_type)


@override_settings(STORAGES=PRIVATE)
class PaymentProofTestCase(ParentPortalTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Family B also has an open invoice (their first one is fully paid).
        cls.invoice_b_open = issued_invoice([add_charge(cls.b2, "UNIFORM", "Uniform", "80.00")])
        cls.coach_user = cls.coach_a_user
        cls.student_user = make_user("student_a1", Role.STUDENT)
        StudentAccount.objects.create(user=cls.student_user, student=cls.a1)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_ROOT, ignore_errors=True)

    def post_proof(self, user, invoice, file=None, **fields):
        data = {"invoice": invoice.pk if hasattr(invoice, "pk") else invoice, "file": file or upload(), **fields}
        return self.api(user).post("/api/payment-proofs/", data, format="multipart")

    def proof_for_b(self):
        response = self.post_proof(self.parent_b_user, self.invoice_b_open)
        self.assertEqual(response.status_code, 201, response.content)
        return PaymentProof.objects.get(pk=response.json()["id"])


class ParentUploadTests(PaymentProofTestCase):
    def test_parent_uploads_proof_for_own_invoice_and_nothing_financial_changes(self):
        before = Invoice.objects.get(pk=self.invoice_a.pk)
        payments, receipts = Payment.objects.count(), Receipt.objects.count()
        response = self.post_proof(self.parent_a_user, self.invoice_a, amount_claimed="250.00",
                                   payment_date=self.today.isoformat(), reference="MBB998877", note="Balance")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["invoice_number"], body["amount_claimed"]),
                         ("PENDING_REVIEW", self.invoice_a.number, "250.00"))
        for hidden in ("file", "sha256", "reviewed_by_name"):
            self.assertNotIn(hidden, body)
        after = Invoice.objects.get(pk=self.invoice_a.pk)
        self.assertEqual((after.status, after.amount_paid, after.balance_due),
                         (before.status, before.amount_paid, before.balance_due))
        self.assertEqual((Payment.objects.count(), Receipt.objects.count()), (payments, receipts))

    def test_parent_views_and_downloads_own_proof(self):
        proof_id = self.post_proof(self.parent_a_user, self.invoice_a).json()["id"]
        client = self.api(self.parent_a_user)
        listed = client.get("/api/payment-proofs/", {"invoice": self.invoice_a.pk}).json()["results"]
        self.assertEqual([p["id"] for p in listed], [proof_id])
        self.assertEqual(client.get(f"/api/payment-proofs/{proof_id}/").json()["status"], "PENDING_REVIEW")
        download = client.get(f"/api/payment-proofs/{proof_id}/file/")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content), PNG)
        self.assertEqual(download["Content-Type"], "image/png")
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertEqual(download["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", download["Content-Security-Policy"])
        self.assertIn("no-store", download["Cache-Control"])

    def test_repeated_uploads_keep_history_and_are_bounded(self):
        for _ in range(5):
            self.assertEqual(self.post_proof(self.parent_a_user, self.invoice_a).status_code, 201)
        self.assertEqual(PaymentProof.objects.filter(invoice=self.invoice_a).count(), 5)
        sixth = self.post_proof(self.parent_a_user, self.invoice_a)
        self.assertEqual(sixth.status_code, 400)
        self.assertIn("waiting for review", str(sixth.json()))

    def test_only_open_invoices_take_proofs(self):
        # Family B's first invoice is fully paid.
        response = self.post_proof(self.parent_b_user, self.invoice_b)
        self.assertEqual(response.status_code, 400)
        self.assertIn("not awaiting payment", str(response.json()))

    def test_future_payment_dates_are_refused(self):
        response = self.post_proof(self.parent_a_user, self.invoice_a, payment_date="2999-01-01")
        self.assertEqual(response.status_code, 400)
        self.assertIn("payment_date", response.json())


class FileSecurityTests(PaymentProofTestCase):
    def test_accepted_types(self):
        for name, content in (("proof.pdf", PDF), ("proof.jpg", JPG), ("proof.JPEG", JPG), ("proof.png", PNG)):
            with self.subTest(name=name):
                self.assertEqual(self.post_proof(self.parent_a_user, self.invoice_a,
                                                 file=upload(name, content)).status_code, 201)
                PaymentProof.objects.filter(invoice=self.invoice_a).update(status="ACCEPTED",
                                                                          reviewed_at="2026-01-01T00:00Z")

    def test_executables_scripts_and_disguised_files_are_refused(self):
        cases = [
            ("setup.exe", b"MZ\x90\x00" + b"\x00" * 40),
            ("page.html", b"<html><script>alert(1)</script></html>"),
            ("image.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"),
            ("fake.png", b"<html><script>alert(1)</script></html>"),   # right extension, wrong content
            ("fake.pdf", b"MZ\x90\x00 not a pdf"),
            ("noextension", PNG),
            ("empty.png", b""),
        ]
        for name, content in cases:
            with self.subTest(name=name):
                response = self.post_proof(self.parent_a_user, self.invoice_a, file=upload(name, content))
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("file", response.json())
        self.assertFalse(PaymentProof.objects.exists())

    @override_settings(PAYMENT_PROOF_MAX_BYTES=100)
    def test_size_limit(self):
        response = self.post_proof(self.parent_a_user, self.invoice_a, file=upload("big.png", PNG + b"\x00" * 200))
        self.assertEqual(response.status_code, 400)
        self.assertIn("too large", str(response.json()))

    def test_stored_name_is_generated_and_label_is_sanitized(self):
        response = self.post_proof(self.parent_a_user, self.invoice_a,
                                   file=upload("../../etc/pass wd<script>.png", PNG))
        proof = PaymentProof.objects.get(pk=response.json()["id"])
        self.assertRegex(proof.file.name, r"^payment-proofs/\d{4}/\d{2}/[0-9a-f]{32}\.png$")
        self.assertNotIn("/", proof.original_name)
        self.assertNotIn("<", proof.original_name)
        self.assertTrue(proof.original_name.endswith(".png"))
        self.assertEqual(len(proof.sha256), 64)


class ParentIdorTests(PaymentProofTestCase):
    def test_parent_cannot_upload_for_another_familys_invoice(self):
        for bad_file in (upload(), upload("x.exe", b"MZ")):
            response = self.post_proof(self.parent_a_user, self.invoice_b_open, file=bad_file)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.post_proof(self.parent_a_user, 999999).status_code, 404)
        self.assertEqual(self.post_proof(self.parent_a_user, "abc").status_code, 400)
        self.assertFalse(PaymentProof.objects.filter(invoice=self.invoice_b_open).exists())

    def test_parent_cannot_see_another_familys_proof(self):
        proof = self.proof_for_b()
        client = self.api(self.parent_a_user)
        self.assertEqual(client.get(f"/api/payment-proofs/{proof.pk}/").status_code, 404)
        self.assertEqual(client.get(f"/api/payment-proofs/{proof.pk}/file/").status_code, 404)
        self.assertEqual(client.get("/api/payment-proofs/").json()["count"], 0)
        self.assertEqual(client.get("/api/payment-proofs/", {"invoice": self.invoice_b_open.pk}).json()["count"], 0)
        self.assertEqual(client.get("/api/payment-proofs/", {"family": self.family_b.pk}).json()["count"], 0)

    def test_parent_cannot_review_pay_or_issue_anything(self):
        own = self.post_proof(self.parent_a_user, self.invoice_a).json()["id"]
        other = self.proof_for_b().pk
        client = self.api(self.parent_a_user)
        cases = [
            ("post", f"/api/payment-proofs/{own}/accept/", {}, 403),
            ("post", f"/api/payment-proofs/{own}/reject/", {"reason": "x"}, 403),
            ("post", f"/api/payment-proofs/{other}/accept/", {}, 403),
            ("patch", f"/api/payment-proofs/{own}/", {"status": "ACCEPTED"}, 405),
            ("delete", f"/api/payment-proofs/{own}/", None, 405),
            ("post", "/api/payments/", {"amount": "250.00", "method": "CASH",
                                        "allocations": [{"invoice": self.invoice_a.pk, "amount": "250.00"}]}, 403),
            ("post", f"/api/payments/{self.payment_a.pk}/void/", {"reason": "x"}, 403),
            ("patch", f"/api/payments/{self.payment_a.pk}/", {"amount": "1.00"}, 405),
            ("post", "/api/receipts/", {}, 405),
            ("patch", f"/api/receipts/{self.receipt_a.pk}/", {"total": "1.00"}, 405),
            ("post", f"/api/invoices/{self.invoice_a.pk}/issue/", {}, 403),
            ("post", f"/api/invoices/{self.invoice_a.pk}/void/", {"reason": "x"}, 403),
            ("patch", "/api/payment-info/", {"bank_name": "Evil Bank"}, 403),
        ]
        for method, url, data, expected in cases:
            with self.subTest(method=method, url=url):
                call = getattr(client, method)
                response = call(url, data, format="json") if data is not None else call(url)
                self.assertEqual(response.status_code, expected, response.content[:200])
        self.assertEqual(PaymentProof.objects.get(pk=own).status, "PENDING_REVIEW")
        self.assertEqual(Payment.objects.filter(family=self.family_a).count(), 1)

    def test_proof_records_cannot_be_rewritten_or_deleted(self):
        proof = PaymentProof.objects.get(pk=self.post_proof(self.parent_a_user, self.invoice_a).json()["id"])
        proof.amount_claimed = Decimal("1.00")
        with self.assertRaises(PermissionDenied):
            proof.save()
        with self.assertRaises(PermissionDenied):
            proof.delete()
        with self.assertRaises(PermissionDenied):
            PaymentProof.objects.all().delete()


class StaffReviewTests(PaymentProofTestCase):
    def test_finance_admin_sees_all_proofs_with_review_details(self):
        self.post_proof(self.parent_a_user, self.invoice_a)
        self.proof_for_b()
        body = self.api(self.finance_user).get("/api/payment-proofs/").json()
        self.assertEqual(body["count"], 2)
        self.assertNotIn("sha256", body["results"][0])  # never returned (Phase 6F)
        pending = self.api(self.finance_user).get("/api/payment-proofs/", {"status": "PENDING_REVIEW"}).json()
        self.assertEqual(pending["count"], 2)

    def test_reject_requires_a_reason_and_happens_once(self):
        proof_id = self.post_proof(self.parent_a_user, self.invoice_a).json()["id"]
        finance = self.api(self.finance_user)
        self.assertEqual(finance.post(f"/api/payment-proofs/{proof_id}/reject/", {"reason": "  "}).status_code, 400)
        rejected = finance.post(f"/api/payment-proofs/{proof_id}/reject/",
                                {"reason": "Payment amount does not match invoice."})
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual((rejected.json()["status"], rejected.json()["review_note"]),
                         ("REJECTED", "Payment amount does not match invoice."))
        self.assertEqual(finance.post(f"/api/payment-proofs/{proof_id}/accept/", {}).status_code, 400)
        # The parent sees the outcome and the reason, not the reviewer's name.
        seen = self.api(self.parent_a_user).get(f"/api/payment-proofs/{proof_id}/").json()
        self.assertEqual((seen["status"], seen["review_note"]), ("REJECTED", "Payment amount does not match invoice."))
        self.assertNotIn("reviewed_by_name", seen)

    def test_accepting_records_no_payment_and_can_link_the_recorded_one(self):
        proof_id = self.post_proof(self.parent_a_user, self.invoice_a, amount_claimed="250.00").json()["id"]
        admin = self.api(self.admin_user)            # ADMIN reviews too (front desk)
        payments = Payment.objects.count()
        other_family_payment = self.payment_b
        bad = admin.post(f"/api/payment-proofs/{proof_id}/accept/", {"payment": other_family_payment.pk},
                         format="json")
        self.assertEqual(bad.status_code, 400)
        accepted = admin.post(f"/api/payment-proofs/{proof_id}/accept/", {"note": "Seen in bank statement"},
                              format="json")
        self.assertEqual(accepted.json()["status"], "ACCEPTED")
        self.assertEqual(Payment.objects.count(), payments)
        self.assertEqual(Invoice.objects.get(pk=self.invoice_a.pk).status, "PARTIALLY_PAID")
        # Staff record the payment through the existing operation: that is what pays the invoice.
        recorded = admin.post("/api/payments/", {"amount": "250.00", "method": "BANK_TRANSFER",
                                                 "reference": "MBB998877",
                                                 "allocations": [{"invoice": self.invoice_a.pk, "amount": "250.00"}]},
                              format="json")
        self.assertEqual(recorded.status_code, 201, recorded.content)
        self.assertEqual(Invoice.objects.get(pk=self.invoice_a.pk).status, "PAID")
        self.assertTrue(Receipt.objects.filter(payment_id=recorded.json()["id"]).exists())

    def test_roles_without_review_capability_are_refused(self):
        proof = self.proof_for_b()
        for user in (self.coach_user, self.student_user):
            client = self.api(user)
            with self.subTest(user=user.username):
                self.assertEqual(client.get("/api/payment-proofs/").status_code, 403)
                self.assertEqual(client.get(f"/api/payment-proofs/{proof.pk}/file/").status_code, 403)
                self.assertEqual(client.post(f"/api/payment-proofs/{proof.pk}/accept/", {}).status_code, 403)
                self.assertEqual(client.get("/api/payment-info/").status_code, 403)
                self.assertEqual(self.post_proof(user, self.invoice_a).status_code, 403)
        # Staff review proofs; they do not upload them on a family's behalf.
        self.assertEqual(self.post_proof(self.finance_user, self.invoice_a).status_code, 403)
        # The super admin can review.
        self.assertEqual(self.api(self.super_user).post(f"/api/payment-proofs/{proof.pk}/reject/",
                                                        {"reason": "Unclear"}).status_code, 200)

    def test_review_is_audited_without_file_contents(self):
        proof_id = self.post_proof(self.parent_a_user, self.invoice_a).json()["id"]
        self.api(self.finance_user).post(f"/api/payment-proofs/{proof_id}/reject/",
                                         {"reason": "Transfer proof is unclear."})
        entries = AuditLog.objects.filter(object_repr__startswith="Payment proof").order_by("id")
        self.assertEqual([e.action for e in entries], ["CREATE", "UPDATE"])
        created, rejected = entries
        self.assertEqual((created.actor, created.reason), (self.parent_a_user, "Payment proof uploaded"))
        self.assertEqual(rejected.actor, self.finance_user)
        self.assertIn("Transfer proof is unclear.", rejected.reason)
        self.assertEqual(rejected.changes["status"]["to"], "REJECTED")
        for entry in entries:
            self.assertNotIn("file", entry.changes)
            self.assertNotIn("sha256", entry.changes)


class CompetitionProofTests(PaymentProofTestCase):
    def test_proof_never_confirms_a_registration_only_the_recorded_payment_does(self):
        parent = self.api(self.parent_a_user)
        registration = parent.post("/api/competition-registrations/",
                                   {"student": self.a1.pk, "event": self.event.pk}, format="json").json()
        invoice = Invoice.objects.get(pk=registration["invoice"]["id"])
        first = self.post_proof(self.parent_a_user, invoice, amount_claimed="50.00").json()["id"]
        finance = self.api(self.finance_user)
        finance.post(f"/api/payment-proofs/{first}/reject/", {"reason": "Transfer proof is unclear."})
        self.assertEqual(CompetitionRegistration.objects.get(pk=registration["id"]).status, "PENDING")
        second = self.post_proof(self.parent_a_user, invoice, amount_claimed="50.00").json()["id"]
        finance.post(f"/api/payment-proofs/{second}/accept/", {}, format="json")
        self.assertEqual(CompetitionRegistration.objects.get(pk=registration["id"]).status, "PENDING")
        # A partial payment leaves it pending; the full payment confirms it.
        finance.post("/api/payments/", {"amount": "20.00", "method": "BANK_TRANSFER",
                                        "allocations": [{"invoice": invoice.pk, "amount": "20.00"}]}, format="json")
        self.assertEqual(CompetitionRegistration.objects.get(pk=registration["id"]).status, "PENDING")
        paid = finance.post("/api/payments/", {"amount": "30.00", "method": "BANK_TRANSFER",
                                               "allocations": [{"invoice": invoice.pk, "amount": "30.00"}]},
                            format="json")
        self.assertEqual(CompetitionRegistration.objects.get(pk=registration["id"]).status, "CONFIRMED")
        self.assertIsNotNone(paid.json()["receipt_number"])
        # History: both proofs remain, with their outcomes.
        history = parent.get("/api/payment-proofs/", {"invoice": invoice.pk}).json()["results"]
        self.assertEqual(sorted(p["status"] for p in history), ["ACCEPTED", "REJECTED"])


class PaymentInfoTests(PaymentProofTestCase):
    def test_parents_read_payment_information_finance_manages_it(self):
        parent = self.api(self.parent_a_user)
        empty = parent.get("/api/payment-info/").json()
        self.assertEqual((empty["configured"], empty["qr_code"]), (False, None))
        finance = self.api(self.finance_user)
        response = finance.patch("/api/payment-info/", {
            "bank_name": "Maybank", "account_name": "SR Wushu Academy", "account_number": "5140 1234 5678",
            "instructions": "Transfer the amount due, then upload your proof.",
            "qr_code": upload("duitnow.png", PNG)}, format="multipart")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["updated_by_name"], "finance")
        info = parent.get("/api/payment-info/").json()
        self.assertEqual((info["configured"], info["bank_name"], info["account_number"]),
                         (True, "Maybank", "5140 1234 5678"))
        self.assertTrue(info["qr_code"].startswith("data:image/png;base64,"))
        self.assertNotIn("updated_by_name", info)   # staff-only detail
        self.assertTrue(AuditLog.objects.filter(reason="Academy payment information updated").exists())

    def test_only_payment_info_managers_change_it_and_the_qr_is_validated(self):
        self.assertEqual(self.api(self.admin_user).patch("/api/payment-info/", {"bank_name": "X"},
                                                         format="json").status_code, 403)
        finance = self.api(self.finance_user)
        bad = finance.patch("/api/payment-info/", {"qr_code": upload("qr.png", b"<svg onload=alert(1)>")},
                            format="multipart")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("qr_code", bad.json())
        pdf = finance.patch("/api/payment-info/", {"qr_code": upload("qr.pdf", PDF)}, format="multipart")
        self.assertEqual(pdf.status_code, 400)
        self.assertEqual(self.api(self.super_user).patch("/api/payment-info/", {"bank_name": "CIMB"},
                                                         format="json").status_code, 200)
