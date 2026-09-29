from types import SimpleNamespace

from django.contrib import admin
from django.urls import reverse
from django.utils import timezone

from apps.academy.services import assign_substitute
from apps.academy.tests.base import AcademyTestCase
from apps.finance.services import add_charge, record_payment
from apps.finance.tests.helpers import issued_invoice


class AdminSmokeTests(AcademyTestCase):
    def test_every_admin_page_renders(self):
        self.client.force_login(self.super_user)
        invoice = issued_invoice([add_charge(self.student_1, "UNIFORM", "Uniform", "80.00")])
        payment, receipt = record_payment([(invoice, "80.00")], "CASH", self.admin_user)
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user)
        fake_request = SimpleNamespace(user=self.super_user)

        for model, model_admin in admin.site._registry.items():
            opts = model._meta
            prefix = f"admin:{opts.app_label}_{opts.model_name}"
            url = reverse(f"{prefix}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200, url)
            if model_admin.has_add_permission(fake_request):
                url = reverse(f"{prefix}_add")
                self.assertEqual(self.client.get(url).status_code, 200, url)
            obj = model._default_manager.first()
            if obj is not None:
                url = reverse(f"{prefix}_change", args=[obj.pk])
                self.assertEqual(self.client.get(url).status_code, 200, url)

        for url in (
            reverse("admin:academy_trainingsession_substitute", args=[self.session_a.pk]),
            reverse("admin:finance_payment_void", args=[payment.pk]),
            reverse("admin:finance_payment_refund", args=[payment.pk]),
            reverse("admin:finance_invoice_void", args=[invoice.pk]),
            reverse("admin:finance_invoice_generate"),
            reverse("invoice-print", args=[invoice.pk]),
            reverse("receipt-print", args=[receipt.pk]),
            reverse("admin:accounts_user_roles", args=[self.parent_1_user.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_admin_payment_form_issues_receipt(self):
        # ADMIN (front desk) may record payments through the admin site; it goes through record_payment.
        self.client.force_login(self.admin_user)
        charge = add_charge(self.student_1, "REGISTRATION", "Registration fee", "100.00")
        invoice = issued_invoice([charge])
        form = {
            "amount": "100.00", "method": "CASH", "reference": "", "payer_name": "Parent One", "notes": "",
            "received_at": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
            "rows-TOTAL_FORMS": "1", "rows-INITIAL_FORMS": "0", "rows-MIN_NUM_FORMS": "0", "rows-MAX_NUM_FORMS": "1000",
            "rows-0-invoice": invoice.pk, "rows-0-amount": "100.00",
        }
        url = reverse("admin:finance_payment_add")
        wrong = self.client.post(url, {**form, "amount": "90.00"})
        self.assertEqual(wrong.status_code, 200)  # form redisplayed with the mismatch error
        self.assertContains(wrong, "does not match the payment amount")
        response = self.client.post(url, form)
        self.assertEqual(response.status_code, 302)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "PAID")
        self.assertTrue(charge.allocations.get().payment.receipt.number.startswith("SRWA-"))
