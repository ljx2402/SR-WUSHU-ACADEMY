from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.academy.models import (
    ClassSchedule,
    Enrollment,
    Guardianship,
    SessionCoach,
    Student,
    TrainingClass,
    TrainingSession,
)
from apps.accounts.models import Coach, Parent
from apps.attendance.models import AttendanceRecord, AttendanceStatus
from apps.audit.models import AuditLog
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration, CompetitionResult
from apps.finance.models import Charge, FeeType, Payment, PaymentAllocation, Receipt
from apps.payroll.models import Payslip, PayslipLine


class ParentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Parent
        fields = ["id", "full_name", "ic_number", "phone", "alt_phone", "email", "address", "occupation", "is_active"]


class CoachSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coach
        fields = ["id", "full_name", "phone", "email", "specialties", "join_date", "is_active"]


class GuardianSerializer(serializers.ModelSerializer):
    parent = ParentSerializer(read_only=True)
    parent_id = serializers.PrimaryKeyRelatedField(source="parent", queryset=Parent.objects.all(), write_only=True)

    class Meta:
        model = Guardianship
        fields = ["id", "parent", "parent_id", "relationship", "is_primary_contact", "is_emergency_contact",
                  "is_billing_contact"]


class EnrollmentSerializer(serializers.ModelSerializer):
    class_name = serializers.CharField(source="training_class.name", read_only=True)
    category = serializers.CharField(source="training_class.category", read_only=True)
    team_name = serializers.CharField(source="team.name", read_only=True, default=None)
    coach_name = serializers.CharField(source="coach.full_name", read_only=True, default=None)

    class Meta:
        model = Enrollment
        fields = ["id", "student", "training_class", "class_name", "category", "team", "team_name", "coach",
                  "coach_name", "start_date", "end_date", "end_reason"]


class EmergencyContactSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="parent.full_name")
    phone = serializers.CharField(source="parent.phone")

    class Meta:
        model = Guardianship
        fields = ["name", "relationship", "phone"]


class StudentSerializer(serializers.ModelSerializer):
    """Full student record, for admins and the student's own parents."""

    age = serializers.SerializerMethodField()
    guardians = GuardianSerializer(source="guardianships", many=True, read_only=True)
    current_classes = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = ["id", "student_no", "full_name", "chinese_name", "gender", "date_of_birth", "age", "ic_number",
                  "nationality", "school", "phone", "email", "address", "medical_notes", "join_date", "status",
                  "guardians", "current_classes", "created_at", "updated_at"]
        read_only_fields = ["status", "created_at", "updated_at"]

    def get_age(self, obj):
        return obj.age_on(timezone.localdate())

    def get_current_classes(self, obj):
        return EnrollmentSerializer(obj.current_enrollments(), many=True).data


class ParentStudentSerializer(StudentSerializer):
    """For parents: guardians are shown as contacts only (no IC, address, ...)."""

    guardians = EmergencyContactSerializer(source="guardianships", many=True, read_only=True)




class RosterStudentSerializer(serializers.ModelSerializer):
    """What a coach sees: training-relevant info plus emergency contacts only."""

    age = serializers.SerializerMethodField()
    emergency_contacts = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = ["id", "student_no", "full_name", "chinese_name", "gender", "age", "medical_notes", "status",
                  "emergency_contacts"]

    def get_age(self, obj):
        return obj.age_on(timezone.localdate())

    def get_emergency_contacts(self, obj):
        contacts = [g for g in obj.guardianships.all() if g.is_emergency_contact]
        return EmergencyContactSerializer(contacts, many=True).data


class ScheduleSerializer(serializers.ModelSerializer):
    weekday_name = serializers.CharField(source="get_weekday_display", read_only=True)

    class Meta:
        model = ClassSchedule
        fields = ["id", "weekday", "weekday_name", "start_time", "end_time", "venue", "effective_from", "effective_to"]


class TrainingClassSerializer(serializers.ModelSerializer):
    program_name = serializers.CharField(source="program.name", read_only=True)
    team_name = serializers.CharField(source="team.name", read_only=True, default=None)
    schedules = ScheduleSerializer(many=True, read_only=True)
    current_coaches = serializers.SerializerMethodField()

    class Meta:
        model = TrainingClass
        fields = ["id", "code", "name", "category", "program", "program_name", "team", "team_name", "venue",
                  "capacity", "description", "is_active", "schedules", "current_coaches"]

    def get_current_coaches(self, obj):
        return [{"id": c.id, "full_name": c.full_name} for c in obj.coaches_on(timezone.localdate())]


class SessionCoachSerializer(serializers.ModelSerializer):
    coach_name = serializers.CharField(source="coach.full_name", read_only=True)

    class Meta:
        model = SessionCoach
        fields = ["coach", "coach_name", "role", "status", "replaces", "access_starts_at", "access_ends_at"]


class TrainingSessionSerializer(serializers.ModelSerializer):
    class_name = serializers.CharField(source="training_class.name", read_only=True)
    coaches = SessionCoachSerializer(source="coach_slots", many=True, read_only=True)

    class Meta:
        model = TrainingSession
        fields = ["id", "training_class", "class_name", "date", "start_time", "end_time", "venue", "status", "notes",
                  "coaches"]


class AttendanceRecordSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    session_date = serializers.DateField(source="session.date", read_only=True)
    class_name = serializers.CharField(source="session.training_class.name", read_only=True)
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceRecord
        fields = ["id", "session", "session_date", "class_name", "student", "student_name", "status", "remarks",
                  "recorded_by_name", "created_at", "updated_at"]

    def get_recorded_by_name(self, obj):
        user = obj.recorded_by
        return (user.get_full_name() or user.username) if user else None


class AttendanceEntrySerializer(serializers.Serializer):
    student = serializers.IntegerField()
    status = serializers.ChoiceField(choices=AttendanceStatus.choices)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class AttendanceSubmitSerializer(serializers.Serializer):
    records = AttendanceEntrySerializer(many=True)
    reason = serializers.CharField(required=False, allow_blank=True, default="",
                                   help_text="Required when changing attendance already recorded.")


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ["id", "timestamp", "actor", "actor_name", "category", "action", "object_repr", "changes", "reason"]

    def get_actor_name(self, obj):
        return (obj.actor.get_full_name() or obj.actor.username) if obj.actor else None


class ChargeSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    amount_paid = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    balance = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Charge
        fields = ["id", "student", "student_name", "fee_type", "description", "period_start", "period_end", "quantity",
                  "unit_amount", "discount", "amount", "amount_paid", "balance", "due_date", "status", "notes",
                  "charge_item", "created_at"]
        read_only_fields = ["amount", "status", "created_at"]


class ChargeCreateSerializer(serializers.Serializer):
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all())
    fee_type = serializers.ChoiceField(choices=FeeType.choices)
    description = serializers.CharField(max_length=255)
    unit_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    quantity = serializers.DecimalField(max_digits=8, decimal_places=2, default=1)
    discount = serializers.DecimalField(max_digits=10, decimal_places=2, default=0, min_value=0)
    due_date = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class AllocationSerializer(serializers.ModelSerializer):
    description = serializers.CharField(source="charge.description", read_only=True)
    student_name = serializers.CharField(source="charge.student.full_name", read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = ["charge", "description", "student_name", "amount"]


class PaymentSerializer(serializers.ModelSerializer):
    allocations = AllocationSerializer(many=True, read_only=True)
    receipt_id = serializers.IntegerField(source="receipt.id", read_only=True, default=None)
    receipt_number = serializers.CharField(source="receipt.number", read_only=True, default=None)

    class Meta:
        model = Payment
        fields = ["id", "parent", "payer_name", "amount", "method", "reference", "received_on", "status", "notes",
                  "allocations", "receipt_id", "receipt_number", "created_at"]


class AllocationInputSerializer(serializers.Serializer):
    charge = serializers.PrimaryKeyRelatedField(queryset=Charge.objects.all())
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))


class PaymentCreateSerializer(serializers.Serializer):
    parent = serializers.PrimaryKeyRelatedField(queryset=Parent.objects.all(), required=False, allow_null=True)
    payer_name = serializers.CharField(max_length=200)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    received_on = serializers.DateField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    allocations = AllocationInputSerializer(many=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField()


class ReceiptSerializer(serializers.ModelSerializer):
    is_void = serializers.BooleanField(read_only=True)
    void_reason = serializers.CharField(source="void_record.reason", read_only=True, default=None)

    class Meta:
        model = Receipt
        fields = ["id", "number", "payment", "issued_at", "payer_name", "total", "content", "is_void", "void_reason"]


class CompetitionEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompetitionEvent
        fields = ["id", "competition", "event_type", "name", "gender", "min_age", "max_age", "weight_class", "fee",
                  "max_entries"]


class CompetitionSerializer(serializers.ModelSerializer):
    events = CompetitionEventSerializer(many=True, read_only=True)
    is_open = serializers.SerializerMethodField()

    class Meta:
        model = Competition
        fields = ["id", "name", "organiser", "venue", "start_date", "end_date", "registration_deadline", "status",
                  "allow_parent_registration", "max_events_per_student", "age_reference_date", "description",
                  "is_open", "events"]

    def get_is_open(self, obj):
        return obj.is_open_for_registration()


class CompetitionResultSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="registration.student.full_name", read_only=True)
    event_name = serializers.CharField(source="registration.event.name", read_only=True)

    class Meta:
        model = CompetitionResult
        fields = ["id", "registration", "student_name", "event_name", "placing", "medal", "score", "remarks"]


class CompetitionRegistrationSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    event_name = serializers.CharField(source="event.name", read_only=True)
    competition = serializers.IntegerField(source="event.competition_id", read_only=True)
    competition_name = serializers.CharField(source="event.competition.name", read_only=True)
    fee = serializers.DecimalField(source="charge.amount", max_digits=10, decimal_places=2, read_only=True, default=None)
    fee_status = serializers.CharField(source="charge.status", read_only=True, default=None)
    result = CompetitionResultSerializer(read_only=True, default=None)

    class Meta:
        model = CompetitionRegistration
        fields = ["id", "competition", "competition_name", "event", "event_name", "student", "student_name", "status",
                  "registered_at", "notes", "fee", "fee_status", "result"]
        read_only_fields = ["status", "registered_at"]


class PayslipLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayslipLine
        fields = ["kind", "description", "session", "quantity", "rate", "amount"]


class PayslipSerializer(serializers.ModelSerializer):
    year = serializers.IntegerField(source="run.year", read_only=True)
    month = serializers.IntegerField(source="run.month", read_only=True)
    run_status = serializers.CharField(source="run.status", read_only=True)
    coach_name = serializers.CharField(source="coach.full_name", read_only=True)
    lines = PayslipLineSerializer(many=True, read_only=True)

    class Meta:
        model = Payslip
        fields = ["id", "year", "month", "run_status", "coach", "coach_name", "regular_sessions", "substitute_sessions",
                  "hours", "gross_pay", "total_deductions", "net_pay", "lines"]
