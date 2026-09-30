"""Phase 6E: the API as the Staff Core Operations Portal uses it.

Academy staff (ADMIN, SUPER_ADMIN) run the day; FINANCE_ADMIN keeps only what
its own capabilities allow (the student directory), never academy management.
The clock is 20:00 today: session A (17:00-19:00) has ended, session B
(19:00-21:00) is in progress.
"""

import datetime
from unittest import mock

from rest_framework.test import APIClient

from apps.academy.models import (
    ClassSchedule, Enrollment, Program, SessionCoach, StudentAccount, TrainingClass, TrainingSession,
)
from apps.academy.services import assign_substitute
from apps.academy.tests.base import AcademyTestCase, make_user
from apps.accounts.capabilities import Role
from apps.accounts.models import Coach
from apps.attendance.models import AttendanceRecord
from apps.attendance.services import mark_attendance
from apps.audit.utils import history_for

SENSITIVE_LIST_KEYS = {"ic_number", "address", "phone", "email", "medical_notes", "guardians", "date_of_birth",
                       "emergency_contacts"}
FINANCE_KEYS = {"invoice", "invoices", "balance", "balance_due", "fee", "payments", "bank_name", "bank_account_no"}


def keys_in(data):
    if isinstance(data, dict):
        return set(data) | {k for v in data.values() for k in keys_in(v)}
    if isinstance(data, list):
        return {k for v in data for k in keys_in(v)}
    return set()


class StaffPortalTestCase(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.student_1.ic_number, cls.student_1.medical_notes, cls.student_1.chinese_name = "140101-10-1111", "Asthma", "阿里"
        cls.student_1.address = "1 Jalan Rahsia"
        cls.student_1.save()
        cls.student_login = make_user("raj", Role.STUDENT)
        StudentAccount.objects.create(user=cls.student_login, student=cls.student_3)

        def session(training_class, days, hour=17, coach=None):
            s = TrainingSession.objects.create(training_class=training_class,
                                               date=cls.today + datetime.timedelta(days=days),
                                               start_time=datetime.time(hour), end_time=datetime.time(hour + 2),
                                               venue="Hall A", notes="Staff note")
            if coach:
                s.coach_slots.create(coach=coach)
            return s

        cls.past_a = session(cls.class_a, -5, coach=cls.coach_a)           # locked, unmarked
        cls.future_a = session(cls.class_a, 2, coach=cls.coach_a)
        cls.uncoached = session(cls.class_b, 3)                            # no coach at all
        cls.program = Program.objects.get(code="taolu")
        cls.class_c = TrainingClass.objects.create(code="c", name="Class C", category="ADDITIONAL", program=cls.program)
        ClassSchedule.objects.create(training_class=cls.class_a, weekday=0, start_time=datetime.time(17),
                                     end_time=datetime.time(19), venue="Hall A",
                                     effective_from=cls.today - datetime.timedelta(days=30))
        cls.inactive_coach = Coach.objects.create(full_name="Former Coach", phone="019", is_active=False)

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    @staticmethod
    def rows(response):
        body = response.json()
        return body.get("results", body) if isinstance(body, dict) else body

    def ids(self, response):
        return {r["id"] for r in self.rows(response)}


class DashboardTests(StaffPortalTestCase):
    def test_admin_sees_operations_from_real_state(self):
        body = self.api(self.admin_user).get("/api/staff/dashboard/").json()
        self.assertEqual(body["date"], self.today.isoformat())
        self.assertEqual(body["counts"], {"sessions_today": 2, "in_progress": 1, "cancelled_today": 0,
                                          "substitute_covered_today": 0, "active_students": 3, "active_classes": 3})
        alerts = {a["code"]: a for a in body["alerts"]}
        self.assertEqual({(r["id"], r["unmarked"]) for r in alerts["attendance_open"]["sessions"]},
                         {(self.session_a.pk, 2), (self.session_b.pk, 1)})
        self.assertEqual([(r["id"], r["unmarked"]) for r in alerts["attendance_locked"]["sessions"]],
                         [(self.past_a.pk, 2)])
        self.assertEqual([r["id"] for r in alerts["session_without_coach"]["sessions"]], [self.uncoached.pk])
        self.assertEqual(alerts["class_without_coach"]["classes"], [{"id": self.class_c.pk, "name": "Class C"}])
        self.assertFalse(keys_in(body) & (FINANCE_KEYS | SENSITIVE_LIST_KEYS))

    def test_alerts_follow_the_data(self):
        mark_attendance(self.session_a, self.student_1, "PRESENT", self.admin_user)
        mark_attendance(self.session_a, self.student_3, "ABSENT", self.admin_user)
        self.uncoached.coach_slots.create(coach=self.coach_b)
        cancelled = TrainingSession.objects.create(training_class=self.class_a, date=self.today,
                                                   start_time=datetime.time(8), end_time=datetime.time(9),
                                                   status=TrainingSession.Status.CANCELLED)
        assign_substitute(self.session_b, self.coach_a, self.coach_b, self.admin_user, "Coach B away")
        body = self.api(self.super_user).get("/api/staff/dashboard/").json()
        alerts = {a["code"]: a for a in body["alerts"]}
        self.assertNotIn(self.session_a.pk, {r["id"] for r in alerts["attendance_open"]["sessions"]})
        self.assertEqual(alerts["session_without_coach"]["count"], 0)
        self.assertEqual((body["counts"]["cancelled_today"], body["counts"]["substitute_covered_today"]), (1, 1))
        self.assertNotIn(cancelled.pk, {r["id"] for a in body["alerts"] for r in a.get("sessions", [])})

    def test_only_staff_who_see_every_session(self):
        for user in (self.finance_user, self.coach_a_user, self.parent_1_user, self.student_login):
            with self.subTest(user=user.username):
                self.assertEqual(self.api(user).get("/api/staff/dashboard/").status_code, 403)
        self.assertEqual(APIClient().get("/api/staff/dashboard/").status_code, 401)
        self.assertEqual(self.api(self.admin_user).post("/api/staff/dashboard/", {}).status_code, 403)


class StudentTests(StaffPortalTestCase):
    def test_list_rows_are_minimal_and_detail_is_full_for_admin(self):
        rows = self.rows(self.api(self.admin_user).get("/api/students/"))
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(rows[0]), {"id", "student_no", "full_name", "chinese_name", "gender", "age", "status",
                                        "join_date", "family", "family_name", "current_classes"})
        self.assertFalse(keys_in(rows) & SENSITIVE_LIST_KEYS)
        ali = next(r for r in rows if r["id"] == self.student_1.pk)
        self.assertEqual(ali["current_classes"], [{"id": self.class_a.pk, "name": "Class A"}])
        detail = self.api(self.admin_user).get(f"/api/students/{self.student_1.pk}/").json()
        self.assertEqual((detail["ic_number"], detail["medical_notes"], detail["address"]),
                         ("140101-10-1111", "Asthma", "1 Jalan Rahsia"))
        self.assertEqual(detail["guardians"][0]["parent"]["full_name"], "Parent One")
        self.assertFalse(keys_in(detail) & FINANCE_KEYS)

    def test_search_and_filters(self):
        client = self.api(self.admin_user)
        self.assertEqual(self.ids(client.get("/api/students/", {"search": "ali"})), {self.student_1.pk})
        self.assertEqual(self.ids(client.get("/api/students/", {"search": "阿里"})), {self.student_1.pk})
        self.assertEqual(self.ids(client.get("/api/students/", {"search": "S3"})), {self.student_3.pk})
        self.assertEqual(self.ids(client.get("/api/students/", {"class": self.class_a.pk})),
                         {self.student_1.pk, self.student_3.pk})
        self.assertEqual(self.ids(client.get("/api/students/", {"family": self.student_2.family_id})),
                         {self.student_2.pk})
        self.assertEqual(self.ids(client.get("/api/students/", {"status": "WITHDRAWN"})), set())
        self.assertEqual(client.get("/api/students/", {"class": "x"}).status_code, 400)
        self.assertEqual(client.get("/api/students/", {"family": "x"}).status_code, 400)

    def test_finance_admin_gets_the_directory_only(self):
        client = self.api(self.finance_user)
        rows = self.rows(client.get("/api/students/"))
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertFalse(set(row) & {"ic_number", "medical_notes", "address", "date_of_birth", "current_classes"})
        detail = client.get(f"/api/students/{self.student_1.pk}/").json()
        self.assertFalse(set(detail) & {"ic_number", "medical_notes", "address", "date_of_birth"})
        self.assertEqual(client.get(f"/api/students/{self.student_1.pk}/attendance-summary/").status_code, 403)
        self.assertEqual(client.get(f"/api/students/{self.student_1.pk}/history/").status_code, 403)
        # Filters never widen: the directory stays the directory.
        self.assertEqual(self.ids(client.get("/api/students/", {"class": self.class_a.pk})),
                         {self.student_1.pk, self.student_3.pk})

    def test_editing_uses_the_existing_rules_and_is_audited(self):
        client = self.api(self.admin_user)
        response = client.patch(f"/api/students/{self.student_1.pk}/", {"full_name": "Ali Bin Abu"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(history_for(self.student_1).first().actor, self.admin_user)
        status = client.post(f"/api/students/{self.student_1.pk}/change-status/",
                             {"status": "ON_LEAVE", "reason": "Exams"}, format="json")
        self.assertEqual(status.json()["status"], "ON_LEAVE")
        self.assertEqual(self.student_1.status_history.first().reason, "Exams")
        self.assertEqual(client.post(f"/api/students/{self.student_1.pk}/change-status/", {"status": "NOPE"}).status_code,
                         400)
        enrol = client.post("/api/enrollments/", {"student": self.student_1.pk, "training_class": self.class_c.pk},
                            format="json")
        self.assertEqual(enrol.status_code, 201, enrol.content)
        again = client.post("/api/enrollments/", {"student": self.student_1.pk, "training_class": self.class_c.pk},
                            format="json")
        self.assertEqual(again.status_code, 400)
        end = client.post(f"/api/enrollments/{enrol.json()['id']}/end/", {"reason": "Moved"}, format="json")
        self.assertEqual(end.status_code, 200)
        self.assertIsNotNone(Enrollment.objects.get(pk=enrol.json()["id"]).end_date)

    def test_non_managers_cannot_edit(self):
        for user in (self.finance_user, self.coach_a_user, self.parent_1_user, self.student_login):
            client = self.api(user)
            with self.subTest(user=user.username):
                self.assertIn(client.patch(f"/api/students/{self.student_1.pk}/", {"full_name": "X"},
                                           format="json").status_code, (403, 404))
                self.assertEqual(client.post(f"/api/students/{self.student_1.pk}/change-status/",
                                             {"status": "WITHDRAWN"}).status_code, 403)
                self.assertEqual(client.post("/api/enrollments/", {"student": self.student_1.pk,
                                                                   "training_class": self.class_c.pk}).status_code, 403)
        self.student_1.refresh_from_db()
        self.assertEqual((self.student_1.full_name, self.student_1.status), ("Ali", "ACTIVE"))


class ClassAndTimetableTests(StaffPortalTestCase):
    def test_list_detail_counts_roster_and_timetable(self):
        client = self.api(self.admin_user)
        classes = {c["id"]: c for c in self.rows(client.get("/api/classes/"))}
        self.assertEqual((classes[self.class_a.pk]["active_students"], classes[self.class_c.pk]["active_students"]), (2, 0))
        self.assertEqual(classes[self.class_a.pk]["current_coaches"], [{"id": self.coach_a.pk, "full_name": "Coach A"}])
        self.assertEqual(classes[self.class_a.pk]["schedules"][0]["weekday_name"], "Monday")
        detail = client.get(f"/api/classes/{self.class_a.pk}/").json()
        self.assertEqual(detail["active_students"], 2)
        roster = client.get(f"/api/classes/{self.class_a.pk}/students/").json()
        self.assertEqual({r["full_name"] for r in roster}, {"Ali", "Raj"})
        self.assertEqual(self.ids(client.get("/api/classes/", {"active": "1"})),
                         {self.class_a.pk, self.class_b.pk, self.class_c.pk})
        # Others do not get the staff count.
        coach_view = self.rows(self.api(self.coach_a_user).get("/api/classes/"))
        self.assertNotIn("active_students", coach_view[0])

    def test_create_edit_and_deactivate_are_for_class_managers_only(self):
        client = self.api(self.admin_user)
        self.assertEqual([p["name"] for p in client.get("/api/programs/").json()], ["Wushu Taolu"])
        created = client.post("/api/classes/", {"code": "d", "name": "Class D", "category": "ELITE",
                                                "program": self.program.pk, "venue": "Hall D"}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        new = TrainingClass.objects.get(code="d")
        self.assertEqual(history_for(new).first().actor, self.admin_user)
        off = client.patch(f"/api/classes/{new.pk}/", {"is_active": False}, format="json")
        self.assertFalse(off.json()["is_active"])
        self.assertEqual(client.post("/api/classes/", {"code": "d", "name": "Dup", "category": "ELITE",
                                                       "program": self.program.pk}, format="json").status_code, 400)
        for user in (self.finance_user, self.coach_a_user, self.parent_1_user, self.student_login):
            other = self.api(user)
            with self.subTest(user=user.username):
                self.assertEqual(other.post("/api/classes/", {"code": "e", "name": "E", "category": "ELITE",
                                                              "program": self.program.pk}).status_code, 403)
                self.assertEqual(other.patch(f"/api/classes/{self.class_a.pk}/", {"name": "X"}).status_code, 403)
                self.assertEqual(other.get("/api/programs/").status_code, 403)
        self.assertEqual(self.api(self.finance_user).get("/api/classes/").status_code, 403)
        self.assertEqual(self.api(self.finance_user).get(f"/api/classes/{self.class_a.pk}/students/").status_code, 403)


class SessionTests(StaffPortalTestCase):
    def test_list_filters_including_coach(self):
        client = self.api(self.admin_user)
        self.assertEqual(self.ids(client.get("/api/sessions/", {"date": self.today.isoformat()})),
                         {self.session_a.pk, self.session_b.pk})
        self.assertEqual(self.ids(client.get("/api/sessions/", {"class": self.class_b.pk})),
                         {self.session_b.pk, self.uncoached.pk})
        self.assertEqual(self.ids(client.get("/api/sessions/", {"coach": self.coach_a.pk})),
                         {self.session_a.pk, self.past_a.pk, self.future_a.pk})
        self.assertEqual(self.ids(client.get("/api/sessions/coaching/", {"coach": self.coach_b.pk})),
                         {self.session_b.pk})
        self.assertEqual(client.get("/api/sessions/", {"coach": "x"}).status_code, 400)
        detail = client.get(f"/api/sessions/{self.past_a.pk}/").json()
        self.assertEqual((detail["notes"], detail["coaches"][0]["coach_name"]), ("Staff note", "Coach A"))
        # A coach cannot use the coach filter to see another coach's sessions (it is ignored for coaches).
        own = self.ids(self.api(self.coach_a_user).get("/api/sessions/", {"coach": self.coach_b.pk}))
        self.assertEqual(own, self.ids(self.api(self.coach_a_user).get("/api/sessions/")))
        self.assertFalse(own & {self.session_b.pk, self.uncoached.pk})

    def test_reschedule_cancel_reinstate_follow_the_lifecycle_rules(self):
        client = self.api(self.admin_user)
        url = f"/api/sessions/{self.future_a.pk}/"
        new_date = (self.today + datetime.timedelta(days=4)).isoformat()
        self.assertEqual(client.post(url + "reschedule/", {"date": new_date}, format="json").status_code, 400)
        moved = client.post(url + "reschedule/", {"date": new_date, "reason": "Hall booked"}, format="json")
        self.assertEqual((moved.status_code, moved.json()["date"]), (200, new_date))
        started = client.post(f"/api/sessions/{self.session_a.pk}/reschedule/", {"date": new_date, "reason": "x"},
                              format="json")
        self.assertEqual(started.status_code, 400)
        other_date = (self.today + datetime.timedelta(days=6)).isoformat()
        self.assertEqual(client.patch(url, {"date": other_date}, format="json").status_code, 400)  # no direct edits
        self.assertEqual(client.post(url + "cancel/", {}, format="json").status_code, 400)       # reason required
        cancelled = client.post(url + "cancel/", {"reason": "Flood"}, format="json")
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        self.assertEqual(client.post(url + "cancel/", {"reason": "Again"}, format="json").status_code, 400)
        reinstated = client.post(url + "reinstate/", {"reason": "Hall reopened"}, format="json")
        self.assertEqual(reinstated.json()["status"], "SCHEDULED")
        reasons = [e.reason for e in history_for(self.future_a)]
        self.assertTrue(any("Flood" in r for r in reasons) and any("Hall reopened" in r for r in reasons))

    def test_coach_reassignment_validates_the_coach(self):
        client = self.api(self.admin_user)
        url = f"/api/sessions/{self.future_a.pk}/reassign-coach/"
        self.assertEqual(client.post(url, {"from_coach": self.coach_a.pk, "to_coach": 999999, "reason": "x"},
                                     format="json").status_code, 404)
        self.assertEqual(client.post(url, {"from_coach": self.coach_a.pk, "to_coach": "abc", "reason": "x"},
                                     format="json").status_code, 400)
        self.assertEqual(client.post(url, {"from_coach": self.coach_a.pk, "to_coach": self.inactive_coach.pk,
                                           "reason": "x"}, format="json").status_code, 400)
        self.assertEqual(client.post(url, {"from_coach": self.coach_a.pk, "to_coach": self.coach_b.pk},
                                     format="json").status_code, 400)
        done = client.post(url, {"from_coach": self.coach_a.pk, "to_coach": self.coach_b.pk, "reason": "Timetable"},
                           format="json")
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(done.json()["coach_name"], "Coach B")
        started = client.post(f"/api/sessions/{self.session_a.pk}/reassign-coach/",
                              {"from_coach": self.coach_a.pk, "to_coach": self.coach_b.pk, "reason": "x"}, format="json")
        self.assertEqual(started.status_code, 400)

    def test_substitutes_one_active_revoke_and_cancellation(self):
        client = self.api(self.admin_user)
        url = f"/api/sessions/{self.future_a.pk}/"
        assigned = client.post(url + "assign-substitute/", {"substitute": self.coach_b.pk, "replaces": self.coach_a.pk,
                                                            "reason": "Coach A at a course"}, format="json")
        self.assertEqual((assigned.status_code, assigned.json()["status"]), (201, "ASSIGNED"))
        duplicate = client.post(url + "assign-substitute/", {"substitute": self.coach_b.pk, "replaces": self.coach_a.pk,
                                                             "reason": "Again"}, format="json")
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(SessionCoach.objects.filter(session=self.future_a, role="SUBSTITUTE", status="ASSIGNED").count(), 1)
        revoked = client.post(url + "revoke-substitute/", {"substitute": self.coach_b.pk, "reason": "Back"}, format="json")
        self.assertEqual(revoked.json()["status"], "REVOKED")
        client.post(url + "assign-substitute/", {"substitute": self.coach_b.pk, "replaces": self.coach_a.pk,
                                                 "reason": "Away again"}, format="json")
        client.post(url + "cancel/", {"reason": "Flood"}, format="json")
        active = SessionCoach.objects.filter(session=self.future_a, role="SUBSTITUTE").order_by("-id").first()
        self.assertEqual(active.status, "CANCELLED")

    def test_session_management_is_for_session_managers_only(self):
        for user in (self.finance_user, self.coach_a_user, self.parent_1_user, self.student_login):
            client = self.api(user)
            with self.subTest(user=user.username):
                for action, body in (("cancel/", {"reason": "x"}), ("reinstate/", {"reason": "x"}),
                                     ("reschedule/", {"date": "2030-01-01", "reason": "x"}),
                                     ("assign-substitute/", {"substitute": self.coach_b.pk, "reason": "x"}),
                                     ("reassign-coach/", {"from_coach": self.coach_a.pk, "to_coach": self.coach_b.pk,
                                                          "reason": "x"})):
                    self.assertEqual(client.post(f"/api/sessions/{self.future_a.pk}/{action}", body,
                                                 format="json").status_code, 403, action)
        self.assertEqual(self.api(self.finance_user).get("/api/sessions/").status_code, 403)
        self.assertEqual(self.api(self.finance_user).get(f"/api/sessions/{self.session_a.pk}/").status_code, 403)
        self.assertEqual(TrainingSession.objects.get(pk=self.future_a.pk).status, "SCHEDULED")


class AttendanceMonitoringTests(StaffPortalTestCase):
    def test_monitoring_uses_the_backend_counts(self):
        mark_attendance(self.session_a, self.student_1, "PRESENT", self.coach_a_user)
        rows = {r["id"]: r for r in self.rows(self.api(self.admin_user).get(
            "/api/sessions/coaching/", {"start": (self.today - datetime.timedelta(days=7)).isoformat(),
                                        "end": self.today.isoformat()}))}
        self.assertEqual({k: rows[self.session_a.pk]["attendance"][k] for k in ("state", "expected", "marked", "unmarked",
                                                                               "percentage")},
                         {"state": "OPEN", "expected": 2, "marked": 1, "unmarked": 1, "percentage": "100.00"})
        self.assertEqual(rows[self.past_a.pk]["attendance"]["state"], "LOCKED")
        sheet = self.api(self.admin_user).get(f"/api/sessions/{self.session_a.pk}/attendance/").json()
        self.assertEqual({r["student"]: r["status"] for r in sheet["sheet"]},
                         {self.student_1.pk: "PRESENT", self.student_3.pk: "UNMARKED"})

    def test_corrections_after_the_window_need_a_reason_and_are_audited(self):
        url = f"/api/sessions/{self.past_a.pk}/attendance/"
        entry = {"records": [{"student": self.student_1.pk, "status": "PRESENT"}]}
        self.assertEqual(self.api(self.coach_a_user).post(url, entry, format="json").status_code, 403)
        self.assertEqual(self.api(self.admin_user).post(url, entry, format="json").status_code, 400)
        done = self.api(self.admin_user).post(url, {**entry, "reason": "Register found"}, format="json")
        self.assertEqual(done.status_code, 200, done.content)
        record = AttendanceRecord.objects.get(session=self.past_a, student=self.student_1)
        entry_log = history_for(record).first()
        self.assertEqual(entry_log.actor, self.admin_user)
        self.assertIn("Administrative correction", entry_log.reason)
        self.assertIn("Register found", entry_log.reason)
        history = self.api(self.admin_user).get(f"/api/attendance/{record.pk}/history/")
        self.assertEqual(history.status_code, 200)
        for user in (self.finance_user, self.parent_1_user, self.student_login):
            self.assertEqual(self.api(user).get(f"/api/attendance/{record.pk}/history/").status_code, 403)

    def test_nothing_before_the_start_or_for_a_cancelled_session(self):
        future = self.api(self.admin_user).post(f"/api/sessions/{self.future_a.pk}/attendance/",
                                               {"records": [{"student": self.student_1.pk, "status": "PRESENT"}],
                                                "reason": "x"}, format="json")
        self.assertEqual(future.status_code, 400)
        with mock.patch("django.utils.timezone.now", return_value=self.clock):
            self.api(self.admin_user).post(f"/api/sessions/{self.session_b.pk}/cancel/", {"reason": "Flood"}, format="json")
        cancelled = self.api(self.admin_user).post(f"/api/sessions/{self.session_b.pk}/attendance/",
                                                  {"records": [{"student": self.student_2.pk, "status": "PRESENT"}]},
                                                  format="json")
        self.assertEqual(cancelled.status_code, 400)

    def test_finance_and_non_staff_cannot_monitor(self):
        for user in (self.finance_user, self.parent_1_user, self.student_login):
            client = self.api(user)
            with self.subTest(user=user.username):
                self.assertEqual(client.get("/api/sessions/coaching/").status_code, 403)
                self.assertEqual(client.get(f"/api/sessions/{self.session_a.pk}/attendance/").status_code, 403)
        # A coach sees only their own sessions' sheets.
        self.assertEqual(self.api(self.coach_b_user).get(f"/api/sessions/{self.session_a.pk}/attendance/").status_code, 404)


class FinanceIsolationTests(StaffPortalTestCase):
    def test_finance_admin_gains_no_academy_management(self):
        client = self.api(self.finance_user)
        for path in ("/api/staff/dashboard/", "/api/classes/", "/api/programs/", "/api/sessions/",
                     "/api/sessions/coaching/", "/api/attendance/", "/api/enrollments/"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 403)

    def test_staff_operations_responses_carry_no_finance_or_bank_data(self):
        client = self.api(self.admin_user)
        for path in ("/api/staff/dashboard/", "/api/students/", f"/api/students/{self.student_1.pk}/", "/api/classes/",
                     f"/api/classes/{self.class_a.pk}/", "/api/sessions/", f"/api/sessions/{self.session_a.pk}/",
                     "/api/sessions/coaching/", f"/api/sessions/{self.session_a.pk}/attendance/"):
            with self.subTest(path=path):
                self.assertFalse(keys_in(client.get(path).json()) & FINANCE_KEYS)
        coaches = self.rows(client.get("/api/coaches/"))
        self.assertFalse({"bank_name", "bank_account_no", "epf_no", "socso_no"} & keys_in(coaches))
