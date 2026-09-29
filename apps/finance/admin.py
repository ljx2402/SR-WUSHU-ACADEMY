from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from apps.audit.context import audit_context

from . import services
from .models import (
    Charge,
    ChargeItem,
    ClassFee,
    Payment,
    PaymentAllocation,
    Receipt,
    ReceiptVoid,
    StudentFeePlan,
)


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
    list_display = ("student", "description", "fee_type", "amount", "paid", "status", "due_date")
    list_filter = ("status", "fee_type", "period_start")
    search_fields = ("student__full_name", "student__student_no", "description")
    autocomplete_fields = ("student",)
    readonly_fields = ("amount", "status", "created_by", "class_fee", "enrollment")
    fields = ("student", "fee_type", "charge_item", "description", "quantity", "unit_amount", "discount", "amount",
              "period_start", "period_end", "due_date", "status", "notes", "class_fee", "enrollment", "created_by")
    actions = ["cancel_charges", "waive_charges"]

    @admin.display(description="Paid (RM)")
    def paid(self, obj):
        return obj.amount_paid

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

    @admin.action(description="Cancel selected charges")
    def cancel_charges(self, request, queryset):
        self._close(request, queryset, waive=False)

    @admin.action(description="Waive selected charges")
    def waive_charges(self, request, queryset):
        self._close(request, queryset, waive=True)


class AllocationFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        rows = [f.cleaned_data for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if not rows:
            raise ValidationError("Allocate the payment to at least one charge.")
        total = sum((r["amount"] for r in rows), Decimal("0"))
        if self.instance.amount is not None and total != self.instance.amount:
            raise ValidationError(f"Allocations total RM {total}, but the payment is RM {self.instance.amount}.")
        for row in rows:
            charge = row["charge"]
            if charge.status in (Charge.Status.CANCELLED, Charge.Status.WAIVED):
                raise ValidationError(f"'{charge.description}' is {charge.get_status_display().lower()}.")
            if row["amount"] > charge.balance:
                raise ValidationError(f"RM {row['amount']} exceeds the balance RM {charge.balance} of '{charge.description}'.")


class AllocationInline(admin.TabularInline):
    model = PaymentAllocation
    formset = AllocationFormSet
    extra = 1
    autocomplete_fields = ("charge",)
    can_delete = False

    def has_change_permission(self, request, obj=None):
        return obj is None

    def has_add_permission(self, request, obj=None):
        return obj is None


class VoidForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea, help_text="Why is this payment being voided?")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "received_on", "payer_name", "amount", "method", "reference", "status", "receipt_link")
    list_filter = ("status", "method", "received_on")
    search_fields = ("payer_name", "reference", "receipt__number", "allocations__charge__student__full_name")
    autocomplete_fields = ("parent",)
    inlines = [AllocationInline]
    date_hierarchy = "received_on"

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ("status", "received_by")
        return [f.name for f in Payment._meta.fields if f.name != "notes"]

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Receipt")
    def receipt_link(self, obj):
        receipt = getattr(obj, "receipt", None)
        if not receipt:
            return "-"
        label = f"{receipt.number} (VOID)" if receipt.is_void else receipt.number
        return format_html('<a href="{}" target="_blank">{}</a>', reverse("receipt-print", args=[receipt.pk]), label)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.received_by = request.user
        with audit_context(request.user, "Payment received"):
            super().save_model(request, obj, form, change)

    @transaction.atomic
    def save_related(self, request, form, formsets, change):
        with audit_context(request.user, "Payment received"):
            super().save_related(request, form, formsets, change)
            payment = form.instance
            if not change:
                for allocation in payment.allocations.select_related("charge"):
                    allocation.charge.refresh_status()
                receipt = services.issue_receipt(payment, request.user)
                self.message_user(request, f"Receipt {receipt.number} issued.")

    def get_urls(self):
        return [
            path("<int:pk>/void/", self.admin_site.admin_view(self.void_view), name="finance_payment_void"),
        ] + super().get_urls()

    def void_view(self, request, pk):
        payment = get_object_or_404(Payment, pk=pk)
        form = VoidForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                services.void_payment(payment, form.cleaned_data["reason"], request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, "Payment and receipt voided.")
            return redirect(reverse("admin:finance_payment_change", args=[pk]))
        context = {**self.admin_site.each_context(request), "payment": payment, "form": form,
                   "opts": self.model._meta, "title": f"Void payment #{payment.pk}"}
        return render(request, "admin/finance/payment/void.html", context)


class ReceiptVoidInline(admin.StackedInline):
    model = ReceiptVoid
    can_delete = False
    readonly_fields = ("voided_at", "voided_by", "reason")
    extra = 0

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    list_display = ("number", "issued_at", "payer_name", "total", "void", "print_link")
    search_fields = ("number", "payer_name")
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
