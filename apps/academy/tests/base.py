import datetime
import itertools
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.academy.models import ClassCoach, Enrollment, Guardianship, Program, Student, TrainingClass, TrainingSession
from apps.accounts.capabilities import Role
from apps.accounts.models import Coach, Parent, User
from apps.accounts.services import set_roles


def make_user(username, *roles):
    """Create a user and give them roles the only supported way (set_roles)."""
    user = User.objects.create_user(username, password="x")
    if roles:
        set_roles(user, roles, actor=None, reason="test setup")
    return user


def academy_time(date, hour, minute=0):
    return timezone.make_aware(datetime.datetime.combine(date, datetime.time(hour, minute)),
                               timezone.get_default_timezone())


class AcademyTestCase(TestCase):
    """Two classes, two coaches, two families, one session per class today.

    The clock starts at 20:00 today (academy time) so results never depend on
    when the suite runs: session A (17:00-19:00) has ended and session B
    (19:00-21:00) is in progress. Tests that need another moment patch
    ``django.utils.timezone.now`` themselves.
    """

    CLOCK_HOUR = 20

    @classmethod
    def setUpClass(cls):
        cls.clock = academy_time(timezone.localdate(), cls.CLOCK_HOUR)
        ticks = itertools.count()
        # Advances one microsecond per call, like a real clock, but deterministically.
        cls._clock_patch = mock.patch("django.utils.timezone.now",
                                      side_effect=lambda: cls.clock + datetime.timedelta(microseconds=next(ticks)))
        cls._clock_patch.start()
        try:
            super().setUpClass()
        except Exception:
            cls._clock_patch.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            cls._clock_patch.stop()

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.super_user = make_user("super", Role.SUPER_ADMIN)
        cls.admin_user = make_user("admin", Role.ADMIN)
        cls.finance_user = make_user("finance", Role.FINANCE_ADMIN)
        cls.program = Program.objects.create(code="taolu", name="Wushu Taolu")

        cls.coach_a_user = make_user("coach_a", Role.COACH)
        cls.coach_a = Coach.objects.create(user=cls.coach_a_user, full_name="Coach A", phone="011")
        cls.coach_b_user = make_user("coach_b", Role.COACH)
        cls.coach_b = Coach.objects.create(user=cls.coach_b_user, full_name="Coach B", phone="012")

        cls.class_a = TrainingClass.objects.create(code="a", name="Class A", category="SCHOOL", program=cls.program)
        cls.class_b = TrainingClass.objects.create(code="b", name="Class B", category="ELITE", program=cls.program)
        start = cls.today - datetime.timedelta(days=365)
        ClassCoach.objects.create(training_class=cls.class_a, coach=cls.coach_a, start_date=start)
        ClassCoach.objects.create(training_class=cls.class_b, coach=cls.coach_b, start_date=start)

        cls.parent_1_user = make_user("parent1", Role.PARENT)
        cls.parent_1 = Parent.objects.create(user=cls.parent_1_user, full_name="Parent One", phone="0123")
        cls.parent_2_user = make_user("parent2", Role.PARENT)
        cls.parent_2 = Parent.objects.create(user=cls.parent_2_user, full_name="Parent Two", phone="0124")

        cls.student_1 = cls.make_student("S1", "Ali", cls.parent_1, cls.class_a)
        cls.student_2 = cls.make_student("S2", "Mei", cls.parent_1, cls.class_b)  # same parent, other class
        cls.student_3 = cls.make_student("S3", "Raj", cls.parent_2, cls.class_a)

        cls.session_a = TrainingSession.objects.create(
            training_class=cls.class_a, date=cls.today, start_time=datetime.time(17), end_time=datetime.time(19)
        )
        cls.session_b = TrainingSession.objects.create(
            training_class=cls.class_b, date=cls.today, start_time=datetime.time(19), end_time=datetime.time(21)
        )
        for session, coach in ((cls.session_a, cls.coach_a), (cls.session_b, cls.coach_b)):
            session.coach_slots.create(coach=coach)

    @classmethod
    def make_student(cls, no, name, parent, training_class, gender="M", dob=datetime.date(2014, 5, 1)):
        student = Student.objects.create(
            student_no=no, full_name=name, gender=gender, date_of_birth=dob,
            join_date=cls.today - datetime.timedelta(days=200),
        )
        Guardianship.objects.create(student=student, parent=parent, relationship="MOTHER")
        Enrollment.objects.create(student=student, training_class=training_class,
                                  start_date=cls.today - datetime.timedelta(days=200))
        return student

