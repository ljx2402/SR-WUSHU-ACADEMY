"""Create the six role groups and move existing users onto them.

Forward mapping (passwords, usernames and profiles are untouched):
  is_superuser                    -> SUPER_ADMIN
  role == "ADMIN"                 -> ADMIN
  role == "COACH"                 -> COACH
  role == "PARENT"                -> PARENT, except a superuser whose PARENT value
                                     was only the old default and who has no parent profile
Then is_staff / is_superuser / role are re-derived from the groups and
auth_version is bumped so pre-existing sessions must sign in again.

Reverse mapping collapses back onto the old single role:
  any staff role -> "ADMIN" (is_superuser kept only for SUPER_ADMIN), COACH -> "COACH", otherwise "PARENT".
"""

from django.db import migrations

ROLES = ["SUPER_ADMIN", "ADMIN", "FINANCE_ADMIN", "COACH", "PARENT", "STUDENT"]
STAFF = {"SUPER_ADMIN", "ADMIN", "FINANCE_ADMIN"}


def forward(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("accounts", "User")
    Parent = apps.get_model("accounts", "Parent")
    groups = {name: Group.objects.get_or_create(name=name)[0] for name in ROLES}
    parent_user_ids = set(Parent.objects.exclude(user=None).values_list("user_id", flat=True))
    for user in User.objects.all():
        legacy = user.role
        roles = set()
        if user.is_superuser:
            roles.add("SUPER_ADMIN")
        if legacy == "ADMIN":
            roles.add("ADMIN")
        if legacy == "COACH":
            roles.add("COACH")
        if legacy == "PARENT" and (not user.is_superuser or user.pk in parent_user_ids):
            roles.add("PARENT")
        user.groups.add(*[groups[r] for r in roles])
        user.is_superuser = "SUPER_ADMIN" in roles
        user.is_staff = bool(roles & STAFF)
        user.role = next((r for r in ROLES if r in roles), "")
        user.auth_version += 1
        user.save(update_fields=["is_superuser", "is_staff", "role", "auth_version"])


def backward(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("accounts", "User")
    for user in User.objects.all():
        roles = set(user.groups.filter(name__in=ROLES).values_list("name", flat=True))
        if roles & STAFF:
            user.role = "ADMIN"
        elif "COACH" in roles:
            user.role = "COACH"
        else:
            user.role = "PARENT"
        user.is_superuser = "SUPER_ADMIN" in roles
        user.is_staff = bool(roles & STAFF)
        user.save(update_fields=["role", "is_superuser", "is_staff"])
    Group.objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_user_auth_version_alter_user_role"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(forward, backward)]
