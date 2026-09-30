import type { ReactNode } from "react";

import { Badge, type Tone } from "../ui/primitives";
import { formatMoney } from "./format";

/**
 * Finance building blocks (Phase 6F builds the finance screens on these).
 *
 * The business model they assume, from the backend:
 * - one Family has one family invoice covering several students' charges;
 * - there is no "billing contact" or "bill to parent";
 * - payments are recorded by staff; there is no online payment in the app;
 * - competition fees are invoiced at registration and confirmed by full payment.
 */

type StatusMap = Record<string, { label: string; tone: Tone }>;

export const INVOICE_STATUS: StatusMap = {
  DRAFT: { label: "Draft", tone: "neutral" },
  ISSUED: { label: "Issued (unpaid)", tone: "warning" },
  PARTIALLY_PAID: { label: "Partially paid", tone: "info" },
  PAID: { label: "Paid", tone: "success" },
  VOID: { label: "Void", tone: "danger" },
};

export const PAYMENT_STATUS: StatusMap = {
  VALID: { label: "Recorded", tone: "success" },
  VOIDED: { label: "Voided", tone: "danger" },
};

export const CHARGE_STATUS: StatusMap = {
  UNPAID: { label: "Unpaid", tone: "warning" },
  PARTIAL: { label: "Partially paid", tone: "info" },
  PAID: { label: "Paid", tone: "success" },
  WAIVED: { label: "Waived", tone: "neutral" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
};

export const REGISTRATION_STATUS: StatusMap = {
  PENDING: { label: "Awaiting payment", tone: "warning" },
  CONFIRMED: { label: "Confirmed", tone: "success" },
  WITHDRAWN: { label: "Withdrawn", tone: "neutral" },
  REJECTED: { label: "Rejected", tone: "danger" },
};

export const PAYROLL_STATUS: StatusMap = {
  DRAFT: { label: "Draft", tone: "neutral" },
  READY: { label: "Ready for approval", tone: "info" },
  FINALIZED: { label: "Finalized", tone: "success" },
};

/** Unknown values are shown as-is (neutral) rather than hidden. */
export function StatusBadge({ map, value }: { map: StatusMap; value: string }) {
  const entry = map[value] ?? { label: value, tone: "neutral" as Tone };
  return <Badge tone={entry.tone}>{entry.label}</Badge>;
}

export const InvoiceStatusBadge = ({ status }: { status: string }) => <StatusBadge map={INVOICE_STATUS} value={status} />;
export const PaymentStatusBadge = ({ status }: { status: string }) => <StatusBadge map={PAYMENT_STATUS} value={status} />;
export const ChargeStatusBadge = ({ status }: { status: string }) => <StatusBadge map={CHARGE_STATUS} value={status} />;
export const RegistrationStatusBadge = ({ status }: { status: string }) =>
  <StatusBadge map={REGISTRATION_STATUS} value={status} />;
export const PayrollStatusBadge = ({ status }: { status: string }) => <StatusBadge map={PAYROLL_STATUS} value={status} />;

/** Right-aligned, tabular money. Negative amounts (deductions, refunds) are marked. */
export function Money({ value, className = "" }: { value: string | number | null | undefined; className?: string }) {
  const text = formatMoney(value);
  return <span className={`money${text.startsWith("−") ? " money-negative" : ""} ${className}`.trim()}>{text}</span>;
}

/**
 * Link to a receipt. The printable receipt page is a Django page that needs a
 * staff session; the app's receipt view arrives with the finance UI (6F).
 */
export function ReceiptLink({ number }: { number: string; children?: ReactNode }) {
  return <span className="receipt-ref" title="Receipt view arrives with the finance screens">{number}</span>;
}

/** Heading for finance context: which family, and which students it covers. */
export function FamilyContext({ familyName, students }: { familyName: string; students: string[] }) {
  return (
    <div className="family-context">
      <p className="family-context-name">{familyName}</p>
      {students.length ? <p className="family-context-students">Students: {students.join(", ")}</p> : null}
    </div>
  );
}

export const PAYMENT_METHOD_LABELS: Record<string, string> = {
  CASH: "Cash",
  BANK_TRANSFER: "Bank transfer",
  DUITNOW: "DuitNow / QR",
  FPX: "FPX online banking",
  CARD: "Debit / credit card",
  CHEQUE: "Cheque",
  EWALLET: "E-wallet",
};

export const FEE_TYPE_LABELS: Record<string, string> = {
  TUITION: "Class fee",
  REGISTRATION: "Registration fee",
  UNIFORM: "Uniform",
  WEAPON: "Weapon",
  COMPETITION: "Competition fee",
  OTHER: "Other",
};
