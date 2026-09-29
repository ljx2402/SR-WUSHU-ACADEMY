"""Test helpers for the invoice-based finance flow (system actor unless given)."""

from apps.academy.models import Family
from apps.finance.services import create_invoice, issue_invoice


def make_family(*students, name="Test family"):
    """Staff explicitly grouping siblings into one household."""
    family = Family.objects.create(name=name)
    for student in students:
        student.family = family
        student.save()
    return family


def issued_invoice(charges, actor=None, family=None):
    charges = list(charges)
    family = family or charges[0].student.family
    return issue_invoice(create_invoice(family, charges, actor), actor)
