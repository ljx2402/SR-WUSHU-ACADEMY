"""Competition registration forms: configured per competition by staff,
rendered by the Parent Portal, validated here.

* SYSTEM fields (student, event, competition, fee, status, payment, invoice,
  dates, notes) belong to the application and are never form fields.
* CUSTOM fields are the competition's own questions (RegistrationFormField),
  of a fixed set of types. No code, no HTML: answers are plain values.
* Staff edit a working copy; ``publish_form`` freezes it into
  ``Competition.published_form`` with a new version. Parents only see and
  submit the published version.
* A registration stores what was asked and answered (key, label, type, value)
  with the form version, so later form changes never alter past answers.
"""

import datetime
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import EmailValidator
from django.db import transaction
from django.utils import timezone

from apps.accounts.capabilities import Cap, can
from apps.audit.context import audit_context

from .models import Competition, RegistrationFormField

FieldType = RegistrationFormField.FieldType

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
RESERVED_KEYS = frozenset({
    "id", "student", "student_id", "event", "event_id", "competition", "competition_id", "status", "fee",
    "fee_status", "payment", "payment_status", "invoice", "charge", "registered_at", "registered_by", "notes",
    "result", "responses", "form_version", "form_responses",
})
# Custom fields must never collect secrets or credentials.
SENSITIVE = re.compile(
    r"pass\s*(word|code|phrase)|\bpin\b|\botp\b|token|api[\s_-]*key|secret|\bcvv\b|\bcvc\b|card\s*(number|no)|"
    r"credit\s*card|security\s*code|login|username|bank\s*(password|login)",
    re.IGNORECASE,
)
CHOICE_TYPES = (FieldType.SINGLE_SELECT, FieldType.MULTI_SELECT)
TEXT_LIMITS = {FieldType.TEXT: 200, FieldType.LONG_TEXT: 2000, FieldType.EMAIL: 254, FieldType.PHONE: 20}
MAX_FIELDS = 30
MAX_OPTIONS = 50
PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9 ()\-]{5,19}$")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def validate_field_definition(field):
    errors = {}
    if not KEY_PATTERN.match(field.key or ""):
        errors["key"] = "Use lowercase letters, digits and _ (start with a letter, up to 40 characters)."
    elif field.key in RESERVED_KEYS:
        errors["key"] = f"“{field.key}” is a system field and cannot be a custom field."
    if not (field.label or "").strip():
        errors["label"] = "A label is required."
    for name in ("key", "label", "help_text", "placeholder"):
        if SENSITIVE.search(getattr(field, name) or ""):
            errors[name] = "Registration forms must not collect passwords, PINs, card details, tokens or keys."
    if field.field_type not in FieldType.values:
        errors["field_type"] = "Unknown field type."
    options = field.options if isinstance(field.options, list) else None
    if field.field_type in CHOICE_TYPES:
        if not options:
            errors["options"] = "Add at least one option."
        elif len(options) > MAX_OPTIONS:
            errors["options"] = f"At most {MAX_OPTIONS} options."
        elif any(not isinstance(o, str) or not o.strip() or len(o) > 100 for o in options):
            errors["options"] = "Options must be non-empty text of up to 100 characters."
        elif len({o.strip().lower() for o in options}) != len(options):
            errors["options"] = "Options must be unique."
        elif any(SENSITIVE.search(o) for o in options):
            errors["options"] = "Options must not refer to passwords, PINs, card details, tokens or keys."
    elif options:
        errors["options"] = "Only single and multiple choice fields have options."
    if field.max_length is not None:
        limit = TEXT_LIMITS.get(field.field_type)
        if field.field_type not in (FieldType.TEXT, FieldType.LONG_TEXT):
            errors["max_length"] = "Only text fields have a maximum length."
        elif not 1 <= field.max_length <= limit:
            errors["max_length"] = f"Between 1 and {limit}."
    if (field.min_value is not None or field.max_value is not None) and field.field_type != FieldType.NUMBER:
        errors["min_value"] = "Only number fields have a minimum or maximum."
    if field.min_value is not None and field.max_value is not None and field.min_value > field.max_value:
        errors["max_value"] = "The maximum must not be below the minimum."
    if errors:
        raise ValidationError(errors)


def _definition(field):
    return {
        "key": field.key,
        "label": field.label.strip(),
        "type": field.field_type,
        "required": field.required,
        "help_text": field.help_text,
        "placeholder": field.placeholder,
        "options": [o.strip() for o in field.options] if field.field_type in CHOICE_TYPES else [],
        "max_length": field.max_length or TEXT_LIMITS.get(field.field_type),
        "min_value": str(field.min_value) if field.min_value is not None else None,
        "max_value": str(field.max_value) if field.max_value is not None else None,
    }


def draft_form(competition):
    """The working copy as it would be published (used for preview)."""
    fields = competition.form_fields.filter(is_active=True).order_by("order", "id")
    return [_definition(f) for f in fields]


def _require_manager(actor):
    if not can(actor, Cap.COMPETITION_MANAGE):
        raise PermissionDenied("You do not have permission to configure competition registration forms.")


@transaction.atomic
def publish_form(competition, actor):
    _require_manager(actor)
    competition = Competition.objects.select_for_update().get(pk=competition.pk)
    definition = draft_form(competition)
    if len(definition) > MAX_FIELDS:
        raise ValidationError(f"A registration form can have at most {MAX_FIELDS} custom fields.")
    for field in competition.form_fields.filter(is_active=True):
        validate_field_definition(field)
    changed = definition != competition.published_form
    with audit_context(actor, f"Registration form published (version "
                              f"{competition.form_version + 1 if changed else competition.form_version})"):
        if changed:
            competition.form_version += 1
            competition.published_form = definition
        competition.form_status = Competition.FormStatus.PUBLISHED
        competition.form_published_at = timezone.now()
        competition.save()
    return competition


@transaction.atomic
def unpublish_form(competition, actor):
    """Stops new parent registrations until the form is published again."""
    _require_manager(actor)
    competition = Competition.objects.select_for_update().get(pk=competition.pk)
    with audit_context(actor, "Registration form unpublished"):
        competition.form_status = Competition.FormStatus.DRAFT
        competition.save()
    return competition


@transaction.atomic
def reorder_fields(competition, keys, actor):
    _require_manager(actor)
    fields = {f.key: f for f in competition.form_fields.all()}
    if sorted(keys) != sorted(fields):
        raise ValidationError({"keys": "List every field key of this form exactly once."})
    with audit_context(actor, "Registration form fields reordered"):
        for position, key in enumerate(keys, start=1):
            field = fields[key]
            if field.order != position:
                field.order = position
                field.save()
    return list(competition.form_fields.order_by("order", "id"))


# --------------------------------------------------------------------------- answers


def _text(value, limit):
    if not isinstance(value, str):
        raise ValidationError("Enter text.")
    value = CONTROL_CHARS.sub("", value).strip()
    if len(value) > limit:
        raise ValidationError(f"At most {limit} characters.")
    return value


def _clean_value(field, raw):
    """(value, display) for one answer, or raise ValidationError. Empty → (None, "")."""
    kind = field["type"]
    if raw is None or raw == "" or raw == []:
        return None, ""
    if kind in (FieldType.TEXT, FieldType.LONG_TEXT):
        value = _text(raw, field.get("max_length") or TEXT_LIMITS[kind])
        return (value, value) if value else (None, "")
    if kind == FieldType.EMAIL:
        value = _text(raw, TEXT_LIMITS[kind])
        EmailValidator(message="Enter a valid email address.")(value)
        return value, value
    if kind == FieldType.PHONE:
        value = _text(raw, TEXT_LIMITS[kind])
        if not PHONE_PATTERN.match(value):
            raise ValidationError("Enter a valid phone number.")
        return value, value
    if kind == FieldType.NUMBER:
        if isinstance(raw, bool):
            raise ValidationError("Enter a number.")
        try:
            number = Decimal(str(raw).strip())
        except InvalidOperation:
            raise ValidationError("Enter a number.") from None
        if not number.is_finite() or abs(number) >= Decimal("1e10"):
            raise ValidationError("Enter a number.")
        if field.get("min_value") is not None and number < Decimal(field["min_value"]):
            raise ValidationError(f"Must be at least {field['min_value']}.")
        if field.get("max_value") is not None and number > Decimal(field["max_value"]):
            raise ValidationError(f"Must be at most {field['max_value']}.")
        text = format(number.normalize(), "f")
        return text, text
    if kind == FieldType.DATE:
        try:
            value = datetime.date.fromisoformat(str(raw))
        except ValueError:
            raise ValidationError("Enter a date as YYYY-MM-DD.") from None
        return value.isoformat(), value.isoformat()
    if kind == FieldType.YES_NO:
        if raw in (True, "true", "yes"):
            return True, "Yes"
        if raw in (False, "false", "no"):
            return False, "No"
        raise ValidationError("Choose yes or no.")
    if kind == FieldType.SINGLE_SELECT:
        if raw not in field["options"]:
            raise ValidationError("Choose one of the listed options.")
        return raw, raw
    if kind == FieldType.MULTI_SELECT:
        if not isinstance(raw, list) or any(v not in field["options"] for v in raw):
            raise ValidationError("Choose from the listed options.")
        chosen = [o for o in field["options"] if o in raw]  # form order, no duplicates
        return chosen, ", ".join(chosen)
    raise ValidationError("Unsupported field.")


def validate_responses(competition, responses, *, enforce_required=True):
    """Check answers against the PUBLISHED form; returns the snapshot to store.

    Unknown keys are refused. Errors are keyed ``responses.<key>`` so the
    client can show them at the right field."""
    if responses is None:
        responses = {}
    if not isinstance(responses, dict):
        raise ValidationError({"responses": "Send the form answers as an object of field keys and values."})
    form = competition.published_form or []
    known = {f["key"] for f in form}
    errors = {f"responses.{key}": "This question is not part of the registration form."
              for key in responses if key not in known}
    snapshot = []
    for field in form:
        try:
            value, display = _clean_value(field, responses.get(field["key"]))
        except ValidationError as exc:
            errors[f"responses.{field['key']}"] = exc.messages
            continue
        if value is None and field.get("required") and enforce_required:
            errors[f"responses.{field['key']}"] = "This field is required."
            continue
        snapshot.append({"key": field["key"], "label": field["label"], "type": field["type"], "value": value,
                         "display": display})
    if errors:
        raise ValidationError(errors)
    return snapshot
