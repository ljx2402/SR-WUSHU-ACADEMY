"""Session lifecycle (Phase 4).

Whether a session is upcoming, in progress or completed is now derived from its
date and times; the stored status only says SCHEDULED or CANCELLED. Legacy rows
marked COMPLETED become SCHEDULED: nothing is lost, because a session whose
end time has passed is completed by definition. Reversing leaves them SCHEDULED
(a valid value in the old schema).
"""

from django.db import migrations, models


def completed_to_scheduled(apps, schema_editor):
    TrainingSession = apps.get_model("academy", "TrainingSession")
    TrainingSession.objects.filter(status="COMPLETED").update(status="SCHEDULED")
    if schema_editor.connection.vendor == "postgresql":
        # Run pending deferred FK checks now so the constraint below can be added.
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE", params=None)


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0005_substitute_authorization_lifecycle"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trainingsession",
            name="status",
            field=models.CharField(
                choices=[("SCHEDULED", "Scheduled"), ("CANCELLED", "Cancelled")],
                default="SCHEDULED",
                max_length=10,
            ),
        ),
        migrations.RunPython(completed_to_scheduled, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="trainingsession",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["SCHEDULED", "CANCELLED"])),
                name="session_status_valid",
            ),
        ),
    ]
