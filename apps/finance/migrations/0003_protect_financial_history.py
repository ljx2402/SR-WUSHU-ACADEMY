"""Database-level protection of financial history (PostgreSQL).

The model and queryset guards stop the application from deleting or rewriting
financial history; these triggers stop everything else (raw SQL, a bulk
``update()``, a future bug) at the database. Violations raise SQLSTATE 23001
(restrict_violation), which Django reports as IntegrityError.

* Never deleted: charges, invoices, invoice lines, payments, allocations,
  receipts, receipt voids, refunds.
* Never changed: allocations, receipts, receipt voids, refunds (their content
  columns; a user FK may still be set to NULL if that user is deleted).
* Payments: amount, family, number, method, dates and references are frozen;
  only the status may move from VALID to VOIDED (and never back) plus notes.
* Invoices: once issued, number, family, amounts, dates and kind are frozen; a
  VOID invoice never changes status again. Balance columns stay writable for
  the payment service (their consistency is enforced by check constraints).
* Invoice lines of an issued invoice: only amount_paid and is_active may change.
* Charges on an active issued invoice: student, type, description and amounts are frozen.

SQLite has no equivalent here; the ORM guards and model rules apply there.
"""

from django.db import migrations

TABLES_NO_DELETE = [
    "finance_charge", "finance_invoice", "finance_invoiceitem", "finance_payment",
    "finance_paymentallocation", "finance_receipt", "finance_receiptvoid", "finance_refund",
]

FORWARD = """
CREATE OR REPLACE FUNCTION sr_reject(message text) RETURNS void AS $$
BEGIN
    RAISE EXCEPTION 'SR Wushu financial history: %', message USING ERRCODE = 'restrict_violation';
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION sr_forbid_delete() RETURNS trigger AS $$
BEGIN
    PERFORM sr_reject(TG_TABLE_NAME || ' rows cannot be deleted');
    RETURN NULL;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION sr_forbid_update() RETURNS trigger AS $$
BEGIN
    PERFORM sr_reject(TG_TABLE_NAME || ' rows cannot be changed');
    RETURN NULL;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION sr_guard_invoiceitem() RETURNS trigger AS $$
BEGIN
    IF (SELECT status FROM finance_invoice WHERE id = OLD.invoice_id) <> 'DRAFT'
       AND (OLD.invoice_id, OLD.charge_id, OLD.student_id, OLD.student_no, OLD.student_name, OLD.description,
            OLD.fee_type, OLD.period_start, OLD.period_end, OLD.quantity, OLD.unit_amount, OLD.discount,
            OLD.amount, OLD.position)
           IS DISTINCT FROM
           (NEW.invoice_id, NEW.charge_id, NEW.student_id, NEW.student_no, NEW.student_name, NEW.description,
            NEW.fee_type, NEW.period_start, NEW.period_end, NEW.quantity, NEW.unit_amount, NEW.discount,
            NEW.amount, NEW.position) THEN
        PERFORM sr_reject('lines of an issued invoice cannot be changed');
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION sr_guard_charge() RETURNS trigger AS $$
BEGIN
    IF (OLD.student_id, OLD.fee_type, OLD.description, OLD.quantity, OLD.unit_amount, OLD.discount, OLD.amount)
           IS DISTINCT FROM
       (NEW.student_id, NEW.fee_type, NEW.description, NEW.quantity, NEW.unit_amount, NEW.discount, NEW.amount)
       AND EXISTS (SELECT 1 FROM finance_invoiceitem i JOIN finance_invoice v ON v.id = i.invoice_id
                   WHERE i.charge_id = OLD.id AND i.is_active AND v.status <> 'DRAFT') THEN
        PERFORM sr_reject('a charge on an issued invoice cannot be changed');
    END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER sr_payment_frozen BEFORE UPDATE ON finance_payment FOR EACH ROW
    WHEN ((OLD.number, OLD.family_id, OLD.payer_name, OLD.amount, OLD.method, OLD.reference, OLD.received_at,
           OLD.idempotency_key, OLD.created_at)
          IS DISTINCT FROM
          (NEW.number, NEW.family_id, NEW.payer_name, NEW.amount, NEW.method, NEW.reference, NEW.received_at,
           NEW.idempotency_key, NEW.created_at)
          OR (OLD.status = 'VOIDED' AND NEW.status <> 'VOIDED'))
    EXECUTE FUNCTION sr_forbid_update();

CREATE TRIGGER sr_invoice_frozen BEFORE UPDATE ON finance_invoice FOR EACH ROW
    WHEN ((OLD.status <> 'DRAFT'
           AND (OLD.number, OLD.family_id, OLD.family_name, OLD.kind, OLD.currency, OLD.issue_date, OLD.due_date,
                OLD.subtotal, OLD.discount_total, OLD.total, OLD.issued_at, OLD.created_at)
               IS DISTINCT FROM
               (NEW.number, NEW.family_id, NEW.family_name, NEW.kind, NEW.currency, NEW.issue_date, NEW.due_date,
                NEW.subtotal, NEW.discount_total, NEW.total, NEW.issued_at, NEW.created_at))
          OR (OLD.status = 'VOID' AND NEW.status <> 'VOID'))
    EXECUTE FUNCTION sr_forbid_update();

CREATE TRIGGER sr_invoiceitem_frozen BEFORE UPDATE ON finance_invoiceitem FOR EACH ROW
    EXECUTE FUNCTION sr_guard_invoiceitem();

CREATE TRIGGER sr_charge_frozen BEFORE UPDATE ON finance_charge FOR EACH ROW
    EXECUTE FUNCTION sr_guard_charge();

CREATE TRIGGER sr_allocation_frozen BEFORE UPDATE ON finance_paymentallocation FOR EACH ROW
    EXECUTE FUNCTION sr_forbid_update();

CREATE TRIGGER sr_receipt_frozen BEFORE UPDATE ON finance_receipt FOR EACH ROW
    WHEN ((OLD.number, OLD.payment_id, OLD.issued_at, OLD.payer_name, OLD.total, OLD.content::text)
          IS DISTINCT FROM (NEW.number, NEW.payment_id, NEW.issued_at, NEW.payer_name, NEW.total, NEW.content::text))
    EXECUTE FUNCTION sr_forbid_update();

CREATE TRIGGER sr_receiptvoid_frozen BEFORE UPDATE ON finance_receiptvoid FOR EACH ROW
    WHEN ((OLD.receipt_id, OLD.voided_at, OLD.reason) IS DISTINCT FROM (NEW.receipt_id, NEW.voided_at, NEW.reason))
    EXECUTE FUNCTION sr_forbid_update();

CREATE TRIGGER sr_refund_frozen BEFORE UPDATE ON finance_refund FOR EACH ROW
    WHEN ((OLD.number, OLD.payment_id, OLD.allocation_id, OLD.amount, OLD.method, OLD.reference, OLD.reason,
           OLD.refunded_at, OLD.created_at)
          IS DISTINCT FROM
          (NEW.number, NEW.payment_id, NEW.allocation_id, NEW.amount, NEW.method, NEW.reference, NEW.reason,
           NEW.refunded_at, NEW.created_at))
    EXECUTE FUNCTION sr_forbid_update();
""" + "\n".join(
    f"CREATE TRIGGER sr_{table}_no_delete BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sr_forbid_delete();"
    for table in TABLES_NO_DELETE
)

BACKWARD = "\n".join(
    [f"DROP TRIGGER IF EXISTS sr_{table}_no_delete ON {table};" for table in TABLES_NO_DELETE]
    + [
        "DROP TRIGGER IF EXISTS sr_payment_frozen ON finance_payment;",
        "DROP TRIGGER IF EXISTS sr_invoice_frozen ON finance_invoice;",
        "DROP TRIGGER IF EXISTS sr_invoiceitem_frozen ON finance_invoiceitem;",
        "DROP TRIGGER IF EXISTS sr_charge_frozen ON finance_charge;",
        "DROP TRIGGER IF EXISTS sr_allocation_frozen ON finance_paymentallocation;",
        "DROP TRIGGER IF EXISTS sr_receipt_frozen ON finance_receipt;",
        "DROP TRIGGER IF EXISTS sr_receiptvoid_frozen ON finance_receiptvoid;",
        "DROP TRIGGER IF EXISTS sr_refund_frozen ON finance_refund;",
        "DROP FUNCTION IF EXISTS sr_guard_charge();",
        "DROP FUNCTION IF EXISTS sr_guard_invoiceitem();",
        "DROP FUNCTION IF EXISTS sr_forbid_update();",
        "DROP FUNCTION IF EXISTS sr_forbid_delete();",
        "DROP FUNCTION IF EXISTS sr_reject(text);",
    ]
)


def forward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(FORWARD, params=None)  # raw SQL: no placeholder interpolation


def backward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(BACKWARD, params=None)


class Migration(migrations.Migration):
    dependencies = [("finance", "0002_invoices_payments")]

    operations = [migrations.RunPython(forward, backward)]
