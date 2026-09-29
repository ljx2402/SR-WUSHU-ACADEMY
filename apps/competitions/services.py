from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.academy import access
from apps.accounts.capabilities import Cap, can
from apps.audit.context import audit_context
from apps.finance import services as finance_services
from apps.finance.models import Charge, FeeType

from .models import CompetitionRegistration


@transaction.atomic
def register(student, event, actor, notes=""):
    """Register a student for a competition event.

    Parents may only register their own children, and only while the
    competition is open. Competition fees are paid at registration: a charge
    and an issued competition invoice (due today) are created, and the
    registration stays PENDING (awaiting payment) until that invoice is paid,
    when it becomes CONFIRMED automatically. A free event is confirmed at once.
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
        )


@transaction.atomic
def withdraw(registration, actor, reason="", status=CompetitionRegistration.Status.WITHDRAWN):
    competition = registration.event.competition
    if not can(actor, Cap.COMPETITION_REGISTRATIONS_MANAGE):
        status = CompetitionRegistration.Status.WITHDRAWN
        if not can(actor, Cap.COMPETITION_REGISTER_OWN_CHILDREN) or not access.is_parent_of(actor, registration.student):
            raise PermissionDenied("You can only withdraw your own children.")
        if not competition.is_open_for_registration():
            raise ValidationError("The registration deadline has passed; please contact the academy.")
    if registration.status in CompetitionRegistration.INACTIVE:
        raise ValidationError("This registration is already withdrawn.")
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
