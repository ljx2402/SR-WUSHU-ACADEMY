from django import forms
from django.contrib import admin, messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from .capabilities import Cap, Role, can, roles_of
from .models import Coach, LoginFailure, Parent, User
from .services import set_roles


class RoleChangeForm(forms.Form):
    roles = forms.MultipleChoiceField(choices=Role.choices, widget=forms.CheckboxSelectMultiple, required=False)
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), help_text="Required. Stored in the audit log.")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Roles are never edited as raw groups/flags here: they change only through
    the "Change roles" page, which requires a reason, is audited and signs the
    user out everywhere."""

    list_display = ("username", "first_name", "last_name", "roles_display", "is_active", "last_login")
    list_filter = ("groups", "is_active")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email", "phone")}),
        ("Roles (use “Change roles” to edit)", {"fields": ("roles_display", "is_active", "is_staff", "is_superuser")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (("Contact", {"fields": ("first_name", "last_name", "email", "phone")}),)
    readonly_fields = ("roles_display", "is_staff", "is_superuser", "last_login", "date_joined")
    change_form_template = "admin/accounts/user/change_form.html"

    @admin.display(description="Roles")
    def roles_display(self, obj):
        return ", ".join(Role(x).label for x in roles_for_display_obj(obj)) or "—"

    def get_urls(self):
        return [
            path("<int:pk>/roles/", self.admin_site.admin_view(self.change_roles_view), name="accounts_user_roles"),
        ] + super().get_urls()

    def render_change_form(self, request, context, *args, **kwargs):
        context["can_manage_roles"] = can(request.user, Cap.ROLES_MANAGE)
        return super().render_change_form(request, context, *args, **kwargs)

    def change_roles_view(self, request, pk):
        if not can(request.user, Cap.ROLES_MANAGE):
            raise PermissionDenied
        target = get_object_or_404(User, pk=pk)
        form = RoleChangeForm(request.POST or None, initial={"roles": sorted(roles_of(target))})
        if request.method == "POST" and form.is_valid():
            try:
                set_roles(target, form.cleaned_data["roles"], request.user, form.cleaned_data["reason"])
            except (ValidationError, PermissionDenied) as exc:
                messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
            else:
                if target.pk == request.user.pk:
                    target.refresh_from_db()
                    update_session_auth_hash(request, target)  # keep the acting admin signed in
                messages.success(request, "Roles updated. The user's existing logins and API tokens were revoked.")
                return redirect(reverse("admin:accounts_user_change", args=[pk]))
        context = {**self.admin_site.each_context(request), "form": form, "target": target,
                   "opts": self.model._meta, "title": f"Change roles – {target.username}"}
        return render(request, "admin/accounts/user/roles.html", context)


def roles_for_display_obj(user):
    # roles_of() ignores inactive users; for display, list the groups regardless.
    names = {g.name for g in user.groups.all()}
    return [r for r in Role.values if r in names]


admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin):
    """Role groups are fixed. Their capabilities live in apps/accounts/capabilities.py."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LoginLinkMixin:
    """Only account managers (super admin) may link a login to a person; others see it read-only."""

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not can(request.user, Cap.USERS_MANAGE) and "user" not in readonly:
            readonly.append("user")
        return readonly


@admin.register(Parent)
class ParentAdmin(LoginLinkMixin, admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "user", "is_active")
    list_filter = ("is_active",)
    search_fields = ("full_name", "phone", "email", "ic_number")
    autocomplete_fields = ("user",)


COACH_BANK_FIELDS = ("bank_name", "bank_account_no", "epf_no", "socso_no")


@admin.register(Coach)
class CoachAdmin(LoginLinkMixin, admin.ModelAdmin):
    """Bank / EPF / SOCSO details are only shown to ``coaches.bank_details``
    (finance and super admin); admins manage the rest of the record."""

    list_display = ("full_name", "phone", "specialties", "is_active")
    list_filter = ("is_active",)
    search_fields = ("full_name", "phone", "email", "ic_number")
    autocomplete_fields = ("user",)

    @staticmethod
    def _editable_fields():
        return [f.name for f in Coach._meta.fields if f.editable and not f.primary_key]

    def get_fields(self, request, obj=None):
        fields = self._editable_fields()
        if not can(request.user, Cap.COACHES_BANK_DETAILS):
            fields = [f for f in fields if f not in COACH_BANK_FIELDS]
        return fields

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not can(request.user, Cap.COACHES_MANAGE):
            # Bank-details-only users (finance) may edit just those fields.
            readonly += [f for f in self._editable_fields() if f not in COACH_BANK_FIELDS and f not in readonly]
        return readonly


@admin.register(LoginFailure)
class LoginFailureAdmin(admin.ModelAdmin):
    """Failed sign-ins (read-only, super admin): usernames tried, IP, time,
    whether the lockout refused the attempt. Passwords are never stored."""

    list_display = ("attempted_at", "username", "ip_address", "locked")
    list_filter = ("locked",)
    search_fields = ("username", "ip_address")
    date_hierarchy = "attempted_at"
    readonly_fields = [f.name for f in LoginFailure._meta.fields]
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
