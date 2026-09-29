"""Django admin for finance.

Financial state only changes through ``apps.finance.services`` (which lock rows,
validate and audit). Money fields, statuses and numbers are read-only here;
issuing, voiding, recording payments and refunds are dedicated actions/pages
that call the services and check the caller's capabilities.
"""

from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.accounts.capabilities import Cap, can

from . import services
from .models import (
    Charge,
    ChargeItem,
    ClassFee,
    Invoice,
    InvoiceItem,
    Payment,
    PaymentAllocation,
    Receipt,
    ReceiptVoid,
    Refund,
    StudentFeePlan,
)


def _errors(exc):
    return "; ".join(getattr(exc, "messages", [str(exc)]))


def _admin_page(model_admin, request, template, title, **context):
    return render(request, template, {**model_admin.admin_site.each_context(request), "opts": model_admin.model._meta,
                                      "title": title, **context})


@admin.register(ClassFee)
class ClassFeeAdmin(admin.ModelAdmin):
    list_display = ("training_class", "name", "fee_type", "billing_cycle", "amount", "is_default",
                    "effective_from", "effective_to")
    list_filter = ("training_class__category", "billing_cycle", "fee_type", "training_class")
    search_fields = ("training_class__name", "name")
    autocomplete_fields = ("training_class",)


@admin.register(StudentFeePlan)
class StudentFeePlanAdmin(admin.ModelAdmin):
    list_display = ("enrollment", "class_fee", "custom_amount", "discount_percent", "start_date", "end_date", "reason")
    search_fields = ("enrollment__student__full_name", "enrollment__student__student_no")
    raw_id_fields = ("enrollment",)


@admin.register(ChargeItem)
class ChargeItemAdmin(admin.ModelAdmin):
    list_display = ("name", "fee_type", "default_amount", "is_active")
    list_filter = ("fee_type", "is_active")
    search_fields = ("name",)


@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_display = ("student", "description", "fee_type", "amount", "paid", "status", "invoice", "due_date")
    list_filter = ("status", "fee_type", "period_start")
    search_fields = ("student__full_name", "student__student_no", "description")
    autocomplete_fields = ("student",)
    readonly_fields = ("amount", "status", "created_by", "class_fee", "enrollment")
    fields = ("student", "fee_type", "charge_item", "description", "quantity", "unit_amount", "discount", "amount",
              "period_start", "period_end", "due_date", "status", "notes", "class_fee", "enrollment", "created_by")
    actions = ["cancel_charges", "waive_charges", "invoice_charges"]

    @admin.display(description="Paid (RM)")
    def paid(self, obj):
        return obj.amount_paid

    @admin.display(description="Invoice")
    def invoice(self, obj):
        item = obj.active_invoice_item()
        return str(item.invoice) if item else "—"

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and (obj.active_invoice_item() or obj.valid_allocations().exists()):
            return [f for f in self.fields if f != "notes"]  # invoiced / paid: frozen
        return self.readonly_fields

    def has_invoice_permission(self, request):
        return can(request.user, Cap.FINANCE_INVOICES_MANAGE)

    @admin.action(description="Create draft invoices for selected charges (one per family)", permissions=["invoice"])
    def invoice_charges(self, request, queryset):
        by_family = {}
        for charge in queryset.select_related("student__family"):
            by_family.setdefault(charge.student.family, []).append(charge)
        for family, charges in by_family.items():
            try:
                invoice = services.create_invoice(family, charges, request.user)
                self.message_user(request, f"Drafted {invoice} for {family.name}.")
            except ValidationError as exc:
                self.message_user(request, f"{family.name}: {_errors(exc)}", messages.ERROR)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False

    def _close(self, request, queryset, waive):
        done = 0
        for charge in queryset:
            try:
                services.cancel_charge(charge, "Waived via admin" if waive else "Cancelled via admin", request.user, waive)
                done += 1
            except ValidationError as exc:
                self.message_user(request, f"{charge}: {'; '.join(exc.messages)}", messages.ERROR)
        self.message_user(request, f"Updated {done} charge(s).")

    @admin.action(description="Cancel selected charges", permissions=["change"])
    def cancel_charges(self, request, queryset):
        self._close(request, queryset, waive=False)

    @admin.action(description="Waive selected charges", permissions=["change"])
    def waive_charges(self, request, queryset):
        self._close(request, queryset, waive=True)


class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), help_text="Required. Stored in the audit log.")


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    can_delete = False
    fields = ("student_name", "student_no", "description", "fee_type", "quantity", "unit_amount", "discount", "amount",
              "amount_paid", "is_active")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("__str__", "family", "kind", "status", "total", "amount_paid", "balance_due", "issue_date",
                    "due_date", "print_link")
    list_filter = ("status", "kind", "issue_date")
    search_fields = ("number", "family__name", "family_name", "items__student_name", "items__student_no")
    date_hierarchy = "created_at"
    inlines = [InvoiceItemInline]
    actions = ["issue_selected"]
    change_form_template = "admin/finance/invoice/change_form.html"
    change_list_template = "admin/finance/invoice/change_list.html"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in Invoice._meta.fields]

    def has_add_permission(self, request):
        return False  # drafted from charges (Charges → action, or "Generate drafts")

    def has_delete_permission(self, request, obj=None):
        return False

    def has_issue_permission(self, request):
        return can(request.user, Cap.FINANCE_INVOICES_MANAGE)

    @admin.display(description="Print")
    def print_link(self, obj):
        return format_html('<a href="{}" target="_blank">Open</a>', reverse("invoice-print", args=[obj.pk]))

    @admin.action(description="Issue selected draft invoices", permissions=["issue"])
    def issue_selected(self, request, queryset):
        for invoice in queryset.filter(status=Invoice.Status.DRAFT):
            try:
                issued = services.issue_invoice(invoice, request.user)
                self.message_user(request, f"Issued {issued}.")
            except ValidationError as exc:
                self.message_user(request, f"{invoice}: {_errors(exc)}", messages.ERROR)

    def get_urls(self):
        return [
            path("generate/", self.admin_site.admin_view(self.generate_view), name="finance_invoice_generate"),
            path("<int:pk>/issue/", self.admin_site.admin_view(self.issue_view), name="finance_invoice_issue"),
            path("<int:pk>/void/", self.admin_site.admin_view(self.void_view), name="finance_invoice_void"),
        ] + super().get_urls()

    def render_change_form(self, request, context, *args, **kwargs):
        context["can_manage_invoices"] = can(request.user, Cap.FINANCE_INVOICES_MANAGE)
        return super().render_change_form(request, context, *args, **kwargs)

    def changelist_view(self, request, extra_context=None):
        extra = {"can_manage_invoices": can(request.user, Cap.FINANCE_INVOICES_MANAGE), **(extra_context or {})}
        return super().changelist_view(request, extra)

    def _require_manage(self, request):
        if not can(request.user, Cap.FINANCE_INVOICES_MANAGE):
            raise PermissionDenied

    def generate_view(self, request):
        self._require_manage(request)
        if request.method == "POST":
            try:
                created = services.generate_draft_invoices(request.user)
                messages.success(request, f"Drafted {len(created)} family invoice(s). Review and issue them.")
            except ValidationError as exc:
                messages.error(request, _errors(exc))
            return redirect(reverse("admin:finance_invoice_changelist"))
        pending = services.uninvoiced_charges().count()
        return _admin_page(self, request, "admin/finance/invoice/generate.html", "Generate draft invoices",
                           pending=pending)

    def issue_view(self, request, pk):
        self._require_manage(request)
        invoice = get_object_or_404(Invoice, pk=pk)
        if request.method == "POST":
            try:
                services.issue_invoice(invoice, request.user)
                messages.success(request, "Invoice issued.")
            except ValidationError as exc:
                messages.error(request, _errors(exc))
        return redirect(reverse("admin:finance_invoice_change", args=[pk]))

    def void_view(self, request, pk):
        self._require_manage(request)
        invoice = get_object_or_404(Invoice, pk=pk)
        form = ReasonForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                services.void_invoice(invoice, form.cleaned_data["reason"], request.user)
                messages.success(request, "Invoice voided; its charges can be invoiced again.")
            except ValidationError as exc:
                messages.error(request, _errors(exc))
            return redirect(reverse("admin:finance_invoice_change", args=[pk]))
        return _admin_page(self, request, "admin/finance/reason_form.html", f"Void invoice {invoice}", form=form,
                           object=invoice, warning="Voiding keeps the invoice on record, marked VOID.")


class PaymentRowForm(forms.Form):
    invoice = forms.ModelChoiceField(queryset=Invoice.objects.none(), required=False)
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invoice"].queryset = Invoice.objects.filter(status__in=Invoice.OPEN_FOR_PAYMENT).order_by("number")
        self.fields["invoice"].label_from_instance = (
            lambda inv: f"{inv.number} – {inv.family_name} – balance RM {inv.balance_due}")


PaymentRowFormSet = forms.formset_factory(PaymentRowForm, extra=3)


class RecordPaymentForm(forms.Form):
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"),
                                help_text="Total received. Must equal the amounts applied below.")
    method = forms.ChoiceField(choices=Payment.Method.choices)
    received_at = forms.DateTimeField(initial=timezone.now, help_text="When the money was received (Malaysia time).")
    reference = forms.CharField(required=False, help_text="Bank / transaction reference, cheque no.")
    payer_name = forms.CharField(required=False, help_text="Optional: who handed over the money.")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class RefundForm(forms.Form):
    allocation = forms.ModelChoiceField(queryset=PaymentAllocation.objects.none(), label="Payment line")
    amount = forms.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    method = forms.ChoiceField(choices=Payment.Method.choices, initial=Payment.Method.BANK_TRANSFER)
    reference = forms.CharField(required=False)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}),
                             help_text="Required. Competition fees are normally non-refundable; explain the exception.")

    def __init__(self, payment, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["allocation"].queryset = payment.allocations.select_related("invoice_item")
        self.fields["allocation"].label_from_instance = (
            lambda a: f"{a.invoice_item.student_name} – {a.invoice_item.description} – RM {a.amount}")


class AllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    can_delete = False
    fields = ("invoice", "invoice_item", "amount")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("number", "received_at", "family", "amount", "method", "reference", "status", "receipt_link")
    list_filter = ("status", "method", "received_at")
    search_fields = ("number", "payer_name", "reference", "receipt__number", "family__name",
                     "allocations__invoice__number", "allocations__invoice_item__student_name")
    inlines = [AllocationInline]
    date_hierarchy = "received_at"
    change_form_template = "admin/finance/payment/change_form.html"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in Payment._meta.fields]

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # recorded payments are never edited

    @admin.display(description="Receipt")
    def receipt_link(self, obj):
        receipt = getattr(obj, "receipt", None)
        if not receipt:
            return "-"
        label = f"{receipt.number} (VOID)" if receipt.is_void else receipt.number
        return format_html('<a href="{}" target="_blank">{}</a>', reverse("receipt-print", args=[receipt.pk]), label)

    def add_view(self, request, form_url="", extra_context=None):
        return self.record_view(request)

    def get_urls(self):
        return [
            path("<int:pk>/void/", self.admin_site.admin_view(self.void_view), name="finance_payment_void"),
            path("<int:pk>/refund/", self.admin_site.admin_view(self.refund_view), name="finance_payment_refund"),
        ] + super().get_urls()

    def render_change_form(self, request, context, *args, **kwargs):
        context["can_void"] = can(request.user, Cap.FINANCE_PAYMENTS_VOID)
        context["can_refund"] = can(request.user, Cap.FINANCE_REFUNDS_RECORD)
        return super().render_change_form(request, context, *args, **kwargs)

    def record_view(self, request):
        if not can(request.user, Cap.FINANCE_PAYMENTS_RECORD):
            raise PermissionDenied
        form = RecordPaymentForm(request.POST or None)
        rows = PaymentRowFormSet(request.POST or None, prefix="rows")
        if request.method == "POST" and form.is_valid() and rows.is_valid():
            allocations = [(r["invoice"], r["amount"]) for r in rows.cleaned_data if r.get("invoice") and r.get("amount")]
            data = form.cleaned_data
            try:
                payment, receipt = services.record_payment(
                    allocations, data["method"], request.user, amount=data["amount"], payer_name=data["payer_name"],
                    reference=data["reference"], received_at=data["received_at"], notes=data["notes"])
            except ValidationError as exc:
                form.add_error(None, _errors(exc))
            else:
                messages.success(request, f"Payment {payment.number} recorded; receipt {receipt.number} issued.")
                return redirect(reverse("admin:finance_payment_change", args=[payment.pk]))
        return _admin_page(self, request, "admin/finance/payment/record.html", "Record payment", form=form, rows=rows)

    def void_view(self, request, pk):
        if not can(request.user, Cap.FINANCE_PAYMENTS_VOID):
            raise PermissionDenied
        payment = get_object_or_404(Payment, pk=pk)
        form = ReasonForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                services.void_payment(payment, form.cleaned_data["reason"], request.user)
                messages.success(request, "Payment and receipt voided.")
            except ValidationError as exc:
                messages.error(request, _errors(exc))
            return redirect(reverse("admin:finance_payment_change", args=[pk]))
        return _admin_page(self, request, "admin/finance/reason_form.html", f"Void payment {payment.number}",
                           form=form, object=payment,
                           warning="Voiding keeps the payment and its receipt on record, marked VOID, and re-opens "
                                   "the invoices. This cannot be undone.")

    def refund_view(self, request, pk):
        if not can(request.user, Cap.FINANCE_REFUNDS_RECORD):
            raise PermissionDenied
        payment = get_object_or_404(Payment, pk=pk)
        form = RefundForm(payment, request.POST or None)
        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data
            try:
                refund = services.record_exceptional_refund(data["allocation"], data["amount"], data["reason"],
                                                            request.user, method=data["method"],
                                                            reference=data["reference"])
                messages.success(request, f"Refund {refund.number} recorded.")
                return redirect(reverse("admin:finance_payment_change", args=[pk]))
            except ValidationError as exc:
                form.add_error(None, _errors(exc))
        return _admin_page(self, request, "admin/finance/reason_form.html",
                           f"Exceptional refund – payment {payment.number}", form=form, object=payment,
                           warning="Competition fees are non-refundable. Record a refund only for an authorized "
                                   "exception. The original payment and receipt stay unchanged.")


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    list_display = ("number", "refunded_at", "payment", "amount", "method", "recorded_by", "reason")
    search_fields = ("number", "payment__number", "reason")
    date_hierarchy = "refunded_at"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in Refund._meta.fields]

    def has_add_permission(self, request):
        return False  # from the payment page ("Exceptional refund")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReceiptVoidInline(admin.StackedInline):
    model = ReceiptVoid
    can_delete = False
    readonly_fields = ("voided_at", "voided_by", "reason")
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("number", "issued_at", "payment", "total", "void", "print_link")
    search_fields = ("number", "payment__number", "payer_name")
    date_hierarchy = "issued_at"
    inlines = [ReceiptVoidInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in Receipt._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(boolean=True)
    def void(self, obj):
        return obj.is_void

    @admin.display(description="Print")
    def print_link(self, obj):
        return format_html('<a href="{}" target="_blank">Print</a>', reverse("receipt-print", args=[obj.pk]))
