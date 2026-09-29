"""The 0003 data migration maps legacy single roles onto role groups, and back."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("accounts", "0002_user_auth_version_alter_user_role")]
AFTER = [("accounts", "0003_role_groups")]


class RoleMigrationTests(TransactionTestCase):
    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_forward_and_backward_mapping(self):
        apps = self.migrate(BEFORE)
        User = apps.get_model("accounts", "User")
        Parent = apps.get_model("accounts", "Parent")
        make = lambda name, **kw: User.objects.create(username=name, password="pbkdf2_sha256$keep-me", **kw)  # noqa: E731
        make("root", role="PARENT", is_superuser=True, is_staff=True)  # old default role on a superuser
        make("desk", role="ADMIN")
        make("coach", role="COACH")
        make("mum", role="PARENT")
        rootparent = make("rootparent", role="PARENT", is_superuser=True, is_staff=True)
        Parent.objects.create(user=rootparent, full_name="Root Parent", phone="1")

        apps = self.migrate(AFTER)
        User = apps.get_model("accounts", "User")

        def roles(name):
            return set(User.objects.get(username=name).groups.values_list("name", flat=True))

        self.assertEqual(set(apps.get_model("auth", "Group").objects.values_list("name", flat=True)),
                         {"SUPER_ADMIN", "ADMIN", "FINANCE_ADMIN", "COACH", "PARENT", "STUDENT"})
        self.assertEqual(roles("root"), {"SUPER_ADMIN"})
        self.assertEqual(roles("desk"), {"ADMIN"})
        self.assertEqual(roles("coach"), {"COACH"})
        self.assertEqual(roles("mum"), {"PARENT"})
        self.assertEqual(roles("rootparent"), {"SUPER_ADMIN", "PARENT"})
        desk = User.objects.get(username="desk")
        self.assertTrue(desk.is_staff)  # staff roles now reach the admin site
        self.assertEqual((desk.role, desk.auth_version), ("ADMIN", 1))
        self.assertFalse(User.objects.get(username="mum").is_staff)
        self.assertEqual(User.objects.get(username="coach").password, "pbkdf2_sha256$keep-me")  # untouched
        self.assertEqual(User.objects.count(), 5)  # no duplicates created

        apps = self.migrate(BEFORE)
        User = apps.get_model("accounts", "User")
        legacy = dict(User.objects.values_list("username", "role"))
        self.assertEqual(legacy, {"root": "ADMIN", "desk": "ADMIN", "coach": "COACH", "mum": "PARENT",
                                  "rootparent": "ADMIN"})
        self.assertTrue(User.objects.get(username="root").is_superuser)
        self.assertFalse(apps.get_model("auth", "Group").objects.filter(name="SUPER_ADMIN").exists())
