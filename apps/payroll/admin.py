from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.html import format_html_join

from apps.accounts.capabilities import Cap, can

from . import services
from .models import CoachRate, PayrollAdjustment, PayrollRun, Payslip, PayslipLine


class CoachRateForm(forms.ModelForm):
    class Meta:
        model = CoachRate
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "rate_type" in self.fields:
            # Only per-session rate types can be chosen; legacy rows stay as history.
            self.fields["rate_type"].choices = [c for c in CoachRate.RateType.choices if c[0] in CoachRate.ACTIVE_TYPES]


@admin.register(CoachRate)
class CoachRateAdmin(admin.ModelAdmin):
    form = CoachRateForm
    list_display = ("coach", "rate_type", "amount", "training_class", "effective_from", "effective_to")
    list_filter = ("rate_type", "coach")
    search_fields = ("coach__full_name",)
    autocomplete_fields = ("coach", "training_class")

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and (obj.rate_type not in CoachRate.ACTIVE_TYPES or obj.paid_lines().filter(
                payslip__run__status=PayrollRun.Status.FINALIZED).exists()):
            # Legacy or already used in a finalized payroll: only end it (effective-to) or add notes.
            return ("coach", "rate_type", "amount", "training_class", "effective_from")
        return ()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PayrollAdjustment)
class PayrollAdjustmentAdmin(admin.ModelAdmin):
    list_display = ("coach", "year", "month", "kind", "description", "amount")
    list_filter = ("year", "month", "kind")
    search_fields = ("coach__full_name", "description")
    autocomplete_fields = ("coach",)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and PayrollRun.objects.filter(
                year=obj.year, month=obj.month, status=PayrollRun.Status.FINALIZED).exists():
            return [f.name for f in PayrollAdjustment._meta.fields]
        return ()

    def has_delete_permission(self, request, obj=None):
        if obj is not None and PayrollRun.objects.filter(
                year=obj.year, month=obj.month, status=PayrollRun.Status.FINALIZED).exists():
            return False
        return super().has_delete_permission(request, obj)


class PayslipLineInline(admin.TabularInline):
    model = PayslipLine
    extra = 0
    can_delete = False
    fields = ("kind", "description", "session", "slot", "rate", "rule", "issue", "amount")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    """Read-only: payslips are produced only by the payroll calculation."""

    list_display = ("coach", "run", "regular_sessions", "substitute_sessions", "gross_pay",
                    "total_deductions", "net_pay")
    list_filter = ("run",)
    search_fields = ("coach__full_name",)
    inlines = [PayslipLineInline]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PayrollRun)
class PayrollRunAdmin(admin.ModelAdmin):
    """Create a period (year + month), then "Calculate" (finance) and "Finalize"
    (super admin only). Status and results are never edited by hand."""

    list_display = ("__str__", "status", "issues", "calculated_at", "finalized_at", "finalized_by")
    readonly_fields = ("status", "calculated_at", "calculated_by", "finalized_at", "finalized_by", "issues",
                       "excluded_sessions")
    actions = ["calculate", "finalize"]

    def get_readonly_fields(self, request, obj=None):
        readonly = list(self.readonly_fields)
        if obj is not None:
            readonly += ["year", "month"]
        return readonly

    def get_fields(self, request, obj=None):
        return ["year", "month", "notes", *self.readonly_fields]

    @admin.display(description="Unresolved issues")
    def issues(self, obj):
        return obj.issue_count if obj.pk else 0

    @admin.display(description="Not paid (and why)")
    def excluded_sessions(self, obj):
        if not obj.excluded:
            return "–"
        return format_html_join(
            "\n", "<div>{} {} – {} ({}): {}</div>",
            ((row["date"], row["class"], row["coach"], row["role"].lower(), row["reason"]) for row in obj.excluded))

    def has_delete_permission(self, request, obj=None):
        return obj is not None and not obj.is_locked and can(request.user, Cap.PAYROLL_PREPARE)

    def has_calculate_permission(self, request):
        return can(request.user, Cap.PAYROLL_PREPARE)

    def has_finalize_permission(self, request):
        return can(request.user, Cap.PAYROLL_FINALIZE)

    @admin.action(description="Calculate / recalculate payslips", permissions=["calculate"])
    def calculate(self, request, queryset):
        for run in queryset:
            try:
                run = services.calculate_run(run.year, run.month, request.user)
                level = messages.SUCCESS if run.status == PayrollRun.Status.READY else messages.WARNING
                self.message_user(request, f"{run} calculated; {run.issue_count} issue(s).", level)
            except (ValidationError, PermissionDenied) as exc:
                self.message_user(request, f"{run}: {'; '.join(getattr(exc, 'messages', [str(exc)]))}", messages.ERROR)

    @admin.action(description="Finalize (locks payslips) – super admin only", permissions=["finalize"])
    def finalize(self, request, queryset):
        for run in queryset:
            try:
                services.finalize_run(run, request.user)
                self.message_user(request, f"{run} finalized.")
            except (ValidationError, PermissionDenied) as exc:
                self.message_user(request, f"{run}: {'; '.join(getattr(exc, 'messages', [str(exc)]))}", messages.ERROR)
