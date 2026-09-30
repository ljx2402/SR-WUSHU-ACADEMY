import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { Button } from "./Button";
import { TextAreaField } from "./Field";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Accessible modal dialog: role="dialog", aria-modal, labelled by its title,
 * focus moves inside on open, Tab stays inside, Escape closes, and focus goes
 * back to the element that opened it.
 */
export function Dialog({ open, title, onClose, children, footer, describedBy }: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  describedBy?: string;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    opener.current = document.activeElement as HTMLElement | null;
    const first = panel.current?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel.current)?.focus();
    return () => opener.current?.focus();
  }, [open]);

  if (!open) return null;

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !panel.current) return;
    const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={panel}
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={describedBy}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <h2 id={titleId} className="dialog-title">{title}</h2>
        <div className="dialog-body">{children}</div>
        {footer ? <div className="dialog-footer">{footer}</div> : null}
      </div>
    </div>
  );
}

/**
 * Confirmation for sensitive actions (voids, refunds, corrections, revocations).
 * With `requireReason`, the action stays disabled until a reason is typed; the
 * backend requires and audits the reason too.
 */
export function ConfirmDialog({ open, title, message, confirmLabel, tone = "danger", requireReason = false,
                               reasonLabel = "Reason", busy = false, error, onConfirm, onCancel }: {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel: string;
  tone?: "danger" | "primary";
  requireReason?: boolean;
  reasonLabel?: string;
  busy?: boolean;
  error?: string | null;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState("");
  const messageId = useId();
  useEffect(() => {
    if (open) setReason("");
  }, [open]);
  const missingReason = requireReason && !reason.trim();
  return (
    <Dialog
      open={open}
      title={title}
      onClose={onCancel}
      describedBy={messageId}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant={tone} onClick={() => onConfirm(reason.trim())} disabled={missingReason} busy={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div id={messageId}>{message}</div>
      {requireReason ? (
        <TextAreaField label={reasonLabel} required value={reason} rows={3}
                       hint="Required. It is stored in the audit log." onChange={(e) => setReason(e.target.value)} />
      ) : null}
      {error ? <p className="field-error" role="alert">{error}</p> : null}
    </Dialog>
  );
}
