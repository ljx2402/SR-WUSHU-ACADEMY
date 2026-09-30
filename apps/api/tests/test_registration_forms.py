"""Competition-specific registration forms.

Staff configure each competition's form (custom fields; system fields are
fixed), publish it as a new version, and parents register their own children
by answering the published form. The backend validates every answer and keeps
what was asked and answered with each registration, so later form changes
never alter past registrations.
"""

from django.core.exceptions import PermissionDenied

from apps.audit.models import AuditLog
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration
from apps.finance.models import Invoice
from apps.finance.services import record_payment

from .test_parent_portal import ParentPortalTestCase

SHIRT = {"key": "shirt_size", "label": "T-shirt size", "field_type": "SINGLE_SELECT", "required": True,
         "options": ["S", "M", "L", "XL"]}
ACCOMMODATION = {"key": "accommodation", "label": "Accommodation", "field_type": "YES_NO", "required": True}
REMARKS = {"key": "remarks", "label": "Special remarks", "field_type": "LONG_TEXT", "required": False,
           "max_length": 300}


class FormTestCase(ParentPortalTestCase):
    def add_field(self, user=None, competition=None, **data):
        payload = {"competition": (competition or self.competition).pk, **data}
        return self.api(user or self.admin_user).post("/api/competition-form-fields/", payload, format="json")

    def publish(self, user=None, competition=None):
        return self.api(user or self.admin_user).post(
            f"/api/competitions/{(competition or self.competition).pk}/publish-form/")

    def register(self, user=None, student=None, event=None, responses=None, **extra):
        event = event or self.event
        body = {"student": (student or self.a1).pk, "event": getattr(event, "pk", event), **extra}
        if responses is not None:
            body["responses"] = responses
        return self.api(user or self.parent_a_user).post("/api/competition-registrations/", body, format="json")

    def published_fields(self, user=None):
        return self.api(user or self.parent_a_user).get(
            f"/api/competitions/{self.competition.pk}/").json()["registration_form"]

    def setup_form(self, *fields):
        for field in fields:
            self.assertEqual(self.add_field(**field).status_code, 201)
        self.assertEqual(self.publish().status_code, 200)


class FormConfigurationTests(FormTestCase):
    def test_admin_builds_a_draft_and_publishes_it_as_a_new_version(self):
        for field in (SHIRT, ACCOMMODATION, REMARKS):
            self.assertEqual(self.add_field(**field).status_code, 201)
        staff_view = self.api(self.admin_user).get(f"/api/competitions/{self.competition.pk}/form/").json()
        self.assertTrue(staff_view["has_unpublished_changes"])
        self.assertEqual([f["key"] for f in staff_view["preview"]], ["shirt_size", "accommodation", "remarks"])
        # Parents still see the published version (no custom fields yet).
        self.assertEqual(self.published_fields()["fields"], [])
        published = self.publish().json()
        self.assertEqual((published["status"], published["version"], published["has_unpublished_changes"]),
                         ("PUBLISHED", 2, False))
        form = self.published_fields()
        self.assertEqual(form["version"], 2)
        self.assertEqual(form["fields"][0], {
            "key": "shirt_size", "label": "T-shirt size", "type": "SINGLE_SELECT", "required": True, "help_text": "",
            "placeholder": "", "options": ["S", "M", "L", "XL"], "max_length": None, "min_value": None,
            "max_value": None})
        self.assertTrue(AuditLog.objects.filter(reason="Registration form published (version 2)").exists())

    def test_reorder_and_unpublish(self):
        self.setup_form(SHIRT, ACCOMMODATION)
        admin = self.api(self.admin_user)
        url = f"/api/competitions/{self.competition.pk}/reorder-form/"
        self.assertEqual(admin.post(url, {"keys": ["accommodation"]}, format="json").status_code, 400)
        self.assertEqual(admin.post(url, {"keys": ["accommodation", "shirt_size"]}, format="json").status_code, 200)
        self.publish()
        self.assertEqual([f["key"] for f in self.published_fields()["fields"]], ["accommodation", "shirt_size"])
        admin.post(f"/api/competitions/{self.competition.pk}/unpublish-form/")
        form = self.published_fields()
        self.assertEqual((form["status"], form["fields"]), ("DRAFT", []))
        response = self.register(responses={"shirt_size": "M", "accommodation": True})
        self.assertEqual(response.status_code, 400)
        self.assertIn("not available", str(response.json()))

    def test_field_definitions_are_validated(self):
        cases = [
            ({"key": "student", "label": "Student", "field_type": "TEXT"}, "key"),           # system field
            ({"key": "event_id", "label": "Event", "field_type": "TEXT"}, "key"),
            ({"key": "Bad Key", "label": "X", "field_type": "TEXT"}, "key"),
            ({"key": "bank_pw", "label": "Online banking password", "field_type": "TEXT"}, "label"),
            ({"key": "api_key", "label": "Key", "field_type": "TEXT"}, "key"),
            ({"key": "card", "label": "Card", "field_type": "TEXT", "help_text": "Your credit card number"}, "help_text"),
            ({"key": "size", "label": "Size", "field_type": "SINGLE_SELECT", "options": []}, "options"),
            ({"key": "size", "label": "Size", "field_type": "SINGLE_SELECT", "options": ["S", "s"]}, "options"),
            ({"key": "size", "label": "Size", "field_type": "TEXT", "options": ["S"]}, "options"),
            ({"key": "age", "label": "Age", "field_type": "NUMBER", "min_value": "10", "max_value": "5"}, "max_value"),
            ({"key": "name", "label": "Name", "field_type": "TEXT", "max_length": 5000}, "max_length"),
            ({"key": "x", "label": "X", "field_type": "SCRIPT"}, "field_type"),
        ]
        for data, field in cases:
            with self.subTest(data=data):
                response = self.add_field(**data)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn(field, response.json())
        self.assertEqual(self.add_field(**SHIRT).status_code, 201)
        self.assertEqual(self.add_field(**SHIRT).status_code, 400)                       # duplicate key
        field_id = self.competition.form_fields.get(key="shirt_size").pk
        renamed = self.api(self.admin_user).patch(f"/api/competition-form-fields/{field_id}/", {"key": "size"},
                                                  format="json")
        self.assertEqual(renamed.status_code, 400)

    def test_only_competition_managers_configure_forms(self):
        self.assertEqual(self.add_field(**SHIRT).status_code, 201)
        field_id = self.competition.form_fields.get().pk
        for user in (self.parent_a_user, self.coach_a_user, self.finance_user):
            client = self.api(user)
            with self.subTest(user=user.username):
                self.assertEqual(self.add_field(user=user, **ACCOMMODATION).status_code, 403)
                self.assertEqual(client.patch(f"/api/competition-form-fields/{field_id}/", {"required": False},
                                              format="json").status_code, 403)
                self.assertEqual(client.delete(f"/api/competition-form-fields/{field_id}/").status_code, 403)
                self.assertEqual(self.publish(user=user).status_code, 403)
                self.assertEqual(client.get(f"/api/competitions/{self.competition.pk}/form/").status_code, 403)
                self.assertEqual(client.get("/api/competition-form-fields/").status_code, 403)
        # The super admin can.
        self.assertEqual(self.publish(user=self.super_user).status_code, 200)


class ParentRegistrationTests(FormTestCase):
    def setUp(self):
        self.setup_form(SHIRT, ACCOMMODATION, REMARKS)

    def test_parent_registers_with_answers_and_gets_the_events_fee_invoice(self):
        response = self.register(responses={"shirt_size": "L", "accommodation": False,
                                            "remarks": "<script>alert(1)</script>\x00 Vegetarian"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual((body["status"], body["fee"], body["form_version"]), ("PENDING", "50.00", 2))
        self.assertEqual(body["form_responses"], [
            {"key": "shirt_size", "label": "T-shirt size", "type": "SINGLE_SELECT", "value": "L", "display": "L"},
            {"key": "accommodation", "label": "Accommodation", "type": "YES_NO", "value": False, "display": "No"},
            # Stored as plain text (control characters removed); clients render it escaped.
            {"key": "remarks", "label": "Special remarks", "type": "LONG_TEXT",
             "value": "<script>alert(1)</script> Vegetarian", "display": "<script>alert(1)</script> Vegetarian"},
        ])
        invoice = Invoice.objects.get(pk=body["invoice"]["id"])
        self.assertEqual((invoice.status, invoice.total), ("ISSUED", self.event.fee))
        # Another event of the same competition has its own fee.
        other = self.register(student=self.a3, event=self.event_2, responses={"shirt_size": "S", "accommodation": True})
        self.assertEqual(other.json()["fee"], "30.00")

    def test_required_fields_types_and_options_are_enforced(self):
        cases = [
            ({}, ["responses.shirt_size", "responses.accommodation"]),
            ({"shirt_size": "XXL", "accommodation": True}, ["responses.shirt_size"]),
            ({"shirt_size": ["L"], "accommodation": True}, ["responses.shirt_size"]),
            ({"shirt_size": "L", "accommodation": "maybe"}, ["responses.accommodation"]),
            ({"shirt_size": "L", "accommodation": True, "remarks": "x" * 301}, ["responses.remarks"]),
            ({"shirt_size": "L", "accommodation": True, "password": "hunter2"}, ["responses.password"]),
        ]
        for responses, keys in cases:
            with self.subTest(responses=responses):
                response = self.register(responses=responses)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(sorted(k for k in response.json() if k.startswith("responses.")), sorted(keys))
        self.assertEqual(self.register(responses=["not", "an", "object"]).status_code, 400)
        self.assertFalse(CompetitionRegistration.objects.filter(student=self.a1).exists())

    def test_number_date_email_phone_and_multi_select_types(self):
        extra = [
            {"key": "weight", "label": "Weight (kg)", "field_type": "NUMBER", "min_value": "20", "max_value": "120"},
            {"key": "arrival", "label": "Arrival date", "field_type": "DATE"},
            {"key": "contact_email", "label": "Contact email", "field_type": "EMAIL"},
            {"key": "emergency_phone", "label": "Emergency phone", "field_type": "PHONE"},
            {"key": "meals", "label": "Meals", "field_type": "MULTI_SELECT", "options": ["Breakfast", "Lunch", "Dinner"]},
        ]
        self.setup_form(*extra)
        base = {"shirt_size": "M", "accommodation": True}
        bad = [("weight", "heavy"), ("weight", "5"), ("weight", True), ("arrival", "31/12/2026"),
               ("contact_email", "not-an-email"), ("emergency_phone", "call me"), ("meals", ["Supper"]),
               ("meals", "Lunch")]
        for key, value in bad:
            with self.subTest(key=key, value=value):
                response = self.register(responses={**base, key: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(f"responses.{key}", response.json())
        good = self.register(responses={**base, "weight": "42.50", "arrival": "2026-10-30",
                                        "contact_email": "a@example.com", "emergency_phone": "+60 12-345 6789",
                                        "meals": ["Dinner", "Breakfast", "Dinner"]})
        self.assertEqual(good.status_code, 201, good.content)
        answers = {a["key"]: a for a in good.json()["form_responses"]}
        self.assertEqual(answers["weight"]["value"], "42.5")
        self.assertEqual(answers["meals"]["value"], ["Breakfast", "Dinner"])

    def test_competition_rules_still_apply(self):
        answers = {"shirt_size": "M", "accommodation": True}
        self.assertEqual(self.register(responses=answers).status_code, 201)
        duplicate = self.register(responses=answers)
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("already registered", str(duplicate.json()))
        self.event_2.gender = "F"
        self.event_2.save()
        ineligible = self.register(student=self.a3, event=self.event_2, responses=answers)
        self.assertEqual(ineligible.status_code, 400)
        self.assertIn("female", str(ineligible.json()).lower())
        self.competition.status = Competition.Status.CLOSED
        self.competition.save()
        self.assertEqual(self.register(student=self.a2, responses=answers).status_code, 400)

    def test_payment_lifecycle_is_unchanged(self):
        body = self.register(responses={"shirt_size": "M", "accommodation": True}).json()
        invoice = Invoice.objects.get(pk=body["invoice"]["id"])
        record_payment([(invoice, "20.00")], "CASH", self.admin_user)
        self.assertEqual(CompetitionRegistration.objects.get(pk=body["id"]).status, "PENDING")
        record_payment([(invoice, "30.00")], "CASH", self.admin_user)
        self.assertEqual(CompetitionRegistration.objects.get(pk=body["id"]).status, "CONFIRMED")


class RegistrationIdorTests(FormTestCase):
    def setUp(self):
        self.setup_form(SHIRT, ACCOMMODATION)
        self.answers = {"shirt_size": "L", "accommodation": True}
        self.own = self.register(responses=self.answers).json()
        self.theirs = self.register(user=self.parent_b_user, student=self.b2, responses=self.answers).json()

    def test_parent_cannot_register_another_familys_child_or_mismatch_ids(self):
        self.assertEqual(self.register(student=self.b1, event=self.event_2, responses=self.answers).status_code, 403)
        other = Competition.objects.create(
            name="Other Open", start_date=self.competition.start_date, end_date=self.competition.end_date,
            registration_deadline=self.competition.registration_deadline, status="OPEN")
        other_event = CompetitionEvent.objects.create(competition=other, event_type="OTHER", name="X", fee="10.00")
        mismatch = self.register(student=self.a2, event=self.event_2, competition=other.pk, responses=self.answers)
        self.assertEqual(mismatch.status_code, 400)
        self.assertIn("event", mismatch.json())
        # The other competition's own (empty) form applies to its own event: answers for this one are refused.
        wrong_form = self.register(student=self.a2, event=other_event, responses=self.answers)
        self.assertEqual(wrong_form.status_code, 400)
        self.assertEqual(self.register(student=self.a2, event=999999, responses=self.answers).status_code, 400)
        self.assertEqual(self.register(student=self.a2, event="x", responses=self.answers).status_code, 400)

    def test_parent_cannot_read_or_edit_another_familys_registration(self):
        client = self.api(self.parent_a_user)
        self.assertEqual(client.get(f"/api/competition-registrations/{self.theirs['id']}/").status_code, 404)
        listed = client.get("/api/competition-registrations/").json()["results"]
        self.assertEqual([r["id"] for r in listed], [self.own["id"]])
        self.assertEqual(client.get("/api/competition-registrations/", {"competition": self.competition.pk})
                         .json()["count"], 1)
        for method in ("put", "patch"):
            self.assertEqual(getattr(client, method)(f"/api/competition-registrations/{self.own['id']}/",
                                                     {"form_responses": []}, format="json").status_code, 405)
        self.assertEqual(client.post(f"/api/competition-registrations/{self.theirs['id']}/withdraw/").status_code, 404)

    def test_answers_are_for_the_family_and_competition_staff_only(self):
        self.assertIn("form_responses", self.api(self.parent_a_user).get(
            f"/api/competition-registrations/{self.own['id']}/").json())
        self.assertIn("form_responses", self.api(self.admin_user).get(
            f"/api/competition-registrations/{self.own['id']}/").json())
        # Coach A coaches Aaron's class: sees the entry, not the answers.
        coach_view = self.api(self.coach_a_user).get(f"/api/competition-registrations/{self.own['id']}/")
        self.assertEqual(coach_view.status_code, 200)
        self.assertNotIn("form_responses", coach_view.json())

    def test_submitted_answers_cannot_be_rewritten(self):
        registration = CompetitionRegistration.objects.get(pk=self.own["id"])
        registration.form_responses = [{"key": "shirt_size", "label": "T-shirt size", "type": "SINGLE_SELECT",
                                        "value": "S", "display": "S"}]
        with self.assertRaises(PermissionDenied):
            registration.save()


class HistoricalAnswersTests(FormTestCase):
    def test_old_registrations_keep_the_form_they_answered(self):
        self.setup_form(SHIRT)                                                   # version 2: T-shirt size
        old = self.register(responses={"shirt_size": "L"}).json()
        self.assertEqual(old["form_version"], 2)
        # Version 3: T-shirt size removed, Accommodation added.
        admin = self.api(self.admin_user)
        shirt = self.competition.form_fields.get(key="shirt_size")
        self.assertEqual(admin.delete(f"/api/competition-form-fields/{shirt.pk}/").status_code, 204)
        self.assertEqual(self.add_field(**ACCOMMODATION).status_code, 201)
        self.assertEqual(self.publish().json()["version"], 3)
        # The old registration still reads T-shirt size = L.
        again = self.api(self.parent_a_user).get(f"/api/competition-registrations/{old['id']}/").json()
        self.assertEqual((again["form_version"], again["form_responses"]), (2, [
            {"key": "shirt_size", "label": "T-shirt size", "type": "SINGLE_SELECT", "value": "L", "display": "L"}]))
        # New registrations use version 3 and cannot answer the removed question.
        refused = self.register(student=self.a2, event=self.event_2, responses={"shirt_size": "L", "accommodation": True})
        self.assertEqual(refused.status_code, 400)
        self.assertIn("responses.shirt_size", refused.json())
        new = self.register(student=self.a2, event=self.event_2, responses={"accommodation": True}).json()
        self.assertEqual((new["form_version"], [a["key"] for a in new["form_responses"]]), (3, ["accommodation"]))
        # Editing a field's wording later does not touch stored answers either.
        field = self.competition.form_fields.get(key="accommodation")
        admin.patch(f"/api/competition-form-fields/{field.pk}/", {"label": "Hotel needed?"}, format="json")
        self.publish()
        stored = CompetitionRegistration.objects.get(pk=new["id"]).form_responses
        self.assertEqual(stored[0]["label"], "Accommodation")


class WithdrawalRuleTests(FormTestCase):
    def test_each_competition_decides_whether_parents_may_withdraw(self):
        self.setup_form()
        entry = self.register(responses={}).json()
        self.competition.allow_parent_withdrawal = False
        self.competition.save()
        refused = self.api(self.parent_a_user).post(f"/api/competition-registrations/{entry['id']}/withdraw/")
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(CompetitionRegistration.objects.get(pk=entry["id"]).status, "PENDING")
        self.assertFalse(self.published_fields() is None)
        self.assertFalse(self.api(self.parent_a_user).get(f"/api/competitions/{self.competition.pk}/")
                         .json()["allow_parent_withdrawal"])


class AdminFormBuilderTests(FormTestCase):
    def test_admin_page_shows_the_builder_preview_and_publishes(self):
        self.add_field(**SHIRT)
        self.client.force_login(self.admin_user)
        page = self.client.get(f"/admin/competitions/competition/{self.competition.pk}/change/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Registration form (custom fields; publish to make changes live)")
        self.assertContains(page, "Yes: publish to make them live")
        self.assertContains(page, "T-shirt size")
        response = self.client.post("/admin/competitions/competition/", {
            "action": "publish_registration_form", "_selected_action": [self.competition.pk]}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.competition.refresh_from_db()
        self.assertEqual((self.competition.form_status, self.competition.form_version), ("PUBLISHED", 2))
        # A coach cannot reach the admin builder.
        self.client.force_login(self.coach_a_user)
        self.assertNotEqual(self.client.get(f"/admin/competitions/competition/{self.competition.pk}/change/")
                            .status_code, 200)
