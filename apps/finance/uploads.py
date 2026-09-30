"""Validation and naming for user-uploaded finance files (payment proofs, the
academy's payment QR code).

Uploads are untrusted:
* only PDF, JPEG and PNG are accepted, and the file's first bytes must match
  the type its extension claims (a renamed executable or HTML file is refused);
* size is limited (settings.PAYMENT_PROOF_MAX_BYTES / PAYMENT_QR_MAX_BYTES);
* the stored name is generated (random, no user text), under a dated folder;
  the original name is kept only as a sanitized label for display;
* files live in the "private" storage alias, which is never served directly.
"""

import hashlib
import os
import uuid

from django.core.exceptions import ValidationError
from django.core.files.storage import storages
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils import timezone
from django.utils.functional import LazyObject, empty
from django.utils.text import get_valid_filename

SIGNATURES = {
    "pdf": (b"%PDF-",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
}
EXTENSIONS = {"pdf": "pdf", "png": "png", "jpg": "jpg", "jpeg": "jpg"}
CONTENT_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg"}


class PrivateStorage(LazyObject):
    """The "private" storage alias (settings.STORAGES), resolved lazily like
    Django's default_storage, so the backend can be swapped by configuration
    (e.g. an external store later) and follows settings changes."""

    def _setup(self):
        self._wrapped = storages["private"]


_private = PrivateStorage()


@receiver(setting_changed)
def _reset_private_storage(setting, **kwargs):
    if setting == "STORAGES":
        _private._wrapped = empty


def private_storage():
    """The storage for private uploads."""
    return _private


def detect_kind(upload, allowed):
    """The file's real kind ("pdf", "png", "jpg"), checked against extension and content."""
    name = getattr(upload, "name", "") or ""
    extension = os.path.splitext(name)[1].lower().lstrip(".")
    kind = EXTENSIONS.get(extension)
    if kind is None or kind not in allowed:
        raise ValidationError(f"Upload a {' or '.join(k.upper() for k in allowed)} file.")
    upload.seek(0)
    head = upload.read(16)
    upload.seek(0)
    if not any(head.startswith(signature) for signature in SIGNATURES[kind]):
        raise ValidationError("The file's contents do not match its type. Upload a real "
                              f"{' or '.join(k.upper() for k in allowed)} file.")
    return kind


def validate_upload(upload, allowed, max_bytes):
    if upload is None:
        raise ValidationError("Choose a file to upload.")
    size = getattr(upload, "size", None)
    if not size:
        raise ValidationError("The file is empty.")
    if size > max_bytes:
        raise ValidationError(f"The file is too large (maximum {max_bytes // (1024 * 1024)} MB).")
    return detect_kind(upload, allowed)


def display_name(upload, kind):
    """A safe label from the user's file name (never used as a path)."""
    base = os.path.splitext(os.path.basename(getattr(upload, "name", "") or ""))[0]
    cleaned = get_valid_filename(base)[:80] if base else ""
    return f"{cleaned or 'payment-proof'}.{kind}"


def stored_name(folder, kind):
    now = timezone.now()
    return f"{folder}/{now:%Y}/{now:%m}/{uuid.uuid4().hex}.{kind}"


def sha256_of(upload):
    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    return digest.hexdigest()
