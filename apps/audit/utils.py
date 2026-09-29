import datetime
import decimal
import uuid

from django.contrib.contenttypes.models import ContentType

from .context import get_actor, get_reason
from .models import AuditLog


def _serialize(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, (decimal.Decimal, float, uuid.UUID)):
        return str(value)
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    return str(value)


def snapshot(instance):
    exclude = set(getattr(instance, "audit_exclude", ()))
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in exclude:
            continue
        data[field.attname] = _serialize(getattr(instance, field.attname))
    return data


def diff(before, after):
    keys = set(before) | set(after)
    return {
        key: {"from": before.get(key), "to": after.get(key)}
        for key in sorted(keys)
        if before.get(key) != after.get(key)
    }


def record(instance, action, changes=None, reason="", category=None, actor=None):
    return AuditLog.objects.create(
        actor=actor if actor is not None else get_actor(),
        category=category or getattr(instance, "audit_category", "GENERAL"),
        action=action,
        content_type=ContentType.objects.get_for_model(instance.__class__),
        object_id=str(instance.pk),
        object_repr=str(instance)[:255],
        changes=changes or {},
        reason=reason or get_reason(),
    )


def history_for(instance):
    return AuditLog.objects.filter(
        content_type=ContentType.objects.get_for_model(instance.__class__),
        object_id=str(instance.pk),
    ).select_related("actor")
