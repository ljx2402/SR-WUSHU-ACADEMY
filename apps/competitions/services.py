from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.academy import access
from apps.accounts.capabilities import Cap, can
from apps.audit.context import audit_context
from apps.finance import services as finance_services
from apps.finance.models import Charge, FeeType

from . import registration_forms
from .models import Competition, CompetitionRegistration, CompetitionResult


@transaction.atomic
def register(student, event, actor, notes="", responses=None):
    """Register a student for a competition event.

    Parents may only register their own children, and only while the
    competition is open. Competition fees are paid at registration: a charge
    and an issued competition invoice (due today) are created, and the
    registration stays PENDING (awaiting payment) until that invoice is paid,
    when it becomes CONFIRMED automatically. A free event is confirmed at once.

    ``responses`` are the answers to the competition's PUBLISHED registration
    form, validated against it and stored with the form version (see
    ``registration_forms``). Parents can only register while the form is
    published and must answer its required questions; staff registering on a
    family's behalf may leave questions they cannot answer empty.
    """
    competition = event.competition
    is_manager = can(actor, Cap.COMPETITION_REGISTRATIONS_MANAGE)
    if not is_manager:
        if not can(actor, Cap.COMPETITION_REGISTER_OWN_CHILDREN) or not access.is_parent_of(actor, student):
            raise PermissionDenied("You can only register your own children.")
        if not competition.allow_parent_registration:
            raise PermissionDenied("Registration for this competition is handled by the academy.")
        if not competition.is_open_for_registration():
            raise ValidationError("Registration for this competition is closed.")
        if competition.form_status != Competition.FormStatus.PUBLISHED:
            raise ValidationError("The registration form for this competition is not available yet.")
    answers = registration_forms.validate_responses(competition, responses, enforce_required=not is_manager)
    errors = event.eligibility_errors(student)
    if errors:
        raise ValidationError(errors)
    if event.active_registrations().filter(student=student).exists():
        raise ValidationError(f"{student.full_name} is already registered for {event.name}.")
    if event.max_entries is not None and event.active_registrations().count() >= event.max_entries:
        raise ValidationError(f"{event.name} is full.")
    if competition.max_events_per_student is not None:
        existing = CompetitionRegistration.objects.filter(event__competition=competition, student=student).exclude(
            status__in=CompetitionRegistration.INACTIVE
        )
        if existing.count() >= competition.max_events_per_student:
            raise ValidationError(f"A student may enter at most {competition.max_events_per_student} events.")

    with audit_context(actor, "Competition registration"):
        charge = None
        if event.fee > 0:
            charge = finance_services._create_charge(
                student, FeeType.COMPETITION, f"{competition.name} – {event.name}", event.fee, actor,
                due_date=timezone.localdate(),
            )
            finance_services.create_competition_invoice(charge, actor)
        return CompetitionRegistration.objects.create(
            event=event,
            student=student,
            status=CompetitionRegistration.Status.PENDING if charge else CompetitionRegistration.Status.CONFIRMED,
            registered_by=actor,
            charge=charge,
            notes=notes,
            form_version=competition.form_version,
            form_responses=answers,
        )


@transaction.atomic
def withdraw(registration, actor, reason="", status=CompetitionRegistration.Status.WITHDRAWN):
    # Lock: a withdrawal and a result entry for the same registration happen one after the other.
    registration = CompetitionRegistration.objects.select_for_update().get(pk=registration.pk)
    competition = registration.event.competition
    if not can(actor, Cap.COMPETITION_REGISTRATIONS_MANAGE):
        status = CompetitionRegistration.Status.WITHDRAWN
        if not can(actor, Cap.COMPETITION_REGISTER_OWN_CHILDREN) or not access.is_parent_of(actor, registration.student):
            raise PermissionDenied("You can only withdraw your own children.")
        if not competition.allow_parent_withdrawal:
            raise PermissionDenied("Withdrawal from this competition is handled by the academy; please contact "
                                   "the academy.")
        if not competition.is_open_for_registration():
            raise ValidationError("The registration deadline has passed; please contact the academy.")
    if registration.status in CompetitionRegistration.INACTIVE:
        raise ValidationError("This registration is already withdrawn.")
    if CompetitionResult.objects.filter(registration=registration).exists():
        raise ValidationError("A result has been recorded for this registration; it cannot be withdrawn.")
    with audit_context(actor, reason or "Withdrawn"):
        registration.status = status
        registration.save()
        if registration.charge is not None:
            # Unpaid: the competition invoice is voided and the charge cancelled.
            # Paid: nothing happens financially. Competition fees are
            # non-refundable; any exceptional refund is a separate, authorized act.
            finance_services.cancel_unpaid_competition_charge(
                registration.charge, reason or f"Registration {status.lower()}", actor)
    return registration


def is_paid(registration):
    return registration.charge is None or registration.charge.status == Charge.Status.PAID


@transaction.atomic
def confirm(registration, actor):
    """Manual confirmation by staff. Never confirms an unpaid registration."""
    if not can(actor, Cap.COMPETITION_REGISTRATIONS_MANAGE):
        raise PermissionDenied("You do not have permission to perform this action.")
    registration = CompetitionRegistration.objects.select_for_update().get(pk=registration.pk)
    if registration.status in CompetitionRegistration.INACTIVE:
        raise ValidationError("A withdrawn or rejected registration cannot be confirmed.")
    if not is_paid(registration):
        raise ValidationError("The competition fee has not been paid; the registration cannot be confirmed.")
    with audit_context(actor, "Registration confirmed"):
        registration.status = CompetitionRegistration.Status.CONFIRMED
        registration.save()
    return registration


RESULT_FIELDS = ("placing", "medal", "score", "remarks")


@transaction.atomic
def record_result(registration, actor, **values):
    """Record or update the result of a CONFIRMED registration."""
    if not can(actor, Cap.COMPETITION_RESULTS_MANAGE):
        raise PermissionDenied("You do not have permission to record competition results.")
    registration = CompetitionRegistration.objects.select_for_update().get(pk=registration.pk)
    if registration.status != CompetitionRegistration.Status.CONFIRMED or not is_paid(registration):
        raise ValidationError(f"Results can only be recorded for a confirmed (paid) registration; "
                              f"this registration is {registration.get_status_display().lower()}.")
    unknown = set(values) - set(RESULT_FIELDS)
    if unknown:
        raise ValidationError(f"Unknown result fields: {', '.join(sorted(unknown))}")
    result = CompetitionResult.objects.filter(registration=registration).first() or CompetitionResult(
        registration=registration)
    with audit_context(actor, "Competition result recorded"):
        for field, value in values.items():
            setattr(result, field, value)
        result.save()
    return result
