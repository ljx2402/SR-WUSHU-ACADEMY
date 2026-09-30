"""What the audit log may keep of sensitive values.

Audit entries keep enough to show *that* something changed and to recognize
the value, never the value itself:

* identity and account numbers (IC / NRIC / MyKid / passport, bank account,
  EPF, SOCSO): only the last 4 characters, e.g. ``********1234``;
* medical notes: never copied; only whether a note is present and its length;
* credentials and secrets (password, token, key, secret, auth version):
  never copied at all.

Applied by ``apps.audit.utils.record`` to every entry, whatever the model."""

import re

IDENTIFIER_FIELDS = {"ic_number", "nric", "passport_no", "passport_number", "bank_account_no", "epf_no", "socso_no"}
MEDICAL_FIELDS = {"medical_notes"}
# Field names that *are* a credential: password, new_password, token, api_key, secret_key, key...
# (not counters such as "tokens_revoked").
SECRET_FIELD = re.compile(r"(?i)(^|_)(password|passwd|secret|token|api_?key|private_?key|auth_version|key)$")

SECRET = "[SECRET]"


def mask_identifier(value, keep=4):
    if value is None:
        return None
    text = str(value)
    if not text:
        return text
    if len(text) <= keep:
        return "*" * len(text)
    return "*" * (len(text) - keep) + text[-keep:]


def mask_medical(value):
    if value is None:
        return None
    text = str(value)
    return f"[MEDICAL NOTE – {len(text)} characters]" if text else ""


def mask_value(field, value):
    name = str(field)
    if SECRET_FIELD.search(name):
        return SECRET if value not in (None, "") else value
    if name in IDENTIFIER_FIELDS:
        return mask_identifier(value)
    if name in MEDICAL_FIELDS:
        return mask_medical(value)
    return value


def mask_changes(changes):
    """Mask a changes dict: {field: {"from": x, "to": y}} or {field: value}."""
    masked = {}
    for field, change in (changes or {}).items():
        if isinstance(change, dict) and set(change) <= {"from", "to"}:
            masked[field] = {k: mask_value(field, v) for k, v in change.items()}
        else:
            masked[field] = mask_value(field, change)
    return masked
