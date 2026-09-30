"""Phase 4: session lifecycle (derived phase, guarded changes, cancellation,
reinstatement, rescheduling, class change, coach reassignment) through the
service, the model, the API and the admin, plus the attendance start boundary."""

import datetime
from contextlib import contextmanager
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.academy.models import SessionCoach, TrainingSession
from apps.academy.services import (
    assign_substitute,
    cancel_session,
    generate_sessions,
    reassign_regular_coach,
    reinstate_session,
    reschedule_session,
)
from apps.accounts.models import Coach
from apps.attendance.models import AttendanceRecord
from apps.attendance.services import SessionState, mark_attendance, session_state, student_summary
from apps.audit.utils import history_for
from apps.finance.tests.admin_helpers import form_data

from .base import AcademyTestCase

SECOND = datetime.timedelta(seconds=1)


@contextmanager
def at(moment):
    with mock.patch("django.utils.timezone.now", return_value=moment):
        yield


class LifecycleTestCase(AcademyTestCase):
    def upcoming(self, days=3, training_class=None, coach=None):
        klass = training_class or self.class_a
        session = klass.sessions.create(date=self.today + datetime.timedelta(days=days),
                                        start_time=datetime.time(17), end_time=datetime.time(19))
        session.coach_slots.create(coach=coach or self.coach_a)
        return session

    def api(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client


class PhaseTests(LifecycleTestCase):
    def test_phase_is_derived_from_the_clock_with_exact_boundaries(self):
        s = self.session_a
        self.assertEqual(s.phase(s.starts_at - SECOND), TrainingSession.Phase.UPCOMING)
        self.assertEqual(s.phase(s.starts_at), TrainingSession.Phase.IN_PROGRESS)
        self.assertEqual(s.phase(s.ends_at - SECOND), TrainingSession.Phase.IN_PROGRESS)
        self.assertEqual(s.phase(s.ends_at), TrainingSession.Phase.COMPLETED)
        s = cancel_session(s, self.admin_user, "Hall closed")
        self.assertEqual(s.phase(s.starts_at - SECOND), TrainingSession.Phase.CANCELLED)

    def test_api_reports_the_phase_and_refuses_the_old_completed_status(self):
        body = self.api(self.admin_user).get(f"/api/sessions/{self.session_a.pk}/").json()
        self.assertEqual(body["phase"], "COMPLETED")
        self.assertEqual(self.api(self.admin_user).get(f"/api/sessions/{self.session_b.pk}/").json()["phase"],
                         "IN_PROGRESS")
        response = self.api(self.admin_user).patch(f"/api/sessions/{self.session_a.pk}/", {"status": "COMPLETED"})
        self.assertEqual(response.status_code, 400)


class CancellationTests(LifecycleTestCase):
    def test_cancel_ends_substitute_blocks_attendance_keeps_history(self):
        mark_attendance(self.session_b, self.student_2, "PRESENT", self.coach_b_user)
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user,
                                 reason="Coach B away")
        with self.assertRaisesMessage(ValidationError, "reason is required"):
            cancel_session(self.session_b, self.admin_user, " ")
        with self.assertRaises(PermissionDenied):
            cancel_session(self.session_b, self.coach_b_user, "no")
        cancel_session(self.session_b, self.admin_user, "Hall flooded")
        self.session_b.refresh_from_db()
        slot.refresh_from_db()
        self.assertEqual(slot.status, SessionCoach.Status.CANCELLED)
        self.assertTrue(AttendanceRecord.objects.filter(session=self.session_b).exists())   # kept
        with self.assertRaises(ValidationError):
            mark_attendance(self.session_b, self.student_2, "LATE", self.admin_user, reason="x")
        self.assertEqual(session_state(self.session_b), SessionState.CANCELLED)
        # Kept, but no longer counted.
        summary = student_summary(self.student_2)
        self.assertEqual((summary["present"], summary["expected"]), (0, 0))
        entry = history_for(self.session_b).filter(action="UPDATE").last()
        self.assertEqual(entry.changes["status"], {"from": "SCHEDULED", "to": "CANCELLED"})
        self.assertIn("Hall flooded", entry.reason)
        with self.assertRaisesMessage(ValidationError, "already cancelled"):
            cancel_session(self.session_b, self.admin_user, "again")

    def test_reinstate_keeps_substitute_cancelled(self):
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user,
                                 reason="x")
        cancel_session(self.session_b, self.admin_user, "Rain")
        reinstate_session(self.session_b, self.admin_user, "Rain stopped")
        slot.refresh_from_db()
        self.assertEqual(slot.status, SessionCoach.Status.CANCELLED)
        self.assertEqual(self.session_b.coach_slots.get(coach=self.coach_b).status, SessionCoach.Status.ASSIGNED)
        with self.assertRaisesMessage(ValidationError, "Only a cancelled session"):
            reinstate_session(self.session_b, self.admin_user, "again")

    def test_api_and_admin_cancel_paths(self):
        response = self.api(self.admin_user).post(f"/api/sessions/{self.session_a.pk}/cancel/", {"reason": ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.api(self.coach_a_user).post(f"/api/sessions/{self.session_a.pk}/cancel/",
                                                          {"reason": "x"}).status_code, 403)
        self.assertEqual(self.api(self.admin_user).post(f"/api/sessions/{self.session_a.pk}/cancel/",
                                                        {"reason": "Coach ill"}).status_code, 200)
        self.client.force_login(self.admin_user)
        url = reverse("admin:academy_trainingsession_reinstate", args=[self.session_a.pk])
        self.assertEqual(self.client.post(url, {"reason": ""}).status_code, 200)   # reason required
        self.assertEqual(self.client.post(url, {"reason": "Found a coach"}).status_code, 302)
        self.assertEqual(TrainingSession.objects.get(pk=self.session_a.pk).status, "SCHEDULED")
        self.client.force_login(self.finance_user)
        cancel = reverse("admin:academy_trainingsession_cancel", args=[self.session_a.pk])
        self.assertEqual(self.client.post(cancel, {"reason": "x"}).status_code, 403)


class RescheduleTests(LifecycleTestCase):
    def test_upcoming_session_without_history_can_be_rescheduled_and_moved_class(self):
        session = self.upcoming()
        moved = reschedule_session(session, self.admin_user, "Hall booked", date=session.date + datetime.timedelta(
            days=1), start_time=datetime.time(18), end_time=datetime.time(20), training_class=self.class_b)
        self.assertEqual((moved.training_class, moved.start_time), (self.class_b, datetime.time(18)))
        # Regular coaches follow the new class.
        self.assertEqual(set(moved.coach_slots.values_list("coach", flat=True)), {self.coach_b.pk})
        self.assertIn("Hall booked", history_for(moved).filter(action="UPDATE").last().reason)

    def test_dangerous_reschedules_are_refused(self):
        cases = {
            "started": (self.session_b, {"start_time": datetime.time(18)}, "has started"),
            "completed": (self.session_a, {"date": self.today + datetime.timedelta(days=1)}, "has started"),
        }
        into_past = self.upcoming()
        cases["into the past"] = (into_past, {"date": self.today - datetime.timedelta(days=1)}, "into the past")
        with_sub = self.upcoming(days=4)
        assign_substitute(with_sub, self.coach_b, replaces=self.coach_a, actor=self.admin_user, reason="x")
        cases["substitute history"] = (with_sub, {"start_time": datetime.time(16)}, "already has")
        cancelled = self.upcoming(days=5)
        cancel_session(cancelled, self.admin_user, "x")
        cases["cancelled"] = (cancelled, {"start_time": datetime.time(16)}, "reinstate it first")
        bad_times = self.upcoming(days=6)
        cases["end before start"] = (bad_times, {"end_time": datetime.time(16)}, "End time must be after")
        for name, (session, change, message) in cases.items():
            with self.subTest(case=name), self.assertRaisesMessage(ValidationError, message):
                reschedule_session(session, self.admin_user, "try", **change)

    def test_session_with_attendance_cannot_be_moved(self):
        mark_attendance(self.session_b, self.student_2, "PRESENT", self.coach_b_user)
        # Even if the clock is wound back to before the start, attendance pins the session.
        with at(self.session_b.starts_at - datetime.timedelta(hours=2)), \
                self.assertRaisesMessage(ValidationError, "already has"):
            reschedule_session(self.session_b, self.admin_user, "x", start_time=datetime.time(19, 30))

    def test_collision_with_another_session_is_refused(self):
        session = self.upcoming()
        other = self.upcoming(days=4)
        with self.assertRaisesMessage(ValidationError, "already has a session"):
            reschedule_session(session, self.admin_user, "x", date=other.date)

    def test_crafted_api_patch_and_admin_post_cannot_move_a_session(self):
        session = self.upcoming()
        response = self.api(self.admin_user).patch(f"/api/sessions/{self.session_a.pk}/",
                                                   {"date": str(self.today + datetime.timedelta(days=9))})
        self.assertEqual(response.status_code, 400)
        response = self.api(self.admin_user).patch(f"/api/sessions/{session.pk}/", {"training_class": self.class_b.pk})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.api(self.admin_user).patch(f"/api/sessions/{session.pk}/", {"venue": "Hall 2"})
                         .status_code, 200)   # harmless fields stay editable
        self.client.force_login(self.admin_user)
        url = reverse("admin:academy_trainingsession_change", args=[self.session_a.pk])
        data = form_data(self.client.get(url))
        data.update({"date": str(self.today + datetime.timedelta(days=9)), "training_class": self.class_b.pk,
                     "start_time": "06:00"})
        self.client.post(url, data)
        fresh = TrainingSession.objects.get(pk=self.session_a.pk)
        self.assertEqual((fresh.date, fresh.training_class, fresh.start_time),
                         (self.today, self.class_a, datetime.time(17)))

    def test_api_and_admin_reschedule_actions(self):
        session = self.upcoming()
        new_date = str(session.date + datetime.timedelta(days=1))
        url = f"/api/sessions/{session.pk}/reschedule/"
        self.assertEqual(self.api(self.admin_user).post(url, {"date": new_date}).status_code, 400)  # no reason
        self.assertEqual(self.api(self.coach_a_user).post(url, {"date": new_date, "reason": "x"}).status_code, 403)
        self.assertEqual(self.api(self.admin_user).post(url, {"date": new_date, "reason": "Hall"}).status_code, 200)
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("admin:academy_trainingsession_reschedule", args=[self.session_a.pk]),
                                    {"training_class": self.class_a.pk, "date": new_date, "start_time": "17:00",
                                     "end_time": "19:00", "reason": "try"})
        self.assertContains(response, "has started")

    def test_generate_sessions_resyncs_nothing_and_model_blocks_direct_edit(self):
        session = self.upcoming()
        session.start_time = datetime.time(16)
        session.save()   # a direct save of a history-free upcoming session is allowed
        self.session_a.date = self.today + datetime.timedelta(days=10)
        with self.assertRaisesMessage(ValidationError, "has started"):
            self.session_a.save()
        self.assertEqual(generate_sessions(self.class_a, self.today, self.today, self.admin_user), [])


class CoachReassignmentTests(LifecycleTestCase):
    def test_reassign_before_start_only(self):
        session = self.upcoming()
        coach_c = Coach.objects.create(full_name="Coach C", phone="013")
        slot = reassign_regular_coach(session, self.coach_a, coach_c, self.admin_user, "Timetable change")
        self.assertEqual((slot.coach, slot.role, slot.status), (coach_c, "REGULAR", "ASSIGNED"))
        self.assertFalse(session.coach_slots.filter(coach=self.coach_a).exists())
        with self.assertRaisesMessage(ValidationError, "has started"):
            reassign_regular_coach(self.session_b, self.coach_b, coach_c, self.admin_user, "x")
        with self.assertRaises(PermissionDenied):
            reassign_regular_coach(session, coach_c, self.coach_a, self.coach_a_user, "x")

    def test_replaced_coach_cannot_be_reassigned_and_history_is_protected(self):
        session = self.upcoming()
        assign_substitute(session, self.coach_b, replaces=self.coach_a, actor=self.admin_user, reason="x")
        with self.assertRaisesMessage(ValidationError, "not a regular coach currently assigned"):
            reassign_regular_coach(session, self.coach_a, self.coach_b, self.admin_user, "x")
        original = self.session_a.coach_slots.get(coach=self.coach_a)
        with self.assertRaises(PermissionDenied):
            original.delete()     # started session: its coaches are history
        original.coach = self.coach_b
        with self.assertRaisesMessage(ValidationError, "cannot be moved"):
            original.save()

    def test_api_reassign(self):
        session = self.upcoming()
        url = f"/api/sessions/{session.pk}/reassign-coach/"
        data = {"from_coach": self.coach_a.pk, "to_coach": self.coach_b.pk, "reason": "Timetable"}
        self.assertEqual(self.api(self.finance_user).post(url, data).status_code, 403)
        self.assertEqual(self.api(self.admin_user).post(url, {**data, "reason": ""}).status_code, 400)
        self.assertEqual(self.api(self.admin_user).post(url, data).status_code, 200)


class FutureAttendanceTests(LifecycleTestCase):
    def test_exact_attendance_boundaries(self):
        session = self.upcoming(days=1)
        start, end = session.starts_at, session.ends_at
        for moment in (start - datetime.timedelta(days=1), start - SECOND):
            with self.subTest(moment=moment), at(moment):
                for user in (self.coach_a_user, self.admin_user, self.super_user):
                    with self.assertRaisesMessage(ValidationError, "from the session start"):
                        mark_attendance(session, self.student_1, "PRESENT", user, reason="x")
                self.assertEqual(session_state(session), SessionState.NOT_STARTED)
        with at(start):
            mark_attendance(session, self.student_1, "PRESENT", self.coach_a_user)          # at the start
        with at(start + datetime.timedelta(minutes=30)):
            mark_attendance(session, self.student_3, "LATE", self.coach_a_user)             # during
        with at(end + datetime.timedelta(hours=47, minutes=59)):
            mark_attendance(session, self.student_3, "PRESENT", self.coach_a_user, reason="fix")  # after, in window
        with at(end + datetime.timedelta(hours=48)), self.assertRaises(PermissionDenied):
            mark_attendance(session, self.student_3, "LATE", self.coach_a_user, reason="x")      # 48 h: locked
        with at(end + datetime.timedelta(hours=48)):
            mark_attendance(session, self.student_3, "LATE", self.admin_user, reason="Correction")
            self.assertEqual(session_state(session), SessionState.LOCKED)

    def test_api_refuses_future_attendance(self):
        session = self.upcoming(days=1)
        response = self.api(self.coach_a_user).post(f"/api/sessions/{session.pk}/attendance/",
                                                    {"records": [{"student": self.student_1.pk, "status": "PRESENT"}]},
                                                    format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("session start", str(response.json()))
        self.assertEqual(self.api(self.coach_a_user).get(f"/api/sessions/{session.pk}/attendance/").json()["state"],
                         "NOT_STARTED")
        self.assertFalse(AttendanceRecord.objects.exists())
