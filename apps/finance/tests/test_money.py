"""Money precision rules (unit tests + a few stored round trips)."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from apps.academy.tests.base import AcademyTestCase
from apps.finance.money import MAX_AMOUNT, round_money, to_money
from apps.finance.services import add_charge
from apps.finance.tests.helpers import issued_invoice


class ToMoneyTests(SimpleTestCase):
    def test_valid_amounts(self):
        for raw, expected in [("0", "0.00"), ("0.01", "0.01"), ("0.10", "0.10"), ("0.1", "0.10"), ("1", "1.00"),
                              ("99.99", "99.99"), ("100", "100.00"), ("9999.99", "9999.99"), (Decimal("5.5"), "5.50"),
                              (7, "7.00"), ("99999999.99", "99999999.99")]:
            with self.subTest(raw=raw):
                value = to_money(raw)
                self.assertEqual(value, Decimal(expected))
                self.assertEqual(value.as_tuple().exponent, -2)

    def test_rejected_amounts(self):
        for raw in ("-0.01", "-1", "1.005", "0.001", "abc", "", "NaN", "Infinity", "100000000.00", 1.5, 0.1, True):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                to_money(raw)

    def test_zero_can_be_refused(self):
        with self.assertRaises(ValidationError):
            to_money("0.00", allow_zero=False)
        self.assertEqual(to_money("0.01", allow_zero=False), Decimal("0.01"))

    def test_maximum(self):
        self.assertEqual(MAX_AMOUNT, Decimal("99999999.99"))

    def test_computed_amounts_round_half_up(self):
        for raw, expected in [("0.005", "0.01"), ("0.004", "0.00"), ("2.675", "2.68"), ("2.665", "2.67"),
                              ("1.0049", "1.00"), ("3.335", "3.34")]:
            with self.subTest(raw=raw):
                self.assertEqual(round_money(Decimal(raw)), Decimal(expected))


class StoredMoneyTests(AcademyTestCase):
    def test_quantity_times_unit_and_discount(self):
        charge = add_charge(self.student_1, "OTHER", "3 x 33.33", "33.33", quantity=3)
        self.assertEqual(charge.amount, Decimal("99.99"))
        charge = add_charge(self.student_1, "OTHER", "Half session", "25.25", quantity=Decimal("0.5"))
        self.assertEqual(charge.amount, Decimal("12.63"))  # 12.625 rounds half-up

    def test_invoice_totals_are_exact_sums(self):
        charges = [add_charge(self.student_1, "OTHER", f"Ten sen {i}", "0.10") for i in range(33)]
        invoice = issued_invoice(charges)
        invoice.refresh_from_db()
        self.assertEqual((invoice.subtotal, invoice.total, invoice.balance_due),
                         (Decimal("3.30"), Decimal("3.30"), Decimal("3.30")))

    def test_invalid_charge_amounts_rejected(self):
        for raw in ("-5.00", "1.001", 12.5):
            with self.subTest(raw=raw), self.assertRaises(ValidationError):
                add_charge(self.student_1, "OTHER", "Bad", raw)
        with self.assertRaises(ValidationError):
            add_charge(self.student_1, "OTHER", "Discount too big", "10.00", discount="10.01")
        free = add_charge(self.student_1, "OTHER", "Free item", "0")
        self.assertEqual(free.amount, Decimal("0.00"))
