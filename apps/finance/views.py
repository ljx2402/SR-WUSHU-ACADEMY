import datetime

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from apps.academy import access

from .models import Receipt


def can_view_receipt(user, receipt):
    if access.is_admin(user):
        return True
    parent = access.parent_of(user)
    if not parent:
        return False
    if receipt.payment.parent_id == parent.pk:
        return True
    students = receipt.payment.allocations.values_list("charge__student_id", flat=True)
    return parent.guardianships.filter(student_id__in=students).exists()


@login_required
def receipt_print(request, pk):
    receipt = get_object_or_404(Receipt.objects.select_related("payment"), pk=pk)
    if not can_view_receipt(request.user, receipt):
        raise PermissionDenied
    context = {
        "c": receipt.content,
        "issued_at": datetime.datetime.fromisoformat(receipt.content["issued_at"]),
        "void": getattr(receipt, "void_record", None),
    }
    return render(request, "finance/receipt.html", context)
