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
from apps.competitions.models import (
    Competition, CompetitionEvent, CompetitionRegistration, CompetitionResult, RegistrationFormField,
)
from apps.finance.models import (
    AcademyPaymentInfo, Charge, FeeType, Invoice, InvoiceItem, Payment, PaymentAllocation, PaymentProof, Receipt, Refund,
)
from apps.audit.masking import mask_identifier
from apps.payroll.models import PayrollRun, Payslip, PayslipLine


class HideStaffNotesMixin:
    """``notes`` on finance and family records are internal staff notes (they are
    not printed on invoices or receipts). They are left out of the response for
    anyone who cannot see every record of that kind, e.g. parents."""

    STAFF_NOTE_CAPABILITIES = (Cap.FINANCE_VIEW_ALL,)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and not can(user, self.STAFF_NOTE_CAPABILITIES):
            data.pop("notes", None)
        return data


class ParentSerializer(serializers.ModelSerializer):
    """Parent record. The IC / passport number is shown in full only to people who
    manage parent records (and to the parent themselves); others (e.g. finance,
    who may view parents to reach them) see only its last 4 characters."""

    class Meta:
        model = Parent
        fields = ["id", "full_name", "ic_number", "phone", "alt_phone", "email", "address", "occupation", "is_active"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        own = user is not None and instance.user_id is not None and instance.user_id == getattr(user, "pk", None)
        if not own and not can(user, Cap.PARENTS_MANAGE):
            data["ic_number"] = mask_identifier(data.get("ic_number"))
        return data


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


class SelfStudentSerializer(serializers.ModelSerializer):
    """A student's own record as the Student Portal shows it: the basic training
    profile only. Never the IC / passport, date of birth, address, phone, email,
    medical note, guardians, emergency contacts or family (those stay with the
    parents and the academy)."""

    age = serializers.SerializerMethodField()
    current_classes = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = ["id", "student_no", "full_name", "chinese_name", "gender", "age", "status", "join_date",
                  "current_classes"]

    def get_age(self, obj):
        return obj.age_on(timezone.localdate())

    def get_current_classes(self, obj):
        return [{"class_name": e.training_class.name, "category": e.training_class.category,
                 "team_name": e.team.name if e.team_id else None, "start_date": e.start_date}
                for e in obj.current_enrollments().select_related("training_class", "team")]


class StudentDirectorySerializer(serializers.ModelSerializer):
    """Finance directory: enough to identify a student and reach the family."""

    guardians = EmergencyContactSerializer(source="guardianships", many=True, read_only=True)

    family_name = serializers.CharField(source="family.name", read_only=True)

    class Meta:
        model = Student
        fields = ["id", "student_no", "full_name", "chinese_name", "status", "family", "family_name", "guardians"]


class RosterStudentSerializer(serializers.ModelSerializer):
    """A class or session roster: training-relevant info only.

    The medical note is free text (general health information, not a
    coaching restriction) and no capability authorizes coaches to see
    emergency contacts, so both are returned only to staff who can see the
    full student record (``students.view_all``). Coaches, substitutes and a
    serializer used without a request get neither: the check fails closed."""

    SENSITIVE = ("medical_notes", "emergency_contacts")

    age = serializers.SerializerMethodField()
    emergency_contacts = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = ["id", "student_no", "full_name", "chinese_name", "gender", "age", "medical_notes", "status",
                  "emergency_contacts"]

    def get_age(self, obj):
        return obj.age_on(timezone.localdate())

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if not can(getattr(request, "user", None), Cap.STUDENTS_VIEW_ALL):
            for name in self.SENSITIVE:
                fields.pop(name, None)
        return fields

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
    phase = serializers.SerializerMethodField()

    TIMING = ("training_class", "date", "start_time", "end_time")

    class Meta:
        model = TrainingSession
        fields = ["id", "training_class", "class_name", "date", "start_time", "end_time", "venue", "status", "phase",
                  "notes", "coaches"]

    def get_phase(self, obj):
        return obj.phase()

    def validate(self, attrs):
        if self.instance is not None:
            changed = [f for f in self.TIMING if f in attrs and attrs[f] != getattr(self.instance, f)]
            if changed:
                raise serializers.ValidationError(
                    {f: "Use the reschedule action (reason required) to change this." for f in changed})
        return attrs


class CoachingSessionSerializer(TrainingSessionSerializer):
    """A session as the coach (or staff) working it sees it: the caller's own
    role on it and the attendance sheet state and counts (from the attendance
    services; UNMARKED counted separately, never as absent)."""

    my_role = serializers.SerializerMethodField()
    attendance = serializers.SerializerMethodField()

    class Meta(TrainingSessionSerializer.Meta):
        fields = TrainingSessionSerializer.Meta.fields + ["my_role", "attendance"]

    def get_my_role(self, obj):
        coach = self.context.get("coach")
        if coach is None:
            return None
        slots = [slot for slot in obj.coach_slots.all() if slot.coach_id == coach.pk]
        if slots:
            slot = next((s for s in slots if s.status == SessionCoach.Status.ASSIGNED), slots[-1])
            return {"role": slot.role, "status": slot.status, "access_ends_at": slot.access_ends_at}
        return {"role": SessionCoach.Role.REGULAR, "status": None, "access_ends_at": None}

    def get_attendance(self, obj):
        from apps.attendance import services as attendance_services

        summary = attendance_services.session_summary(obj)
        return {
            "state": attendance_services.session_state(obj, summary),
            "coach_edit_deadline": attendance_services.coach_edit_deadline(obj),
            "expected": summary["expected"],
            "marked": summary["marked"],
            "unmarked": summary["unmarked"],
            "percentage": str(summary["percentage"]) if summary["percentage"] is not None else None,
        }


class StudentSessionSerializer(serializers.ModelSerializer):
    """A session as a student sees it: when and where, who coaches it (names
    only) and their own attendance. No staff notes, no substitute
    authorizations, no other students.

    ``context["my_status"]``: {session_id: status} of the student's own records."""

    class_name = serializers.CharField(source="training_class.name", read_only=True)
    phase = serializers.SerializerMethodField()
    coaches = serializers.SerializerMethodField()
    my_attendance = serializers.SerializerMethodField()

    class Meta:
        model = TrainingSession
        fields = ["id", "class_name", "date", "start_time", "end_time", "venue", "status", "phase", "coaches",
                  "my_attendance"]
        read_only_fields = fields

    def get_phase(self, obj):
        return obj.phase()

    def get_coaches(self, obj):
        return sorted({slot.coach.full_name for slot in obj.coach_slots.all()
                       if slot.status == SessionCoach.Status.ASSIGNED})

    def get_my_attendance(self, obj):
        """Their own status once the session has started (UNMARKED if not yet
        marked); None before it starts or when it is cancelled."""
        if obj.status == TrainingSession.Status.CANCELLED or timezone.now() < obj.starts_at:
            return None
        return self.context.get("my_status", {}).get(obj.id, AttendanceStatus.UNMARKED)


class RescheduleSerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    start_time = serializers.TimeField(required=False)
    end_time = serializers.TimeField(required=False)
    training_class = serializers.PrimaryKeyRelatedField(queryset=TrainingClass.objects.all(), required=False)
    reason = serializers.CharField(max_length=255)


class AttendanceRecordSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    session_date = serializers.DateField(source="session.date", read_only=True)
    class_name = serializers.CharField(source="session.training_class.name", read_only=True)
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceRecord
        fields = ["id", "session", "session_date", "class_name", "student", "student_name", "status", "remarks",
                  "recorded_by_name", "created_at", "updated_at"]

    # A student viewing their own attendance gets the status only: the coach's
    # remarks and who recorded it are staff / coach / parent information.
    STUDENT_HIDDEN = ("remarks", "recorded_by_name")
    BROADER = (Cap.ATTENDANCE_VIEW_ALL, Cap.ATTENDANCE_VIEW_ASSIGNED, Cap.ATTENDANCE_VIEW_OWN_CHILDREN)

    def get_fields(self):
        fields = super().get_fields()
        from apps.academy import access

        request = self.context.get("request")
        if request is not None and access.student_view(request.user, self.BROADER):
            for name in self.STUDENT_HIDDEN:
                fields.pop(name, None)
        return fields

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


class ChargeSerializer(HideStaffNotesMixin, serializers.ModelSerializer):
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


class FamilySerializer(HideStaffNotesMixin, serializers.ModelSerializer):
    STAFF_NOTE_CAPABILITIES = (Cap.STUDENTS_VIEW_ALL, Cap.FINANCE_VIEW_ALL)

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


class InvoiceSerializer(HideStaffNotesMixin, serializers.ModelSerializer):
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


class PaymentSerializer(HideStaffNotesMixin, serializers.ModelSerializer):
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


# Competition data for someone who can only view their own entries as a student
# (parents register and pay): no registration form, no fees.
COMPETITION_BROADER = (Cap.COMPETITION_MANAGE, Cap.COMPETITION_REGISTRATIONS_VIEW_ALL,
                       Cap.COMPETITION_REGISTRATIONS_VIEW_ASSIGNED, Cap.COMPETITION_REGISTER_OWN_CHILDREN)


def _competition_student_view(context):
    from apps.academy import access

    request = context.get("request")
    return request is not None and access.student_view(request.user, COMPETITION_BROADER)


class CompetitionEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompetitionEvent
        fields = ["id", "competition", "event_type", "name", "gender", "min_age", "max_age", "weight_class", "fee",
                  "max_entries"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if _competition_student_view(self.context):
            data.pop("fee", None)
        return data


class CompetitionSerializer(serializers.ModelSerializer):
    events = CompetitionEventSerializer(many=True, read_only=True)
    is_open = serializers.SerializerMethodField()
    registration_form = serializers.SerializerMethodField()

    class Meta:
        model = Competition
        fields = ["id", "name", "organiser", "venue", "start_date", "end_date", "registration_deadline", "status",
                  "allow_parent_registration", "allow_parent_withdrawal", "max_events_per_student",
                  "age_reference_date", "description", "rules", "is_open", "events", "registration_form"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if _competition_student_view(self.context):
            data.pop("registration_form", None)  # a student cannot register (parents do); events drop fees
        return data

    def get_is_open(self, obj):
        return obj.is_open_for_registration()

    def get_registration_form(self, obj):
        """The PUBLISHED form only (never the staff working copy)."""
        published = obj.form_status == Competition.FormStatus.PUBLISHED
        return {"status": obj.form_status, "version": obj.form_version, "published_at": obj.form_published_at,
                "fields": obj.published_form if published else []}


class CompetitionResultSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="registration.student.full_name", read_only=True)
    event_name = serializers.CharField(source="registration.event.name", read_only=True)

    class Meta:
        model = CompetitionResult
        fields = ["id", "registration", "student_name", "event_name", "placing", "medal", "score", "remarks"]

    BROADER = (Cap.COMPETITION_REGISTRATIONS_VIEW_ALL, Cap.COMPETITION_REGISTRATIONS_VIEW_ASSIGNED,
               Cap.COMPETITION_REGISTRATIONS_VIEW_OWN_CHILDREN)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        from apps.academy import access

        request = self.context.get("request")
        if request is not None and access.student_view(request.user, self.BROADER):
            data.pop("remarks", None)  # staff remarks on a result are not student-facing
        return data

    def validate(self, attrs):
        if self.instance is not None and "registration" in attrs and attrs["registration"] != self.instance.registration:
            raise serializers.ValidationError({"registration": "A result cannot be moved to another registration."})
        return attrs


class RegistrationFormFieldSerializer(serializers.ModelSerializer):
    """A custom question in a competition's registration form (staff working copy)."""

    class Meta:
        model = RegistrationFormField
        fields = ["id", "competition", "key", "label", "field_type", "required", "help_text", "placeholder",
                  "options", "max_length", "min_value", "max_value", "order", "is_active"]

    def validate(self, attrs):
        from django.core.exceptions import ValidationError as DjangoValidationError

        from apps.competitions.registration_forms import validate_field_definition

        if self.instance is not None and "competition" in attrs and attrs["competition"] != self.instance.competition:
            raise serializers.ValidationError({"competition": "A field cannot be moved to another competition."})
        if self.instance is not None and "key" in attrs and attrs["key"] != self.instance.key:
            raise serializers.ValidationError({"key": "The key cannot change (answers are stored under it). "
                                                      "Deactivate this field and add a new one."})
        candidate = RegistrationFormField(**{**({f: getattr(self.instance, f) for f in self.Meta.fields if f != "id"}
                                                if self.instance else {}), **attrs})
        try:
            validate_field_definition(candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from None
        return attrs


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
                  "registered_at", "notes", "fee", "fee_status", "invoice", "result", "form_version", "form_responses"]
        read_only_fields = ["status", "registered_at", "form_version", "form_responses"]
        # Duplicates are refused by the registration service (after the permission check).
        validators = []

    # Family and competition-staff details: a coach viewing their athletes' entries,
    # or a student viewing their own, sees the entry and its status, never the
    # family's answers, notes, fee or invoice (parents register and pay).
    FAMILY_ONLY = ("form_responses", "notes", "fee", "fee_status", "invoice")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and not can(user, Cap.COMPETITION_REGISTRATIONS_VIEW_ALL):
            from apps.academy import access

            if not access.is_parent_of(user, instance.student):
                for name in self.FAMILY_ONLY:
                    data.pop(name, None)
        return data

    def get_invoice(self, obj):
        item = obj.charge.active_invoice_item() if obj.charge_id else None
        if item is None:
            return None
        return {"id": item.invoice_id, "number": item.invoice.number, "balance_due": str(item.invoice.balance_due)}


class StudentCompetitionEntrySerializer(serializers.ModelSerializer):
    """A student's own competition entry: the competition, the event, the entry
    status and the result. Never the fee, payment, invoice, the family's form
    answers or notes, or staff remarks."""

    competition = serializers.SerializerMethodField()
    event = serializers.SerializerMethodField()
    result = serializers.SerializerMethodField()

    class Meta:
        model = CompetitionRegistration
        fields = ["id", "status", "registered_at", "competition", "event", "result"]
        read_only_fields = fields

    def get_competition(self, obj):
        c = obj.event.competition
        return {"id": c.id, "name": c.name, "organiser": c.organiser, "venue": c.venue, "start_date": c.start_date,
                "end_date": c.end_date, "status": c.status, "rules": c.rules}

    def get_event(self, obj):
        e = obj.event
        return {"name": e.name, "event_type": e.event_type, "gender": e.gender, "min_age": e.min_age,
                "max_age": e.max_age, "weight_class": e.weight_class}

    def get_result(self, obj):
        result = getattr(obj, "result", None)
        if result is None:
            return None
        return {"placing": result.placing, "medal": result.medal,
                "score": str(result.score) if result.score is not None else None}


class PayslipLineSerializer(serializers.ModelSerializer):
    original_coach = serializers.CharField(source="slot.replaces.full_name", read_only=True, default=None)

    class Meta:
        model = PayslipLine
        fields = ["kind", "description", "session", "slot", "original_coach", "rate", "rule", "issue", "amount"]


class PayrollRunSerializer(serializers.ModelSerializer):
    issue_count = serializers.IntegerField(read_only=True)
    period_start = serializers.DateField(read_only=True)
    period_end = serializers.DateField(read_only=True)

    class Meta:
        model = PayrollRun
        fields = ["id", "year", "month", "period_start", "period_end", "status", "issue_count", "calculated_at",
                  "finalized_at", "excluded", "notes"]
        read_only_fields = fields


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


class PaymentProofSerializer(serializers.ModelSerializer):
    """A parent's payment proof. The file itself is only available through the
    permission-checked download action; its storage path is never returned.
    Reviewer-only details (who reviewed, file checksum) are left out for parents."""

    family_name = serializers.CharField(source="family.name", read_only=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    invoice_status = serializers.CharField(source="invoice.status", read_only=True)
    invoice_balance_due = serializers.DecimalField(source="invoice.balance_due", max_digits=10, decimal_places=2,
                                                   read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()
    reviewed_by_name = serializers.SerializerMethodField()
    payment_number = serializers.CharField(source="payment.number", read_only=True, default=None)

    REVIEWER_ONLY = ("reviewed_by_name", "sha256")

    class Meta:
        model = PaymentProof
        fields = ["id", "family", "family_name", "invoice", "invoice_number", "invoice_status", "invoice_balance_due",
                  "uploaded_by_name", "uploaded_at", "original_name", "content_type", "size", "sha256",
                  "amount_claimed", "payment_date", "reference", "note", "status", "reviewed_by_name",
                  "reviewed_at", "review_note", "payment", "payment_number"]
        read_only_fields = fields

    @staticmethod
    def _name(user):
        return (user.get_full_name() or user.username) if user else None

    def get_uploaded_by_name(self, obj):
        return self._name(obj.uploaded_by)

    def get_reviewed_by_name(self, obj):
        return self._name(obj.reviewed_by)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if not can(getattr(request, "user", None), Cap.FINANCE_PROOFS_REVIEW):
            for name in self.REVIEWER_ONLY:
                data.pop(name, None)
        return data


class PaymentProofUploadSerializer(serializers.Serializer):
    invoice = serializers.IntegerField()
    file = serializers.FileField(allow_empty_file=False, use_url=False)
    amount_claimed = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"),
                                              required=False, allow_null=True)
    payment_date = serializers.DateField(required=False, allow_null=True)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class ProofAcceptSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")
    payment = serializers.PrimaryKeyRelatedField(queryset=Payment.objects.all(), required=False, allow_null=True)


class AcademyPaymentInfoSerializer(serializers.ModelSerializer):
    """What parents see to pay the academy. The QR code is returned inline as a
    data: URI (small image; works under the Content-Security-Policy)."""

    configured = serializers.BooleanField(source="is_configured", read_only=True)
    qr_code = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = AcademyPaymentInfo
        fields = ["configured", "bank_name", "account_name", "account_number", "instructions",
                  "reference_instructions", "qr_code", "updated_at", "updated_by_name"]

    def get_qr_code(self, obj):
        if not obj.qr_code:
            return None
        import base64

        try:
            with obj.qr_code.open("rb") as handle:
                content = handle.read()
        except (FileNotFoundError, OSError):
            return None
        return f"data:{obj.qr_content_type};base64,{base64.b64encode(content).decode()}"

    def get_updated_by_name(self, obj):
        user = obj.updated_by
        return (user.get_full_name() or user.username) if user else None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if not can(getattr(request, "user", None), Cap.FINANCE_PAYMENT_INFO_MANAGE):
            data.pop("updated_by_name", None)
        return data


class AcademyPaymentInfoUpdateSerializer(serializers.Serializer):
    bank_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    account_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    account_number = serializers.CharField(max_length=40, required=False, allow_blank=True)
    instructions = serializers.CharField(required=False, allow_blank=True)
    reference_instructions = serializers.CharField(max_length=255, required=False, allow_blank=True)
    qr_code = serializers.FileField(required=False, allow_empty_file=False, use_url=False)
    remove_qr_code = serializers.BooleanField(required=False, default=False)
