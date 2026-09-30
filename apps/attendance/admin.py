from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from apps.accounts.capabilities import Cap, can

from . import services
from .models import AttendanceRecord, AttendanceStatus


class CorrectionForm(forms.Form):
    status = forms.ChoiceField(choices=AttendanceStatus.choices)
    remarks = forms.CharField(max_length=255, required=False)
    reason = forms.CharField(max_length=255, widget=forms.Textarea(attrs={"rows": 3}),
                             help_text="Required. Stored in the audit log with the previous and new values.")


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    """Read-only. Attendance is recorded from a session's "Attendance" page and
    corrected with "Correct attendance" here; both go through the attendance
    service (expected roster, 48-hour coach window, reason, audit). There are no
    bulk actions."""

    list_display = ("session", "student", "status", "recorded_by", "updated_at")
    list_filter = ("status", "session__training_class", "session__date")
    search_fields = ("student__full_name", "student__student_no")
    actions = None
    change_form_template = "admin/attendance/attendancerecord/change_form.html"

    def get_urls(self):
        return [
            path("<int:pk>/correct/", self.admin_site.admin_view(self.correct_view),
                 name="attendance_attendancerecord_correct"),
        ] + super().get_urls()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def render_change_form(self, request, context, *args, **kwargs):
        context["can_correct"] = can(request.user, (Cap.ATTENDANCE_TAKE_ANY, Cap.ATTENDANCE_CORRECT))
        return super().render_change_form(request, context, *args, **kwargs)

    def correct_view(self, request, pk):
        if not can(request.user, (Cap.ATTENDANCE_TAKE_ANY, Cap.ATTENDANCE_CORRECT)):
            raise PermissionDenied
        record = get_object_or_404(AttendanceRecord.objects.select_related("session", "student"), pk=pk)
        form = CorrectionForm(request.POST or None, initial={"status": record.status, "remarks": record.remarks})
        if request.method == "POST" and form.is_valid():
            try:
                services.mark_attendance(record.session, record.student, form.cleaned_data["status"], request.user,
                                         form.cleaned_data["remarks"], form.cleaned_data["reason"])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Attendance corrected.")
                return redirect(reverse("admin:attendance_attendancerecord_change", args=[pk]))
        context = {**self.admin_site.each_context(request), "opts": self.model._meta, "form": form, "object": record,
                   "title": f"Correct attendance – {record}",
                   "warning": f"Coach edit window for this session closes "
                              f"{services.coach_edit_deadline(record.session):%Y-%m-%d %H:%M}."}
        return render(request, "admin/finance/reason_form.html", context)
