"""Phase 5: Django admin against direct URLs and forged POSTs, for every role."""

from django.urls import reverse

from apps.academy.models import StudentAccount
from apps.academy.tests.base import AcademyTestCase, make_user
from apps.accounts.capabilities import Role
from apps.accounts.models import Coach, User
from apps.audit.models import AuditLog
from apps.audit.utils import record
from apps.finance.models import Invoice, Payment
from apps.finance.services import add_charge, record_payment
from apps.finance.tests.helpers import issued_invoice
from apps.payroll.models import PayrollRun


class AdminSecurityTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student_user = make_user("raj", Role.STUDENT)
        StudentAccount.objects.create(user=cls.student_user, student=cls.student_3)
        cls.invoice = issued_invoice([add_charge(cls.student_1, "UNIFORM", "Uniform", "80.00")])
        cls.payment, cls.receipt = record_payment([(cls.invoice, "80.00")], "CASH", cls.admin_user)
        cls.payroll_run = PayrollRun.objects.create(year=2020, month=1)
        cls.entry = record(cls.student_1, AuditLog.Action.EVENT, changes={}, reason="seed")
        cls.coach_a.bank_account_no = "5140123456"
        cls.coach_a.save()

    def sensitive_urls(self):
        return {
            "user change": reverse("admin:accounts_user_change", args=[self.parent_1_user.pk]),
            "user password": f"/admin/accounts/user/{self.parent_1_user.pk}/password/",
            "user roles": reverse("admin:accounts_user_roles", args=[self.parent_1_user.pk]),
            "groups": reverse("admin:auth_group_changelist"),
            "login failures": reverse("admin:accounts_loginfailure_changelist"),
            "audit delete": reverse("admin:audit_auditlog_delete", args=[self.entry.pk]),
            "payment delete": reverse("admin:finance_payment_delete", args=[self.payment.pk]),
            "invoice delete": reverse("admin:finance_invoice_delete", args=[self.invoice.pk]),
            "payroll delete": reverse("admin:payroll_payrollrun_delete", args=[self.payroll_run.pk]),
            "token list": "/admin/authtoken/tokenproxy/",
        }

    def test_non_staff_roles_never_reach_the_admin(self):
        for user in (self.coach_a_user, self.parent_1_user, self.student_user):
            self.client.force_login(user)
            for name, url in self.sensitive_urls().items():
                for method in ("get", "post"):
                    with self.subTest(user=user.username, page=name, method=method):
                        response = getattr(self.client, method)(url, {"post": "yes"} if method == "post" else None)
                        self.assertEqual(response.status_code, 302)
                        self.assertIn(reverse("admin:login"), response["Location"])
        self.assertTrue(Payment.objects.filter(pk=self.payment.pk).exists())
        self.assertTrue(AuditLog.objects.filter(pk=self.entry.pk).exists())

    def test_staff_roles_get_403_outside_their_capabilities(self):
        denied = {
            self.admin_user: ["user change", "user password", "user roles", "groups", "login failures",
                              "audit delete", "payment delete", "invoice delete", "payroll delete", "token list"],
            self.finance_user: ["user change", "user password", "user roles", "groups", "login failures",
                                "audit delete", "payment delete", "invoice delete", "token list"],
        }
        urls = self.sensitive_urls()
        for user, names in denied.items():
            self.client.force_login(user)
            for name in names:
                with self.subTest(user=user.username, page=name):
                    self.assertEqual(self.client.post(urls[name], {"post": "yes"}).status_code, 403)
        self.assertTrue(Invoice.objects.filter(pk=self.invoice.pk).exists())

    def test_forged_user_change_cannot_grant_superuser(self):
        self.client.force_login(self.admin_user)
        self.client.post(reverse("admin:accounts_user_change", args=[self.admin_user.pk]),
                         {"username": "admin", "is_superuser": "on", "is_staff": "on", "is_active": "on"})
        self.admin_user.refresh_from_db()
        self.assertFalse(self.admin_user.is_superuser)
        # Even a super admin's form cannot set the flag directly: it is derived from roles.
        self.client.force_login(self.super_user)
        target = User.objects.get(pk=self.parent_1_user.pk)
        self.client.post(reverse("admin:accounts_user_change", args=[target.pk]),
                         {"username": target.username, "is_superuser": "on", "is_active": "on"})
        target.refresh_from_db()
        self.assertFalse(target.is_superuser)

    def test_coach_bank_details_in_the_admin(self):
        url = reverse("admin:accounts_coach_change", args=[self.coach_a.pk])
        self.client.force_login(self.admin_user)
        page = self.client.get(url).content.decode()
        self.assertNotIn("5140123456", page)
        self.client.post(url, {"full_name": "Coach A", "phone": "011", "bank_account_no": "999999999",
                               "is_active": "on"})
        self.assertEqual(Coach.objects.get(pk=self.coach_a.pk).bank_account_no, "5140123456")
        self.client.force_login(self.finance_user)
        self.assertIn("5140123456", self.client.get(url).content.decode())

    def test_audit_log_security_entries_are_super_admin_only(self):
        from apps.audit.utils import security_event

        security_event(self.parent_1_user, "LOGIN", {}, actor=self.parent_1_user)
        security_entry = AuditLog.objects.filter(category="SECURITY").latest("id")
        url = reverse("admin:audit_auditlog_change", args=[security_entry.pk])
        for user, status in ((self.admin_user, 302), (self.finance_user, 302), (self.super_user, 200)):
            self.client.force_login(user)
            with self.subTest(user=user.username):
                # Not in the user's queryset: the admin redirects away instead of showing it.
                self.assertEqual(self.client.get(url).status_code, status)
