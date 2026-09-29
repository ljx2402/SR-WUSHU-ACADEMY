"""Family invoices: grouping, numbering, totals, lifecycle, immutability, history."""

import re
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.academy.models import Family, Guardianship, Student
from apps.academy.tests.base import AcademyTestCase
from apps.audit.models import AuditLog
from apps.finance.models import Charge, ClassFee, Invoice, InvoiceItem, Payment
from apps.finance.services import (
    add_charge,
    cancel_charge,
    create_invoice,
    generate_draft_invoices,
    issue_invoice,
    record_payment,
    void_invoice,
)
from apps.finance.tests.helpers import issued_invoice, make_family


class FamilyInvoiceTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Three siblings grouped explicitly by staff into one household.
        cls.student_c = cls.make_student("S4", "Chloe", cls.parent_1, cls.class_a)
        cls.family = make_family(cls.student_1, cls.student_2, cls.student_c, name="Lim family")

    def charges(self):
        return [
            add_charge(self.student_1, "TUITION", "Monthly fee", "220.00", self.finance_user),
            add_charge(self.student_2, "TUITION", "Monthly fee", "280.00", self.finance_user),
            add_charge(self.student_c, "COMPETITION", "Competition fee", "50.00", self.finance_user),
        ]

    def test_one_invoice_for_three_siblings(self):
        invoice = issue_invoice(create_invoice(self.family, self.charges(), self.finance_user), self.finance_user)
        lines = [(i.student_name, i.description, i.amount) for i in invoice.active_items()]
        self.assertEqual(sorted(lines), sorted([("Ali", "Monthly fee", Decimal("220.00")),
                                                ("Mei", "Monthly fee", Decimal("280.00")),
                                                ("Chloe", "Competition fee", Decimal("50.00"))]))
        self.assertEqual((invoice.subtotal, invoice.discount_total, invoice.total, invoice.amount_paid,
                          invoice.balance_due), (Decimal("550.00"), Decimal("0.00"), Decimal("550.00"),
                                                 Decimal("0.00"), Decimal("550.00")))
        self.assertEqual(invoice.currency, "MYR")
        self.assertEqual(invoice.family_name, "Lim family")
        self.assertEqual({i.student_id for i in invoice.active_items()},
                         {self.student_1.pk, self.student_2.pk, self.student_c.pk})

    def test_no_bill_to_parent_anywhere(self):
        for model in (Invoice, InvoiceItem, Payment, Guardianship, Family):
            names = {f.name for f in model._meta.get_fields()}
            self.assertFalse({n for n in names if "bill" in n}, model.__name__)
        for model in (Invoice, InvoiceItem, Payment):  # financial documents have no parent/recipient field
            names = {f.name for f in model._meta.get_fields()}
            self.assertFalse(names & {"parent", "recipient", "bill_to"}, model.__name__)

    def test_numbering(self):
        drafts = [create_invoice(self.family, [c], self.finance_user) for c in self.charges()]
        self.assertTrue(all(d.number is None for d in drafts))  # drafts are unnumbered: no gaps
        issued = [issue_invoice(d, self.finance_user) for d in drafts]
        year = timezone.localdate().year
        self.assertEqual([i.number for i in issued], [f"INV-{year}-00000{n}" for n in (1, 2, 3)])
        self.assertTrue(all(re.fullmatch(r"INV-\d{4}-\d{6}", i.number) for i in issued))

    def test_discounted_lines(self):
        charge = add_charge(self.student_1, "TUITION", "Monthly fee", "220.00", self.finance_user, discount="22.00")
        invoice = issued_invoice([charge, add_charge(self.student_2, "UNIFORM", "Uniform x2", "80.00",
                                                     self.finance_user, quantity=2)], self.finance_user)
        self.assertEqual((invoice.subtotal, invoice.discount_total, invoice.total),
                         (Decimal("380.00"), Decimal("22.00"), Decimal("358.00")))
        uniform = invoice.active_items().get(description="Uniform x2")
        self.assertEqual((uniform.quantity, uniform.unit_amount, uniform.amount),
                         (Decimal("2.00"), Decimal("80.00"), Decimal("160.00")))

    def test_charges_must_belong_to_the_family(self):
        stranger = add_charge(self.student_3, "OTHER", "Other family", "10.00", self.finance_user)
        with self.assertRaises(ValidationError):
            create_invoice(self.family, [stranger], self.finance_user)

    def test_charge_cannot_be_on_two_invoices(self):
        charge = add_charge(self.student_1, "OTHER", "Once", "10.00", self.finance_user)
        create_invoice(self.family, [charge], self.finance_user)
        with self.assertRaises(ValidationError):
            create_invoice(self.family, [charge], self.finance_user)
        # Database backstop even if the service is bypassed.
        other = Invoice.objects.create(family=self.family)
        with self.assertRaises(IntegrityError), transaction.atomic():
            InvoiceItem.objects.create(invoice=other, charge=charge, student=self.student_1, student_no="S1",
                                       student_name="Ali", description="x", fee_type="OTHER", quantity=1,
                                       unit_amount=Decimal("10"), amount=Decimal("10"))

    def test_generate_drafts_one_per_family(self):
        self.charges()
        add_charge(self.student_3, "OTHER", "Other family fee", "15.00", self.finance_user)
        drafts = generate_draft_invoices(self.finance_user)
        self.assertEqual(len(drafts), 2)
        by_family = {d.family_id: d for d in drafts}
        self.assertEqual(by_family[self.family.pk].total, Decimal("550.00"))
        self.assertEqual(by_family[self.student_3.family_id].total, Decimal("15.00"))
        self.assertEqual(generate_draft_invoices(self.finance_user), [])  # nothing left to invoice

    def test_status_transitions(self):
        invoice = create_invoice(self.family, self.charges(), self.finance_user)
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        invoice = issue_invoice(invoice, self.finance_user)
        with self.assertRaises(ValidationError):
            issue_invoice(invoice, self.finance_user)  # already issued
        record_payment([(invoice, "100.00")], "CASH", self.admin_user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PARTIALLY_PAID)
        record_payment([(invoice, "450.00")], "CASH", self.admin_user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        for forbidden in (Invoice.Status.DRAFT, Invoice.Status.VOID):
            invoice.status = forbidden
            with self.assertRaises(ValidationError):
                invoice.save()
            invoice.refresh_from_db()
        with self.assertRaises(ValidationError):
            void_invoice(invoice, "mistake", self.finance_user)  # paid: void the payments first

    def test_void_unpaid_invoice_frees_charges(self):
        charges = self.charges()
        invoice = issued_invoice(charges, self.finance_user)
        with self.assertRaises(ValidationError):
            void_invoice(invoice, "", self.finance_user)  # reason required
        void_invoice(invoice, "Wrong month", self.finance_user)
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.void_reason), (Invoice.Status.VOID, "Wrong month"))
        self.assertTrue(invoice.number)  # voided invoices keep their number and stay on record
        reissued = issued_invoice(charges, self.finance_user)
        self.assertNotEqual(reissued.number, invoice.number)
        voided = Invoice.objects.get(pk=invoice.pk)
        voided.status = Invoice.Status.ISSUED
        with self.assertRaises(ValidationError):
            voided.save()

    def test_issued_invoice_and_lines_are_immutable(self):
        invoice = issued_invoice(self.charges(), self.finance_user)
        for field, value in (("due_date", timezone.localdate()), ("family_name", "Changed")):
            fresh = Invoice.objects.get(pk=invoice.pk)
            setattr(fresh, field, value)
            with self.assertRaises(ValidationError):
                fresh.save()
        with self.assertRaises(PermissionDenied):
            invoice.delete()
        item = invoice.active_items().first()
        item.amount = Decimal("1.00")
        with self.assertRaises(ValidationError):
            item.save()
        with self.assertRaises(PermissionDenied):
            item.delete()
        with self.assertRaises(ValidationError):
            InvoiceItem.objects.create(invoice=invoice, charge=add_charge(self.student_1, "OTHER", "Late", "1.00"),
                                       student=self.student_1, student_no="S1", student_name="Ali", description="Late",
                                       fee_type="OTHER", quantity=1, unit_amount=Decimal("1"), amount=Decimal("1"))

    def test_invoiced_charge_is_locked(self):
        charge = self.charges()[0]
        issued_invoice([charge], self.finance_user)
        charge.refresh_from_db()
        charge.unit_amount = Decimal("250.00")
        with self.assertRaises(ValidationError):
            charge.save()
        with self.assertRaises(ValidationError):
            cancel_charge(Charge.objects.get(pk=charge.pk), "not needed", self.finance_user)

    def test_historical_snapshot(self):
        fee = ClassFee.objects.create(training_class=self.class_a, name="Monthly fee", amount=Decimal("220.00"),
                                      effective_from=timezone.localdate())
        charge = add_charge(self.student_1, "TUITION", "Monthly fee (Class A)", "220.00", self.finance_user)
        invoice = issued_invoice([charge], self.finance_user)
        # Later changes to the student, family, guardians and fee configuration...
        Student.objects.filter(pk=self.student_1.pk).update(full_name="Ali Renamed", student_no="S1-NEW")
        family = Family.objects.get(pk=self.family.pk)
        family.name = "Lim-Tan family"
        family.save()
        ClassFee.objects.filter(pk=fee.pk).update(amount=Decimal("280.00"))
        Guardianship.objects.filter(student=self.student_1).update(relationship="FATHER")
        # ...never rewrite the issued document.
        invoice = Invoice.objects.get(pk=invoice.pk)
        item = invoice.active_items().get()
        self.assertEqual((invoice.family_name, invoice.total), ("Lim family", Decimal("220.00")))
        self.assertEqual((item.student_name, item.student_no, item.description, item.amount),
                         ("Ali", "S1", "Monthly fee (Class A)", Decimal("220.00")))

    def test_issue_refused_if_student_changed_family(self):
        draft = create_invoice(self.family, self.charges(), self.finance_user)
        make_family(self.student_c, name="Chloe moved out")
        with self.assertRaises(ValidationError):
            issue_invoice(draft, self.finance_user)

    def test_database_constraints_backstop_balances(self):
        invoice = issued_invoice(self.charges(), self.finance_user)
        for bad in ({"amount_paid": Decimal("600.00"), "balance_due": Decimal("-50.00")},  # overpaid
                    {"balance_due": Decimal("1.00")},                                        # inconsistent balance
                    {"number": None}):                                                       # issued without number
            with self.subTest(bad=bad), self.assertRaises(IntegrityError), transaction.atomic():
                Invoice.objects.filter(pk=invoice.pk).update(**bad)

    def test_invoice_operations_are_audited(self):
        invoice = create_invoice(self.family, self.charges(), self.finance_user)
        issue_invoice(invoice, self.finance_user)
        other = issued_invoice([add_charge(self.student_1, "OTHER", "x", "5.00")], self.finance_user)
        void_invoice(other, "Duplicate", self.finance_user)
        entries = AuditLog.objects.filter(category="FINANCE", content_type__model="invoice")
        self.assertTrue(entries.filter(object_id=str(invoice.pk), action="CREATE", actor=self.finance_user).exists())
        issue = entries.filter(object_id=str(invoice.pk), action="UPDATE", reason="Invoice issued").get()
        self.assertEqual(issue.changes["status"], {"from": "DRAFT", "to": "ISSUED"})
        self.assertTrue(entries.filter(object_id=str(other.pk), reason="Duplicate").exists())

    def test_capabilities(self):
        charge = add_charge(self.student_1, "OTHER", "x", "5.00")
        for user in (self.admin_user, self.coach_a_user, self.parent_1_user):
            with self.assertRaises(PermissionDenied):
                create_invoice(self.family, [charge], user)
        draft = create_invoice(self.family, [charge], self.super_user)
        with self.assertRaises(PermissionDenied):
            issue_invoice(draft, self.admin_user)
        issue_invoice(draft, self.finance_user)
