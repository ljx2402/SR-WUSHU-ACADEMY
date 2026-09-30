"""Competition results only for CONFIRMED registrations (Phase 4).

PostgreSQL trigger: inserting a result, or changing one, requires its
registration to be CONFIRMED, and a result cannot move to another
registration. Existing results are kept unchanged (the rule applies to new
writes). Results cannot be deleted.
"""

from django.db import migrations

FORWARD_SQL = """
CREATE OR REPLACE FUNCTION sr_guard_competitionresult() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'SR Wushu competition history: results cannot be deleted'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.registration_id IS DISTINCT FROM NEW.registration_id THEN
        RAISE EXCEPTION 'SR Wushu competition history: a result cannot move to another registration'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (SELECT status FROM competitions_competitionregistration WHERE id = NEW.registration_id)
       IS DISTINCT FROM 'CONFIRMED' THEN
        RAISE EXCEPTION 'SR Wushu competition history: results need a confirmed (paid) registration'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER sr_competitionresult_guard BEFORE INSERT OR UPDATE OR DELETE ON competitions_competitionresult
    FOR EACH ROW EXECUTE FUNCTION sr_guard_competitionresult();
"""

BACKWARD_SQL = """
DROP TRIGGER IF EXISTS sr_competitionresult_guard ON competitions_competitionresult;
DROP FUNCTION IF EXISTS sr_guard_competitionresult();
"""


def add_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(FORWARD_SQL, params=None)  # raw SQL: no placeholder interpolation


def drop_trigger(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(BACKWARD_SQL, params=None)


class Migration(migrations.Migration):
    dependencies = [("competitions", "0002_registration_pending_payment_label")]

    operations = [migrations.RunPython(add_trigger, drop_trigger)]
