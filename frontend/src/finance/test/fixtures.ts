import type {
  AcademyPaymentInfo, FinanceDashboard, Receipt, StaffInvoice, StaffPayment, StaffPaymentProof,
} from "../../api/types";
import { academyToday } from "../../domain/format";
import { json, makeMe } from "../../test/helpers";

export const today = academyToday();
export const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });

export const financeMe = makeMe(["FINANCE_ADMIN"], { name: "Finance Officer" });
export const adminMe = makeMe(["ADMIN"], { name: "Front Desk" });

export const dashboard: FinanceDashboard = {
  date: today,
  invoices: { unpaid: 2, partially_paid: 1, overdue: 1, outstanding_total: "380.00", competition_open: 1,
              competition_outstanding: "50.00",
              overdue_list: [{ id: 301, number: "INV-2026-0001", family_name: "Tan family", kind: "GENERAL",
                               status: "PARTIALLY_PAID", due_date: "2026-09-01", total: "550.00", balance_due: "250.00" }] },
  payments: { today_count: 1, today_total: "300.00",
              recent: [{ id: 501, number: "PAY-2026-000001", family_name: "Tan family", amount: "300.00", method: "BANK_TRANSFER",
                         received_at: "2026-10-01T10:00:00+08:00", status: "VALID", receipt_number: "SRWA-2026-000001" }] },
  receipts: [{ id: 601, number: "SRWA-2026-000001", issued_at: "2026-10-01T10:00:00+08:00", payer_name: "", total: "300.00", is_void: false }],
  proofs: { pending: 2, accepted_recently: 1, rejected_recently: 0, accepted_awaiting_payment: 1,
            oldest_pending: [{ id: 71, invoice_number: "INV-2026-0003", family_name: "Lee family", amount_claimed: "50.00",
                               uploaded_at: "2026-09-30T09:00:00+08:00" }] },
};

export const invoice: StaffInvoice = {
  id: 301, number: "INV-2026-0001", family: 5, family_name: "Tan family", kind: "GENERAL", status: "PARTIALLY_PAID",
  currency: "MYR", issue_date: "2026-08-25", due_date: "2026-09-01", subtotal: "550.00", discount_total: "0.00",
  total: "550.00", amount_paid: "300.00", balance_due: "250.00", amount_refunded: "0.00", voided_at: null, void_reason: "",
  issued_at: "2026-08-25T09:00:00+08:00", notes: "Staff: matched bank",
  items: [
    { id: 1, student: 11, student_no: "A1", student_name: "Aaron Tan", description: "Monthly class fee", fee_type: "TUITION",
      period_start: null, period_end: null, quantity: "1", unit_amount: "220.00", discount: "0.00", amount: "220.00",
      amount_paid: "220.00", is_active: true, competition_registration: null },
    { id: 2, student: 12, student_no: "A2", student_name: "Beth Tan", description: "Monthly class fee", fee_type: "TUITION",
      period_start: null, period_end: null, quantity: "1", unit_amount: "330.00", discount: "0.00", amount: "330.00",
      amount_paid: "80.00", is_active: true, competition_registration: null },
  ],
};

export const competitionInvoice: StaffInvoice = {
  ...invoice, id: 303, number: "INV-2026-0003", family: 6, family_name: "Lee family", kind: "COMPETITION", status: "ISSUED",
  total: "50.00", amount_paid: "0.00", balance_due: "50.00", notes: "",
  items: [{ ...invoice.items[0], id: 3, student: 21, student_no: "B1", student_name: "Dina Lee", description: "State Open: Open set",
            amount: "50.00", amount_paid: "0.00",
            competition_registration: { id: 91, competition_name: "State Open", event_name: "Open set", status: "PENDING" } }],
};

export const payment: StaffPayment = {
  id: 501, number: "PAY-2026-000001", family: 5, payer_name: "", amount: "300.00", method: "BANK_TRANSFER", reference: "MBB123",
  received_at: "2026-10-01T10:00:00+08:00", status: "VALID", notes: "Staff: matched bank statement",
  allocations: [{ id: 801, invoice: 301, invoice_number: "INV-2026-0001", student: 11, student_name: "Aaron Tan",
                  description: "Monthly class fee", amount: "220.00" },
                { id: 802, invoice: 301, invoice_number: "INV-2026-0001", student: 12, student_name: "Beth Tan",
                  description: "Monthly class fee", amount: "80.00" }],
  receipt_id: 601, receipt_number: "SRWA-2026-000001",
};

export const pendingProof: StaffPaymentProof = {
  id: 71, family: 6, family_name: "Lee family", invoice: 303, invoice_number: "INV-2026-0003", invoice_status: "ISSUED",
  invoice_balance_due: "50.00", uploaded_by_name: "Parent Lee", uploaded_at: "2026-09-30T09:00:00+08:00",
  original_name: "transfer.png", content_type: "image/png", size: 20480, amount_claimed: "50.00",
  payment_date: "2026-09-29", reference: "MBB-777", note: "Paid for Dina", status: "PENDING_REVIEW",
  reviewed_by_name: null, reviewed_at: null, review_note: "", payment: null, payment_number: null,
};
export const acceptedProof: StaffPaymentProof = { ...pendingProof, id: 72, status: "ACCEPTED", reviewed_by_name: "Finance Officer",
  reviewed_at: "2026-09-30T12:00:00+08:00" };

export const receipt: Receipt = {
  id: 601, number: "SRWA-2026-000001", payment: 501, issued_at: "2026-10-01T10:00:00+08:00", total: "300.00", is_void: false,
  void_reason: null,
  content: { academy: { name: "SR Wushu Academy", registration_no: "", address: "", phone: "", email: "" },
             number: "SRWA-2026-000001", payment_number: "PAY-2026-000001", issued_at: "2026-10-01T10:00:00+08:00", currency: "MYR",
             students: ["Aaron Tan", "Beth Tan"], payer_reference: "Tan family", payment_date: "2026-10-01T10:00:00+08:00",
             payment_method: "Bank transfer", reference: "MBB123", total: "300.00", issued_by: "Front Desk",
             lines: [{ invoice_number: "INV-2026-0001", student_no: "A1", student_name: "Aaron Tan", description: "Monthly class fee",
                       fee_type: "TUITION", period_start: null, period_end: null, line_amount: "220.00", amount_paid: "220.00" }] },
};

export const paymentInfo: AcademyPaymentInfo = {
  configured: true, bank_name: "Test Bank", account_name: "SR Wushu Academy", account_number: "5140 1234 5678",
  instructions: "Transfer and upload the slip.", reference_instructions: "Use the invoice number.", qr_code: null,
  updated_at: "2026-09-01T09:00:00+08:00",
};

export const methods = [{ value: "CASH", label: "Cash" }, { value: "BANK_TRANSFER", label: "Bank transfer" },
                        { value: "OTHER", label: "Other" }];

type Req = { url: URL };

export function financeRoutes(overrides: Record<string, unknown> = {}) {
  return {
    "GET /api/me/": json(financeMe),
    "GET /api/finance/dashboard/": json(dashboard),
    "GET /api/invoices/": ({ url }: Req) => {
      const q = url.searchParams;
      let rows = [invoice, competitionInvoice];
      if (q.get("family")) rows = rows.filter((i) => String(i.family) === q.get("family"));
      if (q.get("kind")) rows = rows.filter((i) => i.kind === q.get("kind"));
      if (q.get("search")) rows = rows.filter((i) => `${i.number} ${i.family_name}`.toLowerCase().includes(q.get("search")!.toLowerCase()));
      return json(page(rows));
    },
    "GET /api/invoices/301/": json(invoice),
    "GET /api/invoices/303/": json(competitionInvoice),
    "GET /api/payments/": ({ url }: Req) => json(page(url.searchParams.get("invoice") === "303" ? [] : [payment])),
    "GET /api/payments/501/": json(payment),
    "GET /api/payments/methods/": json(methods),
    "GET /api/refunds/": json(page([])),
    "GET /api/payment-proofs/": ({ url }: Req) => {
      const status = url.searchParams.get("status");
      return json(page([pendingProof, acceptedProof].filter((p) => !status || p.status === status)));
    },
    "GET /api/payment-proofs/71/": json(pendingProof),
    "GET /api/payment-proofs/72/": json(acceptedProof),
    "GET /api/receipts/": json(page([receipt])),
    "GET /api/receipts/601/": json(receipt),
    "GET /api/payment-info/": json(paymentInfo),
    ...overrides,
  } as unknown as Record<string, never>;
}
