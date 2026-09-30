"""Phase 5: production configuration, security headers, error pages and log redaction."""

import logging
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from apps.accounts.logging import RedactSecretsFilter, redact

ROOT = Path(settings.BASE_DIR)
GOOD_PRODUCTION_ENV = {
    "DJANGO_ENV": "production",
    "DJANGO_SECRET_KEY": "k8#Qz!v2Lr9@Wm4$Tn7^Xc1&Hb5*Fj3(Ps6)Gd0-Ay8+Ue2=Oi7",
    "DJANGO_ALLOWED_HOSTS": "academy.example.com",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://academy.example.com",
    "DATABASE_URL": "postgres://app:app@db.internal:5432/sr_academy",
}


def run_django(env, *args):
    """Start a fresh Python process with only the given settings environment."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "DATABASE_", "POSTGRES_"))}
    clean.update(env)
    return subprocess.run([sys.executable, "manage.py", *args], cwd=ROOT, env=clean, capture_output=True,
                          text=True, timeout=120)


class ProductionStartupTests(SimpleTestCase):
    def test_production_refuses_unsafe_configuration(self):
        cases = {
            "missing secret key": {**GOOD_PRODUCTION_ENV, "DJANGO_SECRET_KEY": ""},
            "short secret key": {**GOOD_PRODUCTION_ENV, "DJANGO_SECRET_KEY": "short"},
            "insecure dev key": {**GOOD_PRODUCTION_ENV, "DJANGO_SECRET_KEY": "django-insecure-" + "x" * 60},
            "debug on": {**GOOD_PRODUCTION_ENV, "DJANGO_DEBUG": "true"},
            "no allowed hosts": {**GOOD_PRODUCTION_ENV, "DJANGO_ALLOWED_HOSTS": ""},
            "wildcard host": {**GOOD_PRODUCTION_ENV, "DJANGO_ALLOWED_HOSTS": "*"},
            "http csrf origin": {**GOOD_PRODUCTION_ENV, "DJANGO_CSRF_TRUSTED_ORIGINS": "http://academy.example.com"},
            "sqlite database": {k: v for k, v in GOOD_PRODUCTION_ENV.items() if k != "DATABASE_URL"},
            "unknown environment": {**GOOD_PRODUCTION_ENV, "DJANGO_ENV": "prod"},
        }
        for name, env in cases.items():
            with self.subTest(case=name):
                result = run_django(env, "check")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ImproperlyConfigured", result.stderr)
                self.assertNotIn(GOOD_PRODUCTION_ENV["DJANGO_SECRET_KEY"], result.stderr)

    def test_production_passes_check_deploy_without_warnings(self):
        result = run_django(GOOD_PRODUCTION_ENV, "check", "--deploy", "--fail-level", "WARNING")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no issues", result.stdout)

    def test_development_still_works_with_no_configuration(self):
        result = run_django({}, "check")
        self.assertEqual(result.returncode, 0, result.stderr)


class ProductionValuesTests(SimpleTestCase):
    def test_production_values(self):
        code = ("from django.conf import settings as s; print(s.DEBUG, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, "
                "s.SECURE_SSL_REDIRECT, s.SECURE_HSTS_SECONDS, s.SESSION_COOKIE_NAME, s.CSRF_COOKIE_HTTPONLY, "
                "s.REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'], bool(s.CONTENT_SECURITY_POLICY), "
                "s.DATABASES['default']['OPTIONS'].get('sslmode'))")
        result = run_django(GOOD_PRODUCTION_ENV, "shell", "-c", code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1],
                         "False True True True 31536000 __Host-sessionid True "
                         "['rest_framework.renderers.JSONRenderer'] True require")


class HeaderTests(TestCase):
    @override_settings(CONTENT_SECURITY_POLICY="default-src 'self'; frame-ancestors 'none'")
    def test_security_headers(self):
        response = self.client.get("/admin/login/")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertEqual(response["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(response["Content-Security-Policy"], "default-src 'self'; frame-ancestors 'none'")
        self.assertIn("camera=()", response["Permissions-Policy"])

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_HSTS_SECONDS=31536000, SECURE_HSTS_INCLUDE_SUBDOMAINS=True)
    def test_https_redirect_and_hsts(self):
        self.assertEqual(self.client.get("/admin/login/").status_code, 301)
        response = self.client.get("/admin/login/", secure=True)
        self.assertIn("max-age=31536000", response["Strict-Transport-Security"])

    def test_print_pages_need_no_inline_script(self):
        for template in ("finance/receipt.html", "finance/invoice.html"):
            source = (ROOT / "templates" / template).read_text()
            with self.subTest(template=template):
                self.assertNotIn("onclick", source)
                self.assertNotIn("<script>", source)


class ErrorHandlingTests(TestCase):
    def test_no_debug_pages_and_json_errors(self):
        self.assertFalse(settings.DEBUG)      # the test runner, like production, runs without DEBUG
        response = self.client.get("/definitely-not-a-page/")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"Traceback", response.content)
        self.assertNotIn(b"urlpatterns", response.content)
        body = self.client.get("/api/students/").json()
        self.assertEqual(body, {"detail": "Authentication credentials were not provided."})

    def test_malformed_ids_are_rejected_not_crashed(self):
        from rest_framework.test import APIClient

        from apps.academy.tests.base import make_user
        from apps.accounts.capabilities import Role

        client = APIClient()
        client.force_authenticate(make_user("boss", Role.SUPER_ADMIN))
        for url in ("/api/attendance/?student=abc", "/api/charges/?student=1;DROP", "/api/invoices/?family=x",
                    "/api/sessions/?class=%27", "/api/competition-events/?competition=zz",
                    "/api/students/abc/", "/api/sessions/?start=not-a-date"):
            with self.subTest(url=url):
                self.assertIn(client.get(url).status_code, (400, 404))


class LogRedactionTests(SimpleTestCase):
    def test_redaction(self):
        samples = {
            "Authorization: Token 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b",
            'login failed {"username": "ali", "password": "Hunter2-secret"}': "Hunter2-secret",
            "password=Hunter2-secret&next=/": "Hunter2-secret",
            "connecting to postgres://app:S3cretPass@db:5432/x": "S3cretPass",
            "Cookie: sessionid=abcdef1234567890; csrftoken=zyx987654321": "abcdef1234567890",
            "SECRET_KEY=k8#Qz!v2Lr9@Wm4": "k8#Qz!v2Lr9@Wm4",
        }
        for message, secret in samples.items():
            with self.subTest(message=message):
                self.assertNotIn(secret, redact(message))
        self.assertEqual(redact("Payment PAY-2026-000123 recorded"), "Payment PAY-2026-000123 recorded")

    def test_filter_applies_to_formatted_records(self):
        record = logging.LogRecord("apps", logging.WARNING, __file__, 1, "token %s", ("Token abcdefghijklmnop",),
                                   None)
        RedactSecretsFilter().filter(record)
        self.assertNotIn("abcdefghijklmnop", record.getMessage())
