from django.contrib.auth.models import AbstractUser
from django.db import models

from apps.audit.models import AuditCategory, AuditedModel


class User(AbstractUser):
    """Login account. The role decides which part of the system a user sees."""

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        COACH = "COACH", "Coach"
        PARENT = "PARENT", "Parent"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.PARENT)
    phone = models.CharField(max_length=30, blank=True)

    @property
    def is_academy_admin(self):
        return self.is_active and (self.is_superuser or self.role == self.Role.ADMIN)

    @property
    def coach(self):
        return getattr(self, "coach_profile", None) if self.role == self.Role.COACH else None

    @property
    def parent(self):
        return getattr(self, "parent_profile", None) if self.role == self.Role.PARENT else None


class Parent(AuditedModel):
    audit_category = AuditCategory.STUDENT

    user = models.OneToOneField(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="parent_profile",
        help_text="Login for the Parent App. Leave empty if the parent does not use the app.",
    )
    full_name = models.CharField(max_length=200)
    ic_number = models.CharField("IC / passport no.", max_length=30, blank=True)
    phone = models.CharField(max_length=30)
    alt_phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    occupation = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.phone})"


class Coach(AuditedModel):
    audit_category = AuditCategory.PAYROLL

    user = models.OneToOneField(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="coach_profile"
    )
    full_name = models.CharField(max_length=200)
    ic_number = models.CharField("IC / passport no.", max_length=30, blank=True)
    phone = models.CharField(max_length=30)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    specialties = models.CharField(max_length=255, blank=True, help_text="e.g. Changquan, Sanda, Taiji")
    join_date = models.DateField(null=True, blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
    bank_account_no = models.CharField(max_length=50, blank=True)
    epf_no = models.CharField("EPF no.", max_length=30, blank=True)
    socso_no = models.CharField("SOCSO no.", max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name"]
        verbose_name_plural = "coaches"

    def __str__(self):
        return self.full_name
