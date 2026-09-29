"""Integrity of the single capability map (unit tests, no HTTP)."""

from django.contrib import admin
from django.test import SimpleTestCase
from rest_framework.viewsets import ViewSetMixin

from apps.accounts.capabilities import ALL_CAPABILITIES, MODEL_CAPABILITIES, ROLE_CAPABILITIES, Cap, Role
from apps.api.urls import router


def caps_of(role):
    return ROLE_CAPABILITIES[role]


class RoleMatrixTests(SimpleTestCase):
    def test_every_role_is_defined(self):
        self.assertEqual(set(ROLE_CAPABILITIES), set(Role))
        for role, capabilities in ROLE_CAPABILITIES.items():
            self.assertTrue(capabilities <= ALL_CAPABILITIES, role)

    def test_super_admin_has_everything(self):
        self.assertEqual(caps_of(Role.SUPER_ADMIN), ALL_CAPABILITIES)

    def test_only_super_admin_manages_users_roles_settings_and_finalizes_payroll(self):
        exclusive = {Cap.USERS_VIEW, Cap.USERS_MANAGE, Cap.ROLES_MANAGE, Cap.SETTINGS_VIEW, Cap.SETTINGS_MANAGE,
                     Cap.AUDIT_VIEW_ALL, Cap.PAYROLL_FINALIZE}
        for role in set(Role) - {Role.SUPER_ADMIN}:
            self.assertFalse(caps_of(role) & exclusive, role)

    def test_admin_is_operational_without_payroll_or_bank_details(self):
        admin_caps = caps_of(Role.ADMIN)
        self.assertTrue({Cap.STUDENTS_MANAGE, Cap.PARENTS_MANAGE, Cap.CLASSES_MANAGE, Cap.SESSIONS_MANAGE,
                         Cap.ATTENDANCE_TAKE_ANY, Cap.COMPETITION_MANAGE, Cap.SUBSTITUTE_ASSIGN,
                         Cap.SUBSTITUTE_REVOKE, Cap.FINANCE_PAYMENTS_RECORD} <= admin_caps)
        payroll = {c for c in ALL_CAPABILITIES if c.startswith("payroll.")} - {Cap.PAYROLL_VIEW_OWN}
        self.assertFalse(admin_caps & payroll)
        self.assertNotIn(Cap.COACHES_BANK_DETAILS, admin_caps)
        self.assertNotIn(Cap.REPORTS_PAYROLL, admin_caps)

    def test_finance_admin_prepares_payroll_but_does_not_run_the_academy(self):
        finance = caps_of(Role.FINANCE_ADMIN)
        self.assertTrue({Cap.FINANCE_SETUP, Cap.FINANCE_CHARGES_MANAGE, Cap.FINANCE_PAYMENTS_RECORD,
                         Cap.FINANCE_PAYMENTS_VOID, Cap.PAYROLL_RATES_MANAGE, Cap.PAYROLL_PREPARE,
                         Cap.REPORTS_FINANCE, Cap.REPORTS_PAYROLL} <= finance)
        self.assertFalse(finance & {Cap.STUDENTS_MANAGE, Cap.STUDENTS_VIEW_ALL, Cap.CLASSES_MANAGE,
                                    Cap.SESSIONS_MANAGE, Cap.PAYROLL_FINALIZE, Cap.ROLES_MANAGE})

    def test_non_staff_roles_have_no_administration(self):
        for role in (Role.COACH, Role.PARENT, Role.STUDENT):
            self.assertFalse({c for c in caps_of(role) if c.endswith((".manage", ".view_all", ".record", ".void",
                                                                       ".prepare", ".finalize", ".setup"))}, role)
            self.assertFalse({c for c in caps_of(role) if c.startswith("reports.")}, role)
            self.assertNotIn(Cap.COACHES_BANK_DETAILS, caps_of(role))

    def test_student_has_no_finance_yet(self):
        self.assertFalse({c for c in caps_of(Role.STUDENT) if c.startswith("finance.")})


class MapCoverageTests(SimpleTestCase):
    def test_every_admin_model_is_mapped(self):
        for model in admin.site._registry:
            self.assertIn(model._meta.label_lower, MODEL_CAPABILITIES, model._meta.label_lower)

    def test_model_map_uses_known_capabilities(self):
        for label, (view_cap, manage_cap) in MODEL_CAPABILITIES.items():
            for requirement in (view_cap, manage_cap):
                names = {requirement} if isinstance(requirement, str) else set(requirement or ())
                self.assertTrue(names <= ALL_CAPABILITIES, label)

    def test_every_api_action_declares_known_capabilities(self):
        for prefix, viewset, basename in router.registry:
            self.assertTrue(issubclass(viewset, ViewSetMixin))
            declared = viewset.capabilities
            for requirement in declared.values():
                names = {requirement} if isinstance(requirement, str) else set(requirement)
                self.assertTrue(names <= ALL_CAPABILITIES, prefix)
            routed = {"list", "retrieve"} & set(dir(viewset))
            routed |= {a.__name__ for a in viewset.get_extra_actions()}
            for name in ("create", "update", "partial_update"):
                if hasattr(viewset, name):
                    routed.add(name)
            self.assertEqual(routed - set(declared), set(), f"{prefix}: actions without a capability")
