from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academy import access
from apps.academy import services as academy_services
from apps.academy.models import Enrollment, SessionCoach, Student, TrainingClass, TrainingSession
from apps.accounts.models import Coach, Parent
from apps.attendance import services as attendance_services
from apps.attendance.models import AttendanceRecord
from apps.audit.context import reset_actor, set_actor
from apps.audit.utils import history_for
from apps.competitions import services as competition_services
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration, CompetitionResult
from apps.finance import services as finance_services
from apps.finance.models import Charge, Payment, Receipt
from apps.payroll.models import PayrollRun, Payslip

from . import serializers as s
from .permissions import IsAcademyAdmin, IsAdminOrCoach, IsAdminOrParent, IsAdminOrReadOnly


def as_drf_error(exc):
    if isinstance(exc, DjangoPermissionDenied):
        return PermissionDenied(str(exc))
    return ValidationError({"detail": exc.messages if hasattr(exc, "messages") else [str(exc)]})


def summary_json(summary):
    pct = summary["percentage"]
    return {**summary, "percentage": str(pct) if pct is not None else None}


class AuditActorMixin:
    """Make the authenticated API user the actor for audit entries."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self._audit_token = set_actor(request.user)

    def finalize_response(self, request, response, *args, **kwargs):
        token = getattr(self, "_audit_token", None)
        if token is not None:
            reset_actor(token)
            self._audit_token = None
        return super().finalize_response(request, response, *args, **kwargs)

    def handle_exception(self, exc):
        if isinstance(exc, (DjangoValidationError, DjangoPermissionDenied)):
            exc = as_drf_error(exc)
        return super().handle_exception(exc)


class MeView(AuditActorMixin, APIView):
    def get(self, request):
        user = request.user
        data = {
            "id": user.id,
            "username": user.username,
            "name": user.get_full_name(),
            "role": access.role_of(user),
        }
        parent = access.parent_of(user)
        if parent:
            data["parent"] = s.ParentSerializer(parent).data
            data["children"] = [{"id": c.id, "student_no": c.student_no, "full_name": c.full_name}
                                for c in access.students_for(user)]
        coach = access.coach_of(user)
        if coach:
            data["coach"] = s.CoachSerializer(coach).data
            data["classes"] = [{"id": c.id, "name": c.name} for c in access.classes_for(user)]
            data["substitute_sessions"] = [
                {"session": slot.session_id, "access_ends_at": slot.access_ends_at}
                for slot in access.open_substitute_slots(coach)
            ]
        return Response(data)


class NoDestroyModelViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                            mixins.ListModelMixin, viewsets.GenericViewSet):
    pass


class ParentViewSet(AuditActorMixin, NoDestroyModelViewSet):
    permission_classes = [IsAcademyAdmin]
    serializer_class = s.ParentSerializer
    queryset = Parent.objects.all()


class CoachViewSet(AuditActorMixin, NoDestroyModelViewSet):
    permission_classes = [IsAcademyAdmin]
    serializer_class = s.CoachSerializer
    queryset = Coach.objects.all()


class StudentViewSet(AuditActorMixin, NoDestroyModelViewSet):
    """Admins: full CRUD (no delete). Parents: their own children.
    Coaches: current members of their classes (training info + emergency contacts)."""

    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        qs = access.students_for(self.request.user).prefetch_related("guardianships__parent")
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(full_name__icontains=search) | Q(student_no__icontains=search) | Q(chinese_name__icontains=search))
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs.order_by("full_name")

    def get_serializer_class(self):
        user = self.request.user
        if access.is_admin(user):
            return s.StudentSerializer
        if access.parent_of(user):
            return s.ParentStudentSerializer
        return s.RosterStudentSerializer

    @action(detail=True, methods=["get"], url_path="attendance-summary")
    def attendance_summary(self, request, pk=None):
        student = self.get_object()
        params = request.query_params
        classes = access.classes_for(request.user)
        class_id = params.get("class")
        training_class = get_object_or_404(classes, pk=class_id) if class_id else None
        summary = attendance_services.student_summary(student, training_class, params.get("start"), params.get("end"))
        return Response({"student": student.id, **summary_json(summary)})

    @action(detail=True, methods=["get"], permission_classes=[IsAcademyAdmin])
    def history(self, request, pk=None):
        student = self.get_object()
        entries = list(history_for(student))
        for enrollment in student.enrollments.all():
            entries.extend(history_for(enrollment))
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return Response({
            "status_history": [
                {"previous_status": h.previous_status, "status": h.status, "effective_date": h.effective_date,
                 "reason": h.reason} for h in student.status_history.all()
            ],
            "enrollments": s.EnrollmentSerializer(student.enrollments.all(), many=True).data,
            "changes": s.AuditLogSerializer(entries, many=True).data,
        })

    @action(detail=True, methods=["post"], url_path="change-status", permission_classes=[IsAcademyAdmin])
    def change_status(self, request, pk=None):
        student = self.get_object()
        new_status = request.data.get("status")
        if new_status not in Student.Status.values:
            raise ValidationError({"status": "Invalid status."})
        academy_services.change_student_status(student, new_status, request.data.get("reason", ""), request.user)
        return Response(s.StudentSerializer(student).data)

    @action(detail=True, methods=["post"], url_path="guardians", permission_classes=[IsAcademyAdmin])
    def add_guardian(self, request, pk=None):
        student = self.get_object()
        serializer = s.GuardianSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(student=student)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class EnrollmentViewSet(AuditActorMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                        viewsets.GenericViewSet):
    permission_classes = [IsAcademyAdmin]
    serializer_class = s.EnrollmentSerializer
    queryset = Enrollment.objects.select_related("student", "training_class", "team", "coach")

    def perform_create(self, serializer):
        data = serializer.validated_data
        serializer.instance = academy_services.enroll(
            data["student"], data["training_class"], data.get("start_date"), data.get("team"), data.get("coach"),
            self.request.user,
        )

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        enrollment = self.get_object()
        academy_services.end_enrollment(enrollment, request.data.get("end_date") or None,
                                        request.data.get("reason", ""), request.user)
        return Response(self.get_serializer(enrollment).data)

    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        enrollment = self.get_object()
        new_class = get_object_or_404(TrainingClass, pk=request.data.get("training_class"))
        new = academy_services.transfer(enrollment, new_class, request.data.get("date") or None, actor=request.user,
                                        reason=request.data.get("reason", "Class transfer"))
        return Response(self.get_serializer(new).data, status=status.HTTP_201_CREATED)


class TrainingClassViewSet(AuditActorMixin, NoDestroyModelViewSet):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = s.TrainingClassSerializer

    def get_queryset(self):
        return access.classes_for(self.request.user).select_related("program", "team").prefetch_related("schedules")

    @action(detail=True, methods=["get"], permission_classes=[IsAdminOrCoach])
    def students(self, request, pk=None):
        training_class = self.get_object()
        students = training_class.enrollments.active_on(timezone.localdate())
        qs = Student.objects.filter(enrollments__in=students).distinct().prefetch_related("guardianships__parent")
        return Response(s.RosterStudentSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"], url_path="generate-sessions", permission_classes=[IsAcademyAdmin])
    def generate_sessions(self, request, pk=None):
        from datetime import date

        training_class = self.get_object()
        try:
            start = date.fromisoformat(request.data["start"])
            end = date.fromisoformat(request.data["end"])
        except (KeyError, ValueError):
            raise ValidationError({"detail": "Provide start and end as YYYY-MM-DD."})
        created = academy_services.generate_sessions(training_class, start, end, request.user)
        return Response({"created": len(created)}, status=status.HTTP_201_CREATED)


class TrainingSessionViewSet(AuditActorMixin, NoDestroyModelViewSet):
    """Coaches see their classes' sessions; substitutes see only the session
    they cover, while their access window is open."""

    permission_classes = [IsAdminOrReadOnly]
    serializer_class = s.TrainingSessionSerializer

    def get_queryset(self):
        qs = access.sessions_for(self.request.user).select_related("training_class").prefetch_related("coach_slots__coach")
        params = self.request.query_params
        if params.get("date"):
            qs = qs.filter(date=params["date"])
        if params.get("start"):
            qs = qs.filter(date__gte=params["start"])
        if params.get("end"):
            qs = qs.filter(date__lte=params["end"])
        if params.get("class"):
            qs = qs.filter(training_class_id=params["class"])
        return qs

    def _session_for_staff(self):
        session = self.get_object()
        if access.parent_of(self.request.user):
            raise PermissionDenied("Parents cannot view class rosters.")
        return session

    @action(detail=True, methods=["get"], permission_classes=[IsAdminOrCoach])
    def roster(self, request, pk=None):
        session = self._session_for_staff()
        students = session.roster().prefetch_related("guardianships__parent")
        return Response(s.RosterStudentSerializer(students, many=True).data)

    @action(detail=True, methods=["get", "post"], permission_classes=[IsAdminOrCoach])
    def attendance(self, request, pk=None):
        session = self._session_for_staff()
        if request.method == "GET":
            records = session.attendance.select_related("student", "recorded_by", "session__training_class")
            return Response({
                "session": session.id,
                "summary": summary_json(attendance_services.session_summary(session)),
                "records": s.AttendanceRecordSerializer(records, many=True).data,
            })
        payload = s.AttendanceSubmitSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        roster = {st.id: st for st in session.roster()}
        saved = []
        for entry in payload.validated_data["records"]:
            student = roster.get(entry["student"])
            if student is None:
                raise ValidationError({"detail": f"Student {entry['student']} is not on this session's roster."})
            saved.append(attendance_services.mark_attendance(
                session, student, entry["status"], request.user, entry["remarks"], payload.validated_data["reason"]
            ))
        return Response(s.AttendanceRecordSerializer(saved, many=True).data)

    @action(detail=True, methods=["post"], url_path="assign-substitute", permission_classes=[IsAcademyAdmin])
    def assign_substitute(self, request, pk=None):
        session = self.get_object()
        substitute = get_object_or_404(Coach, pk=request.data.get("substitute"))
        replaces = get_object_or_404(Coach, pk=request.data["replaces"]) if request.data.get("replaces") else None
        slot = academy_services.assign_substitute(session, substitute, replaces, request.user,
                                                  request.data.get("reason", ""))
        return Response(s.SessionCoachSerializer(slot).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="revoke-substitute", permission_classes=[IsAcademyAdmin])
    def revoke_substitute(self, request, pk=None):
        session = self.get_object()
        slot = get_object_or_404(SessionCoach, session=session, coach_id=request.data.get("substitute"),
                                 role=SessionCoach.Role.SUBSTITUTE)
        academy_services.revoke_substitute(slot, request.user, request.data.get("reason", ""))
        return Response(s.SessionCoachSerializer(slot).data)


class AttendanceRecordViewSet(AuditActorMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = s.AttendanceRecordSerializer

    def get_queryset(self):
        user = self.request.user
        qs = AttendanceRecord.objects.select_related("student", "session__training_class", "recorded_by")
        if access.is_admin(user):
            pass
        elif access.parent_of(user):
            qs = qs.filter(student__in=access.students_for(user))
        elif access.coach_of(user):
            qs = qs.filter(session__in=access.sessions_for(user))
        else:
            return qs.none()
        params = self.request.query_params
        if params.get("student"):
            qs = qs.filter(student_id=params["student"])
        if params.get("start"):
            qs = qs.filter(session__date__gte=params["start"])
        if params.get("end"):
            qs = qs.filter(session__date__lte=params["end"])
        return qs.order_by("-session__date", "student__full_name")

    @action(detail=True, methods=["get"], permission_classes=[IsAdminOrCoach])
    def history(self, request, pk=None):
        return Response(s.AuditLogSerializer(history_for(self.get_object()), many=True).data)


class ChargeViewSet(AuditActorMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                    viewsets.GenericViewSet):
    """Fees and charges. Admins manage; parents see their children's. Coaches: no access."""

    permission_classes = [IsAdminOrParent]
    serializer_class = s.ChargeSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Charge.objects.select_related("student")
        if not access.is_admin(user):
            qs = qs.filter(student__in=access.students_for(user))
        params = self.request.query_params
        if params.get("student"):
            qs = qs.filter(student_id=params["student"])
        if params.get("status"):
            qs = qs.filter(status__in=params["status"].split(","))
        if params.get("outstanding"):
            qs = qs.filter(status__in=[Charge.Status.UNPAID, Charge.Status.PARTIAL])
        return qs

    def create(self, request, *args, **kwargs):
        if not access.is_admin(request.user):
            raise PermissionDenied()
        data = s.ChargeCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        charge = finance_services.add_charge(actor=request.user, **data.validated_data)
        return Response(s.ChargeSerializer(charge).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsAcademyAdmin])
    def cancel(self, request, pk=None):
        reason = s.ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        charge = finance_services.cancel_charge(self.get_object(), reason.validated_data["reason"], request.user,
                                                waive=bool(request.data.get("waive")))
        return Response(s.ChargeSerializer(charge).data)

    @action(detail=False, methods=["post"], url_path="generate-monthly", permission_classes=[IsAcademyAdmin])
    def generate_monthly(self, request):
        try:
            year, month = int(request.data["year"]), int(request.data["month"])
        except (KeyError, ValueError):
            raise ValidationError({"detail": "Provide year and month."})
        created = finance_services.generate_tuition_charges(year, month, request.user)
        return Response({"created": len(created)}, status=status.HTTP_201_CREATED)


class PaymentViewSet(AuditActorMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     viewsets.GenericViewSet):
    permission_classes = [IsAdminOrParent]
    serializer_class = s.PaymentSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Payment.objects.select_related("receipt").prefetch_related("allocations__charge__student")
        if access.is_admin(user):
            return qs
        parent = access.parent_of(user)
        return qs.filter(Q(parent=parent) | Q(allocations__charge__student__in=access.students_for(user))).distinct()

    def create(self, request, *args, **kwargs):
        if not access.is_admin(request.user):
            raise PermissionDenied("Payments are recorded by the academy.")
        data = s.PaymentCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        payment, _ = finance_services.record_payment(
            payer_name=v["payer_name"], amount=v["amount"], method=v["method"],
            allocations=[(a["charge"], a["amount"]) for a in v["allocations"]],
            actor=request.user, parent=v.get("parent"), reference=v["reference"],
            received_on=v.get("received_on"), notes=v["notes"],
        )
        return Response(s.PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsAcademyAdmin])
    def void(self, request, pk=None):
        reason = s.ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        payment = finance_services.void_payment(self.get_object(), reason.validated_data["reason"], request.user)
        return Response(s.PaymentSerializer(payment).data)


class ReceiptViewSet(AuditActorMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdminOrParent]
    serializer_class = s.ReceiptSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Receipt.objects.select_related("payment")
        if access.is_admin(user):
            return qs
        return qs.filter(
            Q(payment__parent=access.parent_of(user))
            | Q(payment__allocations__charge__student__in=access.students_for(user))
        ).distinct()


class CompetitionViewSet(AuditActorMixin, NoDestroyModelViewSet):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = s.CompetitionSerializer

    def get_queryset(self):
        qs = Competition.objects.prefetch_related("events")
        if not access.is_admin(self.request.user):
            qs = qs.exclude(status=Competition.Status.DRAFT)
        return qs


class CompetitionEventViewSet(AuditActorMixin, NoDestroyModelViewSet):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = s.CompetitionEventSerializer

    def get_queryset(self):
        qs = CompetitionEvent.objects.select_related("competition")
        if not access.is_admin(self.request.user):
            qs = qs.exclude(competition__status=Competition.Status.DRAFT)
        if self.request.query_params.get("competition"):
            qs = qs.filter(competition_id=self.request.query_params["competition"])
        return qs


class CompetitionRegistrationViewSet(AuditActorMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                                     mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Parents register their own children through the Parent App."""

    serializer_class = s.CompetitionRegistrationSerializer

    def get_queryset(self):
        user = self.request.user
        qs = CompetitionRegistration.objects.select_related("event__competition", "student", "charge", "result")
        if not access.is_admin(user):
            qs = qs.filter(student__in=access.students_for(user))
        if self.request.query_params.get("competition"):
            qs = qs.filter(event__competition_id=self.request.query_params["competition"])
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student = serializer.validated_data["student"]
        event = serializer.validated_data["event"]
        if not access.is_admin(request.user) and not access.is_parent_of(request.user, student):
            raise PermissionDenied("You can only register your own children.")
        if event.competition.status == Competition.Status.DRAFT and not access.is_admin(request.user):
            raise ValidationError({"detail": "This competition is not open."})
        registration = competition_services.register(student, event, request.user,
                                                     serializer.validated_data.get("notes", ""))
        return Response(self.get_serializer(registration).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminOrParent])
    def withdraw(self, request, pk=None):
        registration = competition_services.withdraw(self.get_object(), request.user, request.data.get("reason", ""))
        return Response(self.get_serializer(registration).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAcademyAdmin])
    def confirm(self, request, pk=None):
        registration = self.get_object()
        registration.status = CompetitionRegistration.Status.CONFIRMED
        registration.save()
        return Response(self.get_serializer(registration).data)


class CompetitionResultViewSet(AuditActorMixin, NoDestroyModelViewSet):
    permission_classes = [IsAdminOrReadOnly]
    serializer_class = s.CompetitionResultSerializer

    def get_queryset(self):
        user = self.request.user
        qs = CompetitionResult.objects.select_related("registration__student", "registration__event__competition")
        if not access.is_admin(user):
            qs = qs.filter(registration__student__in=access.students_for(user))
        if self.request.query_params.get("competition"):
            qs = qs.filter(registration__event__competition_id=self.request.query_params["competition"])
        return qs


class PayslipViewSet(AuditActorMixin, viewsets.ReadOnlyModelViewSet):
    """Admins see all payslips; a coach sees only their own finalized payslips."""

    permission_classes = [IsAdminOrCoach]
    serializer_class = s.PayslipSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Payslip.objects.select_related("run", "coach").prefetch_related("lines")
        if access.is_admin(user):
            return qs
        return qs.filter(coach=access.coach_of(user), run__status=PayrollRun.Status.FINALIZED)
