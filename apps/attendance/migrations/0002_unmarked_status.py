"""UNMARKED attendance status (Phase 3), a database check on status values, and
(PostgreSQL) a trigger that refuses to delete attendance history."""

from django.db import migrations, models

FORWARD_SQL = """
CREATE OR REPLACE FUNCTION sr_forbid_attendance_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'SR Wushu attendance history: attendance records cannot be deleted'
        USING ERRCODE = 'restrict_violation';
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER sr_attendance_no_delete BEFORE DELETE ON attendance_attendancerecord
    FOR EACH ROW EXECUTE FUNCTION sr_forbid_attendance_delete();
"""

BACKWARD_SQL = """
DROP TRIGGER IF EXISTS sr_attendance_no_delete ON attendance_attendancerecord;
DROP FUNCTION IF EXISTS sr_forbid_attendance_delete();
"""


def add_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(FORWARD_SQL, params=None)  # raw SQL: no placeholder interpolation


def drop_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(BACKWARD_SQL, params=None)


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attendancerecord",
            name="status",
            field=models.CharField(
                choices=[
                    ("UNMARKED", "Unmarked"),
                    ("PRESENT", "Present"),
                    ("ABSENT", "Absent"),
                    ("LATE", "Late"),
                    ("EXCUSED", "Excused"),
                ],
                max_length=8,
            ),
        ),
        migrations.AddConstraint(
            model_name="attendancerecord",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ["UNMARKED", "PRESENT", "ABSENT", "LATE", "EXCUSED"])),
                name="attendance_status_valid",
            ),
        ),
        migrations.RunPython(add_trigger, drop_trigger),
    ]
