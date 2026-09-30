import datetime

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone

from apps.accounts.capabilities import Cap, can
from apps.accounts.models import Coach

from . import services
from .models import (
    ClassCoach,
    ClassSchedule,
    Enrollment,
    Family,
    Guardianship,
    Program,
    SessionCoach,
    Student,
    StudentAccount,
    StudentStatusHistory,
    Team,
    TrainingClass,
    TrainingSession,
)


class NoDeleteMixin:
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active")
    search_fields = ("name", "code")


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "program", "is_active")
    search_fields = ("name",)


class ClassScheduleInline(admin.TabularInline):
    model = ClassSchedule
    extra = 0


class ClassCoachInline(NoDeleteMixin, admin.TabularInline):
    model = ClassCoach
    extra = 0
    autocomplete_fields = ("coach",)


@admin.register(TrainingClass)
class TrainingClassAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ("name", "code", "category", "program", "team", "is_active")
    list_filter = ("category", "program", "is_active")
    search_fields = ("name", "code")
    inlines = [ClassScheduleInline, ClassCoachInline]
    actions = ["generate_next_month_sessions"]

    def has_generate_permission(self, request):
        return can(request.user, Cap.SESSIONS_MANAGE)

    @admin.action(description="Generate sessions for the next 31 days from the timetable", permissions=["generate"])
    def generate_next_month_sessions(self, request, queryset):
        start = timezone.localdate()
        end = start + datetime.timedelta(days=30)
        total = 0
        for training_class in queryset:
            total += len(services.generate_sessions(training_class, start, end, actor=request.user))
        self.message_user(request, f"Created {total} session(s).")


class GuardianshipInline(NoDeleteMixin, admin.TabularInline):
    model = Guardianship
    extra = 0
    autocomplete_fields = ("parent",)


class EnrollmentInline(NoDeleteMixin, admin.TabularInline):
    model = Enrollment
    extra = 0
    fields = ("training_class", "team", "coach", "start_date", "end_date", "end_reason")
    autocomplete_fields = ("training_class", "coach")


class StudentAccountInline(admin.StackedInline):
    model = StudentAccount
    extra = 0
    can_delete = False
    autocomplete_fields = ("user",)
    verbose_name = "student login"
    verbose_name_plural = "student login (the user also needs the STUDENT role, assigned by a super admin)"


class StatusHistoryInline(admin.TabularInline):
    model = StudentStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ("previous_status", "status", "effective_date", "reason", "recorded_at")

    def has_add_permission(self, request, obj=None):
        return False


class FamilyStudentInline(admin.TabularInline):
    model = Student
    fields = ("student_no", "full_name", "status")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True
    verbose_name_plural = "students (move a student here from the student's page)"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Family)
class FamilyAdmin(NoDeleteMixin, admin.ModelAdmin):
    """Households for invoicing. Siblings are grouped here explicitly; nothing
    is inferred from shared parents. There is no bill-to parent."""

    list_display = ("name", "id", "student_count", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "students__full_name", "students__student_no")
    inlines = [FamilyStudentInline]
    actions = ["draft_invoice"]

    @admin.display(description="Students")
    def student_count(self, obj):
        return obj.students.count()

    def has_invoice_permission(self, request):
        return can(request.user, Cap.FINANCE_INVOICES_MANAGE)

    @admin.action(description="Draft a family invoice from all uninvoiced charges", permissions=["invoice"])
    def draft_invoice(self, request, queryset):
        from apps.finance import services as finance_services

        try:
            created = finance_services.generate_draft_invoices(request.user, families=queryset)
            self.message_user(request, f"Drafted {len(created)} invoice(s).")
        except ValidationError as exc:
            self.message_user(request, "; ".join(exc.messages), messages.ERROR)


@admin.register(Student)
class StudentAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ("student_no", "full_name", "chinese_name", "gender", "date_of_birth", "family", "join_date", "status")
    autocomplete_fields = ("family",)
    list_filter = ("status", "gender", "enrollments__training_class__category", "enrollments__training_class")
    search_fields = ("student_no", "full_name", "chinese_name", "ic_number", "guardianships__parent__full_name")
    inlines = [GuardianshipInline, EnrollmentInline, StudentAccountInline, StatusHistoryInline]
    date_hierarchy = "join_date"

    # Finance staff have a student *directory*, not student records: they may use
    # the student picker on the charge form (it returns only "name [student no.]"),
    # searching by name or number, and nothing else.
    DIRECTORY_AUTOCOMPLETE_SOURCES = {("finance", "charge", "student")}
    DIRECTORY_SEARCH_FIELDS = ("student_no", "full_name", "chinese_name")

    def _is_directory_autocomplete(self, request):
        match = getattr(request, "resolver_match", None)
        source = (request.GET.get("app_label"), request.GET.get("model_name"), request.GET.get("field_name"))
        return (match is not None and match.url_name == "autocomplete"
                and source in self.DIRECTORY_AUTOCOMPLETE_SOURCES
                and can(request.user, Cap.STUDENTS_VIEW_DIRECTORY))

    def has_view_permission(self, request, obj=None):
        if super().has_view_permission(request, obj):
            return True
        return obj is None and self._is_directory_autocomplete(request)

    def get_search_fields(self, request):
        if not can(request.user, Cap.STUDENTS_VIEW_ALL):
            return self.DIRECTORY_SEARCH_FIELDS
        return super().get_search_fields(request)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        initial.setdefault("student_no", services.next_student_no())
        return initial


@admin.register(Enrollment)
class EnrollmentAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ("student", "training_class", "team", "coach", "start_date", "end_date")
    list_filter = ("training_class", "team", "end_date")
    search_fields = ("student__full_name", "student__student_no")
    autocomplete_fields = ("student", "training_class", "coach")


class SessionCoachInline(admin.TabularInline):
    model = SessionCoach
    fk_name = "session"
    extra = 0
    can_delete = False
    fields = ("coach", "role", "status", "replaces", "access_starts_at", "access_ends_at", "reason",
              "revoked_at", "revocation_reason")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class SubstituteForm(forms.Form):
    substitute = forms.ModelChoiceField(queryset=Coach.objects.filter(is_active=True))
    replaces = forms.ModelChoiceField(queryset=Coach.objects.all(), required=False,
                                      help_text="The original coach who cannot attend.")
    reason = forms.CharField(max_length=255, required=False)


class RevokeForm(forms.Form):
    reason = forms.CharField(max_length=255, widget=forms.Textarea(attrs={"rows": 3}),
                             help_text="Required. Stored on the authorization and in the audit log.")


class AttendanceSheetForm(forms.Form):
    """One status + remarks field per expected student; nothing else can be marked."""

    reason = forms.CharField(
        max_length=255, required=False, widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Required when changing a recorded mark, and for any correction after the coach edit window.",
    )

    def __init__(self, rows, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = rows
        from apps.attendance.models import AttendanceStatus

        for row in rows:
            student = row["student"]
            self.fields[f"status_{student.pk}"] = forms.ChoiceField(
                choices=AttendanceStatus.choices, initial=row["status"], label=str(student))
            self.fields[f"remarks_{student.pk}"] = forms.CharField(
                max_length=255, required=False, initial=row["remarks"], label="Remarks")
        self.expected_ids = {row["student"].pk for row in rows}

    def clean(self):
        cleaned = super().clean()
        for key in self.data:
            if key.startswith(("status_", "remarks_")):
                suffix = key.split("_", 1)[1]
                if not suffix.isdigit() or int(suffix) not in self.expected_ids:
                    raise forms.ValidationError(f"Student {suffix} is not on the roster for this session.")
        return cleaned

    def sheet_rows(self):
        return [(row["student"], self[f"status_{row['student'].pk}"], self[f"remarks_{row['student'].pk}"])
                for row in self.rows]

    def entries(self):
        return [(row["student"], self.cleaned_data[f"status_{row['student'].pk}"],
                 self.cleaned_data[f"remarks_{row['student'].pk}"]) for row in self.rows]


def _page(model_admin, request, template, title, **context):
    return render(request, template, {**model_admin.admin_site.each_context(request), "opts": model_admin.model._meta,
                                      "title": title, **context})


@admin.register(TrainingSession)
class TrainingSessionAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ("training_class", "date", "start_time", "end_time", "status")
    list_filter = ("status", "training_class__category", "training_class")
    date_hierarchy = "date"
    search_fields = ("training_class__name",)
    inlines = [SessionCoachInline]
    change_form_template = "admin/academy/trainingsession/change_form.html"

    def get_urls(self):
        return [
            path("<int:pk>/substitute/", self.admin_site.admin_view(self.assign_substitute_view),
                 name="academy_trainingsession_substitute"),
            path("<int:pk>/attendance/", self.admin_site.admin_view(self.attendance_view),
                 name="academy_trainingsession_attendance"),
        ] + super().get_urls()

    def render_change_form(self, request, context, *args, **kwargs):
        context["can_assign_substitute"] = can(request.user, Cap.SUBSTITUTE_ASSIGN)
        context["can_view_attendance"] = can(request.user, Cap.ATTENDANCE_VIEW_ALL)
        return super().render_change_form(request, context, *args, **kwargs)

    def assign_substitute_view(self, request, pk):
        if not can(request.user, Cap.SUBSTITUTE_ASSIGN):
            raise PermissionDenied
        session = get_object_or_404(TrainingSession, pk=pk)
        form = SubstituteForm(request.POST or None)
        form.fields["replaces"].queryset = Coach.objects.filter(
            session_slots__session=session, session_slots__role=SessionCoach.Role.REGULAR).distinct()
        if request.method == "POST" and form.is_valid():
            try:
                services.assign_substitute(
                    session,
                    form.cleaned_data["substitute"],
                    replaces=form.cleaned_data["replaces"],
                    actor=request.user,
                    reason=form.cleaned_data["reason"],
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, "Substitute coach assigned.")
                return redirect(reverse("admin:academy_trainingsession_change", args=[pk]))
        context = {**self.admin_site.each_context(request), "session": session, "form": form,
                   "opts": self.model._meta, "title": f"Assign substitute – {session}"}
        return render(request, "admin/academy/trainingsession/substitute.html", context)

    def attendance_view(self, request, pk):
        """The session's attendance sheet: every expected student, UNMARKED if not
        marked. Saving goes through the attendance service (roster, 48-hour window,
        reasons, audit), all or nothing."""
        from apps.attendance import services as attendance_services

        if not can(request.user, Cap.ATTENDANCE_VIEW_ALL):
            raise PermissionDenied
        session = get_object_or_404(TrainingSession, pk=pk)
        can_record = can(request.user, (Cap.ATTENDANCE_TAKE_ANY, Cap.ATTENDANCE_CORRECT))
        if request.method == "POST" and not can_record:
            raise PermissionDenied
        form = AttendanceSheetForm(attendance_services.session_sheet(session), request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                attendance_services.record_session_attendance(
                    session, form.entries(), request.user, form.cleaned_data["reason"])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Attendance saved.")
                return redirect(reverse("admin:academy_trainingsession_attendance", args=[pk]))
        summary = attendance_services.session_summary(session)
        return _page(self, request, "admin/academy/trainingsession/attendance.html", f"Attendance – {session}",
                     session=session, form=form, summary=summary, can_record=can_record,
                     state=attendance_services.session_state(session, summary),
                     deadline=attendance_services.coach_edit_deadline(session))


@admin.register(SessionCoach)
class SessionCoachAdmin(admin.ModelAdmin):
    """Read-only history of coach slots and substitute authorizations. Substitutes
    are authorized from a session ("Assign substitute coach") and ended here with
    "Revoke", both through the services; nothing is edited or deleted directly."""

    list_display = ("session", "coach", "role", "status", "replaces", "access_starts_at", "access_ends_at",
                    "revoked_at")
    list_filter = ("role", "status")
    search_fields = ("coach__full_name", "session__training_class__name")
    change_form_template = "admin/academy/sessioncoach/change_form.html"

    def get_urls(self):
        return [
            path("<int:pk>/revoke/", self.admin_site.admin_view(self.revoke_view),
                 name="academy_sessioncoach_revoke"),
        ] + super().get_urls()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def render_change_form(self, request, context, *args, **kwargs):
        obj = context.get("original")
        context["can_revoke"] = bool(obj and obj.is_active_substitute and can(request.user, Cap.SUBSTITUTE_REVOKE))
        return super().render_change_form(request, context, *args, **kwargs)

    def revoke_view(self, request, pk):
        if not can(request.user, Cap.SUBSTITUTE_REVOKE):
            raise PermissionDenied
        slot = get_object_or_404(SessionCoach, pk=pk)
        form = RevokeForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                services.revoke_substitute(slot, request.user, form.cleaned_data["reason"])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Substitute authorization revoked.")
                return redirect(reverse("admin:academy_sessioncoach_change", args=[pk]))
        return _page(self, request, "admin/finance/reason_form.html", f"Revoke substitute – {slot}", form=form,
                     object=slot, warning="The substitute loses access to this session immediately. The "
                                          "authorization stays on record as REVOKED and cannot be reactivated.")


@admin.register(StudentAccount)
class StudentAccountAdmin(admin.ModelAdmin):
    list_display = ("student", "user", "created_at")
    search_fields = ("student__full_name", "student__student_no", "user__username")
    autocomplete_fields = ("student", "user")

    def has_delete_permission(self, request, obj=None):
        return False
