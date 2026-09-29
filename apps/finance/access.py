"""Record-level access for finance data (which invoices/payments/receipts).

* ``finance.view_all`` (admin, finance admin, super admin): everything.
* Parent (``finance.view_own_children``): the families their own children belong
  to. A family invoice can list several siblings; every guardian of a student in
  that family sees the whole family document, never other families'. Drafts are
  staff-only.
* Coaching a student never grants finance access (the coach role has no finance
  capability, and these querysets use only the parent relationship).
* Students: no finance access yet (the student-facing finance API comes later).
"""

from django.db.models import Q

from apps.academy import access
from apps.academy.models import Family
from apps.accounts.capabilities import Cap, can

from .models import Invoice, Payment, Receipt, Refund


def families_for(user):
    """Families whose finance records the user may see."""
    if can(user, Cap.FINANCE_VIEW_ALL):
        return Family.objects.all()
    parent = access.parent_of(user)
    if parent and can(user, Cap.FINANCE_VIEW_OWN_CHILDREN):
        return Family.objects.filter(students__guardianships__parent=parent).distinct()
    return Family.objects.none()


def invoices_for(user):
    qs = Invoice.objects.all()
    if can(user, Cap.FINANCE_VIEW_ALL):
        return qs
    return qs.filter(family__in=families_for(user)).exclude(status=Invoice.Status.DRAFT)


def payments_for(user):
    qs = Payment.objects.all()
    if can(user, Cap.FINANCE_VIEW_ALL):
        return qs
    return qs.filter(family__in=families_for(user))


def receipts_for(user):
    qs = Receipt.objects.select_related("payment")
    if can(user, Cap.FINANCE_VIEW_ALL):
        return qs
    return qs.filter(payment__family__in=families_for(user))


def refunds_for(user):
    if can(user, Cap.FINANCE_VIEW_ALL):
        return Refund.objects.all()
    return Refund.objects.filter(Q(payment__family__in=families_for(user)))


def can_view_invoice(user, invoice):
    return invoices_for(user).filter(pk=invoice.pk).exists()


def can_view_receipt(user, receipt):
    return receipts_for(user).filter(pk=receipt.pk).exists()
