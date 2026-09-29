"""Payments, allocations, receipts, voids, exceptional refunds, time boundaries."""

import datetime
import re
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from apps.academy.tests.base import AcademyTestCase
from apps.audit.models import AuditLog
from apps.finance.models import Charge, Invoice, Payment, PaymentAllocation, Receipt, Refund
from apps.finance.services import add_charge, record_exceptional_refund, record_payment, void_payment
from apps.finance.tests.helpers import issued_invoice, make_family
from apps.reports.reports import payments_report

KL = ZoneInfo("Asia/Kuala_Lumpur")


class PaymentTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.family = make_family(cls.student_1, cls.student_2, name="Ali & Mei family")

    def setUp(self):
        self.ali = add_charge(self.student_1, "TUITION", "Monthly fee", "220.00")
        self.mei = add_charge(self.student_2, "TUITION", "Monthly fee", "280.00")
        self.invoice_a = issued_invoice([self.ali])
        self.invoice_b = issued_invoice([self.mei])

    def test_admin_records_full_payment(self):
        payment, receipt = record_payment([(self.invoice_a, "220.00")], "CASH", self.admin_user, payer_name="Mum")
        self.invoice_a.refresh_from_db()
        self.assertEqual((self.invoice_a.status, self.invoice_a.balance_due), (Invoice.Status.PAID, Decimal("0.00")))
        self.assertEqual((payment.family, payment.received_by, payment.status), (self.family, self.admin_user, "VALID"))
        self.assertTrue(re.fullmatch(r"PAY-\d{4}-\d{6}", payment.number))
        self.assertEqual(receipt.payment, payment)
        self.assertEqual(Charge.objects.get(pk=self.ali.pk).status, Charge.Status.PAID)

    def test_one_payment_across_two_invoices(self):
        payment, receipt = record_payment([(self.invoice_a, "220.00"), (self.invoice_b, "280.00")], "BANK_TRANSFER",
                                          self.admin_user, amount="500.00", reference="MBB123")
        allocations = {(a.invoice_id, a.amount) for a in payment.allocations.all()}
        self.assertEqual(allocations, {(self.invoice_a.pk, Decimal("220.00")), (self.invoice_b.pk, Decimal("280.00"))})
        self.assertEqual(sum(a.amount for a in payment.allocations.all()), payment.amount)
        self.assertEqual({(line["student_name"], line["invoice_number"], line["amount_paid"])
                          for line in receipt.content["lines"]},
                         {("Ali", self.invoice_a.number, "220.00"), ("Mei", self.invoice_b.number, "280.00")})
        self.assertEqual(receipt.content["students"], ["Ali (S1)", "Mei (S2)"])

    def test_partial_payments_then_settled(self):
        record_payment([(self.invoice_a, "0.01")], "CASH", self.admin_user)
        record_payment([(self.invoice_a, "119.99")], "CASH", self.admin_user)
        self.invoice_a.refresh_from_db()
        self.assertEqual((self.invoice_a.status, self.invoice_a.amount_paid, self.invoice_a.balance_due),
                         (Invoice.Status.PARTIALLY_PAID, Decimal("120.00"), Decimal("100.00")))
        record_payment([(self.invoice_a, "100.00")], "DUITNOW", self.admin_user)
        self.invoice_a.refresh_from_db()
        self.assertEqual(self.invoice_a.status, Invoice.Status.PAID)

    def test_family_invoice_lines_paid_in_order(self):
        family_invoice = issued_invoice([add_charge(self.student_1, "UNIFORM", "Uniform", "80.00"),
                                         add_charge(self.student_2, "UNIFORM", "Uniform", "90.00")])
        payment, _ = record_payment([(family_invoice, "100.00")], "CASH", self.admin_user)
        paid = {a.invoice_item.student_name: a.amount for a in payment.allocations.all()}
        self.assertEqual(paid, {"Ali": Decimal("80.00"), "Mei": Decimal("20.00")})

    def test_rejections(self):
        cases = [
            ([(self.invoice_a, "220.01")], {}),                        # above balance
            ([(self.invoice_a, "100.00")], {"amount": "150.00"}),     # unallocated remainder / overpayment
            ([(self.invoice_a, "0.00")], {}),                          # zero
            ([(self.invoice_a, "-5.00")], {}),                         # negative
            ([(self.invoice_a, "10.001")], {}),                        # sub-sen
            ([(self.invoice_a, "10.00"), (self.invoice_a, "10.00")], {}),  # same invoice twice
            ([], {}),
        ]
        for allocations, kwargs in cases:
            with self.subTest(allocations=allocations, kwargs=kwargs), self.assertRaises(ValidationError):
                record_payment(allocations, "CASH", self.admin_user, **kwargs)
        with self.assertRaises(ValidationError):
            record_payment([(self.invoice_a, "10.00")], "BITCOIN", self.admin_user)
        with self.assertRaises(ValidationError):
            record_payment([(self.invoice_a, "10.00")], "CASH", self.admin_user,
                           received_at=datetime.datetime(2026, 9, 30, 12, 0))  # naive datetime
        self.assertFalse(Payment.objects.exists())

    def test_only_issued_invoices_take_payments(self):
        from apps.finance.services import create_invoice, void_invoice

        draft = create_invoice(self.family, [add_charge(self.student_1, "OTHER", "Draft", "5.00")])
        with self.assertRaises(ValidationError):
            record_payment([(draft, "5.00")], "CASH", self.admin_user)
        void_invoice(self.invoice_b, "Wrong amount", self.finance_user)
        with self.assertRaises(ValidationError):
            record_payment([(self.invoice_b, "10.00")], "CASH", self.admin_user)
        record_payment([(self.invoice_a, "220.00")], "CASH", self.admin_user)
        with self.assertRaises(ValidationError):
            record_payment([(self.invoice_a, "0.01")], "CASH", self.admin_user)  # already paid

    def test_idempotency_key_prevents_duplicates(self):
        first, receipt = record_payment([(self.invoice_a, "100.00")], "CASH", self.admin_user, idempotency_key="k-1")
        again, receipt_again = record_payment([(self.invoice_a, "100.00")], "CASH", self.admin_user,
                                              idempotency_key="k-1")
        self.assertEqual((first.pk, receipt.pk), (again.pk, receipt_again.pk))
        self.assertEqual(Payment.objects.count(), 1)
        self.invoice_a.refresh_from_db()
        self.assertEqual(self.invoice_a.amount_paid, Decimal("100.00"))

    def test_who_may_record_and_void(self):
        for user in (self.coach_a_user, self.parent_1_user):
            with self.assertRaises(PermissionDenied):
                record_payment([(self.invoice_a, "10.00")], "CASH", user)
        payment, _ = record_payment([(self.invoice_a, "10.00")], "CASH", self.admin_user)
        with self.assertRaises(PermissionDenied):
            void_payment(payment, "keying error", self.admin_user)  # voiding is a finance action
        void_payment(payment, "keying error", self.finance_user)

    def test_receipt_only_from_payment(self):
        self.assertFalse(Receipt.objects.exists())  # issuing invoices creates no receipts
        _, first = record_payment([(self.invoice_a, "220.00")], "CASH", self.admin_user)
        _, second = record_payment([(self.invoice_b, "280.00")], "CASH", self.admin_user)
        year = timezone.localtime(first.issued_at).year
        self.assertEqual([first.number, second.number], [f"SRWA-{year}-000001", f"SRWA-{year}-000002"])
        self.assertEqual((first.total, first.content["payment_method"]), (Decimal("220.00"), "Cash"))
        self.assertNotIn("parent", str(first.content).lower().replace("payer_reference", ""))

    def test_payment_and_allocation_immutable(self):
        payment, _ = record_payment([(self.invoice_a, "220.00")], "CASH", self.admin_user)
        for field, value in (("amount", Decimal("1.00")), ("method", "CARD"), ("received_at", timezone.now())):
            fresh = Payment.objects.get(pk=payment.pk)
            setattr(fresh, field, value)
            with self.assertRaises(ValidationError):
                fresh.save()
        allocation = payment.allocations.get()
        allocation.amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            allocation.save()
        with self.assertRaises(PermissionDenied):
            allocation.delete()
        with self.assertRaises(PermissionDenied):
            payment.delete()

    def test_void_reopens_invoices(self):
        payment, receipt = record_payment([(self.invoice_a, "220.00"), (self.invoice_b, "80.00")], "CHEQUE",
                                          self.admin_user)
        void_payment(payment, "Cheque bounced", self.finance_user)
        for invoice, status in ((self.invoice_a, Invoice.Status.ISSUED), (self.invoice_b, Invoice.Status.ISSUED)):
            invoice.refresh_from_db()
            self.assertEqual((invoice.status, invoice.amount_paid), (status, Decimal("0.00")))
        self.assertTrue(Receipt.objects.get(pk=receipt.pk).is_void)
        self.assertEqual(Payment.objects.get(pk=payment.pk).amount, Decimal("300.00"))  # history kept
        with self.assertRaises(ValidationError):
            void_payment(Payment.objects.get(pk=payment.pk), "again", self.finance_user)

    def test_exceptional_refund(self):
        payment, receipt = record_payment([(self.invoice_a, "220.00")], "CASH", self.admin_user)
        allocation = payment.allocations.get()
        for user in (self.coach_a_user, self.parent_1_user):
            with self.assertRaises(PermissionDenied):
                record_exceptional_refund(allocation, "50.00", "x", user)
        with self.assertRaises(ValidationError):
            record_exceptional_refund(allocation, "50.00", "  ", self.admin_user)  # reason required
        refund = record_exceptional_refund(allocation, "50.00", "Injury, approved by head coach", self.admin_user)
        self.assertTrue(re.fullmatch(r"RFD-\d{4}-\d{6}", refund.number))
        with self.assertRaises(ValidationError):
            record_exceptional_refund(allocation, "170.01", "too much", self.finance_user)
        record_exceptional_refund(allocation, "170.00", "Balance approved", self.finance_user)
        self.invoice_a.refresh_from_db()
        self.assertEqual((self.invoice_a.status, self.invoice_a.amount_paid, self.invoice_a.amount_refunded),
                         (Invoice.Status.PAID, Decimal("220.00"), Decimal("220.00")))
        # The original records are untouched.
        self.assertEqual((Payment.objects.get(pk=payment.pk).amount, Receipt.objects.get(pk=receipt.pk).total),
                         (Decimal("220.00"), Decimal("220.00")))
        self.assertFalse(Receipt.objects.get(pk=receipt.pk).is_void)
        with self.assertRaises(ValidationError):
            void_payment(payment, "after refund", self.finance_user)
        refund.amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            refund.save()
        entry = AuditLog.objects.filter(content_type__model="refund", object_id=str(refund.pk)).get()
        self.assertEqual((entry.actor, entry.reason), (self.admin_user, "Injury, approved by head coach"))

    def test_payment_operations_are_audited(self):
        payment, _ = record_payment([(self.invoice_a, "220.00")], "CASH", self.admin_user)
        void_payment(payment, "Entered on wrong family", self.finance_user)
        finance = AuditLog.objects.filter(category="FINANCE")
        self.assertTrue(finance.filter(content_type__model="payment", action="CREATE", actor=self.admin_user).exists())
        self.assertTrue(finance.filter(content_type__model="paymentallocation", action="CREATE").exists())
        self.assertTrue(finance.filter(content_type__model="receipt", action="EVENT").exists())
        self.assertTrue(finance.filter(content_type__model="payment", action="UPDATE",
                                       reason="Entered on wrong family", actor=self.finance_user).exists())


class MalaysiaTimeTests(AcademyTestCase):
    """Payments carry an aware timestamp; their date is the Kuala Lumpur date."""

    def pay_at(self, when):
        invoice = issued_invoice([add_charge(self.student_1, "OTHER", f"Fee {when}", "10.00")])
        payment, _ = record_payment([(invoice, "10.00")], "CASH", self.admin_user, received_at=when)
        return Payment.objects.get(pk=payment.pk)

    def test_date_boundaries(self):
        before = self.pay_at(datetime.datetime(2026, 9, 30, 23, 59, tzinfo=KL))  # 15:59 UTC
        at_midnight = self.pay_at(datetime.datetime(2026, 10, 1, 0, 0, tzinfo=KL))  # 16:00 UTC on 30 Sep!
        self.assertEqual(before.received_on, datetime.date(2026, 9, 30))
        self.assertEqual(at_midnight.received_on, datetime.date(2026, 10, 1))
        self.assertEqual(at_midnight.received_at.astimezone(datetime.timezone.utc).date(), datetime.date(2026, 9, 30))
        self.assertEqual(set(Payment.objects.filter(received_at__date=datetime.date(2026, 9, 30))), {before})
        self.assertEqual(set(Payment.objects.filter(received_at__date=datetime.date(2026, 10, 1))), {at_midnight})
        _, rows = payments_report({"start": "2026-10-01", "end": "2026-10-01"})
        self.assertEqual([r[0] for r in rows], [at_midnight.number])
        _, rows = payments_report({"start": "2026-09-30", "end": "2026-09-30"})
        self.assertEqual([r[0] for r in rows], [before.number])

    def test_receipt_shows_local_time(self):
        payment = self.pay_at(datetime.datetime(2026, 9, 30, 23, 59, tzinfo=KL))
        self.assertTrue(payment.receipt.content["payment_date"].startswith("2026-09-30T23:59"))

    def test_allocations_sum_to_payment(self):
        payment = self.pay_at(timezone.now())
        self.assertEqual(sum(a.amount for a in PaymentAllocation.objects.filter(payment=payment)), payment.amount)
        self.assertFalse(Refund.objects.exists())
