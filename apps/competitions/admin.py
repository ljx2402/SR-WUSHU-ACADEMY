from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from . import services
from .models import Competition, CompetitionEvent, CompetitionRegistration, CompetitionResult


class EventInline(admin.TabularInline):
    model = CompetitionEvent
    extra = 1


@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "registration_deadline", "status")
    list_filter = ("status",)
    search_fields = ("name", "organiser")
    inlines = [EventInline]


@admin.register(CompetitionEvent)
class CompetitionEventAdmin(admin.ModelAdmin):
    list_display = ("name", "competition", "event_type", "gender", "min_age", "max_age", "fee")
    list_filter = ("competition", "event_type")
    search_fields = ("name", "competition__name")


class ResultInline(admin.StackedInline):
    model = CompetitionResult
    extra = 0


class RegistrationForm(forms.ModelForm):
    class Meta:
        model = CompetitionRegistration
        fields = "__all__"

    def clean(self):
        data = super().clean()
        student, event = data.get("student"), data.get("event")
        if self.instance.pk is None and student and event:
            errors = event.eligibility_errors(student)
            if event.active_registrations().filter(student=student).exists():
                errors.append(f"{student.full_name} is already registered for {event.name}.")
            if errors:
                raise ValidationError(errors)
        return data


@admin.register(CompetitionRegistration)
class CompetitionRegistrationAdmin(admin.ModelAdmin):
    form = RegistrationForm
    list_display = ("student", "event", "status", "registered_by", "registered_at")
    list_filter = ("status", "event__competition", "event__event_type")
    search_fields = ("student__full_name", "student__student_no", "event__name")
    autocomplete_fields = ("student",)
    readonly_fields = ("registered_by", "charge")
    inlines = [ResultInline]
    actions = ["confirm", "reject"]

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        created = services.register(obj.student, obj.event, request.user, obj.notes)
        obj.pk = created.pk
        obj.refresh_from_db()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Confirm selected registrations")
    def confirm(self, request, queryset):
        for registration in queryset.filter(status=CompetitionRegistration.Status.PENDING):
            registration.status = CompetitionRegistration.Status.CONFIRMED
            registration.save()

    @admin.action(description="Reject / withdraw selected registrations")
    def reject(self, request, queryset):
        for registration in queryset.exclude(status__in=CompetitionRegistration.INACTIVE):
            services.withdraw(registration, request.user, "Rejected by admin", CompetitionRegistration.Status.REJECTED)


@admin.register(CompetitionResult)
class CompetitionResultAdmin(admin.ModelAdmin):
    list_display = ("registration", "placing", "medal", "score")
    list_filter = ("medal", "registration__event__competition")
    search_fields = ("registration__student__full_name",)
    raw_id_fields = ("registration",)
