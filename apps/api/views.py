"""REST API for the admin, Parent, Coach and Student apps.

Authorization is two-layered on every endpoint:
1. ``capabilities`` (per action) + ``HasCapability``: may this user perform the action at all?
2. ``get_queryset`` built from ``apps.academy.access``: which records?
Records outside the caller's scope return 404.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academy import access
from apps.academy import services as academy_services
from apps.academy.models import Enrollment, Family, SessionCoach, Student, TrainingClass
from apps.accounts import services as account_services
from apps.accounts.capabilities import Cap, can, capabilities_of
from apps.accounts.models import Coach, Parent
from apps.attendance import services as attendance_services
from apps.attendance.models import AttendanceRecord
from apps.audit.context import reset_actor, set_actor
from apps.audit.utils import history_for
from apps.competitions import services as competition_services
from apps.competitions.models import Competition, CompetitionEvent, CompetitionRegistration, CompetitionResult
from apps.finance import access as finance_access
from apps.finance import services as finance_services
from apps.finance.models import Charge, Invoice
from apps.payroll import services as payroll_services
from apps.payroll.models import PayrollRun, Payslip

from . import serializers as s
from .permissions import HasCapability

READ = ("list", "retrieve")
WRITE = ("create", "update", "partial_update")


def caps(**actions):
    """Build a capability map; ``read=`` and ``write=`` expand to the standard actions."""
    result = {}
    for key, requirement in actions.items():
        targets = READ if key == "read" else WRITE if key == "write" else (key,)
        for target in targets:
            result[target] = requirement
    return result


def as_drf_error(exc):
    if isinstance(exc, DjangoPermissionDenied):
        return PermissionDenied(str(exc))
    return ValidationError({"detail": exc.messages if hasattr(exc, "messages") else [str(exc)]})


def id_param(params, name):
    """A numeric id from the query string, or None. Anything else is a 400, never a 500."""
    value = params.get(name)
    if value in (None, ""):
        return None
    if not str(value).isdigit():
        raise ValidationError({name: "Must be a numeric id."})
    return int(value)


def summary_json(summary):
    pct = summary["percentage"]
    return {**summary, "percentage": str(pct) if pct is not None else None}


class ApiViewMixin:
    """Capability permission, audit actor and Django-exception translation for every API view."""

    permission_classes = [HasCapability]
    capabilities = {}

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


class NoDestroyModelViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                            mixins.ListModelMixin, viewsets.GenericViewSet):
    pass


class MeView(ApiViewMixin, APIView):
    """Any signed-in user: who am I, which roles and capabilities do I hold."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        roles = account_services.roles_for_display(user)
        data = {
            "id": user.id,
            "username": user.username,
            "name": user.get_full_name(),
            "role": roles[0] if roles else None,
            "roles": roles,
            "capabilities": sorted(capabilities_of(user)),
        }
        parent = access.parent_of(user)
        if parent:
            data["parent"] = s.ParentSerializer(parent, context={"request": request}).data
            data["children"] = [{"id": c.id, "student_no": c.student_no, "full_name": c.full_name}
                                for c in access.children_for(user).order_by("full_name")]
        coach = access.coach_of(user)
        if coach:
            data["coach"] = s.CoachSerializer(coach, context={"request": request}).data
            data["classes"] = [{"id": c.id, "name": c.name} for c in access.roster_classes_for(user)]
            data["substitute_sessions"] = [
                {"session": slot.session_id, "access_ends_at": slot.access_ends_at}
                for slot in access.open_substitute_slots(coach)
            ]
        student = access.student_of(user)
        if student:
            data["student"] = {"id": student.id, "student_no": student.student_no, "full_name": student.full_name}
        return Response(data)


class UserViewSet(ApiViewMixin, viewsets.ReadOnlyModelViewSet):
    """User accounts and their roles. Role changes need ``roles.manage`` and a reason."""

    serializer_class = s.UserSerializer
    capabilities = caps(read=Cap.USERS_VIEW, roles=Cap.ROLES_MANAGE)

    def get_queryset(self):
        return get_user_model().objects.prefetch_related("groups").order_by("username")

    @action(detail=True, methods=["post"])
    def roles(self, request, pk=None):
        user = self.get_object()
        payload = s.RoleChangeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        account_services.set_roles(user, payload.validated_data["roles"], request.user, payload.validated_data["reason"])
        return Response(self.get_serializer(user).data)


class ParentViewSet(ApiViewMixin, NoDestroyModelViewSet):
    serializer_class = s.ParentSerializer
    queryset = Parent.objects.all()
    capabilities = caps(read=Cap.PARENTS_VIEW_ALL, write=Cap.PARENTS_MANAGE)


class CoachViewSet(ApiViewMixin, NoDestroyModelViewSet):
    """Bank / EPF / SOCSO details are only included for ``coaches.bank_details``."""

    serializer_class = s.CoachSerializer
    queryset = Coach.objects.all()
    capabilities = caps(read=Cap.COACHES_VIEW_ALL, write=(Cap.COACHES_MANAGE, Cap.COACHES_BANK_DETAILS))


STUDENT_SERIALIZERS = {
    access.FULL: s.StudentSerializer,
    access.OWN: s.OwnStudentSerializer,
    access.ROSTER: s.RosterStudentSerializer,
    access.DIRECTORY: s.StudentDirectorySerializer,
}


class StudentViewSet(ApiViewMixin, NoDestroyModelViewSet):
    """Staff: full records. Parents: own children. Students: themselves.
    Coaches: current members of their classes (training info + emergency contacts).
    Finance: directory (names and guardian contacts).

    Each record is rendered at the level matching how the caller relates to it,
    so a coach who is also a parent sees their own child as a parent and the
    rest of their roster as a coach."""

    serializer_class = s.StudentSerializer
    capabilities = caps(
        read=(Cap.STUDENTS_VIEW_ALL, Cap.STUDENTS_VIEW_DIRECTORY, Cap.STUDENTS_VIEW_ASSIGNED,
              Cap.STUDENTS_VIEW_OWN_CHILDREN, Cap.STUDENTS_VIEW_SELF),
        write=Cap.STUDENTS_MANAGE,
        attendance_summary=(Cap.STUDENTS_VIEW_ALL, Cap.STUDENTS_VIEW_ASSIGNED, Cap.STUDENTS_VIEW_OWN_CHILDREN,
                            Cap.STUDENTS_VIEW_SELF),
        history=Cap.STUDENTS_HISTORY,
        change_status=Cap.STUDENTS_MANAGE,
        add_guardian=Cap.STUDENTS_MANAGE,
    )

    def scope(self):
        if not hasattr(self, "_scope"):
            self._scope = access.student_scope(self.request.user)
        return self._scope

    def get_queryset(self):
        qs = self.scope().queryset().prefetch_related("guardianships__parent")
        if self.action == "attendance_summary":
            # Attendance is never visible through the finance directory.
            scope = self.scope()
            if not scope.full:
                qs = qs.filter(id__in=scope.child_ids | scope.roster_ids | ({scope.self_id} if scope.self_id else set()))
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(full_name__icontains=search) | Q(student_no__icontains=search) | Q(chinese_name__icontains=search))
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs.order_by("full_name")

    def represent(self, student):
        serializer_class = STUDENT_SERIALIZERS[self.scope().level_for(student.id)]
        return serializer_class(student, context=self.get_serializer_context()).data

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response([self.represent(st) for st in page])
        return Response([self.represent(st) for st in queryset])

    def retrieve(self, request, *args, **kwargs):
        return Response(self.represent(self.get_object()))

    @action(detail=True, methods=["get"], url_path="attendance-summary")
    def attendance_summary(self, request, pk=None):
        student = self.get_object()
        params = request.query_params
        class_id = id_param(params, "class")
        training_class = get_object_or_404(access.classes_for(request.user), pk=class_id) if class_id else None
        summary = attendance_services.student_summary(student, training_class, params.get("start"), params.get("end"))
        return Response({"student": student.id, **summary_json(summary)})

    @action(detail=True, methods=["get"])
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

    @action(detail=True, methods=["post"], url_path="change-status")
    def change_status(self, request, pk=None):
        student = self.get_object()
        new_status = request.data.get("status")
        if new_status not in Student.Status.values:
            raise ValidationError({"status": "Invalid status."})
        academy_services.change_student_status(student, new_status, request.data.get("reason", ""), request.user)
        return Response(s.StudentSerializer(student).data)

    @action(detail=True, methods=["post"], url_path="guardians")
    def add_guardian(self, request, pk=None):
        student = self.get_object()
        serializer = s.GuardianSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(student=student)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class EnrollmentViewSet(ApiViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                        viewsets.GenericViewSet):
    serializer_class = s.EnrollmentSerializer
    queryset = Enrollment.objects.select_related("student", "training_class", "team", "coach")
    capabilities = caps(read=Cap.STUDENTS_VIEW_ALL, create=Cap.STUDENTS_MANAGE, end=Cap.STUDENTS_MANAGE,
                        transfer=Cap.STUDENTS_MANAGE)

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


class TrainingClassViewSet(ApiViewMixin, NoDestroyModelViewSet):
    serializer_class = s.TrainingClassSerializer
    capabilities = caps(
        read=(Cap.CLASSES_VIEW_ALL, Cap.CLASSES_VIEW_ASSIGNED, Cap.CLASSES_VIEW_OWN_CHILDREN, Cap.CLASSES_VIEW_SELF),
        write=Cap.CLASSES_MANAGE,
        students=(Cap.ROSTER_VIEW_ALL, Cap.ROSTER_VIEW_ASSIGNED),
        generate_sessions=Cap.SESSIONS_MANAGE,
    )

    def get_queryset(self):
        if self.action == "students":
            qs = access.roster_classes_for(self.request.user)
        else:
            qs = access.classes_for(self.request.user)
        return qs.select_related("program", "team").prefetch_related("schedules")

    @action(detail=True, methods=["get"])
    def students(self, request, pk=None):
        training_class = self.get_object()
        members = training_class.enrollments.active_on(timezone.localdate())
        qs = Student.objects.filter(enrollments__in=members).distinct().prefetch_related("guardianships__parent")
        return Response(s.RosterStudentSerializer(qs, many=True).data)

    @action(detail=True, methods=["post"], url_path="generate-sessions")
    def generate_sessions(self, request, pk=None):
        training_class = self.get_object()
        try:
            start = date.fromisoformat(request.data["start"])
            end = date.fromisoformat(request.data["end"])
        except (KeyError, ValueError):
            raise ValidationError({"detail": "Provide start and end as YYYY-MM-DD."})
        created = academy_services.generate_sessions(training_class, start, end, request.user)
        return Response({"created": len(created)}, status=status.HTTP_201_CREATED)


class TrainingSessionViewSet(ApiViewMixin, NoDestroyModelViewSet):
    """Coaches see their classes' sessions; substitutes see only the session
    they cover, while their access window is open. Rosters and attendance are
    only reachable as staff or coach, never through a parent/student role."""

    serializer_class = s.TrainingSessionSerializer
    capabilities = caps(
        read=(Cap.SESSIONS_VIEW_ALL, Cap.SESSIONS_VIEW_ASSIGNED, Cap.SESSIONS_VIEW_OWN_CHILDREN, Cap.SESSIONS_VIEW_SELF),
        write=Cap.SESSIONS_MANAGE,
        roster=(Cap.ROSTER_VIEW_ALL, Cap.ROSTER_VIEW_ASSIGNED),
        attendance=(Cap.ATTENDANCE_VIEW_ALL, Cap.ATTENDANCE_VIEW_ASSIGNED),
        assign_substitute=Cap.SUBSTITUTE_ASSIGN,
        revoke_substitute=Cap.SUBSTITUTE_REVOKE,
        cancel=Cap.SESSIONS_MANAGE,
        reinstate=Cap.SESSIONS_MANAGE,
        reschedule=Cap.SESSIONS_MANAGE,
        reassign_coach=Cap.SESSIONS_MANAGE,
    )
    STAFF_ACTIONS = {"roster", "attendance"}

    def get_queryset(self):
        user = self.request.user
        qs = access.roster_sessions_for(user) if self.action in self.STAFF_ACTIONS else access.sessions_for(user)
        qs = qs.select_related("training_class").prefetch_related("coach_slots__coach")
        params = self.request.query_params
        if params.get("date"):
            qs = qs.filter(date=params["date"])
        if params.get("start"):
            qs = qs.filter(date__gte=params["start"])
        if params.get("end"):
            qs = qs.filter(date__lte=params["end"])
        if id_param(params, "class"):
            qs = qs.filter(training_class_id=id_param(params, "class"))
        return qs

    @action(detail=True, methods=["get"])
    def roster(self, request, pk=None):
        session = self.get_object()
        students = session.roster().prefetch_related("guardianships__parent")
        return Response(s.RosterStudentSerializer(students, many=True).data)

    @action(detail=True, methods=["get", "post"])
    def attendance(self, request, pk=None):
        """GET: the attendance sheet (every expected student, UNMARKED if not yet
        marked) with expected/marked/unmarked counts. POST: record attendance for
        some or all expected students, all or nothing, through the service."""
        session = self.get_object()
        if request.method == "GET":
            summary = attendance_services.session_summary(session)
            records = session.attendance.select_related("student", "recorded_by", "session__training_class")
            return Response({
                "session": session.id,
                "state": attendance_services.session_state(session, summary),
                "coach_edit_deadline": attendance_services.coach_edit_deadline(session),
                "summary": summary_json(summary),
                "sheet": [
                    {"student": row["student"].id, "student_name": row["student"].full_name,
                     "status": row["status"], "remarks": row["remarks"]}
                    for row in attendance_services.session_sheet(session)
                ],
                "records": s.AttendanceRecordSerializer(records, many=True).data,
            })
        if not can(request.user, (Cap.ATTENDANCE_TAKE_ANY, Cap.ATTENDANCE_TAKE_ASSIGNED, Cap.ATTENDANCE_CORRECT)):
            raise PermissionDenied()
        payload = s.AttendanceSubmitSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        entries = [(e["student"], e["status"], e["remarks"]) for e in payload.validated_data["records"]]
        saved = attendance_services.record_session_attendance(
            session, entries, request.user, payload.validated_data["reason"])
        return Response(s.AttendanceRecordSerializer(saved, many=True).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        session = academy_services.cancel_session(self.get_object(), request.user, request.data.get("reason", ""))
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["post"])
    def reinstate(self, request, pk=None):
        session = academy_services.reinstate_session(self.get_object(), request.user, request.data.get("reason", ""))
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["post"])
    def reschedule(self, request, pk=None):
        session = self.get_object()
        payload = s.RescheduleSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        session = academy_services.reschedule_session(
            session, request.user, data["reason"], date=data.get("date"), start_time=data.get("start_time"),
            end_time=data.get("end_time"), training_class=data.get("training_class"))
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["post"], url_path="reassign-coach")
    def reassign_coach(self, request, pk=None):
        session = self.get_object()
        from_coach = get_object_or_404(Coach, pk=self._coach_id(request, "from_coach"))
        to_coach = get_object_or_404(Coach, pk=self._coach_id(request, "to_coach"))
        slot = academy_services.reassign_regular_coach(session, from_coach, to_coach, request.user,
                                                       request.data.get("reason", ""))
        return Response(s.SessionCoachSerializer(slot).data)

    @staticmethod
    def _coach_id(request, field, required=True):
        value = request.data.get(field)
        if value in (None, "") and not required:
            return None
        if not str(value).isdigit():
            raise ValidationError({field: "A coach id is required."})
        return int(value)

    @action(detail=True, methods=["post"], url_path="assign-substitute")
    def assign_substitute(self, request, pk=None):
        session = self.get_object()
        substitute = get_object_or_404(Coach, pk=self._coach_id(request, "substitute"))
        replaces_id = self._coach_id(request, "replaces", required=False)
        replaces = get_object_or_404(Coach, pk=replaces_id) if replaces_id else None
        slot = academy_services.assign_substitute(session, substitute, replaces, request.user,
                                                  request.data.get("reason", ""))
        return Response(s.SessionCoachSerializer(slot).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="revoke-substitute")
    def revoke_substitute(self, request, pk=None):
        session = self.get_object()
        slots = session.coach_slots.substitutes().filter(coach_id=self._coach_id(request, "substitute"))
        # The active authorization if there is one; otherwise the latest, so the
        # service can say it is already revoked or cancelled.
        slot = slots.filter(status=SessionCoach.Status.ASSIGNED).first() or slots.order_by("-id").first()
        if slot is None:
            raise NotFound("No substitute authorization for that coach on this session.")
        slot = academy_services.revoke_substitute(slot, request.user, request.data.get("reason", ""))
        return Response(s.SessionCoachSerializer(slot).data)


class AttendanceRecordViewSet(ApiViewMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = s.AttendanceRecordSerializer
    capabilities = caps(
        read=(Cap.ATTENDANCE_VIEW_ALL, Cap.ATTENDANCE_VIEW_ASSIGNED, Cap.ATTENDANCE_VIEW_OWN_CHILDREN,
              Cap.ATTENDANCE_VIEW_SELF),
        history=(Cap.ATTENDANCE_VIEW_ALL, Cap.ATTENDANCE_VIEW_ASSIGNED),
    )

    def get_queryset(self):
        user = self.request.user
        qs = AttendanceRecord.objects.select_related("student", "session__training_class", "recorded_by")
        staff_sessions = access.roster_sessions_for(user)
        if self.action == "history":
            # Change history is a staff/coach view, never reachable as a parent.
            qs = qs.filter(session__in=staff_sessions)
        elif not can(user, Cap.ATTENDANCE_VIEW_ALL):
            scope = Q(session__in=staff_sessions)
            if can(user, (Cap.ATTENDANCE_VIEW_OWN_CHILDREN, Cap.ATTENDANCE_VIEW_SELF)):
                scope |= Q(student_id__in=self._own_student_ids(user))
            qs = qs.filter(scope)
        params = self.request.query_params
        if id_param(params, "student"):
            qs = qs.filter(student_id=id_param(params, "student"))
        if params.get("start"):
            qs = qs.filter(session__date__gte=params["start"])
        if params.get("end"):
            qs = qs.filter(session__date__lte=params["end"])
        return qs.order_by("-session__date", "student__full_name")

    @staticmethod
    def _own_student_ids(user):
        ids = set()
        if can(user, Cap.ATTENDANCE_VIEW_OWN_CHILDREN):
            ids |= set(access.children_for(user).values_list("id", flat=True))
        student = access.student_of(user)
        if student and can(user, Cap.ATTENDANCE_VIEW_SELF):
            ids.add(student.id)
        return ids

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        return Response(s.AuditLogSerializer(history_for(self.get_object()), many=True).data)


def family_finance_filter(user, student_field):
    """Finance rows a parent may see: only their own children, never students
    they coach. (Student logins get finance access with the invoice phase.)"""
    return Q(**{f"{student_field}__in": access.children_for(user)})


class ChargeViewSet(ApiViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                    viewsets.GenericViewSet):
    """Fees and charges. Finance staff manage; admins view; parents see their children's."""

    serializer_class = s.ChargeSerializer
    capabilities = caps(
        read=(Cap.FINANCE_VIEW_ALL, Cap.FINANCE_VIEW_OWN_CHILDREN),
        create=Cap.FINANCE_CHARGES_MANAGE,
        cancel=Cap.FINANCE_CHARGES_MANAGE,
        generate_monthly=Cap.FINANCE_CHARGES_MANAGE,
    )

    def get_queryset(self):
        user = self.request.user
        qs = Charge.objects.select_related("student")
        if not can(user, Cap.FINANCE_VIEW_ALL):
            qs = qs.filter(family_finance_filter(user, "student"))
        params = self.request.query_params
        if id_param(params, "student"):
            qs = qs.filter(student_id=id_param(params, "student"))
        if params.get("status"):
            qs = qs.filter(status__in=params["status"].split(","))
        if params.get("outstanding"):
            qs = qs.filter(status__in=[Charge.Status.UNPAID, Charge.Status.PARTIAL])
        return qs

    def create(self, request, *args, **kwargs):
        data = s.ChargeCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        charge = finance_services.add_charge(actor=request.user, **data.validated_data)
        return Response(s.ChargeSerializer(charge).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        reason = s.ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        charge = finance_services.cancel_charge(self.get_object(), reason.validated_data["reason"], request.user,
                                                waive=bool(request.data.get("waive")))
        return Response(s.ChargeSerializer(charge).data)

    @action(detail=False, methods=["post"], url_path="generate-monthly")
    def generate_monthly(self, request):
        try:
            year, month = int(request.data["year"]), int(request.data["month"])
        except (KeyError, ValueError):
            raise ValidationError({"detail": "Provide year and month."})
        created = finance_services.generate_tuition_charges(year, month, request.user)
        return Response({"created": len(created)}, status=status.HTTP_201_CREATED)


class FamilyViewSet(ApiViewMixin, NoDestroyModelViewSet):
    """Households for invoicing. Staff group siblings explicitly; parents see
    the families their own children belong to."""

    serializer_class = s.FamilySerializer
    capabilities = caps(read=(Cap.STUDENTS_VIEW_ALL, Cap.FINANCE_VIEW_ALL, Cap.FINANCE_VIEW_OWN_CHILDREN),
                        write=Cap.STUDENTS_MANAGE)

    def get_queryset(self):
        user = self.request.user
        if can(user, (Cap.STUDENTS_VIEW_ALL, Cap.FINANCE_VIEW_ALL)):
            return Family.objects.all()
        return finance_access.families_for(user)


class InvoiceViewSet(ApiViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     viewsets.GenericViewSet):
    """Family invoices. Finance manages (draft / issue / void); admins view;
    parents see their own families' issued invoices."""

    serializer_class = s.InvoiceSerializer
    capabilities = caps(
        read=(Cap.FINANCE_VIEW_ALL, Cap.FINANCE_VIEW_OWN_CHILDREN),
        create=Cap.FINANCE_INVOICES_MANAGE,
        issue=Cap.FINANCE_INVOICES_MANAGE,
        void=Cap.FINANCE_INVOICES_MANAGE,
        generate_drafts=Cap.FINANCE_INVOICES_MANAGE,
    )

    def get_queryset(self):
        qs = finance_access.invoices_for(self.request.user).prefetch_related("items")
        params = self.request.query_params
        if id_param(params, "family"):
            qs = qs.filter(family_id=id_param(params, "family"))
        if params.get("status"):
            qs = qs.filter(status__in=params["status"].split(","))
        if params.get("outstanding"):
            qs = qs.filter(status__in=Invoice.OPEN_FOR_PAYMENT)
        return qs

    def create(self, request, *args, **kwargs):
        data = s.InvoiceCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        invoice = finance_services.create_invoice(v["family"], v["charges"], request.user, v.get("due_date"), v["notes"])
        return Response(self.get_serializer(invoice).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        due = request.data.get("due_date")
        invoice = finance_services.issue_invoice(self.get_object(), request.user,
                                                 date.fromisoformat(due) if due else None)
        return Response(self.get_serializer(invoice).data)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        reason = s.ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        invoice = finance_services.void_invoice(self.get_object(), reason.validated_data["reason"], request.user)
        return Response(self.get_serializer(invoice).data)

    @action(detail=False, methods=["post"], url_path="generate-drafts")
    def generate_drafts(self, request):
        created = finance_services.generate_draft_invoices(request.user)
        return Response({"created": [inv.pk for inv in created]}, status=status.HTTP_201_CREATED)


class PaymentViewSet(ApiViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     viewsets.GenericViewSet):
    """Money received. Admins and finance record payments against issued
    invoices; send an ``Idempotency-Key`` header so a retried request can never
    record the same payment twice."""

    serializer_class = s.PaymentSerializer
    capabilities = caps(
        read=(Cap.FINANCE_VIEW_ALL, Cap.FINANCE_VIEW_OWN_CHILDREN),
        create=Cap.FINANCE_PAYMENTS_RECORD,
        void=Cap.FINANCE_PAYMENTS_VOID,
        refund=Cap.FINANCE_REFUNDS_RECORD,
    )

    def get_queryset(self):
        return finance_access.payments_for(self.request.user).select_related("receipt").prefetch_related(
            "allocations__invoice", "allocations__invoice_item")

    def create(self, request, *args, **kwargs):
        data = s.PaymentCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        payment, _ = finance_services.record_payment(
            [(a["invoice"], a["amount"]) for a in v["allocations"]], v["method"], request.user,
            amount=v["amount"], payer_name=v["payer_name"], reference=v["reference"],
            received_at=v.get("received_at"), notes=v["notes"],
            idempotency_key=request.headers.get("Idempotency-Key") or None,
        )
        return Response(s.PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        reason = s.ReasonSerializer(data=request.data)
        reason.is_valid(raise_exception=True)
        payment = finance_services.void_payment(self.get_object(), reason.validated_data["reason"], request.user)
        return Response(s.PaymentSerializer(payment).data)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        """Exceptional refund against one line of this payment (reason required)."""
        payment = self.get_object()
        data = s.RefundCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        if v["allocation"].payment_id != payment.pk:
            raise ValidationError({"allocation": "That line does not belong to this payment."})
        refund = finance_services.record_exceptional_refund(v["allocation"], v["amount"], v["reason"], request.user,
                                                            method=v["method"], reference=v["reference"])
        return Response(s.RefundSerializer(refund).data, status=status.HTTP_201_CREATED)


class ReceiptViewSet(ApiViewMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = s.ReceiptSerializer
    capabilities = caps(read=(Cap.FINANCE_VIEW_ALL, Cap.FINANCE_VIEW_OWN_CHILDREN))

    def get_queryset(self):
        return finance_access.receipts_for(self.request.user)


class RefundViewSet(ApiViewMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = s.RefundSerializer
    capabilities = caps(read=(Cap.FINANCE_VIEW_ALL, Cap.FINANCE_VIEW_OWN_CHILDREN))

    def get_queryset(self):
        return finance_access.refunds_for(self.request.user)


class CompetitionViewSet(ApiViewMixin, NoDestroyModelViewSet):
    serializer_class = s.CompetitionSerializer
    capabilities = caps(read=(Cap.COMPETITION_VIEW, Cap.COMPETITION_MANAGE), write=Cap.COMPETITION_MANAGE)

    def get_queryset(self):
        qs = Competition.objects.prefetch_related("events")
        if not can(self.request.user, Cap.COMPETITION_MANAGE):
            qs = qs.exclude(status=Competition.Status.DRAFT)
        return qs


class CompetitionEventViewSet(ApiViewMixin, NoDestroyModelViewSet):
    serializer_class = s.CompetitionEventSerializer
    capabilities = caps(read=(Cap.COMPETITION_VIEW, Cap.COMPETITION_MANAGE), write=Cap.COMPETITION_MANAGE)

    def get_queryset(self):
        qs = CompetitionEvent.objects.select_related("competition")
        if not can(self.request.user, Cap.COMPETITION_MANAGE):
            qs = qs.exclude(competition__status=Competition.Status.DRAFT)
        if id_param(self.request.query_params, "competition"):
            qs = qs.filter(competition_id=id_param(self.request.query_params, "competition"))
        return qs


REGISTRATION_READ = (
    Cap.COMPETITION_REGISTRATIONS_VIEW_ALL, Cap.COMPETITION_REGISTRATIONS_VIEW_ASSIGNED,
    Cap.COMPETITION_REGISTRATIONS_VIEW_OWN_CHILDREN, Cap.COMPETITION_REGISTRATIONS_VIEW_SELF,
)


def registration_scope(user, student_field):
    """Competition entries visible to the user: all, own children, own athletes, or self."""
    if can(user, Cap.COMPETITION_REGISTRATIONS_VIEW_ALL):
        return Q()
    q = Q(pk__in=[])
    if can(user, Cap.COMPETITION_REGISTRATIONS_VIEW_OWN_CHILDREN):
        q |= Q(**{f"{student_field}__in": access.children_for(user)})
    coach = access.coach_of(user)
    if coach and can(user, Cap.COMPETITION_REGISTRATIONS_VIEW_ASSIGNED):
        q |= Q(**{f"{student_field}_id__in": access.coach_roster_student_ids(coach)})
    student = access.student_of(user)
    if student and can(user, Cap.COMPETITION_REGISTRATIONS_VIEW_SELF):
        q |= Q(**{f"{student_field}_id": student.id})
    return q


class CompetitionRegistrationViewSet(ApiViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                                     mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Parents register their own children through the Parent App."""

    serializer_class = s.CompetitionRegistrationSerializer
    capabilities = caps(
        read=REGISTRATION_READ,
        create=(Cap.COMPETITION_REGISTRATIONS_MANAGE, Cap.COMPETITION_REGISTER_OWN_CHILDREN),
        withdraw=(Cap.COMPETITION_REGISTRATIONS_MANAGE, Cap.COMPETITION_REGISTER_OWN_CHILDREN),
        confirm=Cap.COMPETITION_REGISTRATIONS_MANAGE,
    )

    def get_queryset(self):
        user = self.request.user
        qs = CompetitionRegistration.objects.select_related("event__competition", "student", "charge", "result")
        if self.action == "withdraw" and not can(user, Cap.COMPETITION_REGISTRATIONS_MANAGE):
            qs = qs.filter(student__in=access.children_for(user))
        else:
            qs = qs.filter(registration_scope(user, "student"))
        if id_param(self.request.query_params, "competition"):
            qs = qs.filter(event__competition_id=id_param(self.request.query_params, "competition"))
        return qs

    def create(self, request, *args, **kwargs):
        manager = can(request.user, Cap.COMPETITION_REGISTRATIONS_MANAGE)
        if not manager:
            # Check ownership before any validation, so the answer for someone
            # else's child never reveals whether they are already registered.
            student_id = id_param(request.data, "student")
            if student_id is None or not access.children_for(request.user).filter(pk=student_id).exists():
                raise PermissionDenied("You can only register your own children.")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student = serializer.validated_data["student"]
        event = serializer.validated_data["event"]
        if event.competition.status == Competition.Status.DRAFT and not manager:
            raise ValidationError({"detail": "This competition is not open."})
        registration = competition_services.register(student, event, request.user,
                                                     serializer.validated_data.get("notes", ""))
        return Response(self.get_serializer(registration).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        registration = competition_services.withdraw(self.get_object(), request.user, request.data.get("reason", ""))
        return Response(self.get_serializer(registration).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        registration = competition_services.confirm(self.get_object(), request.user)
        return Response(self.get_serializer(registration).data)


class CompetitionResultViewSet(ApiViewMixin, NoDestroyModelViewSet):
    """Results are written through ``competitions.services.record_result``:
    only for a confirmed (paid) registration."""

    serializer_class = s.CompetitionResultSerializer
    capabilities = caps(read=REGISTRATION_READ, write=Cap.COMPETITION_RESULTS_MANAGE)

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        registration = data.pop("registration")
        if CompetitionResult.objects.filter(registration=registration).exists():
            raise ValidationError({"registration": "This registration already has a result; update that result."})
        serializer.instance = competition_services.record_result(registration, self.request.user, **data)

    def perform_update(self, serializer):
        data = dict(serializer.validated_data)
        data.pop("registration", None)
        serializer.instance = competition_services.record_result(serializer.instance.registration,
                                                                 self.request.user, **data)

    def get_queryset(self):
        qs = CompetitionResult.objects.select_related("registration__student", "registration__event__competition")
        qs = qs.filter(registration_scope(self.request.user, "registration__student"))
        if id_param(self.request.query_params, "competition"):
            qs = qs.filter(registration__event__competition_id=id_param(self.request.query_params, "competition"))
        return qs


class PayrollRunViewSet(ApiViewMixin, viewsets.ReadOnlyModelViewSet):
    """Payroll periods. Finance calculates; only a super admin finalizes."""

    serializer_class = s.PayrollRunSerializer
    queryset = PayrollRun.objects.all()
    capabilities = caps(read=Cap.PAYROLL_VIEW_ALL, calculate=Cap.PAYROLL_PREPARE, finalize=Cap.PAYROLL_FINALIZE)

    @action(detail=False, methods=["post"])
    def calculate(self, request):
        try:
            year, month = int(request.data.get("year")), int(request.data.get("month"))
        except (TypeError, ValueError):
            raise ValidationError({"detail": "year and month are required."}) from None
        run = payroll_services.calculate_run(year, month, request.user)
        return Response(self.get_serializer(run).data)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        run = payroll_services.finalize_run(self.get_object(), request.user)
        return Response(self.get_serializer(run).data)


class PayslipViewSet(ApiViewMixin, viewsets.ReadOnlyModelViewSet):
    """Payroll staff see all payslips; a coach sees only their own finalized payslips."""

    serializer_class = s.PayslipSerializer
    capabilities = caps(read=(Cap.PAYROLL_VIEW_ALL, Cap.PAYROLL_VIEW_OWN))

    def get_queryset(self):
        user = self.request.user
        qs = Payslip.objects.select_related("run", "coach").prefetch_related("lines")
        if can(user, Cap.PAYROLL_VIEW_ALL):
            return qs
        coach = access.coach_of(user)
        if coach is None:
            return qs.none()
        return qs.filter(coach=coach, run__status=PayrollRun.Status.FINALIZED)
