import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError

from apps.academy.tests.base import AcademyTestCase
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration
from apps.competitions.services import register, withdraw
from apps.finance.models import Charge


class CompetitionTests(AcademyTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.competition = Competition.objects.create(
            name="KL Wushu Championship", start_date=cls.today + datetime.timedelta(days=30),
            end_date=cls.today + datetime.timedelta(days=31),
            registration_deadline=cls.today + datetime.timedelta(days=10), status=Competition.Status.OPEN,
        )
        cls.changquan = CompetitionEvent.objects.create(competition=cls.competition, event_type="CHANGQUAN",
                                                        name="Changquan Boys U14", gender="M", max_age=13,
                                                        fee=Decimal("40.00"))
        cls.jianshu = CompetitionEvent.objects.create(competition=cls.competition, event_type="JIANSHU",
                                                      name="Jianshu Open", fee=Decimal("40.00"))

    def test_parent_registers_own_child_for_multiple_events(self):
        r1 = register(self.student_1, self.changquan, self.parent_1_user)
        r2 = register(self.student_1, self.jianshu, self.parent_1_user)
        self.assertEqual(r1.status, CompetitionRegistration.Status.PENDING)
        self.assertEqual(r1.charge.amount, Decimal("40.00"))
        self.assertEqual(r2.charge.fee_type, "COMPETITION")

    def test_parent_cannot_register_other_child(self):
        with self.assertRaises(PermissionDenied):
            register(self.student_3, self.jianshu, self.parent_1_user)

    def test_duplicate_and_eligibility(self):
        register(self.student_1, self.jianshu, self.parent_1_user)
        with self.assertRaises(ValidationError):
            register(self.student_1, self.jianshu, self.parent_1_user)
        girl = self.make_student("S9", "Lin", self.parent_1, self.class_a, gender="F")
        with self.assertRaises(ValidationError):
            register(girl, self.changquan, self.parent_1_user)

    def test_deadline(self):
        self.competition.registration_deadline = self.today - datetime.timedelta(days=1)
        self.competition.save()
        with self.assertRaises(ValidationError):
            register(self.student_1, self.jianshu, self.parent_1_user)
        # Admin can still register late entries.
        register(self.student_1, self.jianshu, self.admin_user)

    def test_withdraw_cancels_unpaid_fee(self):
        registration = register(self.student_1, self.jianshu, self.parent_1_user)
        withdraw(registration, self.parent_1_user)
        self.assertEqual(Charge.objects.get(pk=registration.charge_id).status, Charge.Status.CANCELLED)
        register(self.student_1, self.jianshu, self.parent_1_user)  # can re-register after withdrawing
