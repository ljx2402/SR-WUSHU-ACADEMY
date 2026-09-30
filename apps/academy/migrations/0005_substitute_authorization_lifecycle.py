"""Substitute coach authorizations get an explicit lifecycle (Phase 3).

* New statuses REVOKED and CANCELLED (final) instead of reusing the regular
  coach's ABSENT status, plus when/who/why for authorization and revocation.
* One live slot per coach per session (revoked/cancelled rows are history, so
  the same coach can be authorized again as a new row), and at most one active
  substitute per session.
* PostgreSQL trigger: substitute authorizations cannot be deleted, edited after
  authorization, or reactivated once revoked or cancelled.

Existing data is converted, never deleted:
* substitute rows that were revoked the old way (status ABSENT, or any status
  other than ASSIGNED) become REVOKED, revoked_at = the old access end;
* an active substitute on a cancelled session becomes CANCELLED;
* if a session had several active substitutes, the most recent one stays
  active and the others are marked REVOKED ("superseded");
* authorized_at is back-filled from created_at.
"""

import datetime

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone

LEGACY_REVOKED = "Revoked before substitute lifecycle tracking (see audit log for the reason)"
SUPERSEDED = "Superseded: more than one active substitute for the session before one-substitute rule"


def convert_substitutes(apps, schema_editor):
    SessionCoach = apps.get_model("academy", "SessionCoach")
    now = timezone.now()
    subs = SessionCoach.objects.filter(role="SUBSTITUTE")
    for slot in subs.filter(authorized_at__isnull=True):
        slot.authorized_at = slot.created_at
        slot.save(update_fields=["authorized_at"])
    for slot in subs.filter(access_starts_at__isnull=False, access_ends_at__isnull=False):
        if slot.access_starts_at >= slot.access_ends_at:  # empty window: it never granted access
            slot.access_starts_at = slot.access_ends_at - datetime.timedelta(microseconds=1)
            slot.save(update_fields=["access_starts_at"])
    for slot in subs.exclude(status="ASSIGNED"):
        slot.status = "REVOKED"
        slot.revoked_at = slot.access_ends_at or now
        slot.revocation_reason = LEGACY_REVOKED
        slot.save(update_fields=["status", "revoked_at", "revocation_reason"])
    for slot in subs.filter(status="ASSIGNED", session__status="CANCELLED"):
        slot.status = "CANCELLED"
        slot.revoked_at = now
        slot.revocation_reason = "Session cancelled"
        slot.save(update_fields=["status", "revoked_at", "revocation_reason"])
    active = subs.filter(status="ASSIGNED").order_by("session_id", "-created_at", "-id")
    seen = set()
    for slot in active:
        if slot.session_id in seen:
            slot.status = "REVOKED"
            slot.revoked_at = now
            slot.revocation_reason = SUPERSEDED
            slot.save(update_fields=["status", "revoked_at", "revocation_reason"])
        seen.add(slot.session_id)
    _flush_deferred_checks(schema_editor)


def restore_substitutes(apps, schema_editor):
    """Back to the old representation: an ended substitute was ABSENT."""
    SessionCoach = apps.get_model("academy", "SessionCoach")
    SessionCoach.objects.filter(role="SUBSTITUTE", status__in=["REVOKED", "CANCELLED"]).update(status="ABSENT")
    _flush_deferred_checks(schema_editor)


def _flush_deferred_checks(schema_editor):
    """PostgreSQL cannot create an index on a table with pending (deferred)
    foreign-key checks from rows updated earlier in the same transaction. Run
    those checks now so the constraints that follow can be created."""
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE", params=None)


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION sr_guard_sessioncoach() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.role = 'SUBSTITUTE' THEN
            RAISE EXCEPTION 'SR Wushu substitute history: substitute authorizations cannot be deleted'
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.role IS DISTINCT FROM NEW.role THEN
        RAISE EXCEPTION 'SR Wushu substitute history: a coach slot cannot change role'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.role = 'SUBSTITUTE' THEN
        IF (OLD.session_id, OLD.coach_id, OLD.replaces_id, OLD.access_starts_at, OLD.access_ends_at,
            OLD.authorized_at, OLD.reason)
           IS DISTINCT FROM
           (NEW.session_id, NEW.coach_id, NEW.replaces_id, NEW.access_starts_at, NEW.access_ends_at,
            NEW.authorized_at, NEW.reason) THEN
            RAISE EXCEPTION 'SR Wushu substitute history: a substitute authorization cannot be edited'
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF OLD.status IN ('REVOKED', 'CANCELLED')
           AND (OLD.status, OLD.revoked_at, OLD.revocation_reason)
               IS DISTINCT FROM (NEW.status, NEW.revoked_at, NEW.revocation_reason) THEN
            RAISE EXCEPTION 'SR Wushu substitute history: a revoked or cancelled authorization is final'
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER sr_sessioncoach_guard BEFORE UPDATE OR DELETE ON academy_sessioncoach
    FOR EACH ROW EXECUTE FUNCTION sr_guard_sessioncoach();
"""

BACKWARD_SQL = """
DROP TRIGGER IF EXISTS sr_sessioncoach_guard ON academy_sessioncoach;
DROP FUNCTION IF EXISTS sr_guard_sessioncoach();
"""


def add_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(FORWARD_SQL, params=None)  # raw SQL: no placeholder interpolation


def drop_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(BACKWARD_SQL, params=None)


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0004_family"),
        ("accounts", "0003_role_groups"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="sessioncoach",
            options={"ordering": ["session", "role", "id"]},
        ),
        migrations.AddField(
            model_name="sessioncoach",
            name="authorized_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sessioncoach",
            name="revocation_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="sessioncoach",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sessioncoach",
            name="revoked_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="sessioncoach",
            name="assigned_by",
            field=models.ForeignKey(
                blank=True, help_text="Who authorized the substitute (or generated the regular slot).", null=True,
                on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="sessioncoach",
            name="reason",
            field=models.CharField(blank=True, help_text="Why the substitute was needed.", max_length=255),
        ),
        migrations.AlterField(
            model_name="sessioncoach",
            name="replaces",
            field=models.ForeignKey(
                blank=True, help_text="The original coach this substitute covers for.", null=True,
                on_delete=django.db.models.deletion.PROTECT, related_name="replaced_in_slots", to="accounts.coach",
            ),
        ),
        migrations.AlterField(
            model_name="sessioncoach",
            name="status",
            field=models.CharField(
                choices=[
                    ("ASSIGNED", "Assigned"),
                    ("REPLACED", "Replaced by substitute"),
                    ("ABSENT", "Did not attend"),
                    ("REVOKED", "Substitute authorization revoked"),
                    ("CANCELLED", "Session cancelled"),
                ],
                default="ASSIGNED",
                max_length=10,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="sessioncoach",
            name="unique_coach_per_session",
        ),
        migrations.RunPython(convert_substitutes, restore_substitutes),
        migrations.AddConstraint(
            model_name="sessioncoach",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ["REVOKED", "CANCELLED"]), _negated=True),
                fields=("session", "coach"),
                name="unique_live_coach_per_session",
            ),
        ),
        migrations.AddConstraint(
            model_name="sessioncoach",
            constraint=models.UniqueConstraint(
                condition=models.Q(("role", "SUBSTITUTE"), ("status", "ASSIGNED")),
                fields=("session",),
                name="one_active_substitute_per_session",
            ),
        ),
        migrations.AddConstraint(
            model_name="sessioncoach",
            constraint=models.CheckConstraint(
                condition=models.Q(("role", "SUBSTITUTE"), ("status__in", ["ASSIGNED", "REPLACED", "ABSENT"]),
                                   _connector="OR"),
                name="regular_slot_status_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="sessioncoach",
            constraint=models.CheckConstraint(
                condition=models.Q(("role", "REGULAR"), ("status__in", ["ASSIGNED", "REVOKED", "CANCELLED"]),
                                   _connector="OR"),
                name="substitute_status_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="sessioncoach",
            constraint=models.CheckConstraint(
                condition=models.Q(models.Q(("status__in", ["REVOKED", "CANCELLED"]), _negated=True),
                                   ("revoked_at__isnull", False), _connector="OR"),
                name="ended_substitute_has_revoked_at",
            ),
        ),
        migrations.AddConstraint(
            model_name="sessioncoach",
            constraint=models.CheckConstraint(
                condition=models.Q(("access_starts_at__isnull", True), ("access_ends_at__isnull", True),
                                   ("access_starts_at__lt", models.F("access_ends_at")), _connector="OR"),
                name="substitute_window_valid",
            ),
        ),
        migrations.RunPython(add_trigger, drop_trigger),
    ]
