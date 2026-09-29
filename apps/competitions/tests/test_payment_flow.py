"""Competition fees are paid at registration: Registration -> fee -> payment -> CONFIRMED.

This is the Phase 1 integration only (not the full competition phase)."""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.academy.tests.base import AcademyTestCase
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration
from apps.competitions.services import confirm, register, withdraw
from apps.finance.models import Charge, Invoice, Refund
from apps.finance.services import record_exceptional_refund, record_payment, void_payment

PENDING, CONFIRMED = CompetitionRegistration.Status.PENDING, CompetitionRegistration.Status.CONFIRMED


class CompetitionPaymentFlowTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.competition = Competition.objects.create(
            name="KL Open", start_date=cls.today + datetime.timedelta(days=30),
            end_date=cls.today + datetime.timedelta(days=30),
            registration_deadline=cls.today + datetime.timedelta(days=10), status="OPEN")
        cls.event = CompetitionEvent.objects.create(competition=cls.competition, event_type="CHANGQUAN",
                                                    name="Changquan Open", fee=Decimal("50.00"))
        cls.free_event = CompetitionEvent.objects.create(competition=cls.competition, event_type="OTHER",
                                                         name="Demo (free)", fee=Decimal("0.00"))

    def invoice_of(self, registration):
        return registration.charge.active_invoice_item().invoice

    def refresh(self, registration):
        return CompetitionRegistration.objects.get(pk=registration.pk)

    def test_registration_creates_fee_and_invoice_but_is_not_confirmed(self):
        for actor in (self.parent_1_user, self.admin_user):  # even staff registrations wait for payment
            with self.subTest(actor=actor.username):
                student = self.student_1 if actor == self.parent_1_user else self.student_3
                registration = register(student, self.event, actor)
                self.assertEqual(registration.status, PENDING)
                invoice = self.invoice_of(registration)
                self.assertEqual((invoice.kind, invoice.status, invoice.total, invoice.due_date),
                                 (Invoice.Kind.COMPETITION, Invoice.Status.ISSUED, Decimal("50.00"), self.today))
                self.assertEqual(invoice.family, student.family)
                self.assertEqual(registration.charge.fee_type, "COMPETITION")

    def test_unpaid_registration_cannot_be_confirmed(self):
        registration = register(self.student_1, self.event, self.parent_1_user)
        with self.assertRaises(ValidationError):
            confirm(registration, self.admin_user)
        client = APIClient()
        client.force_authenticate(self.admin_user)
        response = client.post(f"/api/competition-registrations/{registration.pk}/confirm/")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.refresh(registration).status, PENDING)

    def test_partial_payment_does_not_confirm(self):
        registration = register(self.student_1, self.event, self.parent_1_user)
        record_payment([(self.invoice_of(registration), "20.00")], "CASH", self.admin_user)
        self.assertEqual(self.refresh(registration).status, PENDING)

    def test_successful_payment_confirms(self):
        registration = register(self.student_1, self.event, self.parent_1_user)
        record_payment([(self.invoice_of(registration), "50.00")], "DUITNOW", self.admin_user)
        registration = self.refresh(registration)
        self.assertEqual(registration.status, CONFIRMED)
        self.assertEqual(registration.charge.status, Charge.Status.PAID)

    def test_voided_payment_unconfirms(self):
        registration = register(self.student_1, self.event, self.parent_1_user)
        payment, _ = record_payment([(self.invoice_of(registration), "50.00")], "CHEQUE", self.admin_user)
        void_payment(payment, "Cheque bounced", self.finance_user)
        self.assertEqual(self.refresh(registration).status, PENDING)

    def test_free_event_is_confirmed_without_invoice(self):
        registration = register(self.student_1, self.free_event, self.parent_1_user)
        self.assertEqual((registration.status, registration.charge), (CONFIRMED, None))

    def test_withdraw_unpaid_voids_invoice(self):
        registration = register(self.student_1, self.event, self.parent_1_user)
        invoice = self.invoice_of(registration)
        withdraw(registration, self.parent_1_user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.VOID)
        self.assertEqual(Charge.objects.get(pk=registration.charge_id).status, Charge.Status.CANCELLED)

    def test_paid_registration_is_non_refundable_on_withdrawal(self):
        registration = register(self.student_1, self.event, self.parent_1_user)
        invoice = self.invoice_of(registration)
        record_payment([(invoice, "50.00")], "CASH", self.admin_user)
        withdraw(self.refresh(registration), self.admin_user, "Athlete injured")
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.amount_paid, invoice.amount_refunded),
                         (Invoice.Status.PAID, Decimal("50.00"), Decimal("0.00")))
        self.assertFalse(Refund.objects.exists())  # no automatic refund
        with self.assertRaises(ValidationError):
            confirm(self.refresh(registration), self.admin_user)  # withdrawn cannot be confirmed

    def test_exceptional_refund_does_not_change_registration(self):
        registration = register(self.student_1, self.event, self.parent_1_user)
        payment, _ = record_payment([(self.invoice_of(registration), "50.00")], "CASH", self.admin_user)
        record_exceptional_refund(payment.allocations.get(), "50.00", "Event cancelled by organiser",
                                  self.finance_user)
        self.assertEqual(self.refresh(registration).status, CONFIRMED)

    def test_status_is_read_only_in_admin(self):
        registration = register(self.student_1, self.event, self.parent_1_user)
        self.client.force_login(self.super_user)
        page = self.client.get(f"/admin/competitions/competitionregistration/{registration.pk}/change/")
        self.assertNotContains(page, 'name="status"')

    def test_api_shows_invoice_to_pay(self):
        client = APIClient()
        client.force_authenticate(self.parent_1_user)
        response = client.post("/api/competition-registrations/", {"event": self.event.pk, "student": self.student_1.pk})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["fee"], body["invoice"]["balance_due"]), ("PENDING", "50.00", "50.00"))
        self.assertTrue(body["invoice"]["number"].startswith("INV-"))
