"""Payment proofs and the academy's payment information.

A proof is a parent's evidence of a manual payment. It never changes money:
staff review it (accept or reject) and, separately, record the actual payment
with ``services.record_payment``, which issues the official receipt and
updates the invoice (and confirms a fully paid competition registration).
"""

import datetime

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Cap, can
from apps.audit.context import audit_context

from . import access
from .models import AcademyPaymentInfo, Invoice, Payment, PaymentProof
from .uploads import CONTENT_TYPES, display_name, private_storage, sha256_of, stored_name, validate_upload

MAX_PENDING_PER_INVOICE = 5


def submit_proof(invoice, upload, actor, *, amount_claimed=None, payment_date=None, reference="", note=""):
    """A parent uploads proof of a payment they made for their family's invoice."""
    if not access.parent_invoices_for(actor).filter(pk=invoice.pk).exists():
        raise PermissionDenied("You can only upload payment proof for your own family's invoices.")
    if invoice.status not in Invoice.OPEN_FOR_PAYMENT:
        raise ValidationError("This invoice is not awaiting payment.")
    if payment_date and payment_date > timezone.localdate() + datetime.timedelta(days=1):
        raise ValidationError({"payment_date": "The payment date cannot be in the future."})
    pending = PaymentProof.objects.filter(invoice=invoice, status=PaymentProof.Status.PENDING_REVIEW).count()
    if pending >= MAX_PENDING_PER_INVOICE:
        raise ValidationError("Several proofs for this invoice are already waiting for review. Please wait for "
                              "the academy to check them.")
    try:
        kind = validate_upload(upload, ("pdf", "jpg", "png"), settings.PAYMENT_PROOF_MAX_BYTES)
    except ValidationError as exc:
        raise ValidationError({"file": exc.messages}) from None
    digest = sha256_of(upload)
    storage = private_storage()
    name = storage.save(stored_name("payment-proofs", kind), upload)
    try:
        with transaction.atomic(), audit_context(actor, "Payment proof uploaded"):
            return PaymentProof.objects.create(
                family=invoice.family, invoice=invoice, uploaded_by=actor, file=name,
                original_name=display_name(upload, kind), content_type=CONTENT_TYPES[kind], size=upload.size,
                sha256=digest, amount_claimed=amount_claimed, payment_date=payment_date,
                reference=reference.strip(), note=note.strip(),
            )
    except Exception:
        storage.delete(name)  # no orphaned file if the record could not be written
        raise


def _review(proof, actor, status, note, payment=None):
    if not can(actor, Cap.FINANCE_PROOFS_REVIEW):
        raise PermissionDenied("You do not have permission to review payment proofs.")
    with transaction.atomic():
        proof = PaymentProof.objects.select_for_update().get(pk=proof.pk)
        if proof.status != PaymentProof.Status.PENDING_REVIEW:
            raise ValidationError(f"This payment proof has already been {proof.get_status_display().lower()}.")
        if payment is not None:
            if payment.family_id != proof.family_id or payment.status != Payment.Status.VALID:
                raise ValidationError({"payment": "Link a valid payment recorded for the same family."})
        reason = "Payment proof accepted" if status == PaymentProof.Status.ACCEPTED else "Payment proof rejected"
        with audit_context(actor, f"{reason}: {note}" if note else reason):
            proof.status = status
            proof.reviewed_by = actor
            proof.reviewed_at = timezone.now()
            proof.review_note = note
            proof.payment = payment
            proof.save()
        return proof


def accept_proof(proof, actor, note="", payment=None):
    """Staff agree the proof shows a payment. Records no money: the payment is
    recorded separately (and may be linked here)."""
    return _review(proof, actor, PaymentProof.Status.ACCEPTED, (note or "").strip(), payment)


def reject_proof(proof, actor, reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A reason is required to reject a payment proof."})
    return _review(proof, actor, PaymentProof.Status.REJECTED, reason)


PAYMENT_INFO_FIELDS = ("bank_name", "account_name", "account_number", "instructions", "reference_instructions")


def update_payment_info(actor, values, qr_upload=None, remove_qr=False):
    if not can(actor, Cap.FINANCE_PAYMENT_INFO_MANAGE):
        raise PermissionDenied("You do not have permission to change the academy's payment information.")
    storage = private_storage()
    new_name = None
    if qr_upload is not None:
        try:
            kind = validate_upload(qr_upload, ("png", "jpg"), settings.PAYMENT_QR_MAX_BYTES)
        except ValidationError as exc:
            raise ValidationError({"qr_code": exc.messages}) from None
        new_name = storage.save(stored_name("payment-qr", kind), qr_upload)
    with transaction.atomic(), audit_context(actor, "Academy payment information updated"):
        info = AcademyPaymentInfo.objects.select_for_update().order_by("pk").first() or AcademyPaymentInfo()
        old_name = info.qr_code.name if info.qr_code else None
        for field in PAYMENT_INFO_FIELDS:
            if field in values:
                setattr(info, field, (values[field] or "").strip())
        if new_name:
            info.qr_code = new_name
            info.qr_content_type = CONTENT_TYPES[kind]
        elif remove_qr:
            info.qr_code = ""
            info.qr_content_type = ""
        info.updated_by = actor
        info.save()
    if old_name and (new_name or remove_qr):
        transaction.on_commit(lambda: storage.delete(old_name))
    return info
