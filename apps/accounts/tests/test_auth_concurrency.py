"""Phase 5: simultaneous sign-ins for one user rotate the token cleanly
(PostgreSQL only; real concurrent transactions)."""

from django.core.cache import cache
from django.db import connection
from django.test import TransactionTestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.academy.tests.base import make_user
from apps.accounts.capabilities import Role
from apps.finance.tests.test_concurrency import POSTGRES_ONLY, run_concurrently


class ConcurrentLoginTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest(POSTGRES_ONLY)
        cache.clear()
        self.user = make_user("parent", Role.PARENT)
        self.user.set_password("Correct-Horse-9-Battery")
        self.user.save()

    def test_simultaneous_sign_ins_never_error_and_leave_one_token(self):
        def sign_in():
            return APIClient().post("/api/auth/token/", {"username": "parent",
                                                         "password": "Correct-Horse-9-Battery"}).status_code

        results = run_concurrently(*[sign_in] * 4)
        self.assertEqual(results, [200, 200, 200, 200])
        self.assertEqual(Token.objects.filter(user=self.user).count(), 1)
