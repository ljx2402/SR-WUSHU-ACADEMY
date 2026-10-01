"""REST API for the admin, Parent, Coach and Student apps.

Authorization is two-layered on every endpoint:
1. ``capabilities`` (per action) + ``HasCapability``: may this user perform the action at all?
2. ``get_queryset`` built from ``apps.academy.access``: which records?
Records outside the caller's scope return 404.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Prefetch, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from django.http import FileResponse
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academy import access
from apps.academy import services as academy_services
from apps.academy.models import (
    ClassCoach, Enrollment, Family, Program, SessionCoach, Student, TrainingClass, TrainingSession,
)
from apps.accounts import services as account_services
from apps.accounts.capabilities import Cap, can, capabilities_of
from apps.accounts.models import Coach, Parent
from apps.attendance import services as attendance_services
from apps.attendance.models import MARKED_STATUSES, AttendanceRecord
from apps.audit.context import reset_actor, set_actor
from apps.audit.utils import history_for
from apps.competitions import registration_forms
from apps.competitions import services as competition_services
from apps.competitions.models import (
    Competition, CompetitionEvent, CompetitionRegistration, CompetitionResult, RegistrationFormField,
)
from apps.finance import access as finance_access
from apps.finance import services as finance_services
from apps.finance import proofs as proof_services
from apps.finance.models import AcademyPaymentInfo, Charge, Invoice, Payment, PaymentProof, Receipt
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


def date_param(params, name):
    """A YYYY-MM-DD date from the query string, or None. Anything else is a 400."""
    value = params.get(name)
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError({name: "Use a date as YYYY-MM-DD."}) from None


def date_range(qs, params, field):
    """Narrow ``qs`` to ``?start=`` / ``?end=`` (inclusive dates) on ``field``."""
    start, end = date_param(params, "start"), date_param(params, "end")
    if start:
        qs = qs.filter(**{f"{field}__gte": start})
    if end:
        qs = qs.filter(**{f"{field}__lte": end})
    return qs


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
    access.SELF: s.SelfStudentSerializer,
    access.ROSTER: s.RosterStudentSerializer,
    access.DIRECTORY: s.StudentDirectorySerializer,
}


class StudentViewSet(ApiViewMixin, NoDestroyModelViewSet):
    """Staff: full records. Parents: own children. Students: themselves (basic profile only).
    Coaches: current members of their classes (training info only; no medical notes or contacts).
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
        qs = self.scope().queryset().prefetch_related("guardianships__parent").select_related("family")
        if self.action == "list" and self.scope().full:
            active = Enrollment.objects.active_on(timezone.localdate()).select_related("training_class")
            qs = qs.prefetch_related(Prefetch("enrollments", queryset=active, to_attr="active_enrollments"))
        if self.action == "attendance_summary":
            # Attendance is never visible through the finance directory.
            scope = self.scope()
            if not scope.full:
                qs = qs.filter(id__in=scope.child_ids | scope.roster_ids | ({scope.self_id} if scope.self_id else set()))
        search = self.request.query_params.get("search")
        if search:
            qs = qs.filter(Q(full_name__icontains=search) | Q(student_no__icontains=search) | Q(chinese_name__icontains=search))
        params = self.request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        # Narrowing filters only: they never widen the caller's scope.
        if id_param(params, "class"):
            current = Enrollment.objects.active_on(timezone.localdate()).filter(training_class_id=id_param(params, "class"))
            qs = qs.filter(enrollments__in=current)
        if id_param(params, "family"):
            qs = qs.filter(family_id=id_param(params, "family"))
        return qs.distinct().order_by("full_name")

    def represent(self, student, listing=False):
        level = self.scope().level_for(student.id)
        # Staff lists get a minimal row; the full record is only in the detail view.
        serializer_class = s.StaffStudentListSerializer if listing and level == access.FULL else STUDENT_SERIALIZERS[level]
        return serializer_class(student, context=self.get_serializer_context()).data

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response([self.represent(st, listing=True) for st in page])
        return Response([self.represent(st, listing=True) for st in queryset])

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
        qs = qs.select_related("program", "team").prefetch_related("schedules")
        if can(self.request.user, Cap.CLASSES_VIEW_ALL) and self.action in READ:
            # Staff see how many students are currently in each class.
            today = timezone.localdate()
            current = Q(enrollments__start_date__lte=today) & (
                Q(enrollments__end_date__isnull=True) | Q(enrollments__end_date__gte=today))
            qs = qs.annotate(active_students=Count("enrollments", filter=current, distinct=True))
        if self.request.query_params.get("active") in ("1", "0"):
            qs = qs.filter(is_active=self.request.query_params["active"] == "1")
        return qs

    @action(detail=True, methods=["get"])
    def students(self, request, pk=None):
        training_class = self.get_object()
        members = training_class.enrollments.active_on(timezone.localdate())
        qs = Student.objects.filter(enrollments__in=members).distinct().prefetch_related("guardianships__parent")
        return Response(s.RosterStudentSerializer(qs, many=True, context=self.get_serializer_context()).data)

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


class ProgramViewSet(ApiViewMixin, viewsets.ReadOnlyModelViewSet):
    """Programs (disciplines), read only: staff choose one when creating a class."""

    serializer_class = s.ProgramSerializer
    queryset = Program.objects.all()
    capabilities = caps(read=Cap.CLASSES_VIEW_ALL)
    pagination_class = None


class TrainingSessionViewSet(ApiViewMixin, NoDestroyModelViewSet):
    """Coaches see their classes' sessions; substitutes see only the session
    they cover, while their access window is open. Rosters and attendance are
    only reachable as staff or coach, never through a parent/student role."""

    serializer_class = s.TrainingSessionSerializer
    capabilities = caps(
        read=(Cap.SESSIONS_VIEW_ALL, Cap.SESSIONS_VIEW_ASSIGNED, Cap.SESSIONS_VIEW_OWN_CHILDREN, Cap.SESSIONS_VIEW_SELF),
        write=Cap.SESSIONS_MANAGE,
        roster=(Cap.ROSTER_VIEW_ALL, Cap.ROSTER_VIEW_ASSIGNED),
        coaching=(Cap.ROSTER_VIEW_ALL, Cap.ROSTER_VIEW_ASSIGNED),
        attendance=(Cap.ATTENDANCE_VIEW_ALL, Cap.ATTENDANCE_VIEW_ASSIGNED),
        assign_substitute=Cap.SUBSTITUTE_ASSIGN,
        revoke_substitute=Cap.SUBSTITUTE_REVOKE,
        cancel=Cap.SESSIONS_MANAGE,
        reinstate=Cap.SESSIONS_MANAGE,
        reschedule=Cap.SESSIONS_MANAGE,
        reassign_coach=Cap.SESSIONS_MANAGE,
    )
    STAFF_ACTIONS = {"roster", "attendance", "coaching"}
    # Seeing sessions only as a student: the student-safe representation.
    BROADER = (Cap.SESSIONS_VIEW_ALL, Cap.SESSIONS_VIEW_ASSIGNED, Cap.SESSIONS_VIEW_OWN_CHILDREN)

    def get_serializer_class(self):
        if self.action in READ and access.student_view(self.request.user, self.BROADER):
            return s.StudentSessionSerializer
        return super().get_serializer_class()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action in READ and access.student_view(self.request.user, self.BROADER):
            context["my_status"] = my_attendance(access.student_of(self.request.user))
        return context

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
        if id_param(params, "coach") and can(user, Cap.SESSIONS_VIEW_ALL):
            # Staff only: sessions this coach is assigned to (regular or substitute). For
            # coaches the scope is always their own and this parameter is ignored.
            slots = SessionCoach.objects.filter(coach_id=id_param(params, "coach"), status=SessionCoach.Status.ASSIGNED)
            qs = qs.filter(id__in=slots.values("session_id"))
        if params.get("status"):
            if params["status"] not in TrainingSession.Status.values:
                raise ValidationError({"status": "Unknown session status."})
            qs = qs.filter(status=params["status"])
        return qs

    @action(detail=False, methods=["get"])
    def coaching(self, request):
        """The sessions the caller works as coach (regular classes, and a
        substitute session only while its authorization is open), or every
        session for staff, with the caller's role and the attendance state.
        The scope comes from ``access.roster_sessions_for``: never from a
        coach id in the request. ``order=asc`` lists the earliest first."""
        qs = self.filter_queryset(self.get_queryset()).prefetch_related("coach_slots__coach")
        if request.query_params.get("order") == "asc":
            qs = qs.order_by("date", "start_time")
        page = self.paginate_queryset(qs)
        context = {**self.get_serializer_context(), "coach": access.coach_of(request.user)}
        data = s.CoachingSessionSerializer(page, many=True, context=context).data
        return self.get_paginated_response(data)

    @action(detail=True, methods=["get"])
    def roster(self, request, pk=None):
        session = self.get_object()
        students = session.roster().prefetch_related("guardianships__parent")
        return Response(s.RosterStudentSerializer(students, many=True, context=self.get_serializer_context()).data)

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


def _session_ref(session, **extra):
    return {"id": session.id, "class_name": session.training_class.name, "date": session.date,
            "start_time": session.start_time, "end_time": session.end_time, **extra}


class StaffDashboardView(ApiViewMixin, APIView):
    """The Staff Portal's operational overview (read only, staff who see every
    session). Every number comes from existing state and services; alerts are
    queries, not stored notifications:

    * attendance incomplete: sessions of the last 7 days that have started, are
      not cancelled and have expected students not marked (the attendance
      service's expected roster); split into still open for coaches and locked
      (administrator correction needed);
    * sessions without a coach: scheduled sessions in the next 14 days with no
      assigned coach (regular or substitute);
    * classes without a coach: active classes with no current regular coach."""

    capabilities = {"get": Cap.SESSIONS_VIEW_ALL}
    ATTENDANCE_DAYS = 7
    COACH_DAYS = 14

    def get(self, request):
        user = request.user
        now = timezone.now()
        today = timezone.localdate()
        todays = list(TrainingSession.objects.filter(date=today).select_related("training_class")
                      .prefetch_related("coach_slots"))
        live = [s for s in todays if s.status != TrainingSession.Status.CANCELLED]
        counts = {
            "sessions_today": len(live),
            "in_progress": sum(1 for s in live if s.starts_at <= now < s.ends_at),
            "cancelled_today": len(todays) - len(live),
            "substitute_covered_today": sum(
                1 for s in live if any(slot.role == SessionCoach.Role.SUBSTITUTE
                                       and slot.status == SessionCoach.Status.ASSIGNED for slot in s.coach_slots.all())),
        }
        if can(user, Cap.STUDENTS_VIEW_ALL):
            counts["active_students"] = Student.objects.filter(status=Student.Status.ACTIVE).count()
        if can(user, Cap.CLASSES_VIEW_ALL):
            counts["active_classes"] = TrainingClass.objects.filter(is_active=True).count()

        # Attendance: expected students without a mark (UNMARKED), per held session.
        held = [s for s in TrainingSession.objects.exclude(status=TrainingSession.Status.CANCELLED)
                .filter(date__gte=today - timedelta(days=self.ATTENDANCE_DAYS), date__lte=today)
                .select_related("training_class").order_by("-date", "-start_time") if s.starts_at <= now]
        expected = attendance_services.expected_pairs(held)
        marked = set(AttendanceRecord.objects.filter(session__in=held, status__in=MARKED_STATUSES)
                     .values_list("session_id", "student_id"))
        unmarked = {}
        for pair in expected - marked:
            unmarked[pair[0]] = unmarked.get(pair[0], 0) + 1
        open_rows, locked_rows = [], []
        for session in held:
            if unmarked.get(session.id):
                row = _session_ref(session, unmarked=unmarked[session.id])
                (open_rows if attendance_services.coach_window_open(session, now) else locked_rows).append(row)

        upcoming = (TrainingSession.objects.filter(status=TrainingSession.Status.SCHEDULED, date__gte=today,
                                                   date__lte=today + timedelta(days=self.COACH_DAYS))
                    .exclude(id__in=SessionCoach.objects.filter(status=SessionCoach.Status.ASSIGNED).values("session_id"))
                    .select_related("training_class").order_by("date", "start_time"))
        no_coach = [_session_ref(s) for s in upcoming if s.ends_at > now]
        coached = ClassCoach.objects.active_on(today).values("training_class_id")
        classes = TrainingClass.objects.filter(is_active=True).exclude(id__in=coached).order_by("name")

        alerts = [
            {"code": "attendance_open", "label": "Attendance not finished (coach window open)",
             "count": len(open_rows), "sessions": open_rows},
            {"code": "attendance_locked", "label": "Attendance incomplete after the 48-hour window (administrator correction)",
             "count": len(locked_rows), "sessions": locked_rows},
            {"code": "session_without_coach", "label": "Sessions without a coach (next 14 days)",
             "count": len(no_coach), "sessions": no_coach},
            {"code": "class_without_coach", "label": "Active classes without a coach",
             "count": classes.count(), "classes": [{"id": c.id, "name": c.name} for c in classes]},
        ]
        return Response({"date": today, "counts": counts, "alerts": alerts})


def my_attendance(student):
    """{session_id: status} of one student's own attendance records."""
    return dict(AttendanceRecord.objects.filter(student=student).values_list("session_id", "status"))


class StudentSelfViewSet(ApiViewMixin, viewsets.GenericViewSet):
    """The Student Portal: ``/api/students/me/...``.

    The student is always the signed-in user's own linked student record
    (``StudentAccount`` + STUDENT role, via ``access``); no student id is ever
    read from the request. Read only: students cannot register, withdraw, mark
    or correct attendance, or edit their profile."""

    capabilities = {
        "profile": Cap.STUDENTS_VIEW_SELF,
        "sessions": Cap.SESSIONS_VIEW_SELF,
        "session": Cap.SESSIONS_VIEW_SELF,
        "attendance": Cap.ATTENDANCE_VIEW_SELF,
        "competitions": Cap.COMPETITION_REGISTRATIONS_VIEW_SELF,
    }
    VIEWS = ("today", "upcoming", "past", "cancelled")

    def me(self):
        student = access.student_of(self.request.user)
        if student is None:
            raise NotFound("No student record is linked to this account.")
        return student

    def profile(self, request):
        return Response(s.SelfStudentSerializer(self.me()).data)

    def _sessions(self, student):
        return access.student_sessions(student).select_related("training_class").prefetch_related("coach_slots__coach")

    def sessions(self, request):
        student = self.me()
        qs = self._sessions(student)
        view = request.query_params.get("view", "")
        now = timezone.localtime()
        today, now_time = now.date(), now.time()
        cancelled = TrainingSession.Status.CANCELLED
        if view and view not in self.VIEWS:
            raise ValidationError({"view": f"Must be one of: {', '.join(self.VIEWS)}."})
        if view == "today":
            qs = qs.filter(date=today).order_by("start_time")
        elif view == "upcoming":
            qs = qs.exclude(status=cancelled).filter(Q(date__gt=today) | Q(date=today, end_time__gt=now_time))
            qs = qs.order_by("date", "start_time")
        elif view == "past":
            qs = qs.exclude(status=cancelled).filter(Q(date__lt=today) | Q(date=today, end_time__lte=now_time))
            qs = qs.order_by("-date", "-start_time")
        elif view == "cancelled":
            qs = qs.filter(status=cancelled).order_by("-date", "-start_time")
        else:
            qs = qs.order_by("-date", "-start_time")
        page = self.paginate_queryset(qs)
        data = s.StudentSessionSerializer(page, many=True, context={"my_status": my_attendance(student)}).data
        return self.get_paginated_response(data)

    def session(self, request, pk=None):
        student = self.me()
        session = get_object_or_404(self._sessions(student), pk=pk)
        return Response(s.StudentSessionSerializer(session, context={"my_status": my_attendance(student)}).data)

    def attendance(self, request):
        """Own attendance: the summary from the attendance service (UNMARKED
        reported separately, never in the percentage) and one row per held,
        non-cancelled session the student was expected at."""
        student = self.me()
        rows = attendance_services.student_history(student, access.student_sessions(student))
        return Response({
            "summary": summary_json(attendance_services.student_summary(student)),
            "sessions": [
                {"session": row["session"].id, "date": row["session"].date, "start_time": row["session"].start_time,
                 "end_time": row["session"].end_time, "class_name": row["session"].training_class.name,
                 "status": row["status"]}
                for row in rows
            ],
        })

    def competitions(self, request):
        student = self.me()
        qs = (CompetitionRegistration.objects.filter(student=student)
              .exclude(event__competition__status=Competition.Status.DRAFT)
              .select_related("event__competition", "result")
              .order_by("-event__competition__start_date", "event__name"))
        return Response(s.StudentCompetitionEntrySerializer(qs, many=True).data)


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
        qs = finance_access.invoices_for(self.request.user).prefetch_related(
            "items__charge__competition_registration__event__competition")
        params = self.request.query_params
        if id_param(params, "family"):
            qs = qs.filter(family_id=id_param(params, "family"))
        if params.get("status"):
            qs = qs.filter(status__in=params["status"].split(","))
        if params.get("outstanding"):
            qs = qs.filter(status__in=Invoice.OPEN_FOR_PAYMENT)
        # Narrowing filters and search (never widen the caller's scope).
        if id_param(params, "student"):
            qs = qs.filter(items__student_id=id_param(params, "student"))
        if params.get("kind") in Invoice.Kind.values:
            qs = qs.filter(kind=params["kind"])
        if params.get("overdue"):
            qs = qs.filter(status__in=Invoice.OPEN_FOR_PAYMENT, due_date__lt=timezone.localdate())
        qs = date_range(qs, params, "issue_date")
        search = (params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(number__icontains=search) | Q(family_name__icontains=search) | Q(family__name__icontains=search)
                           | Q(items__student_name__icontains=search) | Q(items__student_no__icontains=search))
        return qs.distinct()

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
        methods=Cap.FINANCE_PAYMENTS_RECORD,
        void=Cap.FINANCE_PAYMENTS_VOID,
        refund=Cap.FINANCE_REFUNDS_RECORD,
    )

    def get_queryset(self):
        qs = finance_access.payments_for(self.request.user).select_related("receipt", "family").prefetch_related(
            "allocations__invoice", "allocations__invoice_item")
        params = self.request.query_params
        if id_param(params, "family"):
            qs = qs.filter(family_id=id_param(params, "family"))
        if id_param(params, "invoice"):
            qs = qs.filter(allocations__invoice_id=id_param(params, "invoice"))
        if params.get("status") in Payment.Status.values:
            qs = qs.filter(status=params["status"])
        qs = date_range(qs, params, "received_at__date")
        search = (params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(number__icontains=search) | Q(reference__icontains=search) | Q(payer_name__icontains=search)
                           | Q(receipt__number__icontains=search) | Q(family__name__icontains=search)
                           | Q(allocations__invoice__number__icontains=search))
        return qs.distinct()

    @action(detail=False, methods=["get"])
    def methods(self, request):
        """The payment methods the backend accepts (for the manual payment form)."""
        return Response([{"value": value, "label": label} for value, label in Payment.Method.choices])

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
        qs = finance_access.receipts_for(self.request.user)
        params = self.request.query_params
        if id_param(params, "family"):
            qs = qs.filter(payment__family_id=id_param(params, "family"))
        if id_param(params, "invoice"):
            qs = qs.filter(payment__allocations__invoice_id=id_param(params, "invoice"))
        qs = date_range(qs, params, "issued_at__date")
        search = (params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(number__icontains=search) | Q(payer_name__icontains=search)
                           | Q(payment__reference__icontains=search) | Q(payment__number__icontains=search)
                           | Q(payment__allocations__invoice__number__icontains=search))
        return qs.distinct().order_by("-issued_at")


class PaymentProofViewSet(ApiViewMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Payment proofs: a parent uploads evidence of a manual payment for their
    family's open invoice (multipart); staff accept or reject it. Neither step
    records a payment, issues a receipt or changes an invoice: staff record the
    payment with ``POST /api/payments/`` as before. Proofs are never deleted."""

    serializer_class = s.PaymentProofSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    capabilities = caps(
        read=(Cap.FINANCE_PROOFS_REVIEW, Cap.FINANCE_VIEW_OWN_CHILDREN),
        file=(Cap.FINANCE_PROOFS_REVIEW, Cap.FINANCE_VIEW_OWN_CHILDREN),
        create=Cap.FINANCE_PROOFS_UPLOAD_OWN,
        accept=Cap.FINANCE_PROOFS_REVIEW,
        reject=Cap.FINANCE_PROOFS_REVIEW,
    )

    def get_queryset(self):
        qs = finance_access.proofs_for(self.request.user).select_related(
            "family", "invoice", "uploaded_by", "reviewed_by", "payment")
        params = self.request.query_params
        if id_param(params, "invoice"):
            qs = qs.filter(invoice_id=id_param(params, "invoice"))
        if id_param(params, "family"):
            qs = qs.filter(family_id=id_param(params, "family"))
        if params.get("status"):
            qs = qs.filter(status__in=params["status"].split(","))
        if id_param(params, "student"):
            qs = qs.filter(invoice__items__student_id=id_param(params, "student"))
        qs = date_range(qs, params, "uploaded_at__date")
        search = (params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(invoice__number__icontains=search) | Q(family__name__icontains=search)
                           | Q(reference__icontains=search) | Q(invoice__items__student_name__icontains=search))
        return qs.distinct().order_by("-uploaded_at")

    def create(self, request, *args, **kwargs):
        # Ownership first: another family's invoice is "not found", whatever else is wrong.
        invoice_id = id_param(request.data, "invoice")
        if invoice_id is None:
            raise ValidationError({"invoice": "Choose the invoice this payment is for."})
        invoice = finance_access.parent_invoices_for(request.user).filter(pk=invoice_id).first()
        if invoice is None:
            raise NotFound()
        data = s.PaymentProofUploadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        try:
            proof = proof_services.submit_proof(
                invoice, v["file"], request.user, amount_claimed=v.get("amount_claimed"),
                payment_date=v.get("payment_date"), reference=v["reference"], note=v["note"])
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages}) from None
        return Response(self.get_serializer(proof).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def file(self, request, pk=None):
        """The uploaded file, as a download (never rendered inline by the browser)."""
        proof = self.get_object()
        try:
            handle = proof.file.open("rb")
        except (FileNotFoundError, OSError):
            raise NotFound("The file is not available.") from None
        response = FileResponse(handle, as_attachment=True, filename=proof.original_name,
                                content_type=proof.content_type)
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        data = s.ProofAcceptSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        proof = proof_services.accept_proof(self.get_object(), request.user, data.validated_data["note"],
                                            data.validated_data.get("payment"))
        return Response(self.get_serializer(proof).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        proof = proof_services.reject_proof(self.get_object(), request.user, request.data.get("reason", ""))
        return Response(self.get_serializer(proof).data)


class AcademyPaymentInfoView(ApiViewMixin, APIView):
    """The academy's bank details / QR / instructions for paying invoices.
    Parents and finance staff read it; ``finance.payment_info.manage`` changes it."""

    parser_classes = [MultiPartParser, FormParser, JSONParser]
    capabilities = {
        "get": (Cap.FINANCE_VIEW_OWN_CHILDREN, Cap.FINANCE_VIEW_ALL, Cap.FINANCE_PAYMENT_INFO_MANAGE),
        "put": Cap.FINANCE_PAYMENT_INFO_MANAGE,
        "patch": Cap.FINANCE_PAYMENT_INFO_MANAGE,
    }

    def get(self, request):
        info = AcademyPaymentInfo.current() or AcademyPaymentInfo(reference_instructions="")
        return Response(s.AcademyPaymentInfoSerializer(info, context={"request": request}).data)

    def patch(self, request):
        data = s.AcademyPaymentInfoUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = dict(data.validated_data)
        qr = v.pop("qr_code", None)
        remove = v.pop("remove_qr_code", False)
        try:
            info = proof_services.update_payment_info(request.user, v, qr_upload=qr, remove_qr=remove)
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, "error_dict") else {"detail": exc.messages}) from None
        return Response(s.AcademyPaymentInfoSerializer(info, context={"request": request}).data)

    put = patch


class FinanceDashboardView(ApiViewMixin, APIView):
    """The finance staff overview (read only), computed from current records:
    open invoices (unpaid / partially paid / overdue, and the amount still
    due), today's and recent payments, recent receipts and, for proof
    reviewers, the payment-proof queue. No new statuses: only the invoice,
    payment and proof statuses the finance services maintain. No bank details,
    file names or personal data beyond family names and amounts."""

    capabilities = {"get": Cap.FINANCE_VIEW_ALL}
    RECENT = 5
    REVIEW_DAYS = 7

    def get(self, request):
        user = request.user
        today = timezone.localdate()
        zero = Decimal("0.00")
        open_invoices = Invoice.objects.filter(status__in=Invoice.OPEN_FOR_PAYMENT)
        overdue = open_invoices.filter(due_date__lt=today)

        def invoice_ref(inv):
            return {"id": inv.id, "number": inv.number, "family_name": inv.family_name, "kind": inv.kind,
                    "status": inv.status, "due_date": inv.due_date, "total": str(inv.total),
                    "balance_due": str(inv.balance_due)}

        invoices = {
            "unpaid": open_invoices.filter(status=Invoice.Status.ISSUED).count(),
            "partially_paid": open_invoices.filter(status=Invoice.Status.PARTIALLY_PAID).count(),
            "overdue": overdue.count(),
            "outstanding_total": str(open_invoices.aggregate(t=Sum("balance_due"))["t"] or zero),
            "competition_open": open_invoices.filter(kind=Invoice.Kind.COMPETITION).count(),
            "competition_outstanding": str(open_invoices.filter(kind=Invoice.Kind.COMPETITION)
                                           .aggregate(t=Sum("balance_due"))["t"] or zero),
            "overdue_list": [invoice_ref(inv) for inv in overdue.order_by("due_date", "id")[:self.RECENT]],
        }
        valid = Payment.objects.filter(status=Payment.Status.VALID)
        todays = valid.filter(received_at__date=today)
        payments = {
            "today_count": todays.count(),
            "today_total": str(todays.aggregate(t=Sum("amount"))["t"] or zero),
            "recent": [{"id": p.id, "number": p.number, "family_name": p.family.name, "amount": str(p.amount),
                        "method": p.method, "received_at": p.received_at, "status": p.status,
                        "receipt_number": getattr(getattr(p, "receipt", None), "number", None)}
                       for p in Payment.objects.select_related("family", "receipt")[:self.RECENT]],
        }
        receipts = [{"id": r.id, "number": r.number, "issued_at": r.issued_at, "payer_name": r.payer_name,
                     "total": str(r.total), "is_void": r.is_void}
                    for r in Receipt.objects.select_related("void_record")[:self.RECENT]]
        data = {"date": today, "invoices": invoices, "payments": payments, "receipts": receipts}
        if can(user, Cap.FINANCE_PROOFS_REVIEW):
            since = timezone.now() - timedelta(days=self.REVIEW_DAYS)
            pending = PaymentProof.objects.filter(status=PaymentProof.Status.PENDING_REVIEW).select_related("invoice", "family")
            accepted_open = PaymentProof.objects.filter(status=PaymentProof.Status.ACCEPTED, payment__isnull=True,
                                                        invoice__status__in=Invoice.OPEN_FOR_PAYMENT)
            data["proofs"] = {
                "pending": pending.count(),
                "accepted_recently": PaymentProof.objects.filter(status=PaymentProof.Status.ACCEPTED, reviewed_at__gte=since).count(),
                "rejected_recently": PaymentProof.objects.filter(status=PaymentProof.Status.REJECTED, reviewed_at__gte=since).count(),
                "accepted_awaiting_payment": accepted_open.count(),
                "oldest_pending": [{"id": p.id, "invoice_number": p.invoice.number, "family_name": p.family.name,
                                    "amount_claimed": str(p.amount_claimed) if p.amount_claimed is not None else None,
                                    "uploaded_at": p.uploaded_at}
                                   for p in pending.order_by("uploaded_at")[:self.RECENT]],
            }
        return Response(data)


class RefundViewSet(ApiViewMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = s.RefundSerializer
    capabilities = caps(read=(Cap.FINANCE_VIEW_ALL, Cap.FINANCE_VIEW_OWN_CHILDREN))

    def get_queryset(self):
        qs = finance_access.refunds_for(self.request.user)
        if id_param(self.request.query_params, "payment"):
            qs = qs.filter(payment_id=id_param(self.request.query_params, "payment"))
        return qs


class CompetitionViewSet(ApiViewMixin, NoDestroyModelViewSet):
    """Competitions. Each has its own registration form: staff edit the
    working copy (``/api/competition-form-fields/``), preview it (``form``),
    and publish or unpublish it. Everyone else sees only the published form."""

    serializer_class = s.CompetitionSerializer
    capabilities = caps(read=(Cap.COMPETITION_VIEW, Cap.COMPETITION_MANAGE), write=Cap.COMPETITION_MANAGE,
                        form=Cap.COMPETITION_MANAGE, publish_form=Cap.COMPETITION_MANAGE,
                        unpublish_form=Cap.COMPETITION_MANAGE, reorder_form=Cap.COMPETITION_MANAGE,
                        summary=Cap.COMPETITION_REGISTRATIONS_VIEW_ALL)

    def get_queryset(self):
        user, params = self.request.user, self.request.query_params
        qs = Competition.objects.prefetch_related("events")
        if not can(user, Cap.COMPETITION_MANAGE):
            qs = qs.exclude(status=Competition.Status.DRAFT)
        if can(user, Cap.COMPETITION_REGISTRATIONS_VIEW_ALL):
            # Entry counts for the staff portal (the serializer shows them only to these viewers).
            active = ~Q(events__registrations__status__in=CompetitionRegistration.INACTIVE)
            qs = qs.annotate(
                entry_count=Count("events__registrations", filter=active, distinct=True),
                pending_count=Count("events__registrations", distinct=True,
                                    filter=Q(events__registrations__status=CompetitionRegistration.Status.PENDING)),
            )
        # Narrowing filters (never widen the caller's scope).
        statuses = [v for v in (params.get("status") or "").split(",") if v]
        if statuses:
            if any(v not in Competition.Status.values for v in statuses):
                raise ValidationError({"status": "Unknown competition status."})
            qs = qs.filter(status__in=statuses)
        search = (params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(organiser__icontains=search) | Q(venue__icontains=search))
        return qs.order_by("-start_date", "id")

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        """Staff counts for one competition, computed from its registrations
        (entry status, fee status, results). No family or form data."""
        competition = self.get_object()
        regs = CompetitionRegistration.objects.filter(event__competition=competition)
        by_status = dict(regs.values_list("status").annotate(n=Count("id")))
        active = regs.exclude(status__in=CompetitionRegistration.INACTIVE)
        events = []
        for event in competition.events.all():
            event_regs = active.filter(event=event)
            events.append({"id": event.id, "name": event.name, "entries": event_regs.count(),
                           "max_entries": event.max_entries,
                           "confirmed": event_regs.filter(status=CompetitionRegistration.Status.CONFIRMED).count()})
        confirmed = regs.filter(status=CompetitionRegistration.Status.CONFIRMED)
        return Response({
            "registrations": {value: by_status.get(value, 0) for value in CompetitionRegistration.Status.values},
            "fees": {
                "paid": active.filter(charge__status=Charge.Status.PAID).count(),
                "awaiting_payment": active.filter(charge__status__in=(Charge.Status.UNPAID, Charge.Status.PARTIAL)).count(),
                "free": active.filter(charge__isnull=True).count(),
            },
            "results": {"recorded": CompetitionResult.objects.filter(registration__event__competition=competition).count(),
                        "confirmed_without_result": confirmed.filter(result__isnull=True).count()},
            "events": events,
        })

    def _form_payload(self, competition):
        fields = competition.form_fields.order_by("order", "id")
        return {
            "status": competition.form_status,
            "version": competition.form_version,
            "published_at": competition.form_published_at,
            "published_fields": competition.published_form,
            "fields": s.RegistrationFormFieldSerializer(fields, many=True).data,
            "preview": registration_forms.draft_form(competition),
            "has_unpublished_changes": registration_forms.draft_form(competition) != competition.published_form,
        }

    @action(detail=True, methods=["get"])
    def form(self, request, pk=None):
        """Staff view of the form: working copy, preview and published version."""
        return Response(self._form_payload(self.get_object()))

    @action(detail=True, methods=["post"], url_path="publish-form")
    def publish_form(self, request, pk=None):
        competition = registration_forms.publish_form(self.get_object(), request.user)
        return Response(self._form_payload(competition))

    @action(detail=True, methods=["post"], url_path="unpublish-form")
    def unpublish_form(self, request, pk=None):
        competition = registration_forms.unpublish_form(self.get_object(), request.user)
        return Response(self._form_payload(competition))

    @action(detail=True, methods=["post"], url_path="reorder-form")
    def reorder_form(self, request, pk=None):
        keys = request.data.get("keys")
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ValidationError({"keys": "Send the field keys in their new order."})
        competition = self.get_object()
        registration_forms.reorder_fields(competition, keys, request.user)
        return Response(self._form_payload(competition))


class RegistrationFormFieldViewSet(ApiViewMixin, viewsets.ModelViewSet):
    """Custom questions of competition registration forms (staff working copy).
    Changes reach parents only when the form is published again; past
    registrations keep the questions and answers they were submitted with."""

    serializer_class = s.RegistrationFormFieldSerializer
    capabilities = caps(read=Cap.COMPETITION_MANAGE, write=Cap.COMPETITION_MANAGE, destroy=Cap.COMPETITION_MANAGE)

    def get_queryset(self):
        qs = RegistrationFormField.objects.select_related("competition")
        if id_param(self.request.query_params, "competition"):
            qs = qs.filter(competition_id=id_param(self.request.query_params, "competition"))
        return qs.order_by("competition", "order", "id")

    def perform_create(self, serializer):
        competition = serializer.validated_data["competition"]
        if competition.form_fields.count() >= registration_forms.MAX_FIELDS:
            raise ValidationError({"detail": f"A registration form can have at most "
                                             f"{registration_forms.MAX_FIELDS} custom fields."})
        order = serializer.validated_data.get("order") or (competition.form_fields.count() + 1)
        serializer.save(order=order)


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
        reject=Cap.COMPETITION_REGISTRATIONS_MANAGE,
    )

    PAYMENT_FILTERS = {
        "PAID": Q(charge__status=Charge.Status.PAID),
        "UNPAID": Q(charge__status__in=(Charge.Status.UNPAID, Charge.Status.PARTIAL)),
        "FREE": Q(charge__isnull=True),
    }

    def get_queryset(self):
        user, params = self.request.user, self.request.query_params
        qs = CompetitionRegistration.objects.select_related("event__competition", "student", "charge", "result")
        if self.action == "withdraw" and not can(user, Cap.COMPETITION_REGISTRATIONS_MANAGE):
            qs = qs.filter(student__in=access.children_for(user))
        else:
            qs = qs.filter(registration_scope(user, "student"))
        if id_param(params, "competition"):
            qs = qs.filter(event__competition_id=id_param(params, "competition"))
        # Narrowing filters for the staff participant list (never widen the caller's scope).
        if id_param(params, "event"):
            qs = qs.filter(event_id=id_param(params, "event"))
        if id_param(params, "student"):
            qs = qs.filter(student_id=id_param(params, "student"))
        statuses = [v for v in (params.get("status") or "").split(",") if v]
        if statuses:
            if any(v not in CompetitionRegistration.Status.values for v in statuses):
                raise ValidationError({"status": "Unknown registration status."})
            qs = qs.filter(status__in=statuses)
        payment = params.get("payment")
        if payment:
            if payment not in self.PAYMENT_FILTERS:
                raise ValidationError({"payment": "Use PAID, UNPAID or FREE."})
            qs = qs.filter(self.PAYMENT_FILTERS[payment])
        if params.get("result") in ("yes", "no"):
            qs = qs.filter(result__isnull=params["result"] == "no")
        qs = date_range(qs, params, "registered_at__date")
        search = (params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(student__full_name__icontains=search) | Q(student__student_no__icontains=search))
        return qs.order_by("event__name", "student__full_name", "id")

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
        # A client may say which competition it is registering for; it must be the event's.
        competition_id = id_param(request.data, "competition")
        if competition_id is not None and competition_id != event.competition_id:
            raise ValidationError({"event": "This event does not belong to that competition."})
        try:
            registration = competition_services.register(student, event, request.user,
                                                         serializer.validated_data.get("notes", ""),
                                                         responses=request.data.get("responses"))
        except DjangoValidationError as exc:
            if hasattr(exc, "error_dict"):
                raise ValidationError(exc.message_dict) from None
            raise
        return Response(self.get_serializer(registration).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        registration = competition_services.withdraw(self.get_object(), request.user, request.data.get("reason", ""))
        return Response(self.get_serializer(registration).data)

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        registration = competition_services.confirm(self.get_object(), request.user)
        return Response(self.get_serializer(registration).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Staff rejection: the existing withdrawal service with the REJECTED status
        (unpaid invoice voided, paid fees not refunded), with a required reason."""
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ValidationError({"reason": "A reason is required to reject a registration."})
        registration = competition_services.withdraw(self.get_object(), request.user, reason,
                                                     CompetitionRegistration.Status.REJECTED)
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
