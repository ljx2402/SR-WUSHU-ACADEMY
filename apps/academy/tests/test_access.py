import datetime

from django.utils import timezone

from apps.academy import access
from apps.academy.models import SessionCoach
from apps.academy.services import assign_substitute, revoke_substitute

from .base import AcademyTestCase


class ParentAccessTests(AcademyTestCase):
    def test_parent_sees_only_own_children(self):
        self.assertEqual(set(access.students_for(self.parent_1_user)), {self.student_1, self.student_2})
        self.assertEqual(set(access.students_for(self.parent_2_user)), {self.student_3})

    def test_one_student_can_have_multiple_parents(self):
        self.student_3.guardianships.create(parent=self.parent_1, relationship="FATHER")
        self.assertIn(self.student_3, access.students_for(self.parent_1_user))
        self.assertIn(self.student_3, access.students_for(self.parent_2_user))


class CoachAccessTests(AcademyTestCase):
    def test_coach_sees_only_own_classes_and_students(self):
        self.assertEqual(list(access.classes_for(self.coach_a_user)), [self.class_a])
        self.assertEqual(set(access.students_for(self.coach_a_user)), {self.student_1, self.student_3})
        self.assertEqual(list(access.sessions_for(self.coach_a_user)), [self.session_a])
        self.assertFalse(access.can_take_attendance(self.coach_a_user, self.session_b))

    def test_coach_loses_access_when_assignment_ends(self):
        assignment = self.class_a.coach_assignments.get(coach=self.coach_a)
        assignment.end_date = self.today - datetime.timedelta(days=1)
        assignment.save()
        self.assertFalse(access.classes_for(self.coach_a_user).exists())
        self.assertFalse(access.students_for(self.coach_a_user).exists())

    def test_one_class_multiple_coaches(self):
        self.class_a.coach_assignments.create(coach=self.coach_b, role="ASSISTANT", start_date=self.today)
        self.assertEqual(set(self.class_a.coaches_on(self.today)), {self.coach_a, self.coach_b})
        self.assertEqual(set(access.classes_for(self.coach_b_user)), {self.class_a, self.class_b})


class SubstituteAccessTests(AcademyTestCase):
    def test_substitute_gets_only_that_session(self):
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user)
        self.assertTrue(slot.is_substitute)
        # Session B now visible to coach A, but not class B as a whole.
        self.assertIn(self.session_b, access.sessions_for(self.coach_a_user))
        self.assertNotIn(self.class_b, access.classes_for(self.coach_a_user))
        self.assertIn(self.student_2, access.students_for(self.coach_a_user))
        self.assertTrue(access.can_take_attendance(self.coach_a_user, self.session_b))
        # Other sessions of class B stay hidden.
        other = self.class_b.sessions.create(date=self.today + datetime.timedelta(days=7),
                                             start_time=datetime.time(19), end_time=datetime.time(21))
        self.assertNotIn(other, access.sessions_for(self.coach_a_user))
        self.assertFalse(access.can_take_attendance(self.coach_a_user, other))
        # Original coach is marked as replaced (and so not paid).
        self.assertEqual(self.session_b.coach_slots.get(coach=self.coach_b).status, SessionCoach.Status.REPLACED)

    def test_substitute_access_expires(self):
        assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user)
        later = self.session_b.ends_at + datetime.timedelta(hours=25)
        self.assertNotIn(self.session_b, access.sessions_for(self.coach_a_user, at=later))
        self.assertFalse(access.can_take_attendance(self.coach_a_user, self.session_b, at=later))
        self.assertNotIn(self.student_2, access.students_for(self.coach_a_user, at=later))

    def test_substitute_access_not_open_too_early(self):
        assign_substitute(self.session_b, self.coach_a, actor=self.admin_user)
        early = self.session_b.starts_at - datetime.timedelta(days=3)
        self.assertFalse(access.can_take_attendance(self.coach_a_user, self.session_b, at=early))

    def test_revoke_restores_original_coach(self):
        slot = assign_substitute(self.session_b, self.coach_a, replaces=self.coach_b, actor=self.admin_user)
        revoke_substitute(slot, self.admin_user)
        self.assertFalse(access.can_take_attendance(self.coach_a_user, self.session_b, at=timezone.now()))
        self.assertEqual(self.session_b.coach_slots.get(coach=self.coach_b).status, SessionCoach.Status.ASSIGNED)
