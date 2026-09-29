"""Phase 2: admin access for all six roles (and role combinations), by direct
URL and crafted POST, not just by what the menu shows."""

import datetime
from decimal import Decimal

from django.urls import reverse

from apps.academy.models import StudentAccount
from apps.academy.tests.base import AcademyTestCase, make_user
from apps.accounts.capabilities import Role
from apps.accounts.models import Parent
from apps.accounts.services import set_roles
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration
from apps.competitions.services import register
from apps.finance.models import Charge, Invoice, Payment
from apps.finance.services import add_charge, create_invoice, record_payment
from apps.finance.tests.helpers import issued_invoice
from apps.payroll.models import PayrollRun

STAFF = ("super", "admin", "finance")


class AdminAccessByRoleTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student_user = make_user("raj", Role.STUDENT)
        StudentAccount.objects.create(user=cls.student_user, student=cls.student_3)
        cls.coach_parent = make_user("coachparent")
        set_roles(cls.coach_parent, [Role.COACH, Role.PARENT], None, "test")
        Parent.objects.create(user=cls.coach_parent, full_name="CP", phone="1")
        cls.finance_coach = make_user("financecoach")
        set_roles(cls.finance_coach, [Role.FINANCE_ADMIN, Role.COACH], None, "test")
        cls.users = {"super": cls.super_user, "admin": cls.admin_user, "finance": cls.finance_user,
                     "coach": cls.coach_a_user, "parent": cls.parent_1_user, "student": cls.student_user,
                     "coach+parent": cls.coach_parent, "finance+coach": cls.finance_coach}

        cls.invoice = issued_invoice([add_charge(cls.student_1, "TUITION", "Fee", "100.00")])
        cls.draft = create_invoice(cls.student_1.family, [add_charge(cls.student_1, "OTHER", "Draft", "5.00")])
        cls.payment, cls.receipt = record_payment([(cls.invoice, "40.00")], "CASH")
        cls.charge = add_charge(cls.student_1, "OTHER", "Loose charge", "7.00")
        cls.payroll_run = PayrollRun.objects.create(year=2026, month=9, calculated_at=datetime.datetime(
            2026, 9, 30, tzinfo=datetime.timezone.utc))
        competition = Competition.objects.create(
            name="Open", start_date=cls.today + datetime.timedelta(days=30),
            end_date=cls.today + datetime.timedelta(days=30),
            registration_deadline=cls.today + datetime.timedelta(days=10), status="OPEN")
        event = CompetitionEvent.objects.create(competition=competition, event_type="OTHER", name="E",
                                                fee=Decimal("20.00"))
        cls.registration = register(cls.student_1, event, cls.admin_user)

    def get(self, who, url):
        self.client.force_login(self.users[who])
        return self.client.get(url)

    def urls(self):
        r = reverse
        return {
            "index": (r("admin:index"), {"super": 200, "admin": 200, "finance": 200}),
            "invoices": (r("admin:finance_invoice_changelist"), {"super": 200, "admin": 200, "finance": 200}),
            "invoice": (r("admin:finance_invoice_change", args=[self.invoice.pk]),
                        {"super": 200, "admin": 200, "finance": 200}),
            "invoice void": (r("admin:finance_invoice_void", args=[self.draft.pk]),
                             {"super": 200, "admin": 403, "finance": 200}),
            "invoice generate": (r("admin:finance_invoice_generate"), {"super": 200, "admin": 403, "finance": 200}),
            "payment": (r("admin:finance_payment_change", args=[self.payment.pk]),
                        {"super": 200, "admin": 200, "finance": 200}),
            "record payment": (r("admin:finance_payment_add"), {"super": 200, "admin": 200, "finance": 200}),
            "payment void": (r("admin:finance_payment_void", args=[self.payment.pk]),
                             {"super": 200, "admin": 403, "finance": 200}),
            "refund": (r("admin:finance_payment_refund", args=[self.payment.pk]),
                       {"super": 200, "admin": 200, "finance": 200}),
            "receipt": (r("admin:finance_receipt_change", args=[self.receipt.pk]),
                        {"super": 200, "admin": 200, "finance": 200}),
            "charge add": (r("admin:finance_charge_add"), {"super": 200, "admin": 403, "finance": 200}),
            "class fee add": (r("admin:finance_classfee_add"), {"super": 200, "admin": 403, "finance": 200}),
            "family": (r("admin:academy_family_change", args=[self.student_1.family_id]),
                       {"super": 200, "admin": 200, "finance": 200}),
            "students": (r("admin:academy_student_changelist"), {"super": 200, "admin": 200, "finance": 403}),
            "student": (r("admin:academy_student_change", args=[self.student_1.pk]),
                        {"super": 200, "admin": 200, "finance": 403}),
            "registrations": (r("admin:competitions_competitionregistration_changelist"),
                              {"super": 200, "admin": 200, "finance": 403}),
            "payroll runs": (r("admin:payroll_payrollrun_changelist"), {"super": 200, "admin": 403, "finance": 200}),
            "users": (r("admin:auth_group_changelist"), {"super": 200, "admin": 403, "finance": 403}),
            "roles page": (r("admin:accounts_user_roles", args=[self.parent_1_user.pk]),
                           {"super": 200, "admin": 403, "finance": 403}),
            "audit log": (r("admin:audit_auditlog_changelist"), {"super": 200, "admin": 200, "finance": 200}),
        }

    def test_direct_urls_by_role(self):
        for name, (url, staff_expectation) in self.urls().items():
            for who in self.users:
                with self.subTest(page=name, role=who):
                    response = self.get(who, url)
                    if who in STAFF:
                        self.assertEqual(response.status_code, staff_expectation[who])
                    elif who == "finance+coach":
                        self.assertEqual(response.status_code, staff_expectation["finance"])
                    else:  # coach, parent, student, coach+parent never reach the admin
                        self.assertEqual(response.status_code, 302)
                        self.assertIn(reverse("admin:login"), response["Location"])

    def test_non_staff_posts_are_refused(self):
        attempts = [
            (reverse("admin:finance_payment_refund", args=[self.payment.pk]),
             {"allocation": self.payment.allocations.get().pk, "amount": "5.00", "method": "CASH", "reason": "x"}),
            (reverse("admin:finance_invoice_void", args=[self.draft.pk]), {"reason": "x"}),
            (reverse("admin:finance_payment_add"), {"amount": "60.00", "method": "CASH", "rows-TOTAL_FORMS": "1",
                                                    "rows-INITIAL_FORMS": "0", "rows-0-invoice": self.invoice.pk,
                                                    "rows-0-amount": "60.00"}),
            (reverse("admin:finance_invoice_changelist"), {"action": "issue_selected", "_selected_action": [self.draft.pk]}),
        ]
        for who in ("coach", "parent", "student", "coach+parent"):
            self.client.force_login(self.users[who])
            for url, data in attempts:
                with self.subTest(role=who, url=url):
                    self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Invoice.objects.get(pk=self.draft.pk).status, Invoice.Status.DRAFT)

    def test_menu_shows_only_permitted_sections(self):
        expectations = {
            "super": {"Invoices": True, "Students": True, "Payroll runs": True, "Users": True},
            "admin": {"Invoices": True, "Students": True, "Payroll runs": False, "Users": False},
            "finance": {"Invoices": True, "Students": False, "Payroll runs": True, "Users": False},
        }
        for who, sections in expectations.items():
            page = self.get(who, reverse("admin:index")).content.decode()
            for section, visible in sections.items():
                with self.subTest(role=who, section=section):
                    self.assertEqual(f">{section}</a>" in page, visible)

    def test_actions_outside_a_role_are_refused_when_posted(self):
        self.client.force_login(self.admin_user)
        self.client.post(reverse("admin:finance_charge_changelist"),
                         {"action": "invoice_charges", "_selected_action": [self.charge.pk]})
        self.client.post(reverse("admin:finance_charge_changelist"),
                         {"action": "cancel_charges", "_selected_action": [self.charge.pk]})
        self.assertEqual(Charge.objects.get(pk=self.charge.pk).status, Charge.Status.UNPAID)
        self.assertFalse(self.charge.invoice_items.exists())
        self.client.force_login(self.finance_user)
        self.client.post(reverse("admin:payroll_payrollrun_changelist"),
                         {"action": "finalize", "_selected_action": [self.payroll_run.pk]})
        self.assertEqual(PayrollRun.objects.get(pk=self.payroll_run.pk).status, PayrollRun.Status.DRAFT)
        response = self.client.post(reverse("admin:competitions_competitionregistration_changelist"),
                                    {"action": "confirm", "_selected_action": [self.registration.pk]})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(CompetitionRegistration.objects.get(pk=self.registration.pk).status, "PENDING")

    def test_tampered_object_ids_are_rejected(self):
        self.client.force_login(self.finance_user)
        for url in (reverse("admin:finance_invoice_void", args=[999999]),
                    reverse("admin:finance_payment_void", args=[999999]),
                    reverse("admin:finance_payment_refund", args=[999999]),
                    reverse("admin:finance_invoice_issue", args=[999999])):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, {"reason": "x"}).status_code, 404)
