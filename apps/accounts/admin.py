from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Coach, Parent, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "first_name", "last_name", "role", "is_active", "last_login")
    list_filter = ("role", "is_active", "is_staff")
    fieldsets = BaseUserAdmin.fieldsets + (("Academy", {"fields": ("role", "phone")}),)
    add_fieldsets = BaseUserAdmin.add_fieldsets + (("Academy", {"fields": ("role", "phone")}),)


@admin.register(Parent)
class ParentAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "user", "is_active")
    list_filter = ("is_active",)
    search_fields = ("full_name", "phone", "email", "ic_number")
    autocomplete_fields = ("user",)


@admin.register(Coach)
class CoachAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "specialties", "is_active")
    list_filter = ("is_active",)
    search_fields = ("full_name", "phone", "email", "ic_number")
    autocomplete_fields = ("user",)
