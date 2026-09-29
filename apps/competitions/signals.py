"""Competition fees are paid at registration: a registration is CONFIRMED when
its fee is fully paid, and goes back to awaiting payment if that payment is
voided. Runs inside the payment transaction."""

from django.dispatch import receiver

from apps.audit.context import audit_context
from apps.finance.signals import charge_settled, charge_unsettled

from .models import CompetitionRegistration


@receiver(charge_settled)
def confirm_paid_registration(sender, charge, actor=None, **kwargs):
    registration = CompetitionRegistration.objects.select_for_update().filter(
        charge=charge, status=CompetitionRegistration.Status.PENDING).first()
    if registration is not None:
        with audit_context(actor, "Competition fee paid"):
            registration.status = CompetitionRegistration.Status.CONFIRMED
            registration.save()


@receiver(charge_unsettled)
def unconfirm_unpaid_registration(sender, charge, actor=None, **kwargs):
    registration = CompetitionRegistration.objects.select_for_update().filter(
        charge=charge, status=CompetitionRegistration.Status.CONFIRMED).first()
    if registration is not None:
        with audit_context(actor, "Competition fee payment voided"):
            registration.status = CompetitionRegistration.Status.PENDING
            registration.save()
