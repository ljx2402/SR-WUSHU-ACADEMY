"""Phase 2: the Django admin must not be a backdoor around finance and
competition rules. Every test crafts the request an attacker would send
(tampered form values, direct URLs, bulk actions) and checks the server
refuses it and nothing changed. SUPER_ADMIN is used where the point is that
even full permissions cannot bypass business rules."""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.urls import reverse

from apps.academy.models import Family, Student
from apps.academy.tests.base import AcademyTestCase
from apps.audit.models import AuditLog
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration
from apps.competitions.services import register
from apps.finance.models import (
    Charge,
    ClassFee,
    Invoice,
    InvoiceItem,
    Payment,
    PaymentAllocation,
    Receipt,
    ReceiptVoid,
    Refund,
)
from apps.finance.services import (
    add_charge,
    create_invoice,
    record_exceptional_refund,
    record_payment,
    void_payment,
)
from apps.finance.tests.admin_helpers import form_data
from apps.finance.tests.helpers import issued_invoice, make_family


def admin_url(obj, view="change"):
    opts = obj._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_{view}", args=[obj.pk])


def changelist(model):
    return reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")


class GuardrailTestCase(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.family = make_family(cls.student_1, cls.student_2, name="Ali & Mei family")
        cls.other_family = cls.student_3.family
        cls.open_invoice = issued_invoice([add_charge(cls.student_1, "TUITION", "Fee Ali", "220.00"),
                                           add_charge(cls.student_2, "TUITION", "Fee Mei", "280.00")])
        cls.paid_invoice = issued_invoice([add_charge(cls.student_1, "UNIFORM", "Uniform Ali", "80.00")])
        cls.payment, cls.receipt = record_payment([(cls.paid_invoice, "80.00")], "CASH", payer_name="Mum")
        cls.other_invoice = issued_invoice([add_charge(cls.student_3, "TUITION", "Fee Raj", "220.00")])
        cls.draft = create_invoice(cls.family, [add_charge(cls.student_1, "OTHER", "Draft line", "10.00")])

    def login(self, user):
        self.client.force_login(user)

    def snapshot(self, obj, *fields):
        fresh = type(obj).objects.get(pk=obj.pk)
        return {f: getattr(fresh, f) for f in fields}


class InvoiceAdminTamperingTests(GuardrailTestCase):
    FIELDS = ("number", "family_id", "family_name", "status", "total", "subtotal", "amount_paid", "balance_due",
              "issue_date", "issued_at", "due_date", "notes")

    def test_issued_invoice_cannot_be_edited_even_by_super_admin(self):
        for user in (self.super_user, self.finance_user):
            self.login(user)
            url = admin_url(self.open_invoice)
            data = form_data(self.client.get(url))
            data.update({"status": "PAID", "total": "1.00", "amount_paid": "500.00", "family": self.other_family.pk,
                         "number": "INV-HACKED", "notes": "tampered", "issued_at": "2020-01-01 00:00:00",
                         "items-0-amount": "0.01", "items-0-student": self.student_3.pk})
            before = self.snapshot(self.open_invoice, *self.FIELDS)
            response = self.client.post(url, data)
            self.assertEqual(response.status_code, 403, user.username)
            self.assertEqual(self.snapshot(self.open_invoice, *self.FIELDS), before)
            item = self.open_invoice.items.order_by("position").first()
            self.assertEqual((item.amount, item.student_id), (Decimal("220.00"), self.student_1.pk))

    def test_invoice_lines_have_no_admin_of_their_own(self):
        self.login(self.super_user)
        self.assertEqual(self.client.get("/admin/finance/invoiceitem/").status_code, 404)
        self.assertEqual(self.client.get(f"/admin/finance/invoiceitem/{self.open_invoice.items.first().pk}/change/")
                         .status_code, 404)

    def test_status_changes_only_through_actions(self):
        # ADMIN may view invoices but not issue them, whatever it posts.
        self.login(self.admin_user)
        self.client.post(changelist(Invoice), {"action": "issue_selected", "_selected_action": [self.draft.pk]})
        self.assertEqual(self.client.post(reverse("admin:finance_invoice_issue", args=[self.draft.pk])).status_code, 403)
        self.assertEqual(Invoice.objects.get(pk=self.draft.pk).status, Invoice.Status.DRAFT)
        # A GET on the issue URL never changes anything.
        self.login(self.finance_user)
        self.client.get(reverse("admin:finance_invoice_issue", args=[self.draft.pk]))
        self.assertEqual(Invoice.objects.get(pk=self.draft.pk).status, Invoice.Status.DRAFT)

    def test_paid_invoice_cannot_be_voided_by_tampered_pk(self):
        self.login(self.finance_user)
        self.client.post(reverse("admin:finance_invoice_void", args=[self.paid_invoice.pk]), {"reason": "oops"})
        self.assertEqual(Invoice.objects.get(pk=self.paid_invoice.pk).status, Invoice.Status.PAID)

    def test_void_invoice_requires_reason_and_is_audited(self):
        self.login(self.finance_user)
        url = reverse("admin:finance_invoice_void", args=[self.open_invoice.pk])
        self.client.post(url, {"reason": ""})
        self.assertEqual(Invoice.objects.get(pk=self.open_invoice.pk).status, Invoice.Status.ISSUED)
        self.client.post(url, {"reason": "Wrong month"})
        self.assertEqual(Invoice.objects.get(pk=self.open_invoice.pk).status, Invoice.Status.VOID)
        entry = AuditLog.objects.filter(content_type__model="invoice", object_id=str(self.open_invoice.pk),
                                        reason="Wrong month").get()
        self.assertEqual(entry.actor, self.finance_user)

    def test_bulk_issue_validates_every_invoice(self):
        moved = create_invoice(self.family, [add_charge(self.student_2, "OTHER", "Mei extra", "5.00")])
        make_family(self.student_2, name="Mei moved out")  # makes this draft invalid to issue
        self.login(self.finance_user)
        self.client.post(changelist(Invoice), {"action": "issue_selected",
                                               "_selected_action": [self.draft.pk, moved.pk, self.open_invoice.pk]})
        self.assertEqual(Invoice.objects.get(pk=self.draft.pk).status, Invoice.Status.ISSUED)
        self.assertEqual(Invoice.objects.get(pk=moved.pk).status, Invoice.Status.DRAFT)
        self.assertEqual(Invoice.objects.get(pk=self.open_invoice.pk).number, self.open_invoice.number)


class PaymentAdminTamperingTests(GuardrailTestCase):
    FIELDS = ("number", "family_id", "amount", "method", "received_at", "status", "payer_name", "reference")

    def test_payment_cannot_be_edited(self):
        for user in (self.super_user, self.finance_user, self.admin_user):
            self.login(user)
            url = admin_url(self.payment)
            data = form_data(self.client.get(url))
            data.update({"amount": "1.00", "family": self.other_family.pk, "number": "PAY-HACKED",
                         "received_at_0": "2020-01-01", "received_at_1": "00:00:00", "status": "VOIDED",
                         "allocations-0-amount": "1.00", "allocations-0-invoice": self.other_invoice.pk})
            before = self.snapshot(self.payment, *self.FIELDS)
            self.assertEqual(self.client.post(url, data).status_code, 403, user.username)
            self.assertEqual(self.snapshot(self.payment, *self.FIELDS), before)
        allocation = self.payment.allocations.get()
        self.assertEqual((allocation.amount, allocation.invoice_id), (Decimal("80.00"), self.paid_invoice.pk))

    def test_payment_and_allocation_cannot_be_deleted(self):
        self.login(self.super_user)
        self.assertEqual(self.client.post(admin_url(self.payment, "delete"), {"post": "yes"}).status_code, 403)
        self.client.post(changelist(Payment), {"action": "delete_selected", "_selected_action": [self.payment.pk],
                                               "post": "yes"})
        self.assertTrue(Payment.objects.filter(pk=self.payment.pk).exists())
        self.assertEqual(self.client.get("/admin/finance/paymentallocation/").status_code, 404)
        self.assertEqual(PaymentAllocation.objects.filter(payment=self.payment).count(), 1)

    def test_record_payment_rejects_tampered_values(self):
        self.login(self.admin_user)
        url = reverse("admin:finance_payment_add")
        base = form_data(self.client.get(url))

        def attempt(**overrides):
            data = {**base, "amount": "100.00", "method": "CASH", "rows-TOTAL_FORMS": "2",
                    "rows-0-invoice": self.open_invoice.pk, "rows-0-amount": "100.00",
                    "rows-1-invoice": "", "rows-1-amount": ""}
            data.update(overrides)
            return self.client.post(url, data)

        before = Payment.objects.count()
        for overrides in (
            {"rows-0-invoice": self.draft.pk},                                    # draft invoice
            {"rows-0-invoice": self.paid_invoice.pk},                             # already paid
            {"rows-0-invoice": 999999},                                           # nonexistent
            {"amount": "90.00"},                                                  # mismatch
            {"amount": "-100.00", "rows-0-amount": "-100.00"},                    # negative
            {"amount": "100.005", "rows-0-amount": "100.005"},                    # sub-sen
            {"amount": "600.00", "rows-0-amount": "600.00"},                      # over balance
            {"amount": "200.00", "rows-1-invoice": self.other_invoice.pk,        # two families
             "rows-1-amount": "100.00"},
            {"method": "BITCOIN"},
        ):
            with self.subTest(overrides=overrides):
                self.assertEqual(attempt(**overrides).status_code, 200)  # form redisplayed with errors
        self.assertEqual(Payment.objects.count(), before)
        self.assertEqual(attempt().status_code, 302)  # the honest request works (ADMIN may record payments)
        self.assertEqual(Payment.objects.count(), before + 1)

    def test_payment_void_requires_capability_and_reason(self):
        url = reverse("admin:finance_payment_void", args=[self.payment.pk])
        self.login(self.admin_user)
        self.assertEqual(self.client.post(url, {"reason": "x"}).status_code, 403)
        self.login(self.finance_user)
        self.client.post(url, {"reason": ""})
        self.assertEqual(Payment.objects.get(pk=self.payment.pk).status, Payment.Status.VALID)
        self.client.post(url, {"reason": "Cheque bounced"})
        self.assertEqual(Payment.objects.get(pk=self.payment.pk).status, Payment.Status.VOIDED)
        self.assertTrue(AuditLog.objects.filter(content_type__model="payment", object_id=str(self.payment.pk),
                                                reason="Cheque bounced", actor=self.finance_user).exists())


class ReceiptRefundAdminTests(GuardrailTestCase):
    def test_receipt_cannot_be_edited_or_deleted(self):
        self.login(self.super_user)
        url = admin_url(self.receipt)
        data = form_data(self.client.get(url))
        data.update({"number": "SRWA-HACKED", "total": "1.00", "payer_name": "X", "content": "{}",
                     "payment": self.payment.pk})
        self.assertEqual(self.client.post(url, data).status_code, 403)
        self.assertEqual(self.client.post(admin_url(self.receipt, "delete"), {"post": "yes"}).status_code, 403)
        self.client.post(changelist(Receipt), {"action": "delete_selected", "_selected_action": [self.receipt.pk],
                                               "post": "yes"})
        receipt = Receipt.objects.get(pk=self.receipt.pk)
        self.assertEqual((receipt.number, receipt.total, receipt.content), (self.receipt.number, Decimal("80.00"),
                                                                            self.receipt.content))

    def test_refund_only_through_the_refund_page(self):
        self.login(self.super_user)
        self.assertEqual(self.client.get(reverse("admin:finance_refund_add")).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:finance_refund_add"),
                                          {"payment": self.payment.pk, "amount": "80.00", "reason": "x"}).status_code, 403)
        self.assertFalse(Refund.objects.exists())

    def test_refund_page_rules(self):
        url = reverse("admin:finance_payment_refund", args=[self.payment.pk])
        allocation = self.payment.allocations.get()
        other_payment, _ = record_payment([(self.other_invoice, "220.00")], "CASH")
        other_allocation = other_payment.allocations.get()
        self.login(self.admin_user)
        for data in (
            {"allocation": allocation.pk, "amount": "10.00", "method": "CASH", "reason": ""},          # no reason
            {"allocation": other_allocation.pk, "amount": "10.00", "method": "CASH", "reason": "x"},   # other payment
            {"allocation": allocation.pk, "amount": "80.01", "method": "CASH", "reason": "x"},        # above paid
            {"allocation": allocation.pk, "amount": "-5.00", "method": "CASH", "reason": "x"},        # negative
        ):
            with self.subTest(data=data):
                self.client.post(url, data)
        self.assertFalse(Refund.objects.exists())
        self.client.post(url, {"allocation": allocation.pk, "amount": "30.00", "method": "CASH",
                               "reason": "Authorized by owner"})
        refund = Refund.objects.get()
        self.assertEqual((refund.amount, refund.recorded_by), (Decimal("30.00"), self.admin_user))
        self.assertTrue(AuditLog.objects.filter(content_type__model="refund", object_id=str(refund.pk),
                                                reason="Authorized by owner", actor=self.admin_user).exists())
        # The original payment and receipt are untouched.
        self.assertEqual(Payment.objects.get(pk=self.payment.pk).amount, Decimal("80.00"))
        self.assertEqual(Receipt.objects.get(pk=self.receipt.pk).content, self.receipt.content)
        # Refunds cannot be edited or deleted afterwards.
        self.login(self.super_user)
        self.assertEqual(self.client.post(admin_url(refund), {"amount": "1.00"}).status_code, 403)
        self.assertEqual(self.client.post(admin_url(refund, "delete"), {"post": "yes"}).status_code, 403)
        self.assertTrue(Refund.objects.filter(pk=refund.pk, amount=Decimal("30.00")).exists())

    def test_refund_page_denied_without_capability(self):
        url = reverse("admin:finance_payment_refund", args=[self.payment.pk])
        allocation = self.payment.allocations.get()
        for user in (self.coach_a_user, self.parent_1_user):
            self.login(user)
            response = self.client.post(url, {"allocation": allocation.pk, "amount": "10.00", "method": "CASH",
                                              "reason": "x"})
            self.assertEqual(response.status_code, 302)  # not staff: sent to the admin login page
            self.assertIn("/admin/login/", response["Location"])
        self.assertFalse(Refund.objects.exists())


class ChargeAndFeeAdminTests(GuardrailTestCase):
    def test_invoiced_charge_cannot_be_changed(self):
        charge = self.open_invoice.items.order_by("position").first().charge
        self.login(self.finance_user)
        url = admin_url(charge)
        data = form_data(self.client.get(url))
        data.update({"student": self.student_3.pk, "unit_amount": "1.00", "quantity": "5", "discount": "0.50",
                     "fee_type": "OTHER", "description": "rewritten", "status": "PAID", "amount": "0.01",
                     "notes": "Checked with parent"})
        self.client.post(url, data)
        charge = Charge.objects.get(pk=charge.pk)
        self.assertEqual((charge.student_id, charge.unit_amount, charge.quantity, charge.discount, charge.amount,
                          charge.fee_type, charge.description, charge.status),
                         (self.student_1.pk, Decimal("220.00"), Decimal("1.00"), Decimal("0.00"), Decimal("220.00"),
                          "TUITION", "Fee Ali", Charge.Status.UNPAID))
        self.assertEqual(charge.notes, "Checked with parent")  # notes are the only editable field

    def test_uninvoiced_charge_status_cannot_be_set(self):
        charge = add_charge(self.student_1, "OTHER", "Loose", "15.00")
        self.login(self.finance_user)
        url = admin_url(charge)
        data = form_data(self.client.get(url))
        data.update({"status": "PAID", "amount": "0.00"})
        self.client.post(url, data)
        charge = Charge.objects.get(pk=charge.pk)
        self.assertEqual((charge.status, charge.amount), (Charge.Status.UNPAID, Decimal("15.00")))

    def test_charges_cannot_be_deleted(self):
        charge = add_charge(self.student_1, "OTHER", "Keep me", "15.00")
        self.login(self.super_user)
        self.assertEqual(self.client.post(admin_url(charge, "delete"), {"post": "yes"}).status_code, 403)
        self.client.post(changelist(Charge), {"action": "delete_selected", "_selected_action": [charge.pk], "post": "yes"})
        self.assertTrue(Charge.objects.filter(pk=charge.pk).exists())

    def test_charge_added_in_admin_goes_through_the_service(self):
        self.login(self.finance_user)
        url = reverse("admin:finance_charge_add")
        base = {**form_data(self.client.get(url)), "student": self.student_1.pk, "fee_type": "UNIFORM",
                "description": "Uniform", "quantity": "1", "discount": "0"}
        self.client.post(url, {**base, "unit_amount": "80.005"})  # sub-sen: refused
        self.assertFalse(Charge.objects.filter(description="Uniform", fee_type="UNIFORM").exists())
        self.assertEqual(self.client.post(url, {**base, "unit_amount": "80.00"}).status_code, 302)
        charge = Charge.objects.get(description="Uniform", fee_type="UNIFORM")
        self.assertEqual((charge.amount, charge.created_by), (Decimal("80.00"), self.finance_user))
        self.assertTrue(AuditLog.objects.filter(content_type__model="charge", object_id=str(charge.pk),
                                                action="CREATE", reason="Charge added").exists())

    def test_billed_class_fee_is_locked_without_error_page(self):
        fee = ClassFee.objects.create(training_class=self.class_a, name="Monthly fee", amount=Decimal("120.00"),
                                      effective_from=self.today.replace(day=1))
        from apps.finance.services import generate_tuition_charges

        generate_tuition_charges(self.today.year, self.today.month)
        self.login(self.finance_user)
        url = admin_url(fee)
        data = form_data(self.client.get(url))
        data.update({"amount": "999.00", "billing_cycle": "PER_SESSION", "training_class": self.class_b.pk})
        response = self.client.post(url, data)
        self.assertIn(response.status_code, (200, 302))  # never a server error
        fee = ClassFee.objects.get(pk=fee.pk)
        self.assertEqual((fee.amount, fee.billing_cycle, fee.training_class_id),
                         (Decimal("120.00"), "MONTHLY", self.class_a.pk))


class CompetitionAdminTests(GuardrailTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.competition = Competition.objects.create(
            name="KL Open", start_date=cls.today + datetime.timedelta(days=30),
            end_date=cls.today + datetime.timedelta(days=30),
            registration_deadline=cls.today + datetime.timedelta(days=10), status="OPEN")
        cls.event = CompetitionEvent.objects.create(competition=cls.competition, event_type="CHANGQUAN",
                                                    name="Changquan Open", fee=Decimal("50.00"))
        cls.other_event = CompetitionEvent.objects.create(competition=cls.competition, event_type="NANQUAN",
                                                          name="Nanquan Open", fee=Decimal("0.00"))

    def setUp(self):
        self.registration = register(self.student_1, self.event, self.parent_1_user)

    def invoice(self):
        return self.registration.charge.active_invoice_item().invoice

    def status(self):
        return CompetitionRegistration.objects.get(pk=self.registration.pk).status

    def test_status_student_and_event_cannot_be_edited(self):
        self.login(self.super_user)
        url = admin_url(self.registration)
        data = form_data(self.client.get(url))
        data.update({"status": "CONFIRMED", "student": self.student_3.pk, "event": self.other_event.pk,
                     "charge": ""})
        self.client.post(url, data)
        registration = CompetitionRegistration.objects.get(pk=self.registration.pk)
        self.assertEqual((registration.status, registration.student_id, registration.event_id, registration.charge_id),
                         ("PENDING", self.student_1.pk, self.event.pk, self.registration.charge_id))

    def test_unpaid_or_partially_paid_cannot_be_confirmed(self):
        self.login(self.admin_user)
        self.client.post(changelist(CompetitionRegistration), {"action": "confirm",
                                                               "_selected_action": [self.registration.pk]})
        self.assertEqual(self.status(), "PENDING")
        record_payment([(self.invoice(), "20.00")], "CASH")
        self.client.post(changelist(CompetitionRegistration), {"action": "confirm",
                                                               "_selected_action": [self.registration.pk]})
        self.assertEqual(self.status(), "PENDING")
        record_payment([(self.invoice(), "30.00")], "CASH")
        self.assertEqual(self.status(), "CONFIRMED")  # confirmation comes from full payment

    def test_withdrawn_cannot_be_confirmed_and_voids_unpaid_invoice(self):
        self.login(self.admin_user)
        self.client.post(changelist(CompetitionRegistration), {"action": "reject",
                                                               "_selected_action": [self.registration.pk]})
        self.assertEqual(self.status(), "REJECTED")
        self.assertEqual(Invoice.objects.get(items__charge=self.registration.charge).status, Invoice.Status.VOID)
        self.client.post(changelist(CompetitionRegistration), {"action": "confirm",
                                                               "_selected_action": [self.registration.pk]})
        self.assertEqual(self.status(), "REJECTED")

    def test_withdrawal_after_payment_creates_no_refund(self):
        record_payment([(self.invoice(), "50.00")], "CASH")
        self.login(self.admin_user)
        self.client.post(changelist(CompetitionRegistration), {"action": "reject",
                                                               "_selected_action": [self.registration.pk]})
        self.assertFalse(Refund.objects.exists())
        self.assertEqual(self.invoice().status, Invoice.Status.PAID)

    def test_registration_cannot_be_deleted(self):
        self.login(self.super_user)
        self.assertEqual(self.client.post(admin_url(self.registration, "delete"), {"post": "yes"}).status_code, 403)
        self.client.post(changelist(CompetitionRegistration), {"action": "delete_selected",
                                                               "_selected_action": [self.registration.pk], "post": "yes"})
        self.assertTrue(CompetitionRegistration.objects.filter(pk=self.registration.pk).exists())
        with self.assertRaises(PermissionDenied):
            CompetitionRegistration.objects.filter(pk=self.registration.pk).delete()


class FamilyAdminTests(GuardrailTestCase):
    def test_moving_a_student_is_explicit_and_leaves_history_intact(self):
        new_family = Family.objects.create(name="Ali (new household)")
        self.login(self.admin_user)
        url = admin_url(self.student_1)
        data = form_data(self.client.get(url))
        data["family"] = new_family.pk
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(Student.objects.get(pk=self.student_1.pk).family_id, new_family.pk)
        self.assertEqual(Student.objects.get(pk=self.student_2.pk).family_id, self.family.pk)  # sibling not moved
        invoice = Invoice.objects.get(pk=self.open_invoice.pk)  # issued history unchanged
        self.assertEqual((invoice.family_id, invoice.family_name, invoice.total),
                         (self.family.pk, "Ali & Mei family", Decimal("500.00")))
        self.assertTrue(AuditLog.objects.filter(content_type__model="student", object_id=str(self.student_1.pk),
                                                action="UPDATE", actor=self.admin_user).exists())

    def test_families_cannot_be_deleted(self):
        empty = Family.objects.create(name="Empty")
        self.login(self.super_user)
        self.assertEqual(self.client.post(admin_url(empty, "delete"), {"post": "yes"}).status_code, 403)
        self.assertTrue(Family.objects.filter(pk=empty.pk).exists())

    def test_finance_can_pick_students_but_not_open_student_records(self):
        self.login(self.finance_user)
        response = self.client.get(reverse("admin:autocomplete"), {
            "app_label": "finance", "model_name": "charge", "field_name": "student", "term": "Ali"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["id"] for r in response.json()["results"]], [str(self.student_1.pk)])
        self.assertEqual(self.client.get(admin_url(self.student_1)).status_code, 403)
        self.assertEqual(self.client.get(changelist(Student)).status_code, 403)
        # The autocomplete endpoint cannot be pointed at other models to read student data.
        response = self.client.get(reverse("admin:autocomplete"), {
            "app_label": "academy", "model_name": "enrollment", "field_name": "student", "term": "Ali"})
        self.assertEqual(response.status_code, 403)


class CsrfTests(GuardrailTestCase):
    def test_state_changing_admin_pages_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.finance_user)
        attempts = [
            (reverse("admin:finance_invoice_void", args=[self.open_invoice.pk]), {"reason": "x"}),
            (reverse("admin:finance_invoice_issue", args=[self.draft.pk]), {}),
            (reverse("admin:finance_payment_void", args=[self.payment.pk]), {"reason": "x"}),
            (reverse("admin:finance_payment_refund", args=[self.payment.pk]),
             {"allocation": self.payment.allocations.get().pk, "amount": "1.00", "method": "CASH", "reason": "x"}),
            (reverse("admin:finance_payment_add"), {"amount": "10.00", "method": "CASH"}),
            (reverse("admin:finance_invoice_generate"), {}),
        ]
        for url, data in attempts:
            with self.subTest(url=url):
                self.assertEqual(client.post(url, data).status_code, 403)
        self.assertEqual(Invoice.objects.get(pk=self.open_invoice.pk).status, Invoice.Status.ISSUED)
        self.assertEqual(Invoice.objects.get(pk=self.draft.pk).status, Invoice.Status.DRAFT)
        self.assertEqual(Payment.objects.get(pk=self.payment.pk).status, Payment.Status.VALID)
        self.assertFalse(Refund.objects.exists())


class OrmDeleteProtectionTests(GuardrailTestCase):
    """Bulk queryset deletes bypass model.delete(); they are blocked too."""

    def test_bulk_delete_blocked_for_financial_history(self):
        void_payment(self.payment, "for ReceiptVoid row", self.finance_user)
        refund_payment, _ = record_payment([(self.other_invoice, "220.00")], "CASH")
        record_exceptional_refund(refund_payment.allocations.get(), "5.00", "exception", self.admin_user)
        for model in (Charge, Invoice, InvoiceItem, Payment, PaymentAllocation, Receipt, ReceiptVoid, Refund,
                      CompetitionRegistration):
            with self.subTest(model=model.__name__), self.assertRaises(PermissionDenied):
                model.objects.all().delete()
        for model, change in ((PaymentAllocation, {"amount": Decimal("1.00")}), (Receipt, {"total": Decimal("1.00")}),
                              (ReceiptVoid, {"reason": "rewritten"}), (Refund, {"amount": Decimal("1.00")})):
            with self.subTest(model=model.__name__), self.assertRaises(PermissionDenied):
                model.objects.all().update(**change)


class DatabaseTriggerTests(GuardrailTestCase):
    """PostgreSQL triggers protect financial history even from raw SQL."""

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("database triggers are PostgreSQL-only")

    def assertBlocked(self, sql, params):
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(sql, params)

    def test_raw_sql_cannot_rewrite_or_delete_history(self):
        refund = record_exceptional_refund(self.payment.allocations.get(), "5.00", "exception", self.admin_user)
        allocation = self.payment.allocations.get()
        cases = [
            ("DELETE FROM finance_receipt WHERE id = %s", [self.receipt.pk]),
            ("UPDATE finance_receipt SET total = 1 WHERE id = %s", [self.receipt.pk]),
            ("DELETE FROM finance_payment WHERE id = %s", [self.payment.pk]),
            ("UPDATE finance_payment SET amount = 1 WHERE id = %s", [self.payment.pk]),
            ("UPDATE finance_payment SET family_id = %s WHERE id = %s", [self.other_family.pk, self.payment.pk]),
            ("UPDATE finance_payment SET received_at = '2020-01-01' WHERE id = %s", [self.payment.pk]),
            ("DELETE FROM finance_paymentallocation WHERE id = %s", [allocation.pk]),
            ("UPDATE finance_paymentallocation SET amount = 1 WHERE id = %s", [allocation.pk]),
            ("DELETE FROM finance_refund WHERE id = %s", [refund.pk]),
            ("UPDATE finance_refund SET amount = 1 WHERE id = %s", [refund.pk]),
            ("DELETE FROM finance_invoice WHERE id = %s", [self.open_invoice.pk]),
            ("UPDATE finance_invoice SET family_id = %s WHERE id = %s", [self.other_family.pk, self.open_invoice.pk]),
            ("UPDATE finance_invoice SET number = 'INV-HACKED' WHERE id = %s", [self.open_invoice.pk]),
            ("UPDATE finance_invoice SET issued_at = '2020-01-01' WHERE id = %s", [self.open_invoice.pk]),
            ("DELETE FROM finance_invoiceitem WHERE invoice_id = %s", [self.open_invoice.pk]),
            ("UPDATE finance_invoiceitem SET student_id = %s WHERE invoice_id = %s",
             [self.student_3.pk, self.open_invoice.pk]),
            ("UPDATE finance_invoiceitem SET amount = 1, amount_paid = 0 WHERE invoice_id = %s", [self.open_invoice.pk]),
            ("DELETE FROM finance_charge WHERE id = %s", [self.draft.items.get().charge_id]),
        ]
        for sql, params in cases:
            with self.subTest(sql=sql):
                self.assertBlocked(sql, params)

    def test_legitimate_updates_still_work(self):
        # Draft invoices and their lines are not yet history: the service can still change them.
        with connection.cursor() as cursor:
            cursor.execute("UPDATE finance_invoice SET notes = 'draft note' WHERE id = %s", [self.draft.pk])
            cursor.execute("UPDATE finance_invoiceitem SET description = 'renamed' WHERE invoice_id = %s",
                           [self.draft.pk])
            cursor.execute("UPDATE finance_invoice SET notes = 'issued note' WHERE id = %s", [self.open_invoice.pk])
        record_payment([(self.open_invoice, "100.00")], "CASH")  # balance columns may move
        void_payment(self.payment, "bounced", self.finance_user)  # status may move
        self.assertEqual(Payment.objects.get(pk=self.payment.pk).status, Payment.Status.VOIDED)
