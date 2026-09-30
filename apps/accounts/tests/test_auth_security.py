"""Phase 5: authentication hardening.

API sign-in and sign-out, token expiry and rotation, brute-force lockout and
throttling, revocation on role / password / active changes, inactive users,
and security events in the audit log (never containing credentials)."""

import datetime
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.academy.tests.base import AcademyTestCase
from apps.accounts.capabilities import Role
from apps.accounts.models import LoginFailure, User
from apps.accounts.services import set_roles
from apps.audit.models import AuditLog

PASSWORD = "Correct-Horse-9-Battery"
GENERIC = "Unable to sign in with the provided credentials."


class AuthTestCase(AcademyTestCase):
    def setUp(self):
        cache.clear()   # throttle counters
        for user in (self.parent_1_user, self.coach_a_user, self.admin_user):
            user.set_password(PASSWORD)
            user.save()

    def login(self, username, password=PASSWORD, ip="10.0.0.1"):
        return APIClient(REMOTE_ADDR=ip).post("/api/auth/token/", {"username": username, "password": password})

    def api(self, key):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {key}")
        return client


class TokenLoginTests(AuthTestCase):
    def test_login_issues_a_new_token_and_rotates_the_old_one(self):
        first = self.login("parent1")
        self.assertEqual(first.status_code, 200, first.content)
        key_1 = first.json()["token"]
        self.assertEqual(self.api(key_1).get("/api/me/").status_code, 200)
        key_2 = self.login("parent1").json()["token"]
        self.assertNotEqual(key_1, key_2)
        self.assertEqual(self.api(key_1).get("/api/me/").status_code, 401)   # old token is dead
        self.assertEqual(self.api(key_2).get("/api/me/").status_code, 200)

    def test_every_failure_looks_the_same(self):
        self.parent_2_user.set_password(PASSWORD)
        self.parent_2_user.is_active = False
        self.parent_2_user.save()
        answers = [
            self.login("parent1", "wrong-password"),
            self.login("no-such-user"),
            self.login("parent2"),                       # inactive
            APIClient().post("/api/auth/token/", {"username": "parent1"}),   # missing password
        ]
        for response in answers:
            with self.subTest(response=response):
                self.assertEqual((response.status_code, response.json()), (400, {"detail": GENERIC}))

    def test_logout_revokes_the_token(self):
        key = self.login("coach_a").json()["token"]
        self.assertEqual(self.api(key).post("/api/auth/logout/").status_code, 204)
        self.assertEqual(self.api(key).get("/api/me/").status_code, 401)
        self.assertFalse(Token.objects.filter(key=key).exists())
        self.assertEqual(APIClient().post("/api/auth/logout/").status_code, 401)

    def test_tokens_expire(self):
        key = self.login("parent1").json()["token"]
        later = timezone.now() + datetime.timedelta(hours=24 * 14, seconds=1)
        with mock.patch("django.utils.timezone.now", return_value=later):
            response = self.api(key).get("/api/me/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Invalid or expired token.")
        self.assertFalse(Token.objects.filter(key=key).exists())

    @override_settings(API_TOKEN_TTL_HOURS=1)
    def test_token_ttl_is_configurable(self):
        key = self.login("parent1").json()["token"]
        with mock.patch("django.utils.timezone.now", return_value=timezone.now() + datetime.timedelta(minutes=59)):
            self.assertEqual(self.api(key).get("/api/me/").status_code, 200)
        with mock.patch("django.utils.timezone.now", return_value=timezone.now() + datetime.timedelta(minutes=61)):
            self.assertEqual(self.api(key).get("/api/me/").status_code, 401)

    def test_unknown_or_garbage_tokens(self):
        for header in ("Token nope", "Token " + "a" * 40, "Token", "Bearer abc"):
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=header)
            with self.subTest(header=header):
                self.assertEqual(client.get("/api/me/").status_code, 401)


class RevocationTests(AuthTestCase):
    def test_role_change_revokes_tokens_and_sessions(self):
        key = self.login("coach_a").json()["token"]
        self.client.force_login(self.admin_user)
        set_roles(self.coach_a_user, [Role.COACH, Role.PARENT], self.super_user, "Now also a parent")
        self.assertEqual(self.api(key).get("/api/me/").status_code, 401)
        set_roles(self.admin_user, [Role.ADMIN, Role.COACH], self.super_user, "Also coaches")
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)   # web session ended

    def test_deactivation_revokes_tokens_and_ends_sessions(self):
        key = self.login("admin").json()["token"]
        self.client.force_login(self.admin_user)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.admin_user.is_active = False
        self.admin_user.save()
        self.assertEqual(self.api(key).get("/api/me/").status_code, 401)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        self.assertFalse(Token.objects.filter(user=self.admin_user).exists())
        # Even a token created for the inactive user afterwards is refused.
        token = Token.objects.create(user=self.admin_user)
        self.assertEqual(self.api(token.key).get("/api/me/").status_code, 401)
        event = AuditLog.objects.filter(object_id=str(self.admin_user.pk), category="SECURITY",
                                        changes__event="ACCOUNT_DEACTIVATED").get()
        self.assertEqual(event.changes["tokens_revoked"], 1)

    def test_password_change_revokes_tokens_and_sessions(self):
        key = self.login("parent1").json()["token"]
        self.client.force_login(self.admin_user)
        before = AuditLog.objects.filter(object_id=str(self.parent_1_user.pk), changes__event="PASSWORD_CHANGED").count()
        self.parent_1_user.set_password("A-completely-new-Passw0rd")
        self.parent_1_user.save()
        self.assertEqual(self.api(key).get("/api/me/").status_code, 401)
        self.admin_user.set_password("Another-new-Passw0rd")
        self.admin_user.save()
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        entries = AuditLog.objects.filter(object_id=str(self.parent_1_user.pk), changes__event="PASSWORD_CHANGED")
        self.assertEqual(entries.count(), before + 1)
        self.assertEqual(entries.latest("id").changes["tokens_revoked"], 1)


class BruteForceTests(AuthTestCase):
    def test_username_lockout_refuses_even_the_right_password(self):
        for _ in range(5):
            self.assertEqual(self.login("parent1", "guess").status_code, 400)
        response = self.login("parent1")        # correct password, but locked
        self.assertEqual((response.status_code, response.json()), (400, {"detail": GENERIC}))
        self.assertTrue(LoginFailure.objects.filter(username="parent1", locked=True).exists())
        # Another user from another address is unaffected.
        self.assertEqual(self.login("coach_a", ip="10.0.0.2").status_code, 200)
        # The lock expires after the window; a success clears the failures.
        later = timezone.now() + datetime.timedelta(minutes=16)
        with mock.patch("django.utils.timezone.now", return_value=later):
            self.assertEqual(self.login("parent1").status_code, 200)
        self.assertFalse(LoginFailure.objects.filter(username="parent1").exists())

    def test_lockout_does_not_depend_on_case(self):
        for attempt in ("PARENT1", "Parent1", "parent1", "pArent1", "parent1 "):
            self.login(attempt, "guess")
        self.assertEqual(self.login("parent1").status_code, 400)

    @override_settings(LOGIN_LOCKOUT={"MAX_FAILURES_PER_USERNAME": 5, "MAX_FAILURES_PER_IP": 8, "WINDOW_MINUTES": 15})
    def test_ip_lockout_across_usernames(self):
        for i in range(8):
            self.login(f"user{i}", "guess", ip="10.9.9.9")
        self.assertEqual(self.login("coach_a", ip="10.9.9.9").status_code, 400)   # locked address
        self.assertEqual(self.login("coach_a", ip="10.9.9.8").status_code, 200)

    def test_admin_login_is_protected_too(self):
        url = reverse("admin:login")
        for _ in range(5):
            self.client.post(url, {"username": "admin", "password": "guess"})
        response = self.client.post(url, {"username": "admin", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)            # the form again, not a redirect
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "Please enter the correct username and password")

    def test_login_endpoint_is_rate_limited(self):
        from rest_framework.throttling import ScopedRateThrottle

        with mock.patch.object(ScopedRateThrottle, "THROTTLE_RATES", {"login": "3/min"}):
            statuses = [self.login(f"someone{i}", "guess", ip="10.1.1.1").status_code for i in range(4)]
        self.assertEqual(statuses, [400, 400, 400, 429])


class SecurityEventTests(AuthTestCase):
    def test_sign_in_events_are_audited_with_ip_and_never_contain_credentials(self):
        self.login("parent1", "wrong-Secret-123", ip="10.0.0.7")
        failure = LoginFailure.objects.get(username="parent1")
        self.assertEqual(failure.ip_address, "10.0.0.7")
        failures = " ".join(str(v) for v in LoginFailure.objects.values_list())
        self.login("parent1", ip="10.0.0.7")
        self.assertFalse(LoginFailure.objects.filter(username="parent1").exists())   # cleared by the success
        self.client.post(reverse("admin:login"), {"username": "admin", "password": PASSWORD},
                         REMOTE_ADDR="10.0.0.8", HTTP_USER_AGENT="Browser/1.0")
        issued = AuditLog.objects.get(changes__event="API_TOKEN_ISSUED", object_id=str(self.parent_1_user.pk))
        self.assertEqual((issued.actor, issued.category, issued.ip_address),
                         (self.parent_1_user, "SECURITY", "10.0.0.7"))
        login = AuditLog.objects.get(changes__event="LOGIN", object_id=str(self.admin_user.pk))
        self.assertEqual((login.ip_address, login.user_agent), ("10.0.0.8", "Browser/1.0"))
        token_key = Token.objects.get(user=self.parent_1_user).key
        everything = " ".join(str(v) for v in AuditLog.objects.values_list("changes", "reason"))
        everything += failures
        for secret in ("wrong-Secret-123", PASSWORD, token_key, self.parent_1_user.password):
            self.assertNotIn(secret, everything)

    def test_audit_ip_uses_forwarded_header_only_when_trusted(self):
        headers = {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "1.2.3.4, 203.0.113.9"}
        APIClient(**headers).post("/api/auth/token/", {"username": "parent1", "password": PASSWORD})
        self.assertEqual(AuditLog.objects.get(changes__event="API_TOKEN_ISSUED").ip_address, "10.0.0.1")
        with override_settings(TRUST_X_FORWARDED_FOR=True):
            APIClient(**headers).post("/api/auth/token/", {"username": "parent1", "password": PASSWORD})
        self.assertEqual(AuditLog.objects.filter(changes__event="API_TOKEN_ISSUED").latest("id").ip_address,
                         "203.0.113.9")


class UserAdminTests(AuthTestCase):
    def test_only_super_admin_manages_users_and_passwords(self):
        target = User.objects.get(username="parent1")
        urls = [reverse("admin:accounts_user_change", args=[target.pk]),
                reverse("admin:auth_user_password_change", args=[target.pk])
                if False else f"/admin/accounts/user/{target.pk}/password/",
                reverse("admin:accounts_user_roles", args=[target.pk]),
                reverse("admin:accounts_loginfailure_changelist")]
        for user in (self.admin_user, self.finance_user):
            self.client.force_login(user)
            for url in urls:
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
            response = self.client.post(f"/admin/accounts/user/{target.pk}/password/",
                                        {"password1": "Hacked-Passw0rd!", "password2": "Hacked-Passw0rd!"})
            self.assertEqual(response.status_code, 403)
        target.refresh_from_db()
        self.assertTrue(target.check_password(PASSWORD))
