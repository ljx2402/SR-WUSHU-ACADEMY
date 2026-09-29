from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from apps.accounts.capabilities import Cap, can

from . import services
from .models import CoachRate, PayrollAdjustment, PayrollRun, Payslip, PayslipLine


@admin.register(CoachRate)
class CoachRateAdmin(admin.ModelAdmin):
    list_display = ("coach", "rate_type", "amount", "training_class", "effective_from", "effective_to")
    list_filter = ("rate_type", "coach")
    search_fields = ("coach__full_name",)
    autocomplete_fields = ("coach", "training_class")


@admin.register(PayrollAdjustment)
class PayrollAdjustmentAdmin(admin.ModelAdmin):
    list_display = ("coach", "year", "month", "kind", "description", "amount")
    list_filter = ("year", "month", "kind")
    search_fields = ("coach__full_name", "description")
    autocomplete_fields = ("coach",)


class PayslipLineInline(admin.TabularInline):
    model = PayslipLine
    extra = 0
    can_delete = False
    readonly_fields = ("kind", "description", "session", "quantity", "rate", "amount")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    list_display = ("coach", "run", "regular_sessions", "substitute_sessions", "hours", "gross_pay",
                    "total_deductions", "net_pay")
    list_filter = ("run",)
    search_fields = ("coach__full_name",)
    inlines = [PayslipLineInline]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in Payslip._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PayrollRun)
class PayrollRunAdmin(admin.ModelAdmin):
    list_display = ("__str__", "status", "calculated_at", "finalized_at", "finalized_by")
    readonly_fields = ("status", "calculated_at", "finalized_at", "finalized_by")
    actions = ["calculate", "finalize"]

    def has_delete_permission(self, request, obj=None):
        return obj is not None and not obj.is_locked

    def has_calculate_permission(self, request):
        return can(request.user, Cap.PAYROLL_PREPARE)

    def has_finalize_permission(self, request):
        return can(request.user, Cap.PAYROLL_FINALIZE)

    @admin.action(description="Calculate / recalculate payslips", permissions=["calculate"])
    def calculate(self, request, queryset):
        for run in queryset:
            try:
                services.calculate_run(run.year, run.month, request.user)
                self.message_user(request, f"{run} calculated.")
            except ValidationError as exc:
                self.message_user(request, f"{run}: {'; '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Finalize (locks payslips) – super admin only", permissions=["finalize"])
    def finalize(self, request, queryset):
        for run in queryset:
            try:
                services.finalize_run(run, request.user)
                self.message_user(request, f"{run} finalized.")
            except ValidationError as exc:
                self.message_user(request, f"{run}: {'; '.join(exc.messages)}", messages.ERROR)
