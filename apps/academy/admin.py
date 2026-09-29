import datetime

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone

from apps.accounts.models import Coach

from . import services
from .models import (
    ClassCoach,
    ClassSchedule,
    Enrollment,
    Guardianship,
    Program,
    SessionCoach,
    Student,
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

    @admin.action(description="Generate sessions for the next 31 days from the timetable")
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


class StatusHistoryInline(admin.TabularInline):
    model = StudentStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ("previous_status", "status", "effective_date", "reason", "recorded_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Student)
class StudentAdmin(NoDeleteMixin, admin.ModelAdmin):
    list_display = ("student_no", "full_name", "chinese_name", "gender", "date_of_birth", "join_date", "status")
    list_filter = ("status", "gender", "enrollments__training_class__category", "enrollments__training_class")
    search_fields = ("student_no", "full_name", "chinese_name", "ic_number", "guardianships__parent__full_name")
    inlines = [GuardianshipInline, EnrollmentInline, StatusHistoryInline]
    date_hierarchy = "join_date"

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
    fields = ("coach", "role", "status", "replaces", "access_starts_at", "access_ends_at", "reason")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class SubstituteForm(forms.Form):
    substitute = forms.ModelChoiceField(queryset=Coach.objects.filter(is_active=True))
    replaces = forms.ModelChoiceField(queryset=Coach.objects.all(), required=False,
                                      help_text="The original coach who cannot attend.")
    reason = forms.CharField(max_length=255, required=False)


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
        ] + super().get_urls()

    def assign_substitute_view(self, request, pk):
        session = TrainingSession.objects.get(pk=pk)
        form = SubstituteForm(request.POST or None)
        form.fields["replaces"].queryset = Coach.objects.filter(session_slots__session=session)
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


@admin.register(SessionCoach)
class SessionCoachAdmin(admin.ModelAdmin):
    list_display = ("session", "coach", "role", "status", "replaces", "access_starts_at", "access_ends_at")
    list_filter = ("role", "status")
    search_fields = ("coach__full_name", "session__training_class__name")

    def has_add_permission(self, request):
        # Use the "Assign substitute" button on a session so access windows are set correctly.
        return False

    def has_delete_permission(self, request, obj=None):
        return False
