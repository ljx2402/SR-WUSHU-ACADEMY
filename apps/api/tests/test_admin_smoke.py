from types import SimpleNamespace

from django.contrib import admin
from django.urls import reverse

from apps.academy.services import assign_substitute
from apps.academy.tests.base import AcademyTestCase
from apps.finance.services import add_charge, record_payment


class AdminSmokeTests(AcademyTestCase):
    def test_every_admin_page_renders(self):
        self.admin_user.is_staff = self.admin_user.is_superuser = True
        self.admin_user.save()
        self.client.force_login(self.admin_user)
        charge = add_charge(self.student_1, "UNIFORM", "Uniform", "80.00")
        payment, receipt = record_payment("P", "80.00", "CASH", [(charge, "80.00")], actor=self.admin_user)
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user)
        fake_request = SimpleNamespace(user=self.admin_user)

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
            reverse("receipt-print", args=[receipt.pk]),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_admin_payment_form_issues_receipt(self):
        self.admin_user.is_staff = self.admin_user.is_superuser = True
        self.admin_user.save()
        self.client.force_login(self.admin_user)
        charge = add_charge(self.student_1, "REGISTRATION", "Registration fee", "100.00")
        form = {
            "parent": self.parent_1.pk, "payer_name": "Parent One", "amount": "100.00", "method": "CASH",
            "reference": "", "received_on": self.today.isoformat(), "notes": "",
            "allocations-TOTAL_FORMS": "1", "allocations-INITIAL_FORMS": "0",
            "allocations-MIN_NUM_FORMS": "0", "allocations-MAX_NUM_FORMS": "1000",
            "allocations-0-charge": charge.pk, "allocations-0-amount": "100.00",
        }
        wrong = self.client.post(reverse("admin:finance_payment_add"), {**form, "amount": "90.00"})
        self.assertEqual(wrong.status_code, 200)  # form redisplayed with the mismatch error
        self.assertContains(wrong, "Allocations total RM 100.00")
        response = self.client.post(reverse("admin:finance_payment_add"), form)
        self.assertEqual(response.status_code, 302)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "PAID")
        self.assertTrue(charge.allocations.get().payment.receipt.number.startswith("SRWA-"))
