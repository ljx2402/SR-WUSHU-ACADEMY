"""Phase 4: competition registration and result integrity.

Registration -> competition invoice -> full payment -> CONFIRMED -> result.
Results only for CONFIRMED (paid) registrations, in the service, the model,
the API, the admin, bulk operations and (PostgreSQL) raw SQL."""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.urls import reverse
from rest_framework.test import APIClient

from apps.academy.tests.base import AcademyTestCase
from apps.audit.utils import history_for
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration, CompetitionResult
from apps.competitions.services import confirm, record_result, register, withdraw
from apps.finance.models import Refund
from apps.finance.services import record_exceptional_refund, record_payment, void_payment
from apps.finance.tests.admin_helpers import form_data

PENDING, CONFIRMED = CompetitionRegistration.Status.PENDING, CompetitionRegistration.Status.CONFIRMED


class CompetitionIntegrityTestCase(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.competition = Competition.objects.create(
            name="KL Open", start_date=cls.today + datetime.timedelta(days=30),
            end_date=cls.today + datetime.timedelta(days=30),
            registration_deadline=cls.today + datetime.timedelta(days=10), status="OPEN")
        cls.event = CompetitionEvent.objects.create(competition=cls.competition, event_type="CHANGQUAN",
                                                    name="Changquan Open", fee=Decimal("50.00"))

    def invoice_of(self, registration):
        return registration.charge.active_invoice_item().invoice

    def fresh(self, registration):
        return CompetitionRegistration.objects.get(pk=registration.pk)

    def paid_registration(self, student=None):
        registration = register(student or self.student_1, self.event, self.admin_user)
        payment, _ = record_payment([(self.invoice_of(registration), "50.00")], "CASH", self.admin_user)
        return self.fresh(registration), payment

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client


class RegistrationStatusTests(CompetitionIntegrityTestCase):
    def test_full_flow_registration_invoice_payment_confirmed_result(self):
        registration = register(self.student_1, self.event, self.parent_1_user)
        self.assertEqual(registration.status, PENDING)
        invoice = self.invoice_of(registration)
        record_payment([(invoice, "20.00")], "CASH", self.admin_user)
        self.assertEqual(self.fresh(registration).status, PENDING)               # partial: not confirmed
        with self.assertRaisesMessage(ValidationError, "confirmed (paid)"):
            record_result(registration, self.admin_user, placing=1, medal="GOLD")
        record_payment([(invoice, "30.00")], "CASH", self.admin_user)
        registration = self.fresh(registration)
        self.assertEqual(registration.status, CONFIRMED)                          # full payment
        result = record_result(registration, self.admin_user, placing=1, medal="GOLD", score=Decimal("9.1"))
        self.assertEqual((result.placing, result.medal), (1, "GOLD"))
        entry = history_for(result).get(action="CREATE")
        self.assertEqual((entry.actor, entry.reason), (self.admin_user, "Competition result recorded"))

    def test_voided_payment_returns_to_pending_and_blocks_results(self):
        registration, payment = self.paid_registration()
        void_payment(payment, "Cheque bounced", self.finance_user)
        registration = self.fresh(registration)
        self.assertEqual(registration.status, PENDING)
        with self.assertRaises(ValidationError):
            record_result(registration, self.admin_user, placing=2)
        with self.assertRaises(ValidationError):
            confirm(registration, self.admin_user)

    def test_withdrawn_and_rejected_registrations_cannot_confirm_or_get_results(self):
        withdrawn = register(self.student_1, self.event, self.admin_user)
        withdraw(withdrawn, self.admin_user, "Injured")
        rejected = register(self.student_3, self.event, self.admin_user)
        withdraw(rejected, self.admin_user, "Not selected", CompetitionRegistration.Status.REJECTED)
        for registration in (self.fresh(withdrawn), self.fresh(rejected)):
            with self.subTest(status=registration.status):
                with self.assertRaises(ValidationError):
                    confirm(registration, self.admin_user)
                with self.assertRaises(ValidationError):
                    record_result(registration, self.admin_user, placing=3)

    def test_paid_withdrawal_does_not_refund_and_exceptional_refund_needs_reason(self):
        registration, payment = self.paid_registration()
        withdraw(registration, self.admin_user, "Family emergency")
        self.assertFalse(Refund.objects.exists())
        allocation = payment.allocations.get()
        with self.assertRaises(ValidationError):
            record_exceptional_refund(allocation, "50.00", "", self.admin_user)
        with self.assertRaises(PermissionDenied):
            record_exceptional_refund(allocation, "50.00", "x", self.coach_a_user)
        refund = record_exceptional_refund(allocation, "50.00", "Medical certificate", self.admin_user)
        self.assertTrue(history_for(refund).exists())
        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal("50.00"))   # the original payment is untouched

    def test_registration_with_a_result_cannot_be_withdrawn(self):
        registration, _ = self.paid_registration()
        record_result(registration, self.admin_user, placing=4)
        with self.assertRaisesMessage(ValidationError, "result has been recorded"):
            withdraw(registration, self.admin_user, "after the event")
        self.assertEqual(self.fresh(registration).status, CONFIRMED)


class ResultGuardTests(CompetitionIntegrityTestCase):
    def setUp(self):
        self.pending = register(self.student_1, self.event, self.admin_user)
        self.confirmed, _ = self.paid_registration(self.student_3)

    def test_model_and_bulk_writes_refuse_unconfirmed(self):
        with self.assertRaisesMessage(ValidationError, "confirmed (paid)"):
            CompetitionResult.objects.create(registration=self.pending, placing=1)
        with self.assertRaises(PermissionDenied):
            CompetitionResult.objects.bulk_create([CompetitionResult(registration=self.pending, placing=1)])
        result = record_result(self.confirmed, self.admin_user, placing=2)
        with self.assertRaises(PermissionDenied):
            CompetitionResult.objects.filter(pk=result.pk).update(registration=self.pending)
        with self.assertRaises(PermissionDenied):
            CompetitionResult.objects.filter(pk=result.pk).delete()
        result.registration = self.pending
        with self.assertRaisesMessage(ValidationError, "cannot be moved"):
            result.save()

    def test_only_result_managers(self):
        for user in (self.finance_user, self.coach_a_user, self.parent_1_user):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                record_result(self.confirmed, user, placing=1)

    def test_api(self):
        url = "/api/competition-results/"
        response = self.api(self.admin_user).post(url, {"registration": self.pending.pk, "placing": 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.api(self.coach_a_user).post(url, {"registration": self.confirmed.pk}).status_code, 403)
        response = self.api(self.admin_user).post(url, {"registration": self.confirmed.pk, "placing": 1,
                                                       "medal": "GOLD"})
        self.assertEqual(response.status_code, 201, response.content)
        result_id = response.json()["id"]
        self.assertEqual(self.api(self.admin_user).post(url, {"registration": self.confirmed.pk}).status_code, 400)
        moved = self.api(self.admin_user).patch(f"{url}{result_id}/", {"registration": self.pending.pk})
        self.assertEqual(moved.status_code, 400)
        self.assertEqual(self.api(self.admin_user).patch(f"{url}{result_id}/", {"placing": 2}).status_code, 200)
        self.assertEqual(self.api(self.admin_user).delete(f"{url}{result_id}/").status_code, 405)
        result = CompetitionResult.objects.get()
        self.assertEqual((result.registration, result.placing), (self.confirmed, 2))

    def test_admin(self):
        self.client.force_login(self.admin_user)
        add = reverse("admin:competitions_competitionresult_add")
        response = self.client.post(add, {"registration": self.pending.pk, "placing": 1, "medal": "GOLD"})
        self.assertContains(response, "confirmed (paid)")
        self.assertEqual(self.client.post(add, {"registration": self.confirmed.pk, "placing": 1,
                                                "medal": "GOLD"}).status_code, 302)
        result = CompetitionResult.objects.get()
        change = reverse("admin:competitions_competitionresult_change", args=[result.pk])
        self.client.post(change, {"registration": self.pending.pk, "placing": 5, "medal": "NONE"})
        result.refresh_from_db()
        self.assertEqual(result.registration, self.confirmed)
        self.assertEqual(self.client.post(reverse("admin:competitions_competitionresult_delete", args=[result.pk]),
                                          {"post": "yes"}).status_code, 403)
        # The registration page offers no result form for an unconfirmed registration...
        url = reverse("admin:competitions_competitionregistration_change", args=[self.pending.pk])
        data = form_data(self.client.get(url))
        data.update({"result-TOTAL_FORMS": "1", "result-0-placing": "1", "result-0-medal": "GOLD",
                     "result-0-registration": self.pending.pk})
        self.client.post(url, data)   # ...and a crafted inline POST creates nothing
        self.assertFalse(CompetitionResult.objects.filter(registration=self.pending).exists())
        # A crafted admin action cannot confirm an unpaid registration.
        self.client.post(reverse("admin:competitions_competitionregistration_changelist"),
                         {"action": "confirm", "_selected_action": [self.pending.pk]})
        self.assertEqual(self.fresh(self.pending).status, PENDING)

    def test_raw_sql_cannot_attach_results_to_unconfirmed_registrations(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL trigger")
        result = record_result(self.confirmed, self.admin_user, placing=1)
        attempts = [
            ("INSERT INTO competitions_competitionresult (registration_id, \"placing\", medal, remarks, recorded_at) "
             "VALUES (%s, 1, 'GOLD', '', now())", [self.pending.pk]),
            ("UPDATE competitions_competitionresult SET registration_id = %s WHERE id = %s",
             [self.pending.pk, result.pk]),
            ("DELETE FROM competitions_competitionresult WHERE id = %s", [result.pk]),
        ]
        for sql, params in attempts:
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic(), \
                    connection.cursor() as cursor:
                cursor.execute(sql, params)


class CompetitionReportTests(CompetitionIntegrityTestCase):
    def test_reports_show_status_payment_and_results(self):
        paid, _ = self.paid_registration(self.student_3)
        register(self.student_1, self.event, self.admin_user)
        record_result(paid, self.admin_user, placing=1, medal="GOLD")
        client = self.api(self.admin_user)
        rows = client.get("/api/reports/competitions/").json()["rows"]
        self.assertEqual({(r["student"], r["status"], r["fee_status"]) for r in rows},
                         {("Raj", "CONFIRMED", "PAID"), ("Ali", "PENDING", "UNPAID")})
        results = client.get("/api/reports/results/").json()["rows"]
        self.assertEqual([(r["student"], r["registration_status"], r["medal"]) for r in results],
                         [("Raj", "CONFIRMED", "GOLD")])
