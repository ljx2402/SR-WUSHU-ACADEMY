"""Log filter that removes credentials and secrets before a record is written."""

import logging
import re

_PATTERNS = [
    # Authorization: Token abc… / Bearer abc…
    (re.compile(r"(?i)\b(token|bearer)\s+[A-Za-z0-9._\-]{8,}"), r"\1 [REDACTED]"),
    # password=…, "password": "…", secret_key=…, api_key=…, sessionid=…, csrftoken=…
    (re.compile(r"(?i)([\"']?(?:password|passwd|secret(?:_key)?|api[_-]?key|token|sessionid|csrftoken|"
                r"authorization)[\"']?\s*[:=]\s*)([\"']?)[^\s\"',;&}]+"), r"\1\2[REDACTED]"),
    # postgres://user:password@host
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://[^:/\s@]+:)[^@\s]+@"), r"\1[REDACTED]@"),
]


def redact(text):
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactSecretsFilter(logging.Filter):
    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:  # never break logging
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True
