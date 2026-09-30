"""Phase 6C: the API as the Coach Portal uses it.

Coach A coaches Class A (Ali, Raj); Coach B coaches Class B (Mei). The clock
is 20:00 today: session A (17:00–19:00) has ended, session B (19:00–21:00) is
in progress. Every scope comes from the signed-in coach; ids in URLs and
filters are never trusted.
"""

import datetime
from decimal import Decimal
from unittest import mock

from rest_framework.test import APIClient

from apps.academy.models import TrainingSession
from apps.academy.services import assign_substitute, cancel_session, revoke_substitute
from apps.academy.tests.base import AcademyTestCase
from apps.attendance.models import AttendanceRecord
from apps.audit.models import AuditLog
from apps.competitions.models import Competition, CompetitionEvent
from apps.competitions.services import register
from apps.finance.models import Invoice
from apps.finance.services import add_charge, record_payment
from apps.finance.tests.helpers import issued_invoice


class CoachPortalTestCase(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tomorrow_a = TrainingSession.objects.create(
            training_class=cls.class_a, date=cls.today + datetime.timedelta(days=1),
            start_time=datetime.time(10), end_time=datetime.time(12), venue="Hall A")
        cls.tomorrow_a.coach_slots.create(coach=cls.coach_a)
        cls.next_week_b = TrainingSession.objects.create(
            training_class=cls.class_b, date=cls.today + datetime.timedelta(days=7),
            start_time=datetime.time(19), end_time=datetime.time(21))
        cls.next_week_b.coach_slots.create(coach=cls.coach_b)
        cls.old_a = TrainingSession.objects.create(
            training_class=cls.class_a, date=cls.today - datetime.timedelta(days=5),
            start_time=datetime.time(17), end_time=datetime.time(19))
        cls.old_a.coach_slots.create(coach=cls.coach_a)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    @staticmethod
    def ids(response):
        body = response.json()
        return {row["id"] for row in body.get("results", body)}

    def submit(self, user, session, entries, reason=""):
        return self.api(user).post(f"/api/sessions/{session.pk}/attendance/",
                                   {"records": entries, "reason": reason}, format="json")


class SessionScopeTests(CoachPortalTestCase):
    def test_coach_sees_only_their_classes_sessions_with_role_and_attendance(self):
        response = self.api(self.coach_a_user).get("/api/sessions/coaching/")
        self.assertEqual(response.status_code, 200)
        rows = {row["id"]: row for row in response.json()["results"]}
        self.assertEqual(set(rows), {self.session_a.pk, self.tomorrow_a.pk, self.old_a.pk})
        today = rows[self.session_a.pk]
        self.assertEqual(today["my_role"], {"role": "REGULAR", "status": "ASSIGNED", "access_ends_at": None})
        self.assertEqual({k: today["attendance"][k] for k in ("state", "expected", "marked", "unmarked", "percentage")},
                         {"state": "OPEN", "expected": 2, "marked": 0, "unmarked": 2, "percentage": None})
        self.assertEqual(rows[self.tomorrow_a.pk]["attendance"]["state"], "NOT_STARTED")
        self.assertEqual(rows[self.old_a.pk]["attendance"]["state"], "LOCKED")

    def test_filters_and_order_cannot_widen_the_scope(self):
        client = self.api(self.coach_a_user)
        upcoming = client.get("/api/sessions/coaching/", {"start": (self.today + datetime.timedelta(days=1)).isoformat(),
                                                         "order": "asc"})
        self.assertEqual(self.ids(upcoming), {self.tomorrow_a.pk})
        # A class id or date for Coach B's sessions returns nothing of theirs.
        self.assertEqual(self.ids(client.get("/api/sessions/coaching/", {"class": self.class_b.pk})), set())
        self.assertEqual(self.ids(client.get("/api/sessions/coaching/", {"coach": self.coach_b.pk})),
                         {self.session_a.pk, self.tomorrow_a.pk, self.old_a.pk})
        self.assertEqual(client.get("/api/sessions/coaching/", {"class": "x"}).status_code, 400)
        self.assertEqual(client.get("/api/sessions/coaching/", {"status": "PAID"}).status_code, 400)

    def test_cancelled_sessions_are_marked_and_take_no_attendance(self):
        cancel_session(self.tomorrow_a, self.admin_user, "Hall closed")
        client = self.api(self.coach_a_user)
        cancelled = client.get("/api/sessions/coaching/", {"status": "CANCELLED"}).json()["results"]
        self.assertEqual([(r["id"], r["status"], r["phase"], r["attendance"]["state"]) for r in cancelled],
                         [(self.tomorrow_a.pk, "CANCELLED", "CANCELLED", "CANCELLED")])
        with mock.patch("django.utils.timezone.now",
                        return_value=self.tomorrow_a.starts_at + datetime.timedelta(minutes=30)):
            refused = self.submit(self.coach_a_user, self.tomorrow_a, [{"student": self.student_1.pk,
                                                                        "status": "PRESENT"}])
        self.assertEqual(refused.status_code, 400)
        self.assertIn("cancelled", str(refused.json()))

    def test_non_coaches_cannot_use_the_coaching_list(self):
        for user in (self.parent_1_user,):
            self.assertEqual(self.api(user).get("/api/sessions/coaching/").status_code, 403)
        # Staff see every session.
        self.assertEqual(self.api(self.admin_user).get("/api/sessions/coaching/").json()["count"], 5)


class CoachIsolationTests(CoachPortalTestCase):
    def test_coach_a_cannot_reach_coach_b_sessions_rosters_or_attendance(self):
        client = self.api(self.coach_a_user)
        for url in (f"/api/sessions/{self.session_b.pk}/", f"/api/sessions/{self.session_b.pk}/roster/",
                    f"/api/sessions/{self.session_b.pk}/attendance/", f"/api/sessions/{self.next_week_b.pk}/roster/",
                    "/api/sessions/999999/roster/"):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 404)
        self.assertEqual(client.get("/api/sessions/abc/roster/").status_code, 404)
        write = self.submit(self.coach_a_user, self.session_b, [{"student": self.student_2.pk, "status": "ABSENT"}])
        self.assertEqual(write.status_code, 404)
        self.assertFalse(AttendanceRecord.objects.filter(session=self.session_b).exists())

    def test_another_sessions_student_or_record_is_refused(self):
        # Mei (Class B) is not on session A's roster: nothing is saved, not even Ali.
        response = self.submit(self.coach_a_user, self.session_a, [
            {"student": self.student_1.pk, "status": "PRESENT"},
            {"student": self.student_2.pk, "status": "PRESENT"}])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AttendanceRecord.objects.filter(session=self.session_a).exists())
        for bad in ({"student": "x", "status": "PRESENT"}, {"student": self.student_1.pk, "status": "HERE"}):
            with self.subTest(entry=bad):
                self.assertEqual(self.submit(self.coach_a_user, self.session_a, [bad]).status_code, 400)
        record_b = AttendanceRecord.objects.create(session=self.session_b, student=self.student_2, status="PRESENT",
                                                   recorded_by=self.coach_b_user)
        client = self.api(self.coach_a_user)
        self.assertEqual(client.get(f"/api/attendance/{record_b.pk}/").status_code, 404)
        self.assertEqual(client.get(f"/api/attendance/{record_b.pk}/history/").status_code, 404)
        self.assertEqual(client.get("/api/attendance/", {"student": self.student_2.pk}).json()["count"], 0)

    def test_students_outside_the_roster_are_not_found(self):
        client = self.api(self.coach_a_user)
        self.assertEqual(client.get(f"/api/students/{self.student_2.pk}/").status_code, 404)
        roster_view = client.get(f"/api/students/{self.student_1.pk}/").json()
        for hidden in ("ic_number", "address", "phone", "email", "guardians", "family", "date_of_birth"):
            self.assertNotIn(hidden, roster_view)


class AttendanceTests(CoachPortalTestCase):
    def test_roster_shows_coaching_information_only(self):
        roster = self.api(self.coach_a_user).get(f"/api/sessions/{self.session_a.pk}/roster/").json()
        self.assertEqual({r["full_name"] for r in roster}, {"Ali", "Raj"})
        for row in roster:
            self.assertEqual(set(row), {"id", "student_no", "full_name", "chinese_name", "gender", "age", "status"})

    def test_mark_all_then_counts_exclude_unmarked(self):
        saved = self.submit(self.coach_a_user, self.session_a, [
            {"student": self.student_1.pk, "status": "PRESENT"},
            {"student": self.student_3.pk, "status": "UNMARKED"}])
        self.assertEqual(saved.status_code, 200, saved.content)
        sheet = self.api(self.coach_a_user).get(f"/api/sessions/{self.session_a.pk}/attendance/").json()
        self.assertEqual((sheet["state"], sheet["summary"]["expected"], sheet["summary"]["marked"],
                          sheet["summary"]["unmarked"], sheet["summary"]["percentage"]), ("OPEN", 2, 1, 1, "100.00"))
        self.assertEqual({r["student"]: r["status"] for r in sheet["sheet"]},
                         {self.student_1.pk: "PRESENT", self.student_3.pk: "UNMARKED"})
        # Submitting the same marks again creates no duplicates.
        again = self.submit(self.coach_a_user, self.session_a, [{"student": self.student_1.pk, "status": "PRESENT"}])
        self.assertEqual(again.status_code, 200)
        self.assertEqual(AttendanceRecord.objects.filter(session=self.session_a, student=self.student_1).count(), 1)

    def test_changes_need_a_reason_are_audited_and_atomic(self):
        self.submit(self.coach_a_user, self.session_a, [{"student": self.student_1.pk, "status": "PRESENT"}])
        no_reason = self.submit(self.coach_a_user, self.session_a, [
            {"student": self.student_3.pk, "status": "PRESENT"},
            {"student": self.student_1.pk, "status": "ABSENT"}])
        self.assertEqual(no_reason.status_code, 400)
        self.assertFalse(AttendanceRecord.objects.filter(session=self.session_a, student=self.student_3).exists())
        fixed = self.submit(self.coach_a_user, self.session_a, [{"student": self.student_1.pk, "status": "ABSENT",
                                                                 "remarks": "Left early"}], reason="Left before warm-up")
        self.assertEqual(fixed.status_code, 200)
        entry = AuditLog.objects.filter(object_repr__icontains="Ali").order_by("-id").first()
        self.assertEqual((entry.actor, entry.reason), (self.coach_a_user, "Left before warm-up"))

    def test_48_hour_window_for_coaches_and_admin_corrections_with_reason(self):
        late = self.old_a.ends_at + datetime.timedelta(hours=48, minutes=1)
        with mock.patch("django.utils.timezone.now", return_value=late):
            refused = self.submit(self.coach_a_user, self.old_a, [{"student": self.student_1.pk, "status": "PRESENT"}],
                                  reason="Forgot")
            self.assertEqual(refused.status_code, 403)
            self.assertIn("48-hour", refused.json()["detail"])
            no_reason = self.submit(self.admin_user, self.old_a, [{"student": self.student_1.pk, "status": "PRESENT"}])
            self.assertEqual(no_reason.status_code, 400)
            ok = self.submit(self.admin_user, self.old_a, [{"student": self.student_1.pk, "status": "PRESENT"}],
                             reason="Coach reported by phone")
            self.assertEqual(ok.status_code, 200)
        just_inside = self.old_a.ends_at + datetime.timedelta(hours=47, minutes=59)
        with mock.patch("django.utils.timezone.now", return_value=just_inside):
            self.assertEqual(self.submit(self.coach_a_user, self.old_a, [
                {"student": self.student_3.pk, "status": "ABSENT"}]).status_code, 200)

    def test_nothing_before_the_session_starts(self):
        response = self.submit(self.coach_a_user, self.tomorrow_a, [{"student": self.student_1.pk,
                                                                     "status": "PRESENT"}])
        self.assertEqual(response.status_code, 400)
        self.assertIn("session start", str(response.json()))


class SubstituteTests(CoachPortalTestCase):
    def test_substitute_sees_only_the_covered_session_while_authorized(self):
        slot = assign_substitute(self.tomorrow_a, self.coach_b, self.coach_a, self.admin_user, "Coach A away")
        client = self.api(self.coach_b_user)
        rows = {r["id"]: r for r in client.get("/api/sessions/coaching/").json()["results"]}
        self.assertIn(self.tomorrow_a.pk, rows)
        self.assertNotIn(self.session_a.pk, rows)
        self.assertNotIn(self.old_a.pk, rows)
        self.assertEqual((rows[self.tomorrow_a.pk]["my_role"]["role"], rows[self.tomorrow_a.pk]["my_role"]["status"]),
                         ("SUBSTITUTE", "ASSIGNED"))
        self.assertEqual(client.get(f"/api/sessions/{self.tomorrow_a.pk}/roster/").status_code, 200)
        self.assertEqual(client.get(f"/api/sessions/{self.session_a.pk}/roster/").status_code, 404)
        self.assertEqual(client.get(f"/api/students/{self.student_1.pk}/").status_code, 200)   # on the roster
        # The regular coach's view shows they are replaced.
        mine = {r["id"]: r for r in self.api(self.coach_a_user).get("/api/sessions/coaching/").json()["results"]}
        self.assertEqual(mine[self.tomorrow_a.pk]["my_role"]["status"], "REPLACED")
        # Coaches cannot manage substitutes.
        self.assertEqual(client.post(f"/api/sessions/{self.tomorrow_a.pk}/revoke-substitute/",
                                     {"substitute": self.coach_b.pk, "reason": "x"}).status_code, 403)
        revoke_substitute(slot, self.admin_user, "Coach A is back")
        self.assertNotIn(self.tomorrow_a.pk, self.ids(client.get("/api/sessions/coaching/")))
        self.assertEqual(client.get(f"/api/sessions/{self.tomorrow_a.pk}/roster/").status_code, 404)
        self.assertEqual(client.get(f"/api/students/{self.student_1.pk}/").status_code, 404)

    def test_substitute_takes_attendance_inside_the_window_only(self):
        assign_substitute(self.tomorrow_a, self.coach_b, self.coach_a, self.admin_user, "Coach A away")
        during = self.tomorrow_a.starts_at + datetime.timedelta(minutes=10)
        with mock.patch("django.utils.timezone.now", return_value=during):
            ok = self.submit(self.coach_b_user, self.tomorrow_a, [{"student": self.student_1.pk, "status": "LATE"}])
            self.assertEqual(ok.status_code, 200, ok.content)
        after = self.tomorrow_a.ends_at + datetime.timedelta(hours=25)
        with mock.patch("django.utils.timezone.now", return_value=after):
            self.assertEqual(self.submit(self.coach_b_user, self.tomorrow_a, [
                {"student": self.student_3.pk, "status": "PRESENT"}]).status_code, 404)


class FamilyFinanceAndCompetitionIsolationTests(CoachPortalTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        charge = add_charge(cls.student_1, "UNIFORM", "Uniform", "80.00")
        cls.invoice = issued_invoice([charge])
        cls.payment, cls.receipt = record_payment([(cls.invoice, "80.00")], "CASH", cls.admin_user)
        competition = Competition.objects.create(
            name="Open", start_date=cls.today + datetime.timedelta(days=30),
            end_date=cls.today + datetime.timedelta(days=30),
            registration_deadline=cls.today + datetime.timedelta(days=10), status="OPEN")
        cls.competition = competition
        cls.event = CompetitionEvent.objects.create(competition=competition, event_type="OTHER", name="E",
                                                    fee=Decimal("20.00"))
        cls.reg_ali = register(cls.student_1, cls.event, cls.parent_1_user, notes="Family note",
                               responses={})
        cls.reg_mei = register(cls.student_2, cls.event, cls.parent_1_user, responses={})

    def test_coach_has_no_finance_family_or_payroll_access(self):
        client = self.api(self.coach_a_user)
        for url in ("/api/invoices/", f"/api/invoices/{self.invoice.pk}/", "/api/payments/",
                    f"/api/payments/{self.payment.pk}/", "/api/receipts/", f"/api/receipts/{self.receipt.pk}/",
                    "/api/refunds/", "/api/charges/", "/api/payment-proofs/", "/api/payment-info/",
                    "/api/families/", f"/api/families/{self.student_1.family_id}/", "/api/parents/",
                    f"/api/parents/{self.parent_1.pk}/", "/api/users/", "/api/payroll-runs/",
                    "/api/competition-form-fields/", f"/api/competitions/{self.competition.pk}/form/"):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 403)
        for method, url, data in (
            ("post", "/api/payments/", {"amount": "1.00", "method": "CASH",
                                        "allocations": [{"invoice": self.invoice.pk, "amount": "1.00"}]}),
            ("patch", "/api/payment-info/", {"bank_name": "X"}),
            ("post", "/api/payment-proofs/1/accept/", {}),
            ("post", "/api/competition-form-fields/", {"competition": self.competition.pk, "key": "x", "label": "X",
                                                        "field_type": "TEXT"}),
            ("post", f"/api/competitions/{self.competition.pk}/publish-form/", {}),
            ("post", f"/api/competitions/{self.competition.pk}/unpublish-form/", {}),
            ("post", f"/api/competition-registrations/{self.reg_ali.pk}/confirm/", {}),
        ):
            with self.subTest(method=method, url=url):
                self.assertEqual(getattr(client, method)(url, data, format="json").status_code, 403)
        self.assertEqual(Invoice.objects.get(pk=self.invoice.pk).status, "PAID")
        # Payslips: only the coach's own finalized ones exist for them (none here); never another coach's.
        self.assertEqual(client.get("/api/payslips/").json()["count"], 0)

    def test_coach_sees_their_athletes_entries_without_family_or_payment_details(self):
        client = self.api(self.coach_a_user)
        entries = client.get("/api/competition-registrations/").json()["results"]
        self.assertEqual([e["id"] for e in entries], [self.reg_ali.pk])          # Mei is Coach B's athlete
        for hidden in ("form_responses", "notes", "fee", "fee_status", "invoice"):
            self.assertNotIn(hidden, entries[0])
        self.assertEqual((entries[0]["status"], entries[0]["event_name"]), ("PENDING", "E"))
        self.assertEqual(client.get(f"/api/competition-registrations/{self.reg_mei.pk}/").status_code, 404)
        self.assertEqual(client.get("/api/competition-registrations/", {"competition": self.competition.pk})
                         .json()["count"], 1)
        # Coaches cannot register, withdraw or edit entries.
        self.assertEqual(client.post("/api/competition-registrations/", {"student": self.student_1.pk,
                                                                          "event": self.event.pk}).status_code, 403)
        self.assertEqual(client.post(f"/api/competition-registrations/{self.reg_ali.pk}/withdraw/").status_code, 403)
        self.assertEqual(client.patch(f"/api/competition-registrations/{self.reg_ali.pk}/", {"status": "CONFIRMED"},
                                      format="json").status_code, 405)
        # The family and staff still see the details.
        family = self.api(self.parent_1_user).get(f"/api/competition-registrations/{self.reg_ali.pk}/").json()
        self.assertEqual((family["notes"], family["fee"]), ("Family note", "20.00"))
        self.assertIn("invoice", self.api(self.admin_user).get(
            f"/api/competition-registrations/{self.reg_ali.pk}/").json())


class SensitiveRosterDataTests(CoachPortalTestCase):
    """Medical notes and emergency contacts are not coach data: no capability
    grants them to coaches, so the API leaves them out of every coach view."""

    SENSITIVE = ("medical_notes", "emergency_contacts")

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student_1.medical_notes = "Test note"
        cls.student_1.save()

    def coach_views(self, user, session):
        client = self.api(user)
        rosters = {
            "session roster": client.get(f"/api/sessions/{session.pk}/roster/"),
            "class students": client.get(f"/api/classes/{session.training_class_id}/students/"),
        }
        for label, response in rosters.items():
            self.assertEqual(response.status_code, 200, label)
            yield label, response.json()
        detail = client.get(f"/api/students/{self.student_1.pk}/")
        self.assertEqual(detail.status_code, 200)
        yield "student detail", [detail.json()]
        listing = client.get("/api/students/").json()
        yield "student list", listing.get("results", listing)

    def assert_hidden(self, rows, label):
        self.assertTrue(rows, label)
        for row in rows:
            for field in self.SENSITIVE:
                self.assertNotIn(field, row, f"{label}: {field}")
            self.assertNotIn("Test note", str(row), label)
            self.assertNotIn("0123", str(row), label)  # Parent One's phone

    def test_regular_coach_never_receives_medical_notes_or_emergency_contacts(self):
        for label, rows in self.coach_views(self.coach_a_user, self.session_a):
            with self.subTest(view=label):
                self.assert_hidden(rows, label)

    def test_substitute_never_receives_them_either(self):
        assign_substitute(self.tomorrow_a, self.coach_b, self.coach_a, self.admin_user, "Coach A away")
        client = self.api(self.coach_b_user)
        roster = client.get(f"/api/sessions/{self.tomorrow_a.pk}/roster/")
        self.assertEqual(roster.status_code, 200)
        self.assert_hidden(roster.json(), "substitute roster")
        self.assert_hidden([client.get(f"/api/students/{self.student_1.pk}/").json()], "substitute detail")

    def test_query_parameters_cannot_bring_the_fields_back(self):
        client = self.api(self.coach_a_user)
        for params in ({"fields": "medical_notes,emergency_contacts"}, {"expand": "emergency_contacts"},
                       {"coach": self.coach_b.pk}, {"include": "medical_notes"}):
            with self.subTest(params=params):
                response = client.get(f"/api/sessions/{self.session_a.pk}/roster/", params)
                self.assertEqual(response.status_code, 200)
                self.assert_hidden(response.json(), str(params))
                self.assert_hidden([client.get(f"/api/students/{self.student_1.pk}/", params).json()], str(params))

    def test_serializer_without_a_request_fails_closed(self):
        from apps.api.serializers import RosterStudentSerializer
        self.assert_hidden([RosterStudentSerializer(self.student_1).data], "no request")

    def test_staff_and_parent_views_are_unchanged(self):
        admin = self.api(self.admin_user)
        roster = {r["id"]: r for r in admin.get(f"/api/sessions/{self.session_a.pk}/roster/").json()}
        self.assertEqual(roster[self.student_1.pk]["medical_notes"], "Test note")
        self.assertEqual(roster[self.student_1.pk]["emergency_contacts"],
                         [{"name": "Parent One", "relationship": "MOTHER", "phone": "0123"}])
        members = {r["id"]: r for r in admin.get(f"/api/classes/{self.class_a.pk}/students/").json()}
        self.assertEqual(members[self.student_1.pk]["medical_notes"], "Test note")
        self.assertEqual(admin.get(f"/api/students/{self.student_1.pk}/").json()["medical_notes"], "Test note")
        own = self.api(self.parent_1_user).get(f"/api/students/{self.student_1.pk}/").json()
        self.assertEqual(own["medical_notes"], "Test note")
        self.assertEqual(own["guardians"], [{"name": "Parent One", "relationship": "MOTHER", "phone": "0123"}])

    def test_coach_b_still_cannot_reach_coach_a_students(self):
        client = self.api(self.coach_b_user)
        for url in (f"/api/sessions/{self.session_a.pk}/roster/", f"/api/classes/{self.class_a.pk}/students/",
                    f"/api/students/{self.student_1.pk}/"):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 404)
