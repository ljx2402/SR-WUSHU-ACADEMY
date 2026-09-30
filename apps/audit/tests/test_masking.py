"""Phase 5: sensitive values never enter the audit log in the clear, and the
audit log cannot be edited or deleted."""

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from rest_framework.test import APIClient

from apps.academy.tests.base import AcademyTestCase, make_user
from apps.accounts.models import Coach
from apps.audit.masking import mask_changes, mask_identifier, mask_medical, mask_value
from apps.audit.models import AuditLog
from apps.audit.utils import history_for, record

IC = "140501-10-1234"
BANK = "5140 1234 567890"


class MaskingRuleTests(AcademyTestCase):
    def test_rules(self):
        self.assertEqual(mask_identifier(IC), "**********1234")
        self.assertEqual(mask_identifier("123"), "***")
        self.assertEqual(mask_identifier(""), "")
        self.assertEqual(mask_medical("Asthma, carries inhaler"), "[MEDICAL NOTE – 23 characters]")
        self.assertEqual(mask_medical(""), "")
        for field in ("password", "new_password", "token", "api_key", "secret_key", "key", "auth_version"):
            with self.subTest(field=field):
                self.assertEqual(mask_value(field, "value"), "[SECRET]")
        for field in ("tokens_revoked", "full_name", "phone", "status"):
            with self.subTest(field=field):
                self.assertEqual(mask_value(field, "value"), "value")
        self.assertEqual(mask_changes({"ic_number": {"from": None, "to": IC}, "epf_no": "12345678"}),
                         {"ic_number": {"from": None, "to": "**********1234"}, "epf_no": "****5678"})


class ModelAuditMaskingTests(AcademyTestCase):
    def everything(self):
        return " ".join(str(v) for v in AuditLog.objects.values_list("changes", "reason", "object_repr"))

    def test_student_ic_and_medical_notes(self):
        self.student_1.ic_number = IC
        self.student_1.medical_notes = "Severe peanut allergy; EpiPen in bag"
        self.student_1.save()
        change = history_for(self.student_1).filter(action="UPDATE").latest("id").changes
        self.assertEqual(change["ic_number"], {"from": "", "to": "**********1234"})
        self.assertEqual(change["medical_notes"], {"from": "", "to": "[MEDICAL NOTE – 36 characters]"})
        self.student_1.medical_notes = "Peanut allergy (mild)"
        self.student_1.save()
        change = history_for(self.student_1).filter(action="UPDATE").latest("id").changes
        self.assertEqual(change["medical_notes"], {"from": "[MEDICAL NOTE – 36 characters]",
                                                   "to": "[MEDICAL NOTE – 21 characters]"})   # the change is visible
        for secret in (IC, "peanut", "Peanut", "EpiPen"):
            self.assertNotIn(secret, self.everything())

    def test_coach_bank_and_statutory_numbers_and_parent_ic(self):
        coach = Coach.objects.create(full_name="Coach Z", phone="1", ic_number=IC, bank_account_no=BANK,
                                     epf_no="EPF998877", socso_no="SOC554433")
        created = history_for(coach).get(action="CREATE").changes
        self.assertEqual(created["bank_account_no"]["to"], "************7890")
        self.assertEqual(created["epf_no"]["to"], "*****8877")
        self.assertEqual(created["socso_no"]["to"], "*****4433")
        self.parent_1.ic_number = "800101-14-5566"
        self.parent_1.save()
        self.assertEqual(history_for(self.parent_1).latest("id").changes["ic_number"]["to"], "**********5566")
        for secret in (IC, BANK, "EPF998877", "SOC554433", "800101-14-5566"):
            self.assertNotIn(secret, self.everything())

    def test_any_credential_field_is_never_recorded(self):
        entry = record(self.student_1, AuditLog.Action.EVENT,
                       changes={"password": "hunter2", "api_key": "sk-live-abc", "token": "abc123", "note": "ok"})
        self.assertEqual(entry.changes, {"password": "[SECRET]", "api_key": "[SECRET]", "token": "[SECRET]",
                                         "note": "ok"})

    def test_history_api_shows_only_masked_values(self):
        self.student_1.ic_number = IC
        self.student_1.save()
        client = APIClient()
        client.force_authenticate(self.admin_user)
        body = client.get(f"/api/students/{self.student_1.pk}/history/").content.decode()
        self.assertIn("**********1234", body)
        self.assertNotIn(IC, body)


class AuditLogImmutabilityTests(AcademyTestCase):
    def setUp(self):
        self.entry = record(self.student_1, AuditLog.Action.EVENT, changes={"x": 1}, reason="test")

    def test_model_and_queryset_guards(self):
        self.entry.reason = "rewritten"
        with self.assertRaises(PermissionDenied):
            self.entry.save()
        with self.assertRaises(PermissionDenied):
            self.entry.delete()
        with self.assertRaises(PermissionDenied):
            AuditLog.objects.filter(pk=self.entry.pk).update(reason="rewritten")
        with self.assertRaises(PermissionDenied):
            AuditLog.objects.filter(pk=self.entry.pk).delete()
        self.assertEqual(AuditLog.objects.get(pk=self.entry.pk).reason, "test")

    def test_admin_cannot_edit_or_delete(self):
        self.client.force_login(self.super_user)
        base = f"/admin/audit/auditlog/{self.entry.pk}"
        self.assertEqual(self.client.post(f"{base}/change/", {"reason": "x"}).status_code, 403)
        self.assertEqual(self.client.post(f"{base}/delete/", {"post": "yes"}).status_code, 403)
        self.assertEqual(AuditLog.objects.get(pk=self.entry.pk).reason, "test")

    def test_raw_sql_cannot_tamper_but_a_deleted_user_can_be_detached(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL trigger")
        for sql in ("UPDATE audit_auditlog SET reason = 'x' WHERE id = %s",
                    "UPDATE audit_auditlog SET changes = '{}'::jsonb WHERE id = %s",
                    "DELETE FROM audit_auditlog WHERE id = %s"):
            with self.subTest(sql=sql), self.assertRaises(IntegrityError), transaction.atomic(), \
                    connection.cursor() as cursor:
                cursor.execute(sql, [self.entry.pk])
        leaver = make_user("leaver")
        entry = record(self.student_1, AuditLog.Action.EVENT, changes={}, actor=leaver)
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE audit_auditlog SET actor_id = %s WHERE id = %s", [self.super_user.pk, entry.pk])
        leaver.delete()                                   # SET_NULL on the actor is allowed
        entry.refresh_from_db()
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.reason, "")
