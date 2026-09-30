"""Per-session payroll (Phase 4).

* Only PER_SESSION and SUBSTITUTE_PER_SESSION rates are used; HOURLY, MONTHLY
  and SUBSTITUTE_HOURLY rows are kept as history, relabelled "legacy – not used".
* Payroll runs: DRAFT -> READY -> FINALIZED; lines record the paid assignment
  (slot), the rate row and rule used, and any blocking issue.
* PostgreSQL triggers: a finalized payroll run, its payslips and lines cannot be
  changed or deleted (only the run's notes); sessions of a finalized payroll
  month cannot be added, moved, cancelled, reinstated or deleted.

Existing rows are kept unchanged. Draft runs must be recalculated under the new
rules before they can be finalized; finalized runs stay exactly as they were.
"""
import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models

FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION sr_payroll_reject(message text) RETURNS void AS $$
BEGIN
    RAISE EXCEPTION 'SR Wushu payroll history: %', message USING ERRCODE = 'restrict_violation';
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION sr_guard_payrollrun() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'FINALIZED' THEN PERFORM sr_payroll_reject('a finalized payroll cannot be deleted'); END IF;
        RETURN OLD;
    END IF;
    IF (OLD.year, OLD.month) IS DISTINCT FROM (NEW.year, NEW.month) THEN
        PERFORM sr_payroll_reject('a payroll period cannot be moved');
    END IF;
    IF OLD.status = 'FINALIZED'
       AND (OLD.status, OLD.calculated_at, OLD.calculated_by_id, OLD.finalized_at, OLD.excluded::text)
           IS DISTINCT FROM (NEW.status, NEW.calculated_at, NEW.calculated_by_id, NEW.finalized_at, NEW.excluded::text) THEN
        PERFORM sr_payroll_reject('a finalized payroll cannot be changed');
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION sr_guard_payslip() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM payroll_payrollrun r WHERE r.status = 'FINALIZED'
               AND r.id IN (CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.run_id END,
                            CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.run_id END)) THEN
        PERFORM sr_payroll_reject('payslips of a finalized payroll cannot change');
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION sr_guard_payslipline() RETURNS trigger AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM payroll_payslip p JOIN payroll_payrollrun r ON r.id = p.run_id
               WHERE r.status = 'FINALIZED'
               AND p.id IN (CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE OLD.payslip_id END,
                            CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE NEW.payslip_id END)) THEN
        PERFORM sr_payroll_reject('payslip lines of a finalized payroll cannot change');
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION sr_month_finalized(d date) RETURNS boolean AS $$
    SELECT EXISTS (SELECT 1 FROM payroll_payrollrun r WHERE r.status = 'FINALIZED'
                   AND r.year = EXTRACT(YEAR FROM d) AND r.month = EXTRACT(MONTH FROM d));
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION sr_guard_session_payroll() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF sr_month_finalized(OLD.date) THEN PERFORM sr_payroll_reject('sessions of a finalized payroll month cannot be deleted'); END IF;
        RETURN OLD;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF sr_month_finalized(NEW.date) THEN PERFORM sr_payroll_reject('sessions cannot be added to a finalized payroll month'); END IF;
        RETURN NEW;
    END IF;
    IF (OLD.training_class_id, OLD.date, OLD.start_time, OLD.end_time, OLD.status)
       IS DISTINCT FROM (NEW.training_class_id, NEW.date, NEW.start_time, NEW.end_time, NEW.status)
       AND (sr_month_finalized(OLD.date) OR sr_month_finalized(NEW.date)) THEN
        PERFORM sr_payroll_reject('sessions of a finalized payroll month cannot change');
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER sr_payrollrun_guard BEFORE UPDATE OR DELETE ON payroll_payrollrun
    FOR EACH ROW EXECUTE FUNCTION sr_guard_payrollrun();
CREATE TRIGGER sr_payslip_guard BEFORE INSERT OR UPDATE OR DELETE ON payroll_payslip
    FOR EACH ROW EXECUTE FUNCTION sr_guard_payslip();
CREATE TRIGGER sr_payslipline_guard BEFORE INSERT OR UPDATE OR DELETE ON payroll_payslipline
    FOR EACH ROW EXECUTE FUNCTION sr_guard_payslipline();
CREATE TRIGGER sr_session_payroll_guard BEFORE INSERT OR UPDATE OR DELETE ON academy_trainingsession
    FOR EACH ROW EXECUTE FUNCTION sr_guard_session_payroll();
"""

BACKWARD_SQL = """
DROP TRIGGER IF EXISTS sr_session_payroll_guard ON academy_trainingsession;
DROP TRIGGER IF EXISTS sr_payslipline_guard ON payroll_payslipline;
DROP TRIGGER IF EXISTS sr_payslip_guard ON payroll_payslip;
DROP TRIGGER IF EXISTS sr_payrollrun_guard ON payroll_payrollrun;
DROP FUNCTION IF EXISTS sr_guard_session_payroll();
DROP FUNCTION IF EXISTS sr_month_finalized(date);
DROP FUNCTION IF EXISTS sr_guard_payslipline();
DROP FUNCTION IF EXISTS sr_guard_payslip();
DROP FUNCTION IF EXISTS sr_guard_payrollrun();
DROP FUNCTION IF EXISTS sr_payroll_reject(text);
"""


def ready_to_draft(apps, schema_editor):
    """Reverse only: the old schema has no READY status (a calculated run was a draft)."""
    apps.get_model("payroll", "PayrollRun").objects.filter(status="READY").update(status="DRAFT")


def add_triggers(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(FORWARD_SQL, params=None)  # raw SQL: no placeholder interpolation


def drop_triggers(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(BACKWARD_SQL, params=None)



class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0006_session_lifecycle"),
        ("accounts", "0003_role_groups"),
        ("payroll", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="payrollrun",
            name="calculated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="payrollrun",
            name="excluded",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Coach sessions in the period that were not paid, and why.",
            ),
        ),
        migrations.AddField(
            model_name="payslipline",
            name="issue",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "–"),
                    ("MISSING_RATE", "No per-session rate for this coach and session"),
                ],
                default="",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="payslipline",
            name="rate_source",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="payroll.coachrate",
            ),
        ),
        migrations.AddField(
            model_name="payslipline",
            name="rule",
            field=models.CharField(
                blank=True, help_text="Which rate rule priced this line.", max_length=40
            ),
        ),
        migrations.AddField(
            model_name="payslipline",
            name="slot",
            field=models.ForeignKey(
                blank=True,
                help_text="The coach assignment that was paid.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payslip_lines",
                to="academy.sessioncoach",
            ),
        ),
        migrations.AlterField(
            model_name="coachrate",
            name="rate_type",
            field=models.CharField(
                choices=[
                    ("PER_SESSION", "Per session"),
                    ("SUBSTITUTE_PER_SESSION", "Substitute – per session"),
                    ("HOURLY", "Hourly (legacy – not used)"),
                    ("MONTHLY", "Monthly (legacy – not used)"),
                    ("SUBSTITUTE_HOURLY", "Substitute – hourly (legacy – not used)"),
                ],
                max_length=25,
            ),
        ),
        migrations.AlterField(
            model_name="payrollrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("DRAFT", "Draft"),
                    ("READY", "Calculated – ready for approval"),
                    ("FINALIZED", "Finalized"),
                ],
                default="DRAFT",
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="payslip",
            name="hours",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="For information only: pay is per session.",
                max_digits=8,
            ),
        ),
        migrations.AlterField(
            model_name="payslipline",
            name="kind",
            field=models.CharField(
                choices=[
                    ("REGULAR_SESSION", "Regular session"),
                    ("SUBSTITUTE_SESSION", "Substitute session"),
                    ("MONTHLY", "Monthly salary (legacy)"),
                    ("ALLOWANCE", "Allowance"),
                    ("BONUS", "Bonus"),
                    ("DEDUCTION", "Deduction"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="coachrate",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gte", 0)), name="coach_rate_not_negative"
            ),
        ),
        migrations.AddConstraint(
            model_name="payrollrun",
            constraint=models.CheckConstraint(
                condition=models.Q(("month__gte", 1), ("month__lte", 12)),
                name="payroll_month_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="payrollrun",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("status", "FINALIZED"), _negated=True),
                    ("finalized_at__isnull", False),
                    _connector="OR",
                ),
                name="finalized_payroll_has_timestamp",
            ),
        ),
        migrations.AddConstraint(
            model_name="payslipline",
            constraint=models.UniqueConstraint(
                condition=models.Q(("slot__isnull", False)),
                fields=("slot",),
                name="assignment_paid_once",
            ),
        ),
        migrations.AddConstraint(
            model_name="payslipline",
            constraint=models.UniqueConstraint(
                condition=models.Q(("session__isnull", False)),
                fields=("payslip", "session"),
                name="one_line_per_session_per_payslip",
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, ready_to_draft),
        migrations.RunPython(add_triggers, drop_triggers),
    ]
