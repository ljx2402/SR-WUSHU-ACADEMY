import type { ReactNode } from "react";

import type { StaffPaymentProof } from "../api/types";
import { Money, PROOF_STATUS, StatusBadge } from "../domain/finance";
import { Alert, Badge } from "../ui/primitives";

/*
 * Finance staff portal building blocks (Phase 6F). Every status, total and
 * balance shown comes from the backend's finance services; nothing here
 * decides whether something is paid.
 */

export const financeKeys = {
  all: ["finance"] as const,
  dashboard: ["finance", "dashboard"] as const,
  invoices: (query: object) => ["finance", "invoices", query] as const,
  invoice: (id: string) => ["finance", "invoice", id] as const,
  payments: (query: object) => ["finance", "payments", query] as const,
  payment: (id: string) => ["finance", "payment", id] as const,
  methods: ["finance", "methods"] as const,
  proofs: (query: object) => ["finance", "proofs", query] as const,
  proof: (id: string) => ["finance", "proof", id] as const,
  receipts: (query: object) => ["finance", "receipts", query] as const,
  receipt: (id: string) => ["finance", "receipt", id] as const,
  paymentInfo: ["finance", "payment-info"] as const,
};

export const FINANCE_CRUMBS = [{ label: "Finance dashboard", to: "/finance/dashboard" }];

export const INVOICE_KIND: Record<string, string> = { GENERAL: "Fees", COMPETITION: "Competition" };

/** A money value that never wraps between the currency and the digits. */
export function MoneyAmount({ value }: { value: string | number | null | undefined }) {
  return <Money value={value} className="nowrap" />;
}

/**
 * A proof's review state in words (never colour alone, never "Paid"): a proof is
 * evidence, not a payment. Accepted without a recorded payment says so.
 */
export function ProofReviewStatus({ proof }: { proof: StaffPaymentProof }) {
  if (proof.status === "ACCEPTED") {
    if (proof.payment_number) {
      return <span className="badge-row"><Badge tone="success">Accepted</Badge><span className="muted">Payment {proof.payment_number}</span></span>;
    }
    // Only while the invoice is still open does the payment remain to be recorded.
    return awaitsPayment(proof)
      ? <span className="badge-row"><Badge tone="warning">Accepted — payment still requires recording</Badge></span>
      : <span className="badge-row"><Badge tone="success">Accepted</Badge><span className="muted">Invoice {proof.invoice_status === "PAID" ? "paid" : proof.invoice_status.toLowerCase()}</span></span>;
  }
  if (proof.status === "REJECTED") return <StatusBadge map={PROOF_STATUS} value="REJECTED" />;
  return <StatusBadge map={PROOF_STATUS} value="PENDING_REVIEW" />;
}

/** Accepted, no payment linked, and the invoice still open for payment. */
export function awaitsPayment(proof: StaffPaymentProof) {
  return proof.status === "ACCEPTED" && !proof.payment_number
    && (proof.invoice_status === "ISSUED" || proof.invoice_status === "PARTIALLY_PAID");
}

/** Proof reminder shown wherever a proof and a payment could be confused. */
export function ProofIsNotPayment({ children }: { children?: ReactNode }) {
  return (
    <Alert tone="info" title="A payment proof is not a payment.">
      <p>Accepting or rejecting a proof records no money, issues no receipt and does not change the invoice.
        Record the actual payment separately{children ? <> {children}</> : "."}</p>
    </Alert>
  );
}

/** Crypto-quality random key for the payment's Idempotency-Key (one per form). */
export function newIdempotencyKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return `pay-${crypto.randomUUID()}`;
  return `pay-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
