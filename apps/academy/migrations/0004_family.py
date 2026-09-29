"""Families (households) for invoice grouping, and removal of the unused
billing-contact flag (the academy has no bill-to-parent concept).

Existing students each get their own single-student family. Siblings are NOT
merged automatically: sharing a parent does not prove a shared household, so
staff group siblings explicitly afterwards.
"""

import django.db.models.deletion
from django.db import migrations, models


def one_family_per_student(apps, schema_editor):
    Family = apps.get_model("academy", "Family")
    Student = apps.get_model("academy", "Student")
    for student in Student.objects.filter(family__isnull=True):
        student.family = Family.objects.create(name=f"{student.full_name} family")
        student.save(update_fields=["family"])


class Migration(migrations.Migration):
    dependencies = [("academy", "0003_studentaccount")]

    operations = [
        migrations.CreateModel(
            name="Family",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text='Display name, e.g. "Tan family (Ali & Mei)".', max_length=200)),
                ("notes", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name_plural": "families", "ordering": ["name", "id"]},
        ),
        migrations.AddField(
            model_name="student",
            name="family",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, related_name="students", to="academy.family",
            ),
        ),
        migrations.RunPython(one_family_per_student, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="student",
            name="family",
            field=models.ForeignKey(
                blank=True,
                help_text="Siblings who are invoiced together share a family. Leave empty to create a new family.",
                on_delete=django.db.models.deletion.PROTECT, related_name="students", to="academy.family",
            ),
        ),
        migrations.RemoveField(model_name="guardianship", name="is_billing_contact"),
    ]
