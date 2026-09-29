import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError

from apps.academy.tests.base import AcademyTestCase
from apps.attendance.services import mark_attendance
from apps.audit.models import AuditLog
from apps.finance.models import Charge, ClassFee, Payment, Receipt, StudentFeePlan
from apps.finance.services import (
    add_charge,
    cancel_charge,
    generate_tuition_charges,
    record_payment,
    void_payment,
)


class FinanceTestCase(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.year, cls.month = cls.today.year, cls.today.month
        cls.first = cls.today.replace(day=1)
        cls.fee_a = ClassFee.objects.create(training_class=cls.class_a, name="Monthly fee", amount=Decimal("120.00"),
                                            effective_from=cls.first - datetime.timedelta(days=400))
        cls.fee_b = ClassFee.objects.create(training_class=cls.class_b, name="Monthly fee", amount=Decimal("250.00"),
                                            effective_from=cls.first - datetime.timedelta(days=400))


class FeeTests(FinanceTestCase):
    def test_monthly_billing_is_idempotent(self):
        created = generate_tuition_charges(self.year, self.month, self.admin_user)
        self.assertEqual({(c.student, c.amount) for c in created},
                         {(self.student_1, Decimal("120.00")), (self.student_2, Decimal("250.00")),
                          (self.student_3, Decimal("120.00"))})
        self.assertEqual(generate_tuition_charges(self.year, self.month, self.admin_user), [])

    def test_billed_fee_is_locked_and_history_preserved(self):
        charge = generate_tuition_charges(self.year, self.month)[0]
        self.fee_a.amount = Decimal("150.00")
        with self.assertRaises(ValidationError):
            self.fee_a.save()
        # Correct way: end the old rate, add a new one.
        self.fee_a.refresh_from_db()
        next_month = (self.first + datetime.timedelta(days=32)).replace(day=1)
        self.fee_a.effective_to = next_month - datetime.timedelta(days=1)
        self.fee_a.save()
        ClassFee.objects.create(training_class=self.class_a, name="Monthly fee", amount=Decimal("150.00"),
                                effective_from=next_month)
        new_charges = generate_tuition_charges(next_month.year, next_month.month)
        amounts = {c.student_id: c.amount for c in new_charges}
        self.assertEqual(amounts[self.student_1.id], Decimal("150.00"))
        charge.refresh_from_db()
        self.assertIn(charge.amount, (Decimal("120.00"), Decimal("250.00")))

    def test_student_fee_plan_discount(self):
        enrollment = self.student_1.enrollments.get()
        StudentFeePlan.objects.create(enrollment=enrollment, discount_percent=Decimal("10"),
                                      reason="Sibling discount", start_date=self.first)
        charges = generate_tuition_charges(self.year, self.month)
        charge = next(c for c in charges if c.student == self.student_1)
        self.assertEqual((charge.unit_amount, charge.discount, charge.amount),
                         (Decimal("120.00"), Decimal("12.00"), Decimal("108.00")))

    def test_per_session_fee(self):
        self.fee_a.is_default = False
        self.fee_a.save()
        ClassFee.objects.create(training_class=self.class_a, name="Per session", billing_cycle="PER_SESSION",
                                amount=Decimal("30.00"), effective_from=self.first)
        mark_attendance(self.session_a, self.student_1, "PRESENT", self.admin_user)
        mark_attendance(self.session_a, self.student_3, "ABSENT", self.admin_user)
        charges = [c for c in generate_tuition_charges(self.year, self.month) if c.class_fee.name == "Per session"]
        self.assertEqual([(c.student, c.quantity, c.amount) for c in charges],
                         [(self.student_1, Decimal("1"), Decimal("30.00"))])

    def test_one_off_charges(self):
        charge = add_charge(self.student_1, "WEAPON", "Jian (straight sword)", "180.00", self.admin_user)
        self.assertEqual(charge.amount, Decimal("180.00"))
        with self.assertRaises(PermissionDenied):
            charge.delete()
        cancel_charge(charge, "Ordered by mistake", self.admin_user)
        self.assertEqual(charge.status, Charge.Status.CANCELLED)


class PaymentReceiptTests(FinanceTestCase):
    def setUp(self):
        self.charge_1 = add_charge(self.student_1, "REGISTRATION", "Registration fee", "100.00", self.admin_user)
        self.charge_2 = add_charge(self.student_2, "UNIFORM", "Uniform", "80.00", self.admin_user)

    def pay(self, amount="180.00", allocations=None):
        allocations = allocations or [(self.charge_1, "100.00"), (self.charge_2, "80.00")]
        return record_payment("Parent One", amount, "CASH", allocations, actor=self.admin_user, parent=self.parent_1)

    def test_payment_issues_receipt_and_settles_charges(self):
        payment, receipt = self.pay()
        self.charge_1.refresh_from_db()
        self.assertEqual(self.charge_1.status, Charge.Status.PAID)
        self.assertRegex(receipt.number, rf"^SRWA-{receipt.issued_at.year}-000001$")
        self.assertEqual(receipt.content["total"], "180.00")
        self.assertEqual(len(receipt.content["lines"]), 2)
        _, second = record_payment("X", "1.00", "CASH",
                                   [(add_charge(self.student_3, "OTHER", "Misc", "1.00"), "1.00")])
        self.assertTrue(second.number.endswith("000002"))

    def test_partial_payment(self):
        self.pay("50.00", [(self.charge_1, "50.00")])
        self.charge_1.refresh_from_db()
        self.assertEqual((self.charge_1.status, self.charge_1.balance), (Charge.Status.PARTIAL, Decimal("50.00")))

    def test_receipt_is_immutable(self):
        payment, receipt = self.pay()
        receipt.payer_name = "Someone else"
        with self.assertRaises(PermissionDenied):
            receipt.save()
        with self.assertRaises(PermissionDenied):
            receipt.delete()
        payment.amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            payment.save()
        paid_charge = Charge.objects.get(pk=self.charge_1.pk)
        paid_charge.unit_amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            paid_charge.save()

    def test_void_keeps_receipt_and_reopens_charges(self):
        payment, receipt = self.pay()
        void_payment(payment, "Cheque bounced", self.admin_user)
        receipt = Receipt.objects.get(pk=receipt.pk)
        self.assertTrue(receipt.is_void)
        self.assertEqual(receipt.content["total"], "180.00")
        self.charge_1.refresh_from_db()
        self.assertEqual(self.charge_1.status, Charge.Status.UNPAID)
        self.assertEqual(Payment.objects.get(pk=payment.pk).status, Payment.Status.VOIDED)
        self.assertTrue(AuditLog.objects.filter(category="FINANCE", reason="Cheque bounced").exists())

    def test_overpayment_and_mismatch_rejected(self):
        with self.assertRaises(ValidationError):
            self.pay("200.00", [(self.charge_1, "200.00")])
        with self.assertRaises(ValidationError):
            self.pay("150.00", [(self.charge_1, "100.00")])

    def test_payer_must_be_parent_of_student(self):
        other = add_charge(self.student_3, "OTHER", "Misc", "10.00")
        with self.assertRaises(ValidationError):
            self.pay("10.00", [(other, "10.00")])

    def test_financial_changes_audited(self):
        self.pay()
        actions = set(AuditLog.objects.filter(category="FINANCE").values_list("action", flat=True))
        self.assertTrue({"CREATE", "UPDATE", "EVENT"} <= actions)
