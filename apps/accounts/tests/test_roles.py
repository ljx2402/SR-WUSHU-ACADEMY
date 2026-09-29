"""Role storage, role changes, auditing and token/session revocation."""

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.accounts.capabilities import Cap, Role, can, roles_of
from apps.accounts.models import User
from apps.accounts.services import set_roles
from apps.academy.tests.base import make_user
from apps.audit.models import AuditLog


class RoleAssignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.super_user = make_user("root", Role.SUPER_ADMIN)
        cls.admin = make_user("desk", Role.ADMIN)
        cls.target = make_user("someone", Role.PARENT)

    def test_groups_are_the_role_source_and_flags_are_derived(self):
        user = User.objects.get(pk=self.super_user.pk)
        self.assertEqual(roles_of(user), {Role.SUPER_ADMIN})
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertEqual(user.role, Role.SUPER_ADMIN)
        parent = User.objects.get(pk=self.target.pk)
        self.assertFalse(parent.is_staff or parent.is_superuser)

    def test_multiple_roles(self):
        set_roles(self.target, [Role.COACH, Role.PARENT], self.super_user, "Coach whose child trains here")
        user = User.objects.get(pk=self.target.pk)
        self.assertEqual(roles_of(user), {Role.COACH, Role.PARENT})
        self.assertEqual(user.role, Role.COACH)  # display value: highest-ranking role
        self.assertFalse(user.is_staff)

    def test_legacy_role_field_cannot_grant_or_contradict(self):
        user = User.objects.get(pk=self.target.pk)
        user.role = Role.SUPER_ADMIN
        user.is_superuser = user.is_staff = True
        user.save()
        user = User.objects.get(pk=self.target.pk)
        self.assertEqual((user.role, user.is_superuser, user.is_staff), (Role.PARENT, False, False))
        self.assertFalse(can(user, Cap.USERS_VIEW))

    def test_groups_cannot_be_changed_outside_set_roles(self):
        attempts = [
            lambda: self.target.groups.add(Group.objects.get(name=Role.SUPER_ADMIN)),
            lambda: Group.objects.get(name=Role.ADMIN).user_set.add(self.target),
            lambda: self.target.groups.clear(),
        ]
        for attempt in attempts:
            with self.assertRaises(PermissionDenied), transaction.atomic():
                attempt()
        self.assertEqual(roles_of(User.objects.get(pk=self.target.pk)), {Role.PARENT})

    def test_only_role_managers_can_change_roles(self):
        for actor in (self.admin, self.target):
            with self.assertRaises(PermissionDenied):
                set_roles(self.target, [Role.ADMIN], actor, "promotion")
        set_roles(self.target, [Role.ADMIN], self.super_user, "promotion")
        self.assertEqual(roles_of(User.objects.get(pk=self.target.pk)), {Role.ADMIN})

    def test_reason_is_required(self):
        with self.assertRaises(ValidationError):
            set_roles(self.target, [Role.ADMIN], self.super_user, "   ")

    def test_unknown_role_rejected(self):
        with self.assertRaises(ValidationError):
            set_roles(self.target, ["OWNER"], self.super_user, "typo")

    def test_last_super_admin_cannot_be_removed(self):
        with self.assertRaises(PermissionDenied):
            set_roles(self.super_user, [Role.ADMIN], self.super_user, "step down")
        second = make_user("root2", Role.SUPER_ADMIN)
        set_roles(self.super_user, [Role.ADMIN], second, "step down")
        self.assertEqual(roles_of(User.objects.get(pk=self.super_user.pk)), {Role.ADMIN})

    def test_role_change_is_audited_without_secrets(self):
        Token.objects.create(user=self.target)
        set_roles(self.target, [Role.PARENT, Role.COACH], self.super_user, "Also coaches Sanda")
        entry = AuditLog.objects.filter(category="SECURITY", object_id=str(self.target.pk)).latest("id")
        self.assertEqual(entry.actor, self.super_user)
        self.assertEqual(entry.object_repr, "someone")
        self.assertEqual(entry.reason, "Also coaches Sanda")
        self.assertEqual(entry.changes["roles"], {"from": ["PARENT"], "to": ["COACH", "PARENT"]})
        self.assertEqual(entry.changes["tokens_revoked"], 1)
        flat = str(entry.changes)
        user = User.objects.get(pk=self.target.pk)
        for secret in (user.password, "last_login", "password", "key"):
            self.assertNotIn(secret, flat)

    def test_unchanged_roles_do_not_audit_or_revoke(self):
        token = Token.objects.create(user=self.target)
        before = AuditLog.objects.count()
        set_roles(self.target, [Role.PARENT], self.super_user, "no-op")
        self.assertEqual(AuditLog.objects.count(), before)
        self.assertTrue(Token.objects.filter(pk=token.pk).exists())

    def test_createsuperuser_becomes_super_admin(self):
        call_command("createsuperuser", username="boot", email="b@x.my", interactive=False, verbosity=0)
        user = User.objects.get(username="boot")
        self.assertEqual(roles_of(user), {Role.SUPER_ADMIN})
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertTrue(AuditLog.objects.filter(category="SECURITY", object_id=str(user.pk)).exists())

    def test_inactive_user_has_no_capabilities(self):
        user = User.objects.get(pk=self.super_user.pk)
        user.is_active = False
        self.assertFalse(can(user, Cap.STUDENTS_VIEW_ALL))


class RevocationTests(TestCase):
    """Role changes must not leave any login working under the old roles."""

    @classmethod
    def setUpTestData(cls):
        cls.super_user = make_user("root", Role.SUPER_ADMIN)
        cls.staff = make_user("clerk", Role.FINANCE_ADMIN)

    def test_api_token_revoked_on_role_change(self):
        client = APIClient()
        token = client.post("/api/auth/token/", {"username": "clerk", "password": "x"}).json()["token"]
        client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(client.get("/api/charges/").status_code, 200)
        set_roles(self.staff, [Role.PARENT], self.super_user, "Moved out of finance")
        self.assertEqual(client.get("/api/charges/").status_code, 401)
        self.assertFalse(Token.objects.filter(key=token).exists())
        # A fresh login gets a token that reflects the new roles.
        fresh = APIClient()
        new_token = fresh.post("/api/auth/token/", {"username": "clerk", "password": "x"}).json()["token"]
        fresh.credentials(HTTP_AUTHORIZATION=f"Token {new_token}")
        self.assertEqual(fresh.get("/api/me/").json()["roles"], ["PARENT"])
        self.assertEqual(fresh.get("/api/reports/fees/").status_code, 403)

    def test_web_sessions_invalidated_on_role_change(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        set_roles(self.staff, [Role.FINANCE_ADMIN, Role.COACH], self.super_user, "Also coaches")
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)  # sent back to the login page
        self.assertIn("/admin/login/", response["Location"])

    def test_roles_api(self):
        target = make_user("newcoach", Role.PARENT)
        url = f"/api/users/{target.pk}/roles/"
        admin_client = APIClient()
        admin_client.force_authenticate(make_user("desk", Role.ADMIN))
        self.assertEqual(admin_client.post(url, {"roles": ["COACH"], "reason": "x"}, format="json").status_code, 403)
        client = APIClient()
        client.force_authenticate(self.super_user)
        self.assertEqual(client.post(url, {"roles": ["COACH"]}, format="json").status_code, 400)  # reason missing
        response = client.post(url, {"roles": ["COACH", "PARENT"], "reason": "Hired"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["roles"], ["COACH", "PARENT"])


class AdminSiteAccessTests(TestCase):
    """Only staff roles reach the Django admin, and only the parts their capabilities allow."""

    @classmethod
    def setUpTestData(cls):
        cls.users = {role: make_user(role.lower(), role) for role in Role}

    def get(self, role, url):
        self.client.force_login(self.users[role])
        return self.client.get(url)

    def test_admin_site_login_by_role(self):
        for role in Role:
            status = self.get(role, "/admin/").status_code
            self.assertEqual(status, 200 if role in (Role.SUPER_ADMIN, Role.ADMIN, Role.FINANCE_ADMIN) else 302, role)

    def test_admin_sections_follow_capabilities(self):
        expectations = {
            "/admin/accounts/user/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 403, Role.FINANCE_ADMIN: 403},
            "/admin/audit/auditlog/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 200, Role.FINANCE_ADMIN: 200},
            "/admin/academy/student/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 200, Role.FINANCE_ADMIN: 403},
            "/admin/academy/student/add/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 200, Role.FINANCE_ADMIN: 403},
            "/admin/finance/classfee/add/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 403, Role.FINANCE_ADMIN: 200},
            "/admin/finance/payment/add/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 200, Role.FINANCE_ADMIN: 200},
            "/admin/finance/charge/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 200, Role.FINANCE_ADMIN: 200},
            "/admin/finance/charge/add/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 403, Role.FINANCE_ADMIN: 200},
            "/admin/payroll/payrollrun/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 403, Role.FINANCE_ADMIN: 200},
            "/admin/payroll/coachrate/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 403, Role.FINANCE_ADMIN: 200},
            "/admin/authtoken/tokenproxy/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 403, Role.FINANCE_ADMIN: 403},
            # Linking a login to a student is account management (super admin only).
            "/admin/academy/studentaccount/add/": {Role.SUPER_ADMIN: 200, Role.ADMIN: 403, Role.FINANCE_ADMIN: 403},
        }
        for url, by_role in expectations.items():
            for role, status in by_role.items():
                self.assertEqual(self.get(role, url).status_code, status, f"{role} {url}")

    def test_payroll_finalize_action_only_for_super_admin(self):
        from apps.payroll.models import PayrollRun

        PayrollRun.objects.create(year=2026, month=9)
        for role, visible in ((Role.SUPER_ADMIN, True), (Role.FINANCE_ADMIN, False)):
            content = self.get(role, "/admin/payroll/payrollrun/").content.decode()
            self.assertEqual('value="finalize"' in content, visible, role)
            self.assertIn('value="calculate"', content)

    def test_coach_bank_details_hidden_from_admin(self):
        from apps.accounts.models import Coach

        coach = Coach.objects.create(full_name="Coach Z", phone="1", bank_account_no="1234567890")
        url = f"/admin/accounts/coach/{coach.pk}/change/"
        self.assertNotContains(self.get(Role.ADMIN, url), "1234567890")
        self.assertContains(self.get(Role.FINANCE_ADMIN, url), "1234567890")

    def test_login_link_is_read_only_without_account_management(self):
        from apps.accounts.models import Parent

        parent = Parent.objects.create(full_name="P", phone="1")
        url = f"/admin/accounts/parent/{parent.pk}/change/"
        self.assertNotContains(self.get(Role.ADMIN, url), 'name="user"')
        self.assertContains(self.get(Role.SUPER_ADMIN, url), 'name="user"')

    def test_audit_log_categories_by_role(self):
        from apps.accounts.services import set_roles as change

        target = make_user("t")
        change(target, [Role.PARENT], self.users[Role.SUPER_ADMIN], "security event")
        self.assertContains(self.get(Role.SUPER_ADMIN, "/admin/audit/auditlog/?category__exact=SECURITY"), "security event")
        for role in (Role.ADMIN, Role.FINANCE_ADMIN):
            self.assertNotContains(self.get(role, "/admin/audit/auditlog/?category__exact=SECURITY"), "security event")
