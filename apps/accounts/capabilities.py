"""Roles and capabilities: the single source of truth for authorization.

Two layers decide access, and both are always required:

* RBAC (this module): "may this kind of user perform this action at all?"
  A user holds one or more roles (stored as Django Groups). Each role grants a
  fixed set of capabilities. ``can(user, capability)`` is the only decision
  function; the API permission classes, the services and the Django admin
  (via ``CapabilityBackend``) all call it.
* Record access (``apps.academy.access``): "which specific records?"
  e.g. a parent's own children, a coach's assigned classes, a substitute's
  single session.

Capabilities ending in ``_all`` apply to every record. Scoped capabilities
(``_assigned``, ``_own_children``, ``_self``, ``_own``) only let a user reach the
endpoint; ``access.py`` then limits the records to the ones they are related to.
"""

from django.core.exceptions import PermissionDenied
from django.db import models


class Role(models.TextChoices):
    SUPER_ADMIN = "SUPER_ADMIN", "Super admin"
    ADMIN = "ADMIN", "Admin"
    FINANCE_ADMIN = "FINANCE_ADMIN", "Finance admin"
    COACH = "COACH", "Coach"
    PARENT = "PARENT", "Parent"
    STUDENT = "STUDENT", "Student"


# Roles that may sign in to the Django admin site.
STAFF_ROLES = frozenset({Role.SUPER_ADMIN, Role.ADMIN, Role.FINANCE_ADMIN})

# Used only to derive the display-only ``User.role`` value.
ROLE_PRECEDENCE = [Role.SUPER_ADMIN, Role.ADMIN, Role.FINANCE_ADMIN, Role.COACH, Role.PARENT, Role.STUDENT]


class Cap:
    """Capability names. Grouped by area; see ROLE_CAPABILITIES for who holds them."""

    # Administration
    USERS_VIEW = "users.view"
    USERS_MANAGE = "users.manage"
    ROLES_MANAGE = "roles.manage"
    SETTINGS_VIEW = "settings.view"
    SETTINGS_MANAGE = "settings.manage"
    AUDIT_VIEW_ALL = "audit.view_all"
    AUDIT_VIEW_OPERATIONS = "audit.view_operations"
    AUDIT_VIEW_FINANCE = "audit.view_finance"

    # Students and parents
    STUDENTS_VIEW_ALL = "students.view_all"            # full records of every student
    STUDENTS_VIEW_DIRECTORY = "students.view_directory"  # every student, names/contacts only (finance)
    STUDENTS_VIEW_ASSIGNED = "students.view_assigned"  # coach: training view of own rosters
    STUDENTS_VIEW_OWN_CHILDREN = "students.view_own_children"
    STUDENTS_VIEW_SELF = "students.view_self"
    STUDENTS_MANAGE = "students.manage"                # students, guardians, enrollments, student accounts
    STUDENTS_HISTORY = "students.history"
    PARENTS_VIEW_ALL = "parents.view_all"
    PARENTS_MANAGE = "parents.manage"

    # Coaches
    COACHES_VIEW_ALL = "coaches.view_all"
    COACHES_MANAGE = "coaches.manage"
    COACHES_BANK_DETAILS = "coaches.bank_details"      # view and edit bank / EPF / SOCSO details

    # Classes, timetables and sessions
    CLASSES_VIEW_ALL = "classes.view_all"
    CLASSES_VIEW_ASSIGNED = "classes.view_assigned"
    CLASSES_VIEW_OWN_CHILDREN = "classes.view_own_children"
    CLASSES_VIEW_SELF = "classes.view_self"
    CLASSES_MANAGE = "classes.manage"                  # classes, timetables, class-coach assignment
    SESSIONS_VIEW_ALL = "sessions.view_all"
    SESSIONS_VIEW_ASSIGNED = "sessions.view_assigned"  # includes open substitute sessions
    SESSIONS_VIEW_OWN_CHILDREN = "sessions.view_own_children"
    SESSIONS_VIEW_SELF = "sessions.view_self"
    SESSIONS_MANAGE = "sessions.manage"
    ROSTER_VIEW_ALL = "roster.view_all"
    ROSTER_VIEW_ASSIGNED = "roster.view_assigned"
    SUBSTITUTE_ASSIGN = "substitute.assign"
    SUBSTITUTE_REVOKE = "substitute.revoke"

    # Attendance
    ATTENDANCE_VIEW_ALL = "attendance.view_all"
    ATTENDANCE_VIEW_ASSIGNED = "attendance.view_assigned"
    ATTENDANCE_VIEW_OWN_CHILDREN = "attendance.view_own_children"
    ATTENDANCE_VIEW_SELF = "attendance.view_self"
    ATTENDANCE_TAKE_ANY = "attendance.take_any"
    ATTENDANCE_TAKE_ASSIGNED = "attendance.take_assigned"
    ATTENDANCE_CORRECT = "attendance.correct"

    # Finance
    FINANCE_VIEW_ALL = "finance.view_all"              # all charges, invoices, payments, receipts, refunds
    FINANCE_VIEW_OWN_CHILDREN = "finance.view_own_children"  # parent: own children's charges, own families' invoices
    FINANCE_SETUP = "finance.setup"                    # class fees, fee plans, price list
    FINANCE_CHARGES_MANAGE = "finance.charges.manage"  # add / cancel / waive charges, monthly billing
    FINANCE_PAYMENTS_RECORD = "finance.payments.record"
    FINANCE_PAYMENTS_VOID = "finance.payments.void"
    FINANCE_INVOICES_MANAGE = "finance.invoices.manage"  # draft, issue and void invoices
    FINANCE_REFUNDS_RECORD = "finance.refunds.record"    # exceptional refunds (reason required, audited)

    # Competitions
    COMPETITION_VIEW = "competition.view"              # published competitions and events
    COMPETITION_MANAGE = "competition.manage"          # competitions, events, drafts
    COMPETITION_REGISTRATIONS_VIEW_ALL = "competition.registrations.view_all"
    COMPETITION_REGISTRATIONS_VIEW_ASSIGNED = "competition.registrations.view_assigned"
    COMPETITION_REGISTRATIONS_VIEW_OWN_CHILDREN = "competition.registrations.view_own_children"
    COMPETITION_REGISTRATIONS_VIEW_SELF = "competition.registrations.view_self"
    COMPETITION_REGISTRATIONS_MANAGE = "competition.registrations.manage"  # register anyone, approve, reject
    COMPETITION_REGISTER_OWN_CHILDREN = "competition.register_own_children"
    COMPETITION_RESULTS_MANAGE = "competition.results.manage"

    # Payroll
    PAYROLL_VIEW_ALL = "payroll.view_all"
    PAYROLL_VIEW_OWN = "payroll.view_own"              # own finalized payslips
    PAYROLL_RATES_MANAGE = "payroll.rates.manage"
    PAYROLL_PREPARE = "payroll.prepare"                # adjustments, calculate / recalculate
    PAYROLL_FINALIZE = "payroll.finalize"

    # Reports
    REPORTS_STUDENTS = "reports.students"
    REPORTS_ATTENDANCE = "reports.attendance"
    REPORTS_COMPETITIONS = "reports.competitions"
    REPORTS_FINANCE = "reports.finance"
    REPORTS_PAYROLL = "reports.payroll"


ALL_CAPABILITIES = frozenset(
    value for name, value in vars(Cap).items() if name.isupper() and isinstance(value, str)
)

_ADMIN = {
    Cap.AUDIT_VIEW_OPERATIONS,
    Cap.STUDENTS_VIEW_ALL, Cap.STUDENTS_MANAGE, Cap.STUDENTS_HISTORY,
    Cap.PARENTS_VIEW_ALL, Cap.PARENTS_MANAGE,
    Cap.COACHES_VIEW_ALL, Cap.COACHES_MANAGE,
    Cap.CLASSES_VIEW_ALL, Cap.CLASSES_MANAGE,
    Cap.SESSIONS_VIEW_ALL, Cap.SESSIONS_MANAGE,
    Cap.ROSTER_VIEW_ALL, Cap.SUBSTITUTE_ASSIGN, Cap.SUBSTITUTE_REVOKE,
    Cap.ATTENDANCE_VIEW_ALL, Cap.ATTENDANCE_TAKE_ANY, Cap.ATTENDANCE_CORRECT,
    # Front desk may receive and record payments (and so needs to see what is owed),
    # and may authorize an exceptional refund (reason required, audited).
    Cap.FINANCE_VIEW_ALL, Cap.FINANCE_PAYMENTS_RECORD, Cap.FINANCE_REFUNDS_RECORD,
    Cap.COMPETITION_VIEW, Cap.COMPETITION_MANAGE,
    Cap.COMPETITION_REGISTRATIONS_VIEW_ALL, Cap.COMPETITION_REGISTRATIONS_MANAGE, Cap.COMPETITION_RESULTS_MANAGE,
    Cap.REPORTS_STUDENTS, Cap.REPORTS_ATTENDANCE, Cap.REPORTS_COMPETITIONS,
}

_FINANCE_ADMIN = {
    Cap.AUDIT_VIEW_FINANCE,
    Cap.STUDENTS_VIEW_DIRECTORY, Cap.PARENTS_VIEW_ALL,
    Cap.COACHES_VIEW_ALL, Cap.COACHES_BANK_DETAILS,
    Cap.FINANCE_VIEW_ALL, Cap.FINANCE_SETUP, Cap.FINANCE_CHARGES_MANAGE,
    Cap.FINANCE_PAYMENTS_RECORD, Cap.FINANCE_PAYMENTS_VOID,
    Cap.FINANCE_INVOICES_MANAGE, Cap.FINANCE_REFUNDS_RECORD,
    Cap.COMPETITION_VIEW,
    Cap.PAYROLL_VIEW_ALL, Cap.PAYROLL_RATES_MANAGE, Cap.PAYROLL_PREPARE,
    Cap.REPORTS_FINANCE, Cap.REPORTS_PAYROLL,
}

_COACH = {
    Cap.STUDENTS_VIEW_ASSIGNED,
    Cap.CLASSES_VIEW_ASSIGNED, Cap.SESSIONS_VIEW_ASSIGNED, Cap.ROSTER_VIEW_ASSIGNED,
    Cap.ATTENDANCE_VIEW_ASSIGNED, Cap.ATTENDANCE_TAKE_ASSIGNED,
    Cap.COMPETITION_VIEW, Cap.COMPETITION_REGISTRATIONS_VIEW_ASSIGNED,
    Cap.PAYROLL_VIEW_OWN,
}

_PARENT = {
    Cap.STUDENTS_VIEW_OWN_CHILDREN,
    Cap.CLASSES_VIEW_OWN_CHILDREN, Cap.SESSIONS_VIEW_OWN_CHILDREN,
    Cap.ATTENDANCE_VIEW_OWN_CHILDREN,
    Cap.FINANCE_VIEW_OWN_CHILDREN,
    Cap.COMPETITION_VIEW, Cap.COMPETITION_REGISTRATIONS_VIEW_OWN_CHILDREN, Cap.COMPETITION_REGISTER_OWN_CHILDREN,
}

# Student fees/receipts are deliberately not granted yet: they arrive with the
# invoice work (P1) once the student-facing finance rules are built.
_STUDENT = {
    Cap.STUDENTS_VIEW_SELF,
    Cap.CLASSES_VIEW_SELF, Cap.SESSIONS_VIEW_SELF,
    Cap.ATTENDANCE_VIEW_SELF,
    Cap.COMPETITION_VIEW, Cap.COMPETITION_REGISTRATIONS_VIEW_SELF,
}

ROLE_CAPABILITIES = {
    Role.SUPER_ADMIN: ALL_CAPABILITIES,
    Role.ADMIN: frozenset(_ADMIN),
    Role.FINANCE_ADMIN: frozenset(_FINANCE_ADMIN),
    Role.COACH: frozenset(_COACH),
    Role.PARENT: frozenset(_PARENT),
    Role.STUDENT: frozenset(_STUDENT),
}


# Django admin: each model's view permission and its add/change/delete
# ("manage") permission map to capabilities. A tuple means "any of".
# Models not listed here are denied to everyone except SUPER_ADMIN.
MODEL_CAPABILITIES = {
    "auth.group": (Cap.ROLES_MANAGE, None),
    "authtoken.token": (Cap.USERS_MANAGE, Cap.USERS_MANAGE),
    "authtoken.tokenproxy": (Cap.USERS_MANAGE, Cap.USERS_MANAGE),
    "audit.auditlog": ((Cap.AUDIT_VIEW_ALL, Cap.AUDIT_VIEW_OPERATIONS, Cap.AUDIT_VIEW_FINANCE), None),
    "accounts.user": (Cap.USERS_VIEW, Cap.USERS_MANAGE),
    "accounts.parent": (Cap.PARENTS_VIEW_ALL, Cap.PARENTS_MANAGE),
    "accounts.coach": (Cap.COACHES_VIEW_ALL, (Cap.COACHES_MANAGE, Cap.COACHES_BANK_DETAILS)),
    "academy.program": (Cap.CLASSES_VIEW_ALL, Cap.CLASSES_MANAGE),
    "academy.team": (Cap.CLASSES_VIEW_ALL, Cap.CLASSES_MANAGE),
    "academy.trainingclass": (Cap.CLASSES_VIEW_ALL, Cap.CLASSES_MANAGE),
    "academy.classschedule": (Cap.CLASSES_VIEW_ALL, Cap.CLASSES_MANAGE),
    "academy.classcoach": (Cap.CLASSES_VIEW_ALL, Cap.CLASSES_MANAGE),
    "academy.student": (Cap.STUDENTS_VIEW_ALL, Cap.STUDENTS_MANAGE),
    "academy.studentstatushistory": (Cap.STUDENTS_VIEW_ALL, None),
    "academy.guardianship": (Cap.STUDENTS_VIEW_ALL, Cap.STUDENTS_MANAGE),
    "academy.enrollment": (Cap.STUDENTS_VIEW_ALL, Cap.STUDENTS_MANAGE),
    # Linking a login to a person is account management (super admin).
    "academy.studentaccount": (Cap.STUDENTS_VIEW_ALL, Cap.USERS_MANAGE),
    "academy.family": ((Cap.STUDENTS_VIEW_ALL, Cap.FINANCE_VIEW_ALL), Cap.STUDENTS_MANAGE),
    "academy.trainingsession": (Cap.SESSIONS_VIEW_ALL, Cap.SESSIONS_MANAGE),
    "academy.sessioncoach": (Cap.SESSIONS_VIEW_ALL, Cap.SUBSTITUTE_ASSIGN),
    "attendance.attendancerecord": (Cap.ATTENDANCE_VIEW_ALL, Cap.ATTENDANCE_CORRECT),
    "finance.classfee": (Cap.FINANCE_VIEW_ALL, Cap.FINANCE_SETUP),
    "finance.studentfeeplan": (Cap.FINANCE_VIEW_ALL, Cap.FINANCE_SETUP),
    "finance.chargeitem": (Cap.FINANCE_VIEW_ALL, Cap.FINANCE_SETUP),
    "finance.charge": (Cap.FINANCE_VIEW_ALL, Cap.FINANCE_CHARGES_MANAGE),
    "finance.payment": (Cap.FINANCE_VIEW_ALL, Cap.FINANCE_PAYMENTS_RECORD),
    "finance.paymentallocation": (Cap.FINANCE_VIEW_ALL, Cap.FINANCE_PAYMENTS_RECORD),
    "finance.receipt": (Cap.FINANCE_VIEW_ALL, None),
    "finance.receiptvoid": (Cap.FINANCE_VIEW_ALL, None),
    "finance.documentsequence": (None, None),
    "finance.invoice": (Cap.FINANCE_VIEW_ALL, Cap.FINANCE_INVOICES_MANAGE),
    "finance.invoiceitem": (Cap.FINANCE_VIEW_ALL, Cap.FINANCE_INVOICES_MANAGE),
    "finance.refund": (Cap.FINANCE_VIEW_ALL, Cap.FINANCE_REFUNDS_RECORD),
    "competitions.competition": (Cap.COMPETITION_VIEW, Cap.COMPETITION_MANAGE),
    "competitions.competitionevent": (Cap.COMPETITION_VIEW, Cap.COMPETITION_MANAGE),
    "competitions.competitionregistration": (Cap.COMPETITION_REGISTRATIONS_VIEW_ALL, Cap.COMPETITION_REGISTRATIONS_MANAGE),
    "competitions.competitionresult": (Cap.COMPETITION_REGISTRATIONS_VIEW_ALL, Cap.COMPETITION_RESULTS_MANAGE),
    "payroll.coachrate": (Cap.PAYROLL_VIEW_ALL, Cap.PAYROLL_RATES_MANAGE),
    "payroll.payrolladjustment": (Cap.PAYROLL_VIEW_ALL, Cap.PAYROLL_PREPARE),
    "payroll.payrollrun": (Cap.PAYROLL_VIEW_ALL, Cap.PAYROLL_PREPARE),
    "payroll.payslip": (Cap.PAYROLL_VIEW_ALL, Cap.PAYROLL_PREPARE),
    "payroll.payslipline": (Cap.PAYROLL_VIEW_ALL, Cap.PAYROLL_PREPARE),
}


def _as_set(requirement):
    if requirement is None:
        return frozenset()
    if isinstance(requirement, str):
        return frozenset({requirement})
    return frozenset(requirement)


def roles_of(user):
    """The user's roles, from their Django Groups. Cached on the user object."""
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return frozenset()
    cached = getattr(user, "_sr_roles", None)
    if cached is None:
        names = set(user.groups.filter(name__in=Role.values).values_list("name", flat=True))
        cached = frozenset(Role(name) for name in names)
        user._sr_roles = cached
    return cached


def clear_cache(user):
    for attr in ("_sr_roles", "_sr_capabilities"):
        if hasattr(user, attr):
            delattr(user, attr)


def has_role(user, role):
    return role in roles_of(user)


def capabilities_of(user):
    cached = getattr(user, "_sr_capabilities", None) if user is not None else None
    if cached is None:
        cached = frozenset().union(*(ROLE_CAPABILITIES[r] for r in roles_of(user))) if user is not None else frozenset()
        if user is not None and getattr(user, "is_authenticated", False):
            user._sr_capabilities = cached
    return cached


def can(user, capability):
    """True if the user holds the capability. ``capability`` may be a name or an
    iterable of names, in which case any one of them is enough."""
    wanted = _as_set(capability)
    unknown = wanted - ALL_CAPABILITIES
    if unknown:
        raise ValueError(f"Unknown capability: {', '.join(sorted(unknown))}")
    return bool(wanted & capabilities_of(user))


def require(actor, capability):
    """Service-layer guard. ``actor=None`` means a system job (migration,
    management command, scheduled task), which is trusted."""
    if actor is None:
        return
    if not can(actor, capability):
        raise PermissionDenied("You do not have permission to perform this action.")


def primary_role(roles):
    for role in ROLE_PRECEDENCE:
        if role in roles:
            return role
    return ""
