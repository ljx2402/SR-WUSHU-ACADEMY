from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.html import format_html, format_html_join

from . import registration_forms, services
from .models import Competition, CompetitionEvent, CompetitionRegistration, CompetitionResult, RegistrationFormField


class EventInline(admin.TabularInline):
    model = CompetitionEvent
    extra = 1


class FormFieldInline(admin.StackedInline):
    """The registration form builder: one block per custom question. Changes are
    a draft until "Publish registration form" is run on the competition."""

    model = RegistrationFormField
    extra = 0
    ordering = ("order", "id")
    fields = (("label", "key", "field_type"), ("required", "is_active", "order"), "help_text", "placeholder",
              "options", ("max_length", "min_value", "max_value"))
    verbose_name = "registration form field"
    verbose_name_plural = "Registration form (custom fields; publish to make changes live)"

    def get_readonly_fields(self, request, obj=None):
        return ()

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        form = formset.form

        class KeyLockedForm(form):
            """Answers are stored under the key, so an existing field's key is fixed."""

            def clean_key(self):
                key = self.cleaned_data.get("key")
                if self.instance.pk and key != self.instance.key:
                    raise ValidationError("The key cannot change; deactivate this field and add a new one.")
                return key

        formset.form = KeyLockedForm
        return formset


def _render_form(definition):
    """A read-only, escaped rendering of a form definition for preview."""
    if not definition:
        return format_html("<p>{}</p>", "No custom fields: parents choose the child and the event only.")
    return format_html(
        "<ol>{}</ol>",
        format_html_join("", "<li><strong>{}</strong> ({}{}){}{}</li>", (
            (f["label"], RegistrationFormField.FieldType(f["type"]).label, ", required" if f["required"] else "",
             format_html(" – options: {}", ", ".join(f["options"])) if f["options"] else "",
             format_html("<br><small>{}</small>", f["help_text"]) if f["help_text"] else "")
            for f in definition)),
    )


@admin.register(Competition)
class CompetitionAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "registration_deadline", "status", "form_status",
                    "form_version")
    list_filter = ("status", "form_status")
    search_fields = ("name", "organiser")
    inlines = [EventInline, FormFieldInline]
    readonly_fields = ("form_status", "form_version", "form_published_at", "unpublished_changes", "form_preview",
                       "published_form_view")
    actions = ["publish_registration_form", "unpublish_registration_form"]

    @admin.display(description="Unpublished changes")
    def unpublished_changes(self, obj):
        if obj.pk is None:
            return "—"
        return "Yes: publish to make them live" if registration_forms.draft_form(obj) != obj.published_form else "No"

    @admin.display(description="Preview (working copy)")
    def form_preview(self, obj):
        return _render_form(registration_forms.draft_form(obj)) if obj.pk else "—"

    @admin.display(description="Published form (what parents see)")
    def published_form_view(self, obj):
        return _render_form(obj.published_form) if obj.pk else "—"

    def _run(self, request, queryset, service, done):
        for competition in queryset:
            try:
                service(competition, request.user)
                self.message_user(request, f"{competition}: {done}.")
            except (ValidationError, PermissionDenied) as exc:
                self.message_user(request, f"{competition}: {'; '.join(getattr(exc, 'messages', [str(exc)]))}",
                                  messages.ERROR)

    @admin.action(description="Publish registration form", permissions=["change"])
    def publish_registration_form(self, request, queryset):
        self._run(request, queryset, registration_forms.publish_form, "registration form published")

    @admin.action(description="Unpublish registration form (stop parent registrations)", permissions=["change"])
    def unpublish_registration_form(self, request, queryset):
        self._run(request, queryset, registration_forms.unpublish_form, "registration form unpublished")


@admin.register(CompetitionEvent)
class CompetitionEventAdmin(admin.ModelAdmin):
    list_display = ("name", "competition", "event_type", "gender", "min_age", "max_age", "fee")
    list_filter = ("competition", "event_type")
    search_fields = ("name", "competition__name")


class ResultForm(forms.ModelForm):
    """Results only for confirmed (paid) registrations; the registration of an
    existing result is fixed. The model enforces the same rule on save."""

    class Meta:
        model = CompetitionResult
        fields = ["registration", "placing", "medal", "score", "remarks"]

    def clean(self):
        data = super().clean()
        registration = data.get("registration") or getattr(self.instance, "registration", None)
        if self.instance.pk and data.get("registration") and data["registration"].pk != self.instance.registration_id:
            raise ValidationError("A result cannot be moved to another registration.")
        if registration is not None and registration.status != CompetitionRegistration.Status.CONFIRMED:
            raise ValidationError(f"Results can only be recorded for a confirmed (paid) registration; this "
                                  f"registration is {registration.get_status_display().lower()}.")
        return data


class ResultInline(admin.StackedInline):
    model = CompetitionResult
    form = ResultForm
    extra = 0
    can_delete = False
    fields = ["placing", "medal", "score", "remarks"]

    def get_max_num(self, request, obj=None, **kwargs):
        # The result form appears only for a confirmed registration (or to show an existing result).
        if obj is None or (obj.status != CompetitionRegistration.Status.CONFIRMED and not hasattr(obj, "result")):
            return 0
        return 1


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
    # Status changes only through actions: an unpaid registration must never be confirmed.
    readonly_fields = ("status", "registered_by", "charge", "form_version", "submitted_answers")
    inlines = [ResultInline]
    actions = ["confirm", "reject"]

    @admin.display(description="Registration form answers")
    def submitted_answers(self, obj):
        if not obj.form_responses:
            return "—"
        return format_html("<dl>{}</dl>", format_html_join(
            "", "<dt>{}</dt><dd>{}</dd>", ((a["label"], a["display"] or "—") for a in obj.form_responses)))

    def get_readonly_fields(self, request, obj=None):
        if obj is not None:
            # The fee charge and invoice belong to this student and event; they cannot be re-pointed.
            return self.readonly_fields + ("event", "student")
        return self.readonly_fields

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        created = services.register(obj.student, obj.event, request.user, obj.notes)
        obj.pk = created.pk
        obj.refresh_from_db()

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Confirm selected registrations", permissions=["change"])
    def confirm(self, request, queryset):
        for registration in queryset.filter(status=CompetitionRegistration.Status.PENDING):
            try:
                services.confirm(registration, request.user)
            except ValidationError as exc:
                self.message_user(request, f"{registration}: {'; '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Reject / withdraw selected registrations", permissions=["change"])
    def reject(self, request, queryset):
        for registration in queryset.exclude(status__in=CompetitionRegistration.INACTIVE):
            services.withdraw(registration, request.user, "Rejected by admin", CompetitionRegistration.Status.REJECTED)


@admin.register(CompetitionResult)
class CompetitionResultAdmin(admin.ModelAdmin):
    form = ResultForm
    list_display = ("registration", "placing", "medal", "score")
    list_filter = ("medal", "registration__event__competition")
    search_fields = ("registration__student__full_name",)
    raw_id_fields = ("registration",)
    actions = None

    def get_readonly_fields(self, request, obj=None):
        return ("registration",) if obj is not None else ()

    def has_delete_permission(self, request, obj=None):
        return False
