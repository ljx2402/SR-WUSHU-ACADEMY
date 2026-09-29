import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from .access import can_view_invoice, can_view_receipt
from .models import Invoice, Receipt


@login_required
def receipt_print(request, pk):
    receipt = get_object_or_404(Receipt.objects.select_related("payment"), pk=pk)
    if not can_view_receipt(request.user, receipt):
        raise PermissionDenied
    context = {
        "c": receipt.content,
        "issued_at": datetime.datetime.fromisoformat(receipt.content["issued_at"]),
        "payment_date": datetime.datetime.fromisoformat(receipt.content["payment_date"]),
        "void": getattr(receipt, "void_record", None),
    }
    return render(request, "finance/receipt.html", context)


@login_required
def invoice_print(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if not can_view_invoice(request.user, invoice):
        raise PermissionDenied
    items = invoice.items.all() if invoice.status == Invoice.Status.VOID else invoice.active_items()
    return render(request, "finance/invoice.html", {"invoice": invoice, "items": items, "academy": settings.ACADEMY})
