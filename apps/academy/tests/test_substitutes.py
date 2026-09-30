"""Phase 3: substitute coach authorization lifecycle (service, model, database)."""

import datetime
from contextlib import contextmanager
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.academy import access
from apps.academy.models import SessionCoach, TrainingSession
from apps.academy.services import assign_substitute, revoke_substitute
from apps.accounts.capabilities import Role
from apps.accounts.models import Coach
from apps.audit.utils import history_for

from .base import AcademyTestCase, make_user


@contextmanager
def frozen(moment):
    with mock.patch("django.utils.timezone.now", return_value=moment):
        yield


class SubstituteTestCase(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.coach_c_user = make_user("coach_c", Role.COACH)
        cls.coach_c = Coach.objects.create(user=cls.coach_c_user, full_name="Coach C", phone="013")

    def authorize(self, session=None, substitute=None, replaces="default", reason="Coach B at a tournament"):
        session = session or self.session_b
        replaces = self.coach_b if replaces == "default" else replaces
        return assign_substitute(session, substitute or self.coach_a, replaces=replaces, actor=self.admin_user,
                                 reason=reason)

    def slot(self, pk):
        return SessionCoach.objects.get(pk=pk)


class AuthorizationRecordTests(SubstituteTestCase):
    def test_authorization_records_who_what_when_why(self):
        before = timezone.now()
        slot = self.authorize()
        self.assertEqual((slot.role, slot.status), (SessionCoach.Role.SUBSTITUTE, SessionCoach.Status.ASSIGNED))
        self.assertEqual(slot.session, self.session_b)
        self.assertEqual(slot.coach, self.coach_a)            # substitute
        self.assertEqual(slot.replaces, self.coach_b)         # original coach
        self.assertEqual(slot.assigned_by, self.admin_user)   # who authorized
        self.assertEqual(slot.reason, "Coach B at a tournament")
        self.assertGreaterEqual(slot.authorized_at, before)   # when authorized
        self.assertEqual(slot.access_starts_at, self.session_b.starts_at - datetime.timedelta(hours=24))
        self.assertEqual(slot.access_ends_at, self.session_b.ends_at + datetime.timedelta(hours=24))
        self.assertIsNone(slot.revoked_at)
        self.assertEqual(self.session_b.coach_slots.get(coach=self.coach_b).status, SessionCoach.Status.REPLACED)

    def test_only_substitute_assigners_may_authorize(self):
        for user in (self.coach_a_user, self.coach_b_user, self.parent_1_user, self.finance_user):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                assign_substitute(self.session_b, self.coach_c, replaces=self.coach_b, actor=user, reason="x")
        self.assertFalse(SessionCoach.objects.substitutes().exists())

    def test_authorization_is_audited(self):
        slot = self.authorize()
        entry = history_for(slot).get(action="CREATE")
        self.assertEqual((entry.actor, entry.reason, entry.category), (self.admin_user, "Coach B at a tournament",
                                                                       "ACCESS"))
        self.assertEqual(entry.changes["coach_id"]["to"], self.coach_a.pk)


class ConflictTests(SubstituteTestCase):
    def test_second_substitute_for_the_same_session_is_refused(self):
        self.authorize()
        with self.assertRaisesMessage(ValidationError, "already the authorized substitute"):
            self.authorize(substitute=self.coach_c, replaces=None)
        self.assertEqual(SessionCoach.objects.active_substitutes().filter(session=self.session_b).count(), 1)

    def test_same_substitute_twice_is_refused(self):
        self.authorize()
        with self.assertRaisesMessage(ValidationError, "already the authorized substitute"):
            self.authorize(replaces=None)

    def test_regular_coach_cannot_become_substitute_for_own_session(self):
        with self.assertRaisesMessage(ValidationError, "already coaches this session"):
            self.authorize(substitute=self.coach_b, replaces=None)
        self.assertEqual(self.session_b.coach_slots.get(coach=self.coach_b).role, SessionCoach.Role.REGULAR)

    def test_replaced_coach_must_be_a_current_regular_coach(self):
        with self.assertRaisesMessage(ValidationError, "not a regular coach currently assigned"):
            self.authorize(replaces=self.coach_c)
        with self.assertRaisesMessage(ValidationError, "cannot substitute for themselves"):
            self.authorize(substitute=self.coach_b, replaces=self.coach_b)

    def test_cancelled_session_refuses_new_substitute(self):
        self.session_b.status = TrainingSession.Status.CANCELLED
        self.session_b.save()
        with self.assertRaisesMessage(ValidationError, "cancelled session"):
            self.authorize()

    def test_completed_session_refuses_new_substitute(self):
        # Phase 4: "completed" is derived from the clock (no stored COMPLETED status).
        after_end = self.session_b.ends_at
        with frozen(after_end), self.assertRaisesMessage(ValidationError, "has not ended"):
            self.authorize()

    def test_session_whose_access_window_has_ended_refuses_new_substitute(self):
        later = self.session_b.ends_at + datetime.timedelta(hours=25)
        with frozen(later), self.assertRaisesMessage(ValidationError, "has not ended"):
            self.authorize()

    def test_inactive_coach_cannot_be_authorized(self):
        self.coach_c.is_active = False
        self.coach_c.save()
        with self.assertRaisesMessage(ValidationError, "not an active coach"):
            self.authorize(substitute=self.coach_c)

    def test_database_refuses_two_active_substitutes(self):
        self.authorize()
        with self.assertRaises(IntegrityError), transaction.atomic():
            SessionCoach.objects.create(session=self.session_b, coach=self.coach_c, role=SessionCoach.Role.SUBSTITUTE,
                                        authorized_at=timezone.now())

    def test_database_refuses_revoked_row_without_timestamp(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SessionCoach.objects.create(session=self.session_b, coach=self.coach_c, role=SessionCoach.Role.SUBSTITUTE,
                                        status=SessionCoach.Status.REVOKED)

    def test_database_refuses_substitute_statuses_on_regular_slots(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SessionCoach.objects.create(session=self.session_a, coach=self.coach_c, status=SessionCoach.Status.REVOKED,
                                        revoked_at=timezone.now())


class RevocationTests(SubstituteTestCase):
    def test_revocation_stops_access_and_keeps_history(self):
        slot = self.authorize()
        self.assertTrue(access.can_take_attendance(self.coach_a_user, self.session_b))
        revoked = revoke_substitute(slot, self.admin_user, "Coach B recovered")
        # Access stops immediately, everywhere.
        self.assertFalse(access.can_take_attendance(self.coach_a_user, self.session_b))
        self.assertNotIn(self.session_b, access.sessions_for(self.coach_a_user))
        self.assertNotIn(self.session_b, access.roster_sessions_for(self.coach_a_user))
        self.assertNotIn(self.student_2, access.students_for(self.coach_a_user))
        self.assertFalse(access.open_substitute_slots(self.coach_a).exists())
        # The authorization is kept, with who/when/why, and its original access period.
        self.assertEqual(revoked.status, SessionCoach.Status.REVOKED)
        self.assertEqual((revoked.revoked_by, revoked.revocation_reason), (self.admin_user, "Coach B recovered"))
        self.assertIsNotNone(revoked.revoked_at)
        self.assertEqual(revoked.access_ends_at, slot.access_ends_at)
        self.assertEqual(revoked.coach, self.coach_a)
        self.assertEqual(revoked.session, self.session_b)
        entry = history_for(revoked).get(action="UPDATE")
        self.assertEqual(entry.changes["status"], {"from": "ASSIGNED", "to": "REVOKED"})
        self.assertEqual((entry.actor, entry.reason), (self.admin_user, "Coach B recovered"))
        # The original coach is back on the session.
        self.assertEqual(self.session_b.coach_slots.get(coach=self.coach_b).status, SessionCoach.Status.ASSIGNED)

    def test_revocation_needs_a_reason_and_the_capability(self):
        slot = self.authorize()
        with self.assertRaisesMessage(ValidationError, "reason is required"):
            revoke_substitute(slot, self.admin_user, "  ")
        for user in (self.coach_a_user, self.coach_b_user, self.finance_user, self.parent_1_user):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                revoke_substitute(slot, user, "x")
        self.assertEqual(self.slot(slot.pk).status, SessionCoach.Status.ASSIGNED)

    def test_already_revoked_cannot_be_revoked_again(self):
        slot = self.authorize()
        revoke_substitute(slot, self.admin_user, "first")
        with self.assertRaisesMessage(ValidationError, "already revoked"):
            revoke_substitute(slot, self.admin_user, "second")
        self.assertEqual(self.slot(slot.pk).revocation_reason, "first")

    def test_regular_slot_cannot_be_revoked(self):
        with self.assertRaisesMessage(ValidationError, "Only substitute"):
            revoke_substitute(self.session_b.coach_slots.get(coach=self.coach_b), self.admin_user, "x")

    def test_revoked_substitute_cannot_be_reactivated_by_editing(self):
        slot = self.authorize()
        revoke_substitute(slot, self.admin_user, "done")
        slot = self.slot(slot.pk)
        slot.status = SessionCoach.Status.ASSIGNED
        with self.assertRaisesMessage(ValidationError, "final"):
            slot.save()
        self.assertEqual(self.slot(slot.pk).status, SessionCoach.Status.REVOKED)

    def test_reauthorizing_creates_a_new_authorization(self):
        first = self.authorize()
        revoke_substitute(first, self.admin_user, "mistake")
        second = self.authorize(reason="Coach B at a tournament after all")
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(self.slot(first.pk).status, SessionCoach.Status.REVOKED)
        self.assertEqual(second.status, SessionCoach.Status.ASSIGNED)
        self.assertTrue(access.can_take_attendance(self.coach_a_user, self.session_b))

    def test_authorization_details_cannot_be_edited(self):
        slot = self.authorize()
        for field, value in (("access_ends_at", slot.access_ends_at + datetime.timedelta(days=30)),
                             ("session_id", self.session_a.pk), ("coach_id", self.coach_c.pk),
                             ("role", SessionCoach.Role.REGULAR), ("reason", "rewritten")):
            with self.subTest(field=field):
                fresh = self.slot(slot.pk)
                setattr(fresh, field, value)
                with self.assertRaises(ValidationError):
                    fresh.save()

    def test_substitute_history_cannot_be_deleted(self):
        slot = self.authorize()
        with self.assertRaises(PermissionDenied):
            slot.delete()
        with self.assertRaises(PermissionDenied):
            SessionCoach.objects.filter(pk=slot.pk).delete()
        self.assertTrue(SessionCoach.objects.filter(pk=slot.pk).exists())


class SessionCancellationTests(SubstituteTestCase):
    def test_cancelling_the_session_invalidates_the_substitute(self):
        slot = self.authorize()
        self.session_b.status = TrainingSession.Status.CANCELLED
        self.session_b.save()
        cancelled = self.slot(slot.pk)
        self.assertEqual(cancelled.status, SessionCoach.Status.CANCELLED)
        self.assertEqual(cancelled.revocation_reason, "Session cancelled")
        self.assertIsNotNone(cancelled.revoked_at)
        self.assertFalse(access.can_take_attendance(self.coach_a_user, self.session_b))
        self.assertNotIn(self.session_b, access.sessions_for(self.coach_a_user))
        self.assertNotIn(self.student_2, access.students_for(self.coach_a_user))
        self.assertEqual(self.session_b.coach_slots.get(coach=self.coach_b).status, SessionCoach.Status.ASSIGNED)
        # Re-opening the session does not bring the authorization back.
        self.session_b.status = TrainingSession.Status.SCHEDULED
        self.session_b.save()
        self.assertEqual(self.slot(slot.pk).status, SessionCoach.Status.CANCELLED)
        self.assertFalse(access.can_take_attendance(self.coach_a_user, self.session_b))

    def test_bulk_cancellation_still_blocks_access(self):
        # A bulk update skips save(); access checks still refuse a cancelled session.
        self.authorize()
        TrainingSession.objects.filter(pk=self.session_b.pk).update(status=TrainingSession.Status.CANCELLED)
        self.assertFalse(access.can_take_attendance(self.coach_a_user, self.session_b))
        self.assertFalse(access.open_substitute_slots(self.coach_a).exists())
        self.assertNotIn(self.student_2, access.students_for(self.coach_a_user))


class SubstituteScopeTests(SubstituteTestCase):
    def test_substitute_gets_the_session_only_never_class_family_or_finance(self):
        self.authorize()
        other = self.class_b.sessions.create(date=self.today + datetime.timedelta(days=1),
                                             start_time=datetime.time(19), end_time=datetime.time(21))
        self.assertEqual(set(access.sessions_for(self.coach_a_user)), {self.session_a, self.session_b})
        self.assertNotIn(other, access.roster_sessions_for(self.coach_a_user))
        self.assertNotIn(self.class_b, access.classes_for(self.coach_a_user))
        self.assertNotIn(self.class_b, access.roster_classes_for(self.coach_a_user))
        scope = access.student_scope(self.coach_a_user)
        self.assertEqual(scope.level_for(self.student_2.pk), access.ROSTER)  # roster detail, not family/finance
        self.assertFalse(access.children_for(self.coach_a_user).exists())

    def test_access_window_bounds(self):
        slot = self.authorize()
        self.assertFalse(access.can_take_attendance(self.coach_a_user, self.session_b,
                                                    at=slot.access_starts_at - datetime.timedelta(seconds=1)))
        self.assertTrue(access.can_take_attendance(self.coach_a_user, self.session_b, at=slot.access_starts_at))
        self.assertTrue(access.can_take_attendance(self.coach_a_user, self.session_b, at=slot.access_ends_at))
        self.assertFalse(access.can_take_attendance(self.coach_a_user, self.session_b,
                                                    at=slot.access_ends_at + datetime.timedelta(seconds=1)))


class PayrollHistoryTests(SubstituteTestCase):
    """Payroll is unchanged; the substitution history it reads stays correct."""

    def test_only_the_active_substitute_is_a_paid_slot(self):
        slot = self.authorize()
        paid = SessionCoach.objects.filter(session=self.session_b, status=SessionCoach.Status.ASSIGNED)
        self.assertEqual(set(paid.values_list("coach", flat=True)), {self.coach_a.pk})
        revoke_substitute(slot, self.admin_user, "Coach B came")
        self.assertEqual(set(paid.values_list("coach", flat=True)), {self.coach_b.pk})
        self.assertTrue(SessionCoach.objects.filter(pk=slot.pk, replaces=self.coach_b).exists())


class DatabaseTriggerTests(SubstituteTestCase):
    """PostgreSQL refuses edits the ORM guards would stop, even from raw SQL."""

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL triggers")
        self.sub = self.authorize()

    def raw(self, sql, params):
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, params)

    def test_raw_sql_cannot_delete_or_edit_a_substitute_authorization(self):
        attempts = [
            ("DELETE FROM academy_sessioncoach WHERE id = %s", [self.sub.pk]),
            ("UPDATE academy_sessioncoach SET access_ends_at = access_ends_at + interval '30 days' WHERE id = %s",
             [self.sub.pk]),
            ("UPDATE academy_sessioncoach SET coach_id = %s WHERE id = %s", [self.coach_c.pk, self.sub.pk]),
            ("UPDATE academy_sessioncoach SET role = 'REGULAR' WHERE id = %s", [self.sub.pk]),
        ]
        for sql, params in attempts:
            with self.subTest(sql=sql), self.assertRaises(IntegrityError):
                self.raw(sql, params)

    def test_raw_sql_cannot_reactivate_a_revoked_authorization(self):
        revoke_substitute(self.sub, self.admin_user, "done")
        with self.assertRaises(IntegrityError):
            self.raw("UPDATE academy_sessioncoach SET status = 'ASSIGNED', revoked_at = NULL WHERE id = %s",
                     [self.sub.pk])
        self.assertEqual(self.slot(self.sub.pk).status, SessionCoach.Status.REVOKED)

    def test_legitimate_changes_still_work(self):
        revoke_substitute(self.sub, self.admin_user, "done")      # ASSIGNED -> REVOKED, original restored
        self.authorize()                                           # new authorization row
        self.raw("DELETE FROM academy_sessioncoach WHERE id = %s",  # regular slots are not history-locked
                 [self.session_a.coach_slots.get(coach=self.coach_a).pk])
