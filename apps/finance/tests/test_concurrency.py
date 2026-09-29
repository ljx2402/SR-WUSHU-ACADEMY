"""Real concurrent transactions against PostgreSQL (skipped on SQLite, which
serialises all writes and has no row locks).

Each worker thread gets its own database connection. A barrier releases them
together so their transactions genuinely overlap.
"""

import threading
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection, connections
from django.test import TransactionTestCase

from apps.academy.models import Program, Student, TrainingClass
from apps.accounts.capabilities import Role
from apps.finance.models import Charge, Invoice, Payment
from apps.finance.services import add_charge, create_invoice, issue_invoice, record_payment
from apps.finance.tests.helpers import issued_invoice

POSTGRES_ONLY = "requires PostgreSQL row locking"


def run_concurrently(*workers):
    """Run callables in parallel threads; return their results or exceptions."""
    barrier = threading.Barrier(len(workers))
    results = [None] * len(workers)

    def wrap(index, fn):
        try:
            barrier.wait(timeout=10)
            results[index] = fn()
        except Exception as exc:  # collected for the assertions
            results[index] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=wrap, args=(i, fn)) for i, fn in enumerate(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest(POSTGRES_ONLY)
        from apps.academy.tests.base import make_user

        self.admin_1 = make_user("desk1", Role.ADMIN)
        self.admin_2 = make_user("desk2", Role.ADMIN)
        program = Program.objects.create(code="p", name="P")
        TrainingClass.objects.create(code="c", name="C", category="SCHOOL", program=program)
        self.student = Student.objects.create(student_no="C1", full_name="Racer", gender="M",
                                              date_of_birth="2012-01-01")

    def test_two_admins_pay_the_same_invoice_at_once(self):
        invoice = issued_invoice([add_charge(self.student, "TUITION", "Monthly fee", "220.00")])
        results = run_concurrently(
            lambda: record_payment([(invoice, "220.00")], "CASH", self.admin_1),
            lambda: record_payment([(invoice, "220.00")], "CASH", self.admin_2),
        )
        succeeded = [r for r in results if isinstance(r, tuple)]
        failed = [r for r in results if isinstance(r, ValidationError)]
        self.assertEqual((len(succeeded), len(failed)), (1, 1), results)
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.amount_paid, invoice.balance_due),
                         (Invoice.Status.PAID, Decimal("220.00"), Decimal("0.00")))
        self.assertEqual(Payment.objects.count(), 1)

    def test_parallel_partial_payments_never_exceed_balance(self):
        invoice = issued_invoice([add_charge(self.student, "TUITION", "Monthly fee", "100.00")])
        results = run_concurrently(*[
            (lambda: record_payment([(invoice, "30.00")], "CASH", self.admin_1)) for _ in range(5)
        ])
        succeeded = [r for r in results if isinstance(r, tuple)]
        self.assertEqual(len(succeeded), 3, results)  # 3 x 30 fits in 100; the 4th and 5th are refused
        invoice.refresh_from_db()
        self.assertEqual((invoice.amount_paid, invoice.balance_due), (Decimal("90.00"), Decimal("10.00")))
        charge = Charge.objects.get(pk=invoice.items.get().charge_id)
        self.assertEqual(charge.amount_paid, Decimal("90.00"))

    def test_same_idempotency_key_at_once_records_one_payment(self):
        invoice = issued_invoice([add_charge(self.student, "TUITION", "Monthly fee", "50.00")])
        results = run_concurrently(*[
            (lambda: record_payment([(invoice, "20.00")], "CASH", self.admin_1, idempotency_key="tap-twice"))
            for _ in range(2)
        ])
        self.assertEqual(Payment.objects.count(), 1, results)
        self.assertTrue(all(isinstance(r, tuple) for r in results), results)  # both callers get the payment
        self.assertEqual(results[0][0].pk, results[1][0].pk)
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_paid, Decimal("20.00"))

    def test_invoice_numbers_unique_and_gapless_under_load(self):
        drafts = [create_invoice(self.student.family, [add_charge(self.student, "OTHER", f"Fee {i}", "1.00")])
                  for i in range(8)]
        results = run_concurrently(*[(lambda d=d: issue_invoice(d)) for d in drafts])
        self.assertTrue(all(isinstance(r, Invoice) for r in results), results)
        numbers = sorted(int(inv.number.rsplit("-", 1)[1]) for inv in Invoice.objects.all())
        self.assertEqual(numbers, list(range(1, 9)))

    def test_same_charge_cannot_be_invoiced_twice_at_once(self):
        charge = add_charge(self.student, "OTHER", "Contested", "10.00")
        results = run_concurrently(*[(lambda: create_invoice(self.student.family, [charge])) for _ in range(2)])
        self.assertEqual(sum(isinstance(r, Invoice) for r in results), 1, results)
        self.assertEqual(charge.invoice_items.filter(is_active=True).count(), 1)
