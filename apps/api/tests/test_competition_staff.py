"""Phase 6G: the API as the Competition Staff Portal uses it.

Fixtures (Parent Portal tests): "State Open" (OPEN) with "Open set" (RM 50) and
"Second set" (RM 30); Dina (Family B) is entered in "Open set", awaiting payment.

Capabilities decide everything (``apps/accounts/capabilities.py``):
ADMIN and SUPER_ADMIN: competitions, forms, all registrations, confirm / reject /
withdraw, results. FINANCE_ADMIN: published competitions only (no registrations,
no forms, no results). COACH: own athletes' entries only, without family data.
PARENT: own children. STUDENT: own entries.
"""

from decimal import Decimal

from apps.academy.models import StudentAccount
from apps.academy.tests.base import make_user
from apps.accounts.capabilities import Role
from apps.audit.models import AuditLog
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration, CompetitionResult
from apps.finance.models import Charge, Invoice
from apps.finance.services import record_payment

from .test_registration_forms import ACCOMMODATION, SHIRT, FormTestCase

FAMILY_KEYS = {"form_responses", "notes", "fee", "fee_status", "invoice"}


class CompetitionStaffTestCase(FormTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student_user = make_user("aaron_login", Role.STUDENT)
        StudentAccount.objects.create(user=cls.student_user, student=cls.a1)

    def rows(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        return body.get("results", body) if isinstance(body, dict) else body

    def regs(self, user=None, **query):
        query = {"competition": self.competition.pk, **query}
        return self.rows(self.api(user or self.admin_user).get("/api/competition-registrations/", query))

    def pay(self, registration):
        invoice = registration.charge.active_invoice_item().invoice
        record_payment([(invoice, str(invoice.balance_due))], "CASH", self.admin_user)
        registration.refresh_from_db()
        return registration


class ParticipantListTests(CompetitionStaffTestCase):
    def setUp(self):
        self.setup_form(SHIRT)
        self.assertEqual(self.register(responses={"shirt_size": "M"}).status_code, 201)       # Aaron, unpaid
        self.assertEqual(self.register(student=self.a2, responses={"shirt_size": "S"}).status_code, 201)  # Beth
        self.beth = CompetitionRegistration.objects.get(student=self.a2)
        self.pay(self.beth)

    def test_server_side_filters(self):
        self.assertEqual(len(self.regs()), 3)
        self.assertEqual({r["student_name"] for r in self.regs(status="CONFIRMED")}, {self.a2.full_name})
        self.assertEqual({r["student_name"] for r in self.regs(status="PENDING")},
                         {self.a1.full_name, self.b1.full_name})
        self.assertEqual({r["student_name"] for r in self.regs(payment="PAID")}, {self.a2.full_name})
        self.assertEqual(len(self.regs(payment="UNPAID")), 2)
        self.assertEqual(self.regs(payment="FREE"), [])
        self.assertEqual({r["student_name"] for r in self.regs(search="aaron")}, {self.a1.full_name})
        self.assertEqual({r["student_name"] for r in self.regs(search=self.b1.student_no)}, {self.b1.full_name})
        self.assertEqual(len(self.regs(event=self.event.pk)), 3)
        self.assertEqual(self.regs(event=self.event_2.pk), [])
        self.assertEqual(len(self.regs(student=self.a1.pk)), 1)
        self.assertEqual(len(self.regs(result="no")), 3)
        self.assertEqual(self.regs(result="yes"), [])
        self.assertEqual(len(self.regs(start=str(self.today))), 3)

    def test_paginated_and_bad_filters_are_400(self):
        body = self.api(self.admin_user).get("/api/competition-registrations/",
                                             {"competition": self.competition.pk}).json()
        self.assertEqual(body["count"], 3)
        self.assertIn("next", body)
        client = self.api(self.admin_user)
        for bad in ({"status": "PAID"}, {"payment": "maybe"}, {"event": "x"}, {"student": "1;"}, {"start": "today"}):
            self.assertEqual(client.get("/api/competition-registrations/", bad).status_code, 400, bad)

    def test_filters_never_widen_a_parents_or_coachs_scope(self):
        parent_rows = self.regs(self.parent_b_user, status="PENDING,CONFIRMED")
        self.assertEqual({r["student"] for r in parent_rows}, {self.b1.pk})
        self.assertEqual(self.regs(self.parent_b_user, student=self.a1.pk), [])
        coach_rows = self.regs(self.coach_a_user, search="")
        self.assertTrue(coach_rows)
        for row in coach_rows:
            self.assertFalse(FAMILY_KEYS & set(row))

    def test_staff_detail_has_history_answers_and_finance_state(self):
        aaron = CompetitionRegistration.objects.get(student=self.a1)
        body = self.api(self.admin_user).get(f"/api/competition-registrations/{aaron.pk}/").json()
        self.assertEqual(body["form_version"], Competition.objects.get(pk=self.competition.pk).form_version)
        self.assertEqual(body["form_responses"][0]["display"], "M")
        self.assertEqual(body["fee_status"], Charge.Status.UNPAID)
        self.assertEqual(body["invoice"]["balance_due"], "50.00")


class HistoricalFormTests(CompetitionStaffTestCase):
    def test_v1_answers_survive_a_v2_form(self):
        self.setup_form(SHIRT)
        v1 = Competition.objects.get(pk=self.competition.pk).form_version
        self.assertEqual(self.register(responses={"shirt_size": "L"}).status_code, 201)
        old = CompetitionRegistration.objects.get(student=self.a1)
        # Staff change the working copy and publish version 2.
        shirt = self.competition.form_fields.get(key="shirt_size")
        admin = self.api(self.admin_user)
        self.assertEqual(admin.patch(f"/api/competition-form-fields/{shirt.pk}/", {"label": "Jersey size"},
                                     format="json").status_code, 200)
        self.assertEqual(self.add_field(**ACCOMMODATION).status_code, 201)
        form = admin.get(f"/api/competitions/{self.competition.pk}/form/").json()
        self.assertTrue(form["has_unpublished_changes"])
        self.assertEqual(self.publish().json()["version"], v1 + 1)
        body = admin.get(f"/api/competition-registrations/{old.pk}/").json()
        self.assertEqual(body["form_version"], v1)
        self.assertEqual([(a["label"], a["display"]) for a in body["form_responses"]], [("T-shirt size", "L")])
        # A new registration uses version 2.
        self.assertEqual(self.register(student=self.a2, responses={"shirt_size": "S", "accommodation": True})
                         .status_code, 201)
        self.assertEqual(CompetitionRegistration.objects.get(student=self.a2).form_version, v1 + 1)

    def test_key_of_a_field_is_fixed_and_publish_needs_manage(self):
        self.setup_form(SHIRT)
        shirt = self.competition.form_fields.get(key="shirt_size")
        admin = self.api(self.admin_user)
        self.assertEqual(admin.patch(f"/api/competition-form-fields/{shirt.pk}/", {"key": "size"},
                                     format="json").status_code, 400)
        for user in (self.finance_user, self.coach_a_user, self.parent_a_user, self.student_user):
            client = self.api(user)
            self.assertEqual(client.post(f"/api/competitions/{self.competition.pk}/publish-form/").status_code, 403)
            self.assertEqual(client.get(f"/api/competitions/{self.competition.pk}/form/").status_code, 403)
            self.assertEqual(client.get("/api/competition-form-fields/").status_code, 403)
            self.assertEqual(client.patch(f"/api/competition-form-fields/{shirt.pk}/", {"label": "X"},
                                          format="json").status_code, 403)


class StatusActionTests(CompetitionStaffTestCase):
    def test_reject_uses_the_withdrawal_service_with_a_reason(self):
        client = self.api(self.admin_user)
        url = f"/api/competition-registrations/{self.reg_b1.pk}/reject/"
        self.assertEqual(client.post(url, {"reason": " "}, format="json").status_code, 400)
        response = client.post(url, {"reason": "Not eligible this year"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "REJECTED")
        self.reg_b1.refresh_from_db()
        self.assertEqual(self.reg_b1.charge.status, Charge.Status.CANCELLED)
        self.assertEqual(Invoice.objects.get(items__charge=self.reg_b1.charge).status, Invoice.Status.VOID)
        self.assertTrue(AuditLog.objects.filter(reason="Not eligible this year").exists())
        self.assertEqual(client.post(url, {"reason": "again"}, format="json").status_code, 400)

    def test_staff_withdrawal_of_a_paid_entry_refunds_nothing(self):
        reg = self.pay(self.reg_b1)
        self.assertEqual(reg.status, "CONFIRMED")
        response = self.api(self.admin_user).post(f"/api/competition-registrations/{reg.pk}/withdraw/",
                                                  {"reason": "Injury"}, format="json")
        self.assertEqual(response.json()["status"], "WITHDRAWN")
        reg.refresh_from_db()
        self.assertEqual(reg.charge.status, Charge.Status.PAID)
        self.assertEqual(reg.charge.active_invoice_item().invoice.status, Invoice.Status.PAID)

    def test_confirm_never_confirms_unpaid(self):
        response = self.api(self.admin_user).post(f"/api/competition-registrations/{self.reg_b1.pk}/confirm/")
        self.assertEqual(response.status_code, 400)
        self.reg_b1.refresh_from_db()
        self.assertEqual(self.reg_b1.status, "PENDING")

    def test_only_registration_managers_reject_confirm_or_withdraw_others(self):
        for user in (self.finance_user, self.coach_a_user, self.student_user):
            client = self.api(user)
            for action in ("reject", "confirm", "withdraw"):
                response = client.post(f"/api/competition-registrations/{self.reg_b1.pk}/{action}/",
                                       {"reason": "x"}, format="json")
                self.assertEqual(response.status_code, 403, (user.username, action))
        # A parent cannot reject, and cannot withdraw another family's child (404: out of scope).
        parent = self.api(self.parent_a_user)
        self.assertEqual(parent.post(f"/api/competition-registrations/{self.reg_b1.pk}/reject/",
                                     {"reason": "x"}, format="json").status_code, 403)
        self.assertEqual(parent.post(f"/api/competition-registrations/{self.reg_b1.pk}/withdraw/").status_code, 404)
        self.reg_b1.refresh_from_db()
        self.assertEqual(self.reg_b1.status, "PENDING")

    def test_parent_withdrawal_respects_the_competition_setting(self):
        Competition.objects.filter(pk=self.competition.pk).update(allow_parent_withdrawal=False)
        response = self.api(self.parent_b_user).post(f"/api/competition-registrations/{self.reg_b1.pk}/withdraw/")
        self.assertEqual(response.status_code, 403)
        Competition.objects.filter(pk=self.competition.pk).update(allow_parent_withdrawal=True)
        response = self.api(self.parent_b_user).post(f"/api/competition-registrations/{self.reg_b1.pk}/withdraw/")
        self.assertEqual(response.status_code, 200)


class ResultTests(CompetitionStaffTestCase):
    def test_results_only_for_confirmed_paid_entries(self):
        client = self.api(self.admin_user)
        response = client.post("/api/competition-results/", {"registration": self.reg_b1.pk, "placing": 1,
                                                              "medal": "GOLD"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CompetitionResult.objects.exists())
        self.pay(self.reg_b1)
        response = client.post("/api/competition-results/", {"registration": self.reg_b1.pk, "placing": 1,
                                                              "medal": "GOLD", "score": "9.10",
                                                              "remarks": "Staff note"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        result_id = response.json()["id"]
        response = client.patch(f"/api/competition-results/{result_id}/", {"placing": 2, "medal": "SILVER"},
                                format="json")
        self.assertEqual(response.json()["medal"], "SILVER")
        self.assertEqual(client.delete(f"/api/competition-results/{result_id}/").status_code, 405)
        # A result blocks withdrawal.
        self.assertEqual(client.post(f"/api/competition-registrations/{self.reg_b1.pk}/withdraw/",
                                     {"reason": "x"}, format="json").status_code, 400)
        self.assertEqual(len(self.regs(result="yes")), 1)

    def test_only_results_managers_record_and_staff_remarks_stay_internal_for_students(self):
        self.pay(self.reg_b1)
        for user in (self.finance_user, self.coach_a_user, self.parent_b_user, self.student_user):
            response = self.api(user).post("/api/competition-results/",
                                           {"registration": self.reg_b1.pk, "placing": 1}, format="json")
            self.assertEqual(response.status_code, 403, user.username)
        self.assertFalse(CompetitionResult.objects.exists())
        # Aaron's own result: his student login sees no remarks.
        self.setup_form()
        self.assertEqual(self.register().status_code, 201)
        aaron = self.pay(CompetitionRegistration.objects.get(student=self.a1))
        self.api(self.admin_user).post("/api/competition-results/", {"registration": aaron.pk, "placing": 3,
                                                                      "medal": "BRONZE", "remarks": "Internal"},
                                       format="json")
        for row in self.rows(self.api(self.student_user).get("/api/competition-results/")):
            self.assertNotIn("remarks", row)
        for row in self.rows(self.api(self.student_user).get("/api/students/me/competitions/")):
            self.assertNotIn("remarks", row["result"] or {})


class SummaryAndListTests(CompetitionStaffTestCase):
    def test_summary_counts_come_from_registrations(self):
        self.setup_form()
        self.register()
        self.pay(CompetitionRegistration.objects.get(student=self.a1))
        body = self.api(self.admin_user).get(f"/api/competitions/{self.competition.pk}/summary/").json()
        self.assertEqual(body["registrations"], {"PENDING": 1, "CONFIRMED": 1, "WITHDRAWN": 0, "REJECTED": 0})
        self.assertEqual(body["fees"], {"paid": 1, "awaiting_payment": 1, "free": 0})
        self.assertEqual(body["results"], {"recorded": 0, "confirmed_without_result": 1})
        open_set = next(e for e in body["events"] if e["id"] == self.event.pk)
        self.assertEqual((open_set["entries"], open_set["confirmed"]), (2, 1))
        self.assertFalse(FAMILY_KEYS & set(str(body)))

    def test_summary_and_counts_only_for_staff_who_see_every_registration(self):
        for user in (self.finance_user, self.coach_a_user, self.parent_a_user, self.student_user):
            self.assertEqual(self.api(user).get(f"/api/competitions/{self.competition.pk}/summary/").status_code,
                             403, user.username)
            for row in self.rows(self.api(user).get("/api/competitions/")):
                self.assertNotIn("entry_count", row)
        row = next(r for r in self.rows(self.api(self.admin_user).get("/api/competitions/"))
                   if r["id"] == self.competition.pk)
        self.assertEqual((row["entry_count"], row["pending_count"]), (1, 1))
        row = self.api(self.super_user).get(f"/api/competitions/{self.competition.pk}/").json()
        self.assertEqual(row["entry_count"], 1)

    def test_status_filter_search_and_drafts(self):
        draft = Competition.objects.create(name="Winter Draft", start_date=self.today, end_date=self.today,
                                           registration_deadline=self.today, status="DRAFT")
        admin = self.api(self.admin_user)
        self.assertEqual({r["id"] for r in self.rows(admin.get("/api/competitions/", {"status": "DRAFT"}))},
                         {draft.pk})
        self.assertEqual({r["id"] for r in self.rows(admin.get("/api/competitions/", {"search": "state"}))},
                         {self.competition.pk})
        self.assertEqual(admin.get("/api/competitions/", {"status": "ARCHIVED"}).status_code, 400)
        for user in (self.finance_user, self.parent_a_user, self.coach_a_user):
            self.assertEqual(self.rows(self.api(user).get("/api/competitions/", {"status": "DRAFT"})), [])
            self.assertEqual(self.api(user).get(f"/api/competitions/{draft.pk}/").status_code, 404)


class CompetitionEditTests(CompetitionStaffTestCase):
    def test_create_and_edit_through_the_existing_api(self):
        admin = self.api(self.admin_user)
        body = {"name": "Club Cup", "venue": "Hall", "start_date": "2026-12-05", "end_date": "2026-12-04",
                "registration_deadline": "2026-11-20", "status": "DRAFT"}
        self.assertEqual(admin.post("/api/competitions/", body, format="json").status_code, 400)
        body["end_date"] = "2026-12-06"
        response = admin.post("/api/competitions/", body, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        comp_id = response.json()["id"]
        self.assertEqual(admin.patch(f"/api/competitions/{comp_id}/", {"end_date": "2026-12-01"},
                                     format="json").status_code, 400)
        self.assertEqual(admin.patch(f"/api/competitions/{comp_id}/", {"status": "OPEN"},
                                     format="json").json()["status"], "OPEN")
        response = admin.post("/api/competition-events/", {"competition": comp_id, "event_type": "NANQUAN",
                                                           "name": "Nanquan U12", "gender": "OPEN", "max_age": 12,
                                                           "fee": "25.00"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(admin.delete(f"/api/competitions/{comp_id}/").status_code, 405)

    def test_only_competition_managers_write(self):
        for user in (self.finance_user, self.coach_a_user, self.parent_a_user, self.student_user):
            client = self.api(user)
            self.assertEqual(client.patch(f"/api/competitions/{self.competition.pk}/", {"name": "X"},
                                          format="json").status_code, 403, user.username)
            self.assertEqual(client.post("/api/competition-events/", {"competition": self.competition.pk,
                                                                      "event_type": "OTHER", "name": "X",
                                                                      "fee": "1"}, format="json").status_code, 403)
            self.assertEqual(client.patch(f"/api/competition-events/{self.event.pk}/", {"fee": "0"},
                                          format="json").status_code, 403)
        self.event.refresh_from_db()
        self.assertEqual(self.event.fee, Decimal("50.00"))


class RoleMatrixTests(CompetitionStaffTestCase):
    """Direct API access per role: registrations and results of another family stay invisible."""

    def test_registration_and_result_idor(self):
        self.pay(self.reg_b1)
        result = self.api(self.admin_user).post("/api/competition-results/", {
            "registration": self.reg_b1.pk, "placing": 1, "medal": "GOLD"}, format="json").json()
        expectations = {
            self.super_user: 200, self.admin_user: 200,
            self.finance_user: 403,          # no registration capability at all
            self.parent_b_user: 200,         # own child
            self.parent_a_user: 404,         # another family
            self.student_user: 404,          # not their own entry
        }
        for user, code in expectations.items():
            client = self.api(user)
            self.assertEqual(client.get(f"/api/competition-registrations/{self.reg_b1.pk}/").status_code, code,
                             user.username)
            self.assertEqual(client.get(f"/api/competition-results/{result['id']}/").status_code, code,
                             user.username)
        # Coach B does not coach Dina (class A): 404; coach A sees the entry without family details.
        self.assertEqual(self.api(self.coach_b_user).get(
            f"/api/competition-registrations/{self.reg_b1.pk}/").status_code, 404)
        body = self.api(self.coach_a_user).get(f"/api/competition-registrations/{self.reg_b1.pk}/").json()
        self.assertFalse(FAMILY_KEYS & set(body))

    def test_draft_competition_and_form_idor(self):
        draft = Competition.objects.create(name="Hidden", start_date=self.today, end_date=self.today,
                                           registration_deadline=self.today, status="DRAFT")
        CompetitionEvent.objects.create(competition=draft, event_type="OTHER", name="Hidden set", fee="1")
        field = self.add_field(competition=draft, **SHIRT).json()
        for user in (self.finance_user, self.coach_a_user, self.parent_a_user, self.student_user):
            client = self.api(user)
            self.assertEqual(client.get(f"/api/competitions/{draft.pk}/").status_code, 404)
            self.assertEqual(client.get(f"/api/competition-form-fields/{field['id']}/").status_code, 403)
            self.assertEqual(self.rows(client.get("/api/competition-events/", {"competition": draft.pk})), [])
