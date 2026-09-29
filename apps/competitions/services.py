from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from apps.academy import access
from apps.audit.context import audit_context
from apps.finance.models import Charge, FeeType

from .models import CompetitionRegistration


@transaction.atomic
def register(student, event, actor, notes=""):
    """Register a student for a competition event.

    Parents may only register their own children, and only while the
    competition is open. A charge for the event fee is created automatically.
    """
    competition = event.competition
    is_admin = access.is_admin(actor)
    if not is_admin:
        if not access.is_parent_of(actor, student):
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
            charge = Charge.objects.create(
                student=student,
                fee_type=FeeType.COMPETITION,
                description=f"{competition.name} – {event.name}",
                unit_amount=event.fee,
                due_date=competition.registration_deadline,
                created_by=actor,
            )
        return CompetitionRegistration.objects.create(
            event=event,
            student=student,
            status=CompetitionRegistration.Status.CONFIRMED if is_admin else CompetitionRegistration.Status.PENDING,
            registered_by=actor,
            charge=charge,
            notes=notes,
        )


@transaction.atomic
def withdraw(registration, actor, reason="", status=CompetitionRegistration.Status.WITHDRAWN):
    competition = registration.event.competition
    if not access.is_admin(actor):
        status = CompetitionRegistration.Status.WITHDRAWN
        if not access.is_parent_of(actor, registration.student):
            raise PermissionDenied("You can only withdraw your own children.")
        if not competition.is_open_for_registration():
            raise ValidationError("The registration deadline has passed; please contact the academy.")
    if registration.status in CompetitionRegistration.INACTIVE:
        raise ValidationError("This registration is already withdrawn.")
    with audit_context(actor, reason or "Withdrawn"):
        registration.status = status
        registration.save()
        charge = registration.charge
        if charge is not None and not charge.valid_allocations().exists():
            charge.status = Charge.Status.CANCELLED
            charge.save()
    return registration
