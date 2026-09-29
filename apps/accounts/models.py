from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.crypto import salted_hmac

from apps.audit.models import AuditCategory, AuditedModel

from .capabilities import STAFF_ROLES, Role, has_role, primary_role


class User(AbstractUser):
    """Login account.

    Roles are Django Groups and are changed only through
    ``apps.accounts.services.set_roles`` (which audits the change and revokes
    the user's tokens and sessions). ``role``, ``is_staff`` and ``is_superuser``
    are derived from the groups on every save and are never used as an
    authorization source by academy code.
    """

    Role = Role  # backward-compatible alias: User.Role.ADMIN etc.

    role = models.CharField(
        max_length=20, choices=Role.choices, blank=True, default="", editable=False,
        help_text="Display only: the highest-ranking role. Authorization uses the user's groups.",
    )
    phone = models.CharField(max_length=30, blank=True)
    auth_version = models.PositiveIntegerField(
        default=0, editable=False,
        help_text="Incremented to invalidate every existing login session of this user.",
    )

    def save(self, *args, **kwargs):
        if self.pk is None:
            # New accounts start without roles. A superuser created with
            # createsuperuser is promoted to SUPER_ADMIN by a post_save hook.
            self._bootstrap_super_admin = self.is_superuser
            self.is_superuser = self.is_staff = False
            self.role = ""
        else:
            roles = set(self.groups.filter(name__in=Role.values).values_list("name", flat=True))
            self.is_superuser = Role.SUPER_ADMIN in roles
            self.is_staff = bool(roles & STAFF_ROLES)
            self.role = primary_role(roles)
        super().save(*args, **kwargs)

    def _get_session_auth_hash(self, secret=None):
        # Django compares this with the hash stored in each session. Mixing in
        # auth_version lets role changes log the user out everywhere.
        return salted_hmac(
            "apps.accounts.models.User.get_session_auth_hash",
            f"{self.password}:{self.auth_version}",
            secret=secret,
            algorithm="sha256",
        ).hexdigest()

    @property
    def coach(self):
        return getattr(self, "coach_profile", None) if has_role(self, Role.COACH) else None

    @property
    def parent(self):
        return getattr(self, "parent_profile", None) if has_role(self, Role.PARENT) else None

    @property
    def student(self):
        if not has_role(self, Role.STUDENT):
            return None
        account = getattr(self, "student_account", None)
        return account.student if account else None


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
