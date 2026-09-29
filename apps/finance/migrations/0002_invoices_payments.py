"""Phase 1 finance: family invoices, document sequences, payments applied to
invoice lines, exceptional refunds.

Data steps (reversible):
* ReceiptSequence rows move to DocumentSequence(RECEIPT); receipt numbering
  continues unchanged.
* Pre-existing payments get a payment number, the family of the students they
  paid for, and received_at = received_on at 12:00 Malaysia time.
* Pre-existing allocations (payment -> charge) are attached to one issued
  "legacy" invoice per family covering every charge that had been paid, so no
  payment history is lost. Receipts are untouched.

Known legacy edge: before Phase 1 a payment could cover children who are now in
different (single-student) families, because siblings are never merged
automatically. Such a payment is recorded against the family of its first line,
while its lines stay linked to each family's legacy invoice. There is no
production data before Phase 1; for any real data, group the siblings into one
family first.
"""

import django.db.models.deletion
import django.db.models.expressions
import django.utils.timezone
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models

import datetime
from zoneinfo import ZoneInfo

KL = ZoneInfo("Asia/Kuala_Lumpur")
PREFIXES = {"INVOICE": "INV", "PAYMENT": "PAY"}


def _next_number(DocumentSequence, doc_type, year):
    seq, _ = DocumentSequence.objects.get_or_create(doc_type=doc_type, year=year)
    seq.last_number += 1
    seq.save(update_fields=["last_number"])
    return f"{PREFIXES[doc_type]}-{year}-{seq.last_number:06d}"


def copy_receipt_sequences(apps, schema_editor):
    ReceiptSequence = apps.get_model("finance", "ReceiptSequence")
    DocumentSequence = apps.get_model("finance", "DocumentSequence")
    for old in ReceiptSequence.objects.all():
        DocumentSequence.objects.create(doc_type="RECEIPT", year=old.year, last_number=old.last_number)


def restore_receipt_sequences(apps, schema_editor):
    ReceiptSequence = apps.get_model("finance", "ReceiptSequence")
    DocumentSequence = apps.get_model("finance", "DocumentSequence")
    for seq in DocumentSequence.objects.filter(doc_type="RECEIPT"):
        ReceiptSequence.objects.create(year=seq.year, last_number=seq.last_number)


def backfill_legacy_payments(apps, schema_editor):
    Payment = apps.get_model("finance", "Payment")
    PaymentAllocation = apps.get_model("finance", "PaymentAllocation")
    Charge = apps.get_model("finance", "Charge")
    Invoice = apps.get_model("finance", "Invoice")
    InvoiceItem = apps.get_model("finance", "InvoiceItem")
    DocumentSequence = apps.get_model("finance", "DocumentSequence")
    zero = Decimal("0.00")

    for payment in Payment.objects.order_by("received_on", "id"):
        first = PaymentAllocation.objects.filter(payment=payment).select_related("charge__student").first()
        if first is None:
            raise RuntimeError(f"Legacy payment {payment.pk} has no allocations; cannot determine its family.")
        payment.family_id = first.charge.student.family_id
        payment.received_at = datetime.datetime.combine(payment.received_on, datetime.time(12, 0), tzinfo=KL)
        payment.number = _next_number(DocumentSequence, "PAYMENT", payment.received_on.year)
        payment.save(update_fields=["family", "received_at", "number"])

    charge_ids = PaymentAllocation.objects.values_list("charge_id", flat=True).distinct()
    by_family = {}
    for charge in Charge.objects.filter(pk__in=charge_ids).select_related("student").order_by("pk"):
        by_family.setdefault(charge.student.family_id, []).append(charge)
    for family_id, charges in by_family.items():
        family = charges[0].student.family
        issued = min(c.created_at for c in charges)
        issue_date = issued.astimezone(KL).date()
        invoice = Invoice.objects.create(
            family_id=family_id, family_name=family.name, kind="GENERAL", status="DRAFT",
            notes="Created by the Phase 1 migration from payments recorded before invoices existed.",
        )
        total = paid_total = subtotal = zero
        items = {}
        for position, charge in enumerate(charges, start=1):
            paid = sum((a.amount for a in PaymentAllocation.objects.filter(charge=charge, payment__status="VALID")), zero)
            gross = (charge.quantity * charge.unit_amount).quantize(Decimal("0.01"))
            items[charge.pk] = InvoiceItem.objects.create(
                invoice=invoice, charge=charge, student_id=charge.student_id, student_no=charge.student.student_no,
                student_name=charge.student.full_name, description=charge.description, fee_type=charge.fee_type,
                period_start=charge.period_start, period_end=charge.period_end, quantity=charge.quantity,
                unit_amount=charge.unit_amount, discount=charge.discount, amount=charge.amount, amount_paid=paid,
                is_active=True, position=position,
            )
            subtotal += gross
            total += charge.amount
            paid_total += paid
        status = "ISSUED" if paid_total == zero else "PAID" if paid_total >= total else "PARTIALLY_PAID"
        invoice.number = _next_number(DocumentSequence, "INVOICE", issue_date.year)
        invoice.status = status
        invoice.issue_date = issue_date
        invoice.issued_at = issued
        invoice.subtotal, invoice.total, invoice.discount_total = subtotal, total, subtotal - total
        invoice.amount_paid, invoice.balance_due = paid_total, total - paid_total
        invoice.save()
        for allocation in PaymentAllocation.objects.filter(charge_id__in=items):
            allocation.invoice_id = invoice.pk
            allocation.invoice_item_id = items[allocation.charge_id].pk
            allocation.save(update_fields=["invoice", "invoice_item"])


def restore_legacy_payment_fields(apps, schema_editor):
    Payment = apps.get_model("finance", "Payment")
    for payment in Payment.objects.all():
        payment.received_on = payment.received_at.astimezone(KL).date()
        payment.save(update_fields=["received_on"])


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0004_family"),
        ("finance", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentSequence",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "doc_type",
                    models.CharField(
                        choices=[
                            ("INVOICE", "Invoice"),
                            ("PAYMENT", "Payment"),
                            ("RECEIPT", "Receipt"),
                            ("REFUND", "Refund"),
                        ],
                        max_length=10,
                    ),
                ),
                ("year", models.PositiveIntegerField()),
                ("last_number", models.PositiveIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name="Invoice",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "number",
                    models.CharField(
                        blank=True,
                        editable=False,
                        help_text="Assigned when the invoice is issued.",
                        max_length=30,
                        null=True,
                        unique=True,
                    ),
                ),
                (
                    "family_name",
                    models.CharField(
                        blank=True,
                        editable=False,
                        help_text="Snapshot at issue.",
                        max_length=200,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("GENERAL", "Fees"),
                            ("COMPETITION", "Competition registration"),
                        ],
                        default="GENERAL",
                        max_length=12,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("DRAFT", "Draft"),
                            ("ISSUED", "Issued (unpaid)"),
                            ("PARTIALLY_PAID", "Partially paid"),
                            ("PAID", "Paid"),
                            ("VOID", "Void"),
                        ],
                        default="DRAFT",
                        editable=False,
                        max_length=15,
                    ),
                ),
                (
                    "currency",
                    models.CharField(default="MYR", editable=False, max_length=3),
                ),
                ("issue_date", models.DateField(blank=True, editable=False, null=True)),
                ("due_date", models.DateField(blank=True, null=True)),
                (
                    "subtotal",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        editable=False,
                        max_digits=10,
                    ),
                ),
                (
                    "discount_total",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        editable=False,
                        max_digits=10,
                    ),
                ),
                (
                    "total",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        editable=False,
                        max_digits=10,
                    ),
                ),
                (
                    "amount_paid",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        editable=False,
                        max_digits=10,
                    ),
                ),
                (
                    "balance_due",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        editable=False,
                        max_digits=10,
                    ),
                ),
                (
                    "amount_refunded",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        editable=False,
                        help_text="Exceptional refunds; does not reopen the invoice.",
                        max_digits=10,
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                (
                    "issued_at",
                    models.DateTimeField(blank=True, editable=False, null=True),
                ),
                (
                    "voided_at",
                    models.DateTimeField(blank=True, editable=False, null=True),
                ),
                ("void_reason", models.TextField(blank=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="InvoiceItem",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("student_no", models.CharField(max_length=20)),
                ("student_name", models.CharField(max_length=200)),
                ("description", models.CharField(max_length=255)),
                (
                    "fee_type",
                    models.CharField(
                        choices=[
                            ("TUITION", "Tuition / class fee"),
                            ("REGISTRATION", "Registration fee"),
                            ("UNIFORM", "Uniform fee"),
                            ("WEAPON", "Weapon charges"),
                            ("COMPETITION", "Competition fee"),
                            ("OTHER", "Other charges"),
                        ],
                        max_length=12,
                    ),
                ),
                ("period_start", models.DateField(blank=True, null=True)),
                ("period_end", models.DateField(blank=True, null=True)),
                ("quantity", models.DecimalField(decimal_places=2, max_digits=8)),
                ("unit_amount", models.DecimalField(decimal_places=2, max_digits=10)),
                (
                    "discount",
                    models.DecimalField(
                        decimal_places=2, default=Decimal("0.00"), max_digits=10
                    ),
                ),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10)),
                (
                    "amount_paid",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        editable=False,
                        max_digits=10,
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        editable=False,
                        help_text="False once the invoice is voided, so the charge can be re-invoiced.",
                    ),
                ),
                ("position", models.PositiveIntegerField(default=0)),
            ],
            options={
                "ordering": ["invoice", "position", "id"],
            },
        ),
        migrations.CreateModel(
            name="Refund",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "number",
                    models.CharField(editable=False, max_length=30, unique=True),
                ),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10)),
                (
                    "method",
                    models.CharField(
                        choices=[
                            ("CASH", "Cash"),
                            ("BANK_TRANSFER", "Bank transfer"),
                            ("DUITNOW", "DuitNow / QR"),
                            ("FPX", "FPX online banking"),
                            ("CARD", "Debit / credit card"),
                            ("CHEQUE", "Cheque"),
                            ("EWALLET", "E-wallet (TNG, GrabPay, ...)"),
                            ("OTHER", "Other"),
                        ],
                        max_length=15,
                    ),
                ),
                ("reference", models.CharField(blank=True, max_length=100)),
                ("reason", models.TextField()),
                (
                    "refunded_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-refunded_at", "-id"],
            },
        ),
        migrations.RunPython(copy_receipt_sequences, restore_receipt_sequences),
        migrations.DeleteModel(
            name="ReceiptSequence",
        ),
        migrations.AlterModelOptions(
            name="payment",
            options={"ordering": ["-received_at", "-id"]},
        ),
        migrations.AlterModelOptions(
            name="paymentallocation",
            options={"ordering": ["payment", "id"]},
        ),
        migrations.AddField(
            model_name="payment",
            name="family",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payments",
                to="academy.family",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="idempotency_key",
            field=models.CharField(
                blank=True,
                editable=False,
                help_text="Client-supplied key; a retried request returns the same payment.",
                max_length=64,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="number",
            field=models.CharField(
                editable=False, max_length=30, null=True, unique=True
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="received_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="payment",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AlterField(
            model_name="payment",
            name="payer_name",
            field=models.CharField(
                blank=True,
                help_text="Optional reference: who handed over the money, as stated.",
                max_length=200,
            ),
        ),
        migrations.AlterField(
            model_name="payment",
            name="status",
            field=models.CharField(
                choices=[("VALID", "Valid"), ("VOIDED", "Voided")],
                default="VALID",
                editable=False,
                max_length=6,
            ),
        ),
        migrations.AlterField(
            model_name="receipt",
            name="payer_name",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddConstraint(
            model_name="payment",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gt", 0)), name="payment_amount_positive"
            ),
        ),
        migrations.AddConstraint(
            model_name="documentsequence",
            constraint=models.UniqueConstraint(
                fields=("doc_type", "year"), name="unique_document_sequence"
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="family",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invoices",
                to="academy.family",
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="issued_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="voided_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="paymentallocation",
            name="invoice",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="allocations",
                to="finance.invoice",
            ),
        ),
        migrations.AddField(
            model_name="invoiceitem",
            name="charge",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invoice_items",
                to="finance.charge",
            ),
        ),
        migrations.AddField(
            model_name="invoiceitem",
            name="invoice",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="items",
                to="finance.invoice",
            ),
        ),
        migrations.AddField(
            model_name="invoiceitem",
            name="student",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="invoice_items",
                to="academy.student",
            ),
        ),
        migrations.AddField(
            model_name="paymentallocation",
            name="invoice_item",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="allocations",
                to="finance.invoiceitem",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentallocation",
            constraint=models.UniqueConstraint(
                fields=("payment", "invoice_item"),
                name="one_allocation_per_line_per_payment",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentallocation",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gt", 0)), name="allocation_amount_positive"
            ),
        ),
        migrations.AddField(
            model_name="refund",
            name="allocation",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="refunds",
                to="finance.paymentallocation",
            ),
        ),
        migrations.AddField(
            model_name="refund",
            name="payment",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="refunds",
                to="finance.payment",
            ),
        ),
        migrations.AddField(
            model_name="refund",
            name="recorded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="invoice",
            constraint=models.CheckConstraint(
                condition=models.Q(("total__gte", 0)), name="invoice_total_not_negative"
            ),
        ),
        migrations.AddConstraint(
            model_name="invoice",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "total",
                        django.db.models.expressions.CombinedExpression(
                            models.F("subtotal"), "-", models.F("discount_total")
                        ),
                    )
                ),
                name="invoice_total_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="invoice",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("amount_paid__gte", 0), ("amount_paid__lte", models.F("total"))
                ),
                name="invoice_paid_within_total",
            ),
        ),
        migrations.AddConstraint(
            model_name="invoice",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "balance_due",
                        django.db.models.expressions.CombinedExpression(
                            models.F("total"), "-", models.F("amount_paid")
                        ),
                    )
                ),
                name="invoice_balance_consistent",
            ),
        ),
        migrations.AddConstraint(
            model_name="invoice",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("amount_refunded__gte", 0),
                    ("amount_refunded__lte", models.F("amount_paid")),
                ),
                name="invoice_refund_within_paid",
            ),
        ),
        migrations.AddConstraint(
            model_name="invoice",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("number__isnull", True),
                    models.Q(("status", "DRAFT"), _negated=True),
                    _connector="OR",
                ),
                name="draft_has_no_number",
            ),
        ),
        migrations.AddConstraint(
            model_name="invoice",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("number__isnull", False),
                    ("status__in", ["DRAFT", "VOID"]),
                    _connector="OR",
                ),
                name="issued_invoice_has_number",
            ),
        ),
        migrations.AddConstraint(
            model_name="invoiceitem",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_active", True)),
                fields=("charge",),
                name="one_active_invoice_per_charge",
            ),
        ),
        migrations.AddConstraint(
            model_name="invoiceitem",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gte", 0)),
                name="invoice_item_amount_not_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="invoiceitem",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("amount_paid__gte", 0), ("amount_paid__lte", models.F("amount"))
                ),
                name="invoice_item_paid_within_amount",
            ),
        ),
        migrations.AddConstraint(
            model_name="refund",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gt", 0)), name="refund_amount_positive"
            ),
        ),
        migrations.RunPython(backfill_legacy_payments, restore_legacy_payment_fields),
        migrations.AlterField(
            model_name="payment",
            name="number",
            field=models.CharField(editable=False, max_length=30, unique=True),
        ),
        migrations.AlterField(
            model_name="payment",
            name="family",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="academy.family"
            ),
        ),
        migrations.AlterField(
            model_name="paymentallocation",
            name="invoice",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="allocations", to="finance.invoice"
            ),
        ),
        migrations.AlterField(
            model_name="paymentallocation",
            name="invoice_item",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="allocations", to="finance.invoiceitem"
            ),
        ),
        migrations.RemoveField(
            model_name="payment",
            name="parent",
        ),
        migrations.RemoveField(
            model_name="payment",
            name="received_on",
        ),
    ]
