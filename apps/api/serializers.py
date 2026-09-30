from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.academy.models import (
    Family,
    ClassSchedule,
    Enrollment,
    Guardianship,
    SessionCoach,
    Student,
    TrainingClass,
    TrainingSession,
)
from apps.accounts.capabilities import Cap, Role, can
from apps.accounts.models import Coach, Parent, User  # noqa: F401  (Parent used by GuardianSerializer)
from apps.attendance.models import AttendanceRecord, AttendanceStatus
from apps.audit.models import AuditLog
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration, CompetitionResult
from apps.finance.models import Charge, FeeType, Invoice, InvoiceItem, Payment, PaymentAllocation, Receipt, Refund
from apps.payroll.models import Payslip, PayslipLine


class ParentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Parent
        fields = ["id", "full_name", "ic_number", "phone", "alt_phone", "email", "address", "occupation", "is_active"]


class CoachSerializer(serializers.ModelSerializer):
    """Bank / EPF / SOCSO fields exist only for users with ``coaches.bank_details``.
    Users with bank access but not ``coaches.manage`` can edit only those fields."""

    BANK_FIELDS = ["bank_name", "bank_account_no", "epf_no", "socso_no"]

    class Meta:
        model = Coach
        fields = ["id", "full_name", "phone", "email", "specialties", "join_date", "is_active",
                  "bank_name", "bank_account_no", "epf_no", "socso_no"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not can(user, Cap.COACHES_BANK_DETAILS):
            for name in self.BANK_FIELDS:
                self.fields.pop(name)
        if not can(user, Cap.COACHES_MANAGE):
            for name, field in self.fields.items():
                if name not in self.BANK_FIELDS:
                    field.read_only = True


class GuardianSerializer(serializers.ModelSerializer):
    parent = ParentSerializer(read_only=True)
    parent_id = serializers.PrimaryKeyRelatedField(source="parent", queryset=Parent.objects.all(), write_only=True)

    class Meta:
        model = Guardianship
        fields = ["id", "parent", "parent_id", "relationship", "is_primary_contact", "is_emergency_contact"]


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
    family = serializers.PrimaryKeyRelatedField(queryset=Family.objects.all(), required=False,
                                                help_text="Omit to create a new family for this student.")
    family_name = serializers.CharField(source="family.name", read_only=True)

    class Meta:
        model = Student
        fields = ["id", "student_no", "full_name", "chinese_name", "gender", "date_of_birth", "age", "ic_number",
                  "nationality", "school", "phone", "email", "address", "medical_notes", "join_date", "status",
                  "family", "family_name", "guardians", "current_classes", "created_at", "updated_at"]
        read_only_fields = ["status", "created_at", "updated_at"]

    def get_age(self, obj):
        return obj.age_on(timezone.localdate())

    def get_current_classes(self, obj):
        return EnrollmentSerializer(obj.current_enrollments(), many=True).data


class OwnStudentSerializer(StudentSerializer):
    """A parent's own child, or a student's own record: personal details, but
    guardians are shown as contacts only (no IC, address, ...)."""

    guardians = EmergencyContactSerializer(source="guardianships", many=True, read_only=True)


class StudentDirectorySerializer(serializers.ModelSerializer):
    """Finance directory: enough to identify a student and reach the family."""

    guardians = EmergencyContactSerializer(source="guardianships", many=True, read_only=True)

    family_name = serializers.CharField(source="family.name", read_only=True)

    class Meta:
        model = Student
        fields = ["id", "student_no", "full_name", "chinese_name", "status", "family", "family_name", "guardians"]


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
        fields = ["id", "coach", "coach_name", "role", "status", "replaces", "access_starts_at", "access_ends_at",
                  "authorized_at", "revoked_at"]


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
                                   help_text="Required when changing attendance already recorded, and for any "
                                             "administrator correction after the coach edit window.")


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
    invoice_number = serializers.SerializerMethodField()

    class Meta:
        model = Charge
        fields = ["id", "student", "student_name", "fee_type", "description", "period_start", "period_end", "quantity",
                  "unit_amount", "discount", "amount", "amount_paid", "balance", "due_date", "status", "notes",
                  "charge_item", "invoice_number", "created_at"]
        read_only_fields = ["amount", "status", "created_at"]

    def get_invoice_number(self, obj):
        item = obj.active_invoice_item()
        return str(item.invoice) if item else None


class ChargeCreateSerializer(serializers.Serializer):
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all())
    fee_type = serializers.ChoiceField(choices=FeeType.choices)
    description = serializers.CharField(max_length=255)
    unit_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    quantity = serializers.DecimalField(max_digits=8, decimal_places=2, default=1)
    discount = serializers.DecimalField(max_digits=10, decimal_places=2, default=0, min_value=0)
    due_date = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class FamilySerializer(serializers.ModelSerializer):
    students = serializers.SerializerMethodField()

    class Meta:
        model = Family
        fields = ["id", "name", "notes", "is_active", "students"]

    def get_students(self, obj):
        return [{"id": st.id, "student_no": st.student_no, "full_name": st.full_name}
                for st in obj.students.order_by("full_name")]


class InvoiceItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceItem
        fields = ["id", "student", "student_no", "student_name", "description", "fee_type", "period_start",
                  "period_end", "quantity", "unit_amount", "discount", "amount", "amount_paid", "is_active"]


class InvoiceSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = ["id", "number", "family", "family_name", "kind", "status", "currency", "issue_date", "due_date",
                  "subtotal", "discount_total", "total", "amount_paid", "balance_due", "amount_refunded", "notes",
                  "issued_at", "voided_at", "void_reason", "created_at", "updated_at", "items"]

    def get_items(self, obj):
        items = obj.items.all() if obj.status == Invoice.Status.VOID else obj.active_items()
        return InvoiceItemSerializer(items, many=True).data


class InvoiceCreateSerializer(serializers.Serializer):
    family = serializers.PrimaryKeyRelatedField(queryset=Family.objects.all())
    charges = serializers.PrimaryKeyRelatedField(queryset=Charge.objects.all(), many=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class AllocationSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    student = serializers.IntegerField(source="invoice_item.student_id", read_only=True)
    student_name = serializers.CharField(source="invoice_item.student_name", read_only=True)
    description = serializers.CharField(source="invoice_item.description", read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = ["id", "invoice", "invoice_number", "invoice_item", "student", "student_name", "description", "amount"]


class PaymentSerializer(serializers.ModelSerializer):
    allocations = AllocationSerializer(many=True, read_only=True)
    receipt_id = serializers.IntegerField(source="receipt.id", read_only=True, default=None)
    receipt_number = serializers.CharField(source="receipt.number", read_only=True, default=None)

    class Meta:
        model = Payment
        fields = ["id", "number", "family", "payer_name", "amount", "method", "reference", "received_at", "status",
                  "notes", "allocations", "receipt_id", "receipt_number", "created_at", "updated_at"]


class AllocationInputSerializer(serializers.Serializer):
    invoice = serializers.PrimaryKeyRelatedField(queryset=Invoice.objects.all())
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))


class PaymentCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    payer_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    received_at = serializers.DateTimeField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    allocations = AllocationInputSerializer(many=True)


class RefundSerializer(serializers.ModelSerializer):
    class Meta:
        model = Refund
        fields = ["id", "number", "payment", "allocation", "amount", "method", "reference", "reason", "refunded_at",
                  "created_at"]


class RefundCreateSerializer(serializers.Serializer):
    allocation = serializers.PrimaryKeyRelatedField(queryset=PaymentAllocation.objects.all())
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    reason = serializers.CharField()
    method = serializers.ChoiceField(choices=Payment.Method.choices, default=Payment.Method.BANK_TRANSFER)
    reference = serializers.CharField(required=False, allow_blank=True, default="")


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
    invoice = serializers.SerializerMethodField()
    result = CompetitionResultSerializer(read_only=True, default=None)

    class Meta:
        model = CompetitionRegistration
        fields = ["id", "competition", "competition_name", "event", "event_name", "student", "student_name", "status",
                  "registered_at", "notes", "fee", "fee_status", "invoice", "result"]
        read_only_fields = ["status", "registered_at"]

    def get_invoice(self, obj):
        item = obj.charge.active_invoice_item() if obj.charge_id else None
        if item is None:
            return None
        return {"id": item.invoice_id, "number": item.invoice.number, "balance_due": str(item.invoice.balance_due)}


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


class UserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "is_active", "roles", "last_login"]

    def get_roles(self, obj):
        names = [g.name for g in obj.groups.all() if g.name in Role.values]
        return [r for r in Role.values if r in names]


class RoleChangeSerializer(serializers.Serializer):
    roles = serializers.ListField(child=serializers.ChoiceField(choices=Role.choices), allow_empty=True)
    reason = serializers.CharField()
