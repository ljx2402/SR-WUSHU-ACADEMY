"""Finance admin pages follow the capabilities and go through the services."""

from django.urls import reverse

from apps.academy.tests.base import AcademyTestCase
from apps.finance.models import Invoice, Refund
from apps.finance.services import add_charge, create_invoice, record_payment
from apps.finance.tests.helpers import issued_invoice


class FinanceAdminTests(AcademyTestCase):
    def setUp(self):
        self.draft = create_invoice(self.student_1.family, [add_charge(self.student_1, "OTHER", "Draft", "10.00")])
        self.invoice = issued_invoice([add_charge(self.student_1, "TUITION", "Fee", "100.00")])
        self.payment, _ = record_payment([(self.invoice, "40.00")], "CASH")

    def status(self, user, url, method="get", data=None):
        self.client.force_login(user)
        return getattr(self.client, method)(url, data or {}).status_code

    def test_page_access_by_role(self):
        pages = {
            reverse("admin:finance_invoice_changelist"): {"super": 200, "admin": 200, "finance": 200},
            reverse("admin:finance_invoice_change", args=[self.invoice.pk]): {"super": 200, "admin": 200, "finance": 200},
            reverse("admin:finance_invoice_generate"): {"super": 200, "admin": 403, "finance": 200},
            reverse("admin:finance_invoice_void", args=[self.draft.pk]): {"super": 200, "admin": 403, "finance": 200},
            reverse("admin:finance_payment_add"): {"super": 200, "admin": 200, "finance": 200},
            reverse("admin:finance_payment_void", args=[self.payment.pk]): {"super": 200, "admin": 403, "finance": 200},
            reverse("admin:finance_payment_refund", args=[self.payment.pk]): {"super": 200, "admin": 200, "finance": 200},
            reverse("admin:finance_refund_changelist"): {"super": 200, "admin": 200, "finance": 200},
        }
        users = {"super": self.super_user, "admin": self.admin_user, "finance": self.finance_user}
        for url, expected in pages.items():
            for who, status in expected.items():
                self.assertEqual(self.status(users[who], url), status, f"{who} {url}")

    def test_admin_cannot_issue_but_finance_can(self):
        url = reverse("admin:finance_invoice_issue", args=[self.draft.pk])
        self.assertEqual(self.status(self.admin_user, url, "post"), 403)
        self.assertEqual(Invoice.objects.get(pk=self.draft.pk).status, Invoice.Status.DRAFT)
        self.assertEqual(self.status(self.finance_user, url, "post"), 302)
        self.assertEqual(Invoice.objects.get(pk=self.draft.pk).status, Invoice.Status.ISSUED)

    def test_financial_fields_are_read_only(self):
        self.client.force_login(self.super_user)
        page = self.client.get(reverse("admin:finance_invoice_change", args=[self.invoice.pk])).content.decode()
        for field in ("total", "amount_paid", "balance_due", "status", "number"):
            self.assertNotIn(f'name="{field}"', page)
        payment_page = self.client.get(reverse("admin:finance_payment_change", args=[self.payment.pk])).content.decode()
        self.assertNotIn('name="amount"', payment_page)
        # There is no delete action: the request is bounced and nothing is deleted.
        self.client.post(reverse("admin:finance_invoice_changelist"),
                         {"action": "delete_selected", "_selected_action": [self.invoice.pk], "post": "yes"})
        self.assertTrue(Invoice.objects.filter(pk=self.invoice.pk).exists())

    def test_refund_page_requires_reason_and_records_refund(self):
        self.client.force_login(self.admin_user)
        url = reverse("admin:finance_payment_refund", args=[self.payment.pk])
        allocation = self.payment.allocations.get()
        missing = self.client.post(url, {"allocation": allocation.pk, "amount": "5.00", "method": "CASH", "reason": ""})
        self.assertEqual(missing.status_code, 200)
        self.assertFalse(Refund.objects.exists())
        done = self.client.post(url, {"allocation": allocation.pk, "amount": "5.00", "method": "CASH",
                                      "reason": "Approved by owner"})
        self.assertEqual(done.status_code, 302)
        self.assertEqual(Refund.objects.get().reason, "Approved by owner")
