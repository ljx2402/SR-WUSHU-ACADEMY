from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import AuditedModel, AuditLog
from .utils import diff, record, snapshot


@receiver(pre_save)
def _capture_before(sender, instance, raw=False, **kwargs):
    if raw or not isinstance(instance, AuditedModel):
        return
    instance._audit_before = None
    if instance.pk is not None:
        previous = sender._default_manager.filter(pk=instance.pk).first()
        if previous is not None:
            instance._audit_before = snapshot(previous)


@receiver(post_save)
def _record_save(sender, instance, created, raw=False, **kwargs):
    if raw or not isinstance(instance, AuditedModel):
        return
    after = snapshot(instance)
    before = getattr(instance, "_audit_before", None)
    if created or before is None:
        record(instance, AuditLog.Action.CREATE, changes={k: {"from": None, "to": v} for k, v in after.items()})
    else:
        changes = diff(before, after)
        if changes:
            record(instance, AuditLog.Action.UPDATE, changes=changes)
    instance._audit_before = after


@receiver(post_delete)
def _record_delete(sender, instance, **kwargs):
    if not isinstance(instance, AuditedModel):
        return
    record(instance, AuditLog.Action.DELETE, changes={k: {"from": v, "to": None} for k, v in snapshot(instance).items()})
