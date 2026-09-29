"""finance.0002 keeps pre-invoice payment history: forward backfill and rollback."""

import datetime
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("finance", "0001_initial")]
AFTER = [("finance", "0002_invoices_payments")]
FAMILIES = ("academy", "0004_family")  # stays applied while finance moves back and forth


class LegacyPaymentMigrationTests(TransactionTestCase):
    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets + [FAMILIES]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_backfill_and_rollback(self):
        apps = self.migrate(BEFORE)
        Family = apps.get_model("academy", "Family")
        Student = apps.get_model("academy", "Student")
        Charge = apps.get_model("finance", "Charge")
        Payment = apps.get_model("finance", "Payment")
        PaymentAllocation = apps.get_model("finance", "PaymentAllocation")
        ReceiptSequence = apps.get_model("finance", "ReceiptSequence")
        Receipt = apps.get_model("finance", "Receipt")

        family = Family.objects.create(name="Legacy family")
        ali = Student.objects.create(student_no="L1", full_name="Ali", gender="M", family=family,
                                     date_of_birth=datetime.date(2014, 1, 1))
        fee = Charge.objects.create(student=ali, fee_type="TUITION", description="Monthly fee", unit_amount=Decimal("220"),
                                    amount=Decimal("220.00"), status="PARTIAL")
        uniform = Charge.objects.create(student=ali, fee_type="UNIFORM", description="Uniform", unit_amount=Decimal("80"),
                                        amount=Decimal("80.00"), status="PAID")
        payment = Payment.objects.create(payer_name="Mum", amount=Decimal("180.00"), method="CASH",
                                         received_on=datetime.date(2026, 9, 1))
        PaymentAllocation.objects.create(payment=payment, charge=fee, amount=Decimal("100.00"))
        PaymentAllocation.objects.create(payment=payment, charge=uniform, amount=Decimal("80.00"))
        ReceiptSequence.objects.create(year=2026, last_number=7)
        Receipt.objects.create(number="SRWA-2026-000007", payment=payment, issued_at=datetime.datetime(
            2026, 9, 1, 4, 0, tzinfo=datetime.timezone.utc), payer_name="Mum", total=Decimal("180.00"),
            content={"number": "SRWA-2026-000007"})

        apps = self.migrate(AFTER)
        Payment = apps.get_model("finance", "Payment")
        Invoice = apps.get_model("finance", "Invoice")
        DocumentSequence = apps.get_model("finance", "DocumentSequence")
        payment = Payment.objects.get()
        self.assertEqual(payment.number, "PAY-2026-000001")
        self.assertEqual(payment.family_id, family.pk)
        self.assertEqual(payment.received_at, datetime.datetime(2026, 9, 1, 4, 0, tzinfo=datetime.timezone.utc))  # 12:00 KL
        invoice = Invoice.objects.get()
        self.assertEqual((invoice.status, invoice.total, invoice.amount_paid, invoice.balance_due),
                         ("PARTIALLY_PAID", Decimal("300.00"), Decimal("180.00"), Decimal("120.00")))
        self.assertTrue(invoice.number.startswith("INV-"))
        self.assertEqual({(a.invoice_id, a.invoice_item.description) for a in payment.allocations.all()},
                         {(invoice.pk, "Monthly fee"), (invoice.pk, "Uniform")})
        self.assertEqual(DocumentSequence.objects.get(doc_type="RECEIPT", year=2026).last_number, 7)
        self.assertEqual(apps.get_model("finance", "Receipt").objects.get().number, "SRWA-2026-000007")

        apps = self.migrate(BEFORE)
        Payment = apps.get_model("finance", "Payment")
        self.assertEqual(Payment.objects.get().received_on, datetime.date(2026, 9, 1))
        self.assertEqual(apps.get_model("finance", "ReceiptSequence").objects.get(year=2026).last_number, 7)
        self.assertEqual(apps.get_model("finance", "PaymentAllocation").objects.count(), 2)
