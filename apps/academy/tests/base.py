import datetime

from django.test import TestCase
from django.utils import timezone

from apps.academy.models import ClassCoach, Enrollment, Guardianship, Program, Student, TrainingClass, TrainingSession
from apps.accounts.models import Coach, Parent, User


class AcademyTestCase(TestCase):
    """Two classes, two coaches, two families, one session per class today."""

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.admin_user = User.objects.create_user("admin", password="x", role=User.Role.ADMIN)
        cls.program = Program.objects.create(code="taolu", name="Wushu Taolu")

        cls.coach_a_user = User.objects.create_user("coach_a", password="x", role=User.Role.COACH)
        cls.coach_a = Coach.objects.create(user=cls.coach_a_user, full_name="Coach A", phone="011")
        cls.coach_b_user = User.objects.create_user("coach_b", password="x", role=User.Role.COACH)
        cls.coach_b = Coach.objects.create(user=cls.coach_b_user, full_name="Coach B", phone="012")

        cls.class_a = TrainingClass.objects.create(code="a", name="Class A", category="SCHOOL", program=cls.program)
        cls.class_b = TrainingClass.objects.create(code="b", name="Class B", category="ELITE", program=cls.program)
        start = cls.today - datetime.timedelta(days=365)
        ClassCoach.objects.create(training_class=cls.class_a, coach=cls.coach_a, start_date=start)
        ClassCoach.objects.create(training_class=cls.class_b, coach=cls.coach_b, start_date=start)

        cls.parent_1_user = User.objects.create_user("parent1", password="x", role=User.Role.PARENT)
        cls.parent_1 = Parent.objects.create(user=cls.parent_1_user, full_name="Parent One", phone="0123")
        cls.parent_2_user = User.objects.create_user("parent2", password="x", role=User.Role.PARENT)
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

