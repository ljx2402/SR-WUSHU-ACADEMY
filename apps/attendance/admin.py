from django.contrib import admin

from .models import AttendanceRecord


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("session", "student", "status", "recorded_by", "updated_at")
    list_filter = ("status", "session__training_class", "session__date")
    search_fields = ("student__full_name", "student__student_no")
    autocomplete_fields = ("student",)
    raw_id_fields = ("session",)
    readonly_fields = ("recorded_by",)

    def save_model(self, request, obj, form, change):
        obj.recorded_by = request.user
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False
