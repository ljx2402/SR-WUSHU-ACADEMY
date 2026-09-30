/**
 * Family A: one parent, three children (A1 Aaron, A2 Beth, A3 Chen), one
 * family invoice covering all three. Shapes follow the Django serializers.
 * Family B's records are never returned: the fake API answers 404, as the
 * real one does for another family's ids.
 */
import type {
  AttendanceRecord, AttendanceSummaryResponse, Charge, Competition, CompetitionRegistration, Family, Invoice,
  OwnStudent, Paginated, Payment, Receipt, TrainingSession,
} from "../../api/types";
import { academyToday, addDays } from "../../domain/format";
import { json, makeMe } from "../../test/helpers";

export const today = academyToday();
export const page = <T,>(results: T[], count = results.length, next: string | null = null): Paginated<T> =>
  ({ count, next, previous: null, results });

export const children = [
  { id: 11, student_no: "A1", full_name: "Aaron Tan" },
  { id: 12, student_no: "A2", full_name: "Beth Tan" },
  { id: 13, student_no: "A3", full_name: "Chen Tan" },
];

export const parentMe = makeMe(["PARENT"], {
  name: "Parent A",
  parent: { id: 1, full_name: "Parent A", phone: "0191", email: "a@example.com" },
  children,
});

function enrollment(id: number, classId: number, name: string, coach: string) {
  return { id, training_class: classId, class_name: name, category: "SCHOOL", team_name: null, coach_name: coach,
           start_date: addDays(today, -200), end_date: null };
}

export const students: Record<number, OwnStudent> = {
  11: { id: 11, student_no: "A1", full_name: "Aaron Tan", chinese_name: "陈亚伦", gender: "M", date_of_birth: "2014-05-01",
        age: 12, ic_number: "140501101234", nationality: "Malaysian", school: "SK Damai", phone: "", email: "",
        address: "1 Jalan Damai", medical_notes: "Asthma: carries inhaler", join_date: "2025-01-10", status: "ACTIVE",
        family: 7, family_name: "Tan family", guardians: [{ name: "Parent A", relationship: "MOTHER", phone: "0191" }],
        current_classes: [enrollment(1, 100, "Junior Taolu", "Coach Lim")] },
  12: { id: 12, student_no: "A2", full_name: "Beth Tan", chinese_name: "", gender: "F", date_of_birth: "2012-03-02",
        age: 14, ic_number: "120302105678", status: "ACTIVE", join_date: "2024-06-01", family: 7,
        family_name: "Tan family", medical_notes: "", guardians: [],
        current_classes: [enrollment(2, 200, "Senior Sanda", "Coach Wong")] },
  13: { id: 13, student_no: "A3", full_name: "Chen Tan", chinese_name: "", gender: "M", date_of_birth: "2017-08-09",
        age: 9, status: "ON_LEAVE", join_date: "2025-09-01", family: 7, family_name: "Tan family", medical_notes: "",
        guardians: [], current_classes: [enrollment(3, 100, "Junior Taolu", "Coach Lim")] },
};

export const summaries: Record<number, AttendanceSummaryResponse> = {
  // 14 present, 1 absent, 1 not marked: 14 / 15 = 93.33% (the backend's number, not recomputed here).
  11: { student: 11, percentage: "93.33", present: 14, late: 0, absent: 1, excused: 0, marked: 15, attended: 14,
        expected: 16, unmarked: 1 },
  12: { student: 12, percentage: "87.50", present: 7, late: 0, absent: 1, excused: 0, marked: 8, attended: 7,
        expected: 8, unmarked: 0 },
  13: { student: 13, percentage: null, present: 0, late: 0, absent: 0, excused: 0, marked: 0, attended: 0,
        expected: 2, unmarked: 2 },
};

export const family: Family = {
  id: 7, name: "Tan family", is_active: true,
  students: children.map(({ id, student_no, full_name }) => ({ id, student_no, full_name })),
};

function session(id: number, classId: number, className: string, date: string, start: string,
                 phase: TrainingSession["phase"] = "UPCOMING"): TrainingSession {
  return { id, training_class: classId, class_name: className, date, start_time: start, end_time: "20:00:00",
           venue: "Hall A", status: phase === "CANCELLED" ? "CANCELLED" : "SCHEDULED", phase, notes: "",
           coaches: [{ id: id * 10, coach: 1, coach_name: classId === 100 ? "Coach Lim" : "Coach Wong",
                       role: "REGULAR", status: "ASSIGNED", replaces: null }] };
}

export const sessions: TrainingSession[] = [
  session(501, 100, "Junior Taolu", addDays(today, 1), "17:00:00"),
  session(502, 200, "Senior Sanda", addDays(today, 2), "18:00:00", "CANCELLED"),
  session(503, 999, "Class the parent coaches", addDays(today, 1), "09:00:00"),
];

export const attendance: Record<number, AttendanceRecord[]> = {
  11: [
    { id: 901, session: 400, session_date: addDays(today, -2), class_name: "Junior Taolu", student: 11,
      student_name: "Aaron Tan", status: "PRESENT" },
    { id: 902, session: 401, session_date: addDays(today, -4), class_name: "Junior Taolu", student: 11,
      student_name: "Aaron Tan", status: "ABSENT" },
  ],
  12: [
    { id: 903, session: 402, session_date: addDays(today, -3), class_name: "Senior Sanda", student: 12,
      student_name: "Beth Tan", status: "PRESENT" },
  ],
  13: [],
};

function item(id: number, student: number, name: string, no: string, amount: string, paid: string) {
  return { id, student, student_no: no, student_name: name, description: "Monthly class fee", fee_type: "TUITION",
           period_start: "2026-10-01", period_end: "2026-10-31", quantity: "1.00", unit_amount: amount,
           discount: "0.00", amount, amount_paid: paid, is_active: true };
}

export const invoice: Invoice = {
  id: 301, number: "INV-2026-0012", family: 7, family_name: "Tan family", kind: "STANDARD", status: "PARTIALLY_PAID",
  currency: "MYR", issue_date: "2026-10-01", due_date: "2026-10-15", subtotal: "550.00", discount_total: "0.00",
  total: "550.00", amount_paid: "300.00", balance_due: "250.00", amount_refunded: "0.00", voided_at: null,
  void_reason: "",
  items: [item(1, 11, "Aaron Tan", "A1", "220.00", "220.00"), item(2, 12, "Beth Tan", "A2", "280.00", "80.00"),
          item(3, 13, "Chen Tan", "A3", "50.00", "0.00")],
};

export const paidInvoice: Invoice = {
  ...invoice, id: 300, number: "INV-2026-0009", status: "PAID", amount_paid: "100.00", balance_due: "0.00",
  total: "100.00", subtotal: "100.00", items: [item(9, 11, "Aaron Tan", "A1", "100.00", "100.00")],
};

export const charges: Charge[] = [
  { id: 41, student: 11, student_name: "Aaron Tan", fee_type: "TUITION", description: "Monthly class fee",
    period_start: "2026-10-01", period_end: "2026-10-31", amount: "220.00", amount_paid: "220.00", balance: "0.00",
    due_date: "2026-10-15", status: "PAID", invoice_number: "INV-2026-0012" },
  { id: 42, student: 12, student_name: "Beth Tan", fee_type: "TUITION", description: "Monthly class fee",
    period_start: "2026-10-01", period_end: "2026-10-31", amount: "280.00", amount_paid: "80.00", balance: "200.00",
    due_date: "2026-10-15", status: "PARTIAL", invoice_number: "INV-2026-0012" },
];

export const payment: Payment = {
  id: 601, number: "PAY-2026-0030", amount: "300.00", method: "BANK_TRANSFER", reference: "MBB123",
  received_at: "2026-10-05T10:00:00+08:00", status: "VALID", receipt_id: 701, receipt_number: "RCP-2026-0030",
  allocations: [
    { id: 1, invoice: 301, invoice_number: "INV-2026-0012", student: 11, student_name: "Aaron Tan",
      description: "Monthly class fee", amount: "220.00" },
    { id: 2, invoice: 301, invoice_number: "INV-2026-0012", student: 12, student_name: "Beth Tan",
      description: "Monthly class fee", amount: "80.00" },
  ],
};

export const receipt: Receipt = {
  id: 701, number: "RCP-2026-0030", payment: 601, issued_at: "2026-10-05T10:01:00+08:00", total: "300.00",
  is_void: false, void_reason: null,
  content: {
    academy: { name: "SR Wushu Academy", registration_no: "SSM 123", address: "Kuala Lumpur", phone: "03-1234",
               email: "office@example.com" },
    number: "RCP-2026-0030", payment_number: "PAY-2026-0030", issued_at: "2026-10-05T10:01:00+08:00",
    currency: "MYR", students: ["Aaron Tan (A1)", "Beth Tan (A2)"], payer_reference: "Parent A",
    payment_date: "2026-10-05T10:00:00+08:00", payment_method: "Bank transfer", reference: "MBB123",
    lines: [
      { invoice_number: "INV-2026-0012", student_no: "A1", student_name: "Aaron Tan", description: "Monthly class fee",
        fee_type: "Tuition / class fee", period_start: "2026-10-01", period_end: "2026-10-31", line_amount: "220.00",
        amount_paid: "220.00" },
      { invoice_number: "INV-2026-0012", student_no: "A2", student_name: "Beth Tan", description: "Monthly class fee",
        fee_type: "Tuition / class fee", period_start: "2026-10-01", period_end: "2026-10-31", line_amount: "280.00",
        amount_paid: "80.00" },
    ],
    total: "300.00", issued_by: "Office Admin",
  },
};

export const openCompetition: Competition = {
  id: 81, name: "State Wushu Open", organiser: "State Wushu Association", venue: "Stadium Juara",
  start_date: addDays(today, 30), end_date: addDays(today, 31), registration_deadline: addDays(today, 10),
  status: "OPEN", allow_parent_registration: true, max_events_per_student: 2, age_reference_date: null,
  description: "Annual state championship.", is_open: true,
  events: [
    { id: 811, competition: 81, event_type: "CHANGQUAN", name: "Changquan U12", gender: "OPEN", min_age: null,
      max_age: 12, weight_class: "", fee: "50.00", max_entries: null },
    { id: 812, competition: 81, event_type: "NANQUAN", name: "Nanquan Girls", gender: "F", min_age: 10, max_age: 17,
      weight_class: "", fee: "60.00", max_entries: null },
  ],
};

export const closedCompetition: Competition = {
  ...openCompetition, id: 82, name: "National Junior Cup", status: "CLOSED", is_open: false,
  registration_deadline: addDays(today, -5), events: [],
};

export const registrations: CompetitionRegistration[] = [
  { id: 91, competition: 81, competition_name: "State Wushu Open", event: 811, event_name: "Changquan U12",
    student: 11, student_name: "Aaron Tan", status: "PENDING", registered_at: "2026-10-01T09:00:00+08:00", notes: "",
    fee: "50.00", fee_status: "UNPAID", invoice: { id: 305, number: "INV-2026-0020", balance_due: "50.00" },
    result: null },
  { id: 92, competition: 81, competition_name: "State Wushu Open", event: 812, event_name: "Nanquan Girls",
    student: 12, student_name: "Beth Tan", status: "CONFIRMED", registered_at: "2026-09-20T09:00:00+08:00", notes: "",
    fee: "60.00", fee_status: "PAID", invoice: null, result: null },
];

/** 1×1 PNG as the backend returns it (data: URI). */
export const QR = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

export const paymentInfo = {
  configured: true, bank_name: "Maybank", account_name: "SR Wushu Academy", account_number: "5140 1234 5678",
  instructions: "Transfer the amount due, then upload your proof.",
  reference_instructions: "Use the invoice number as the payment reference.", qr_code: QR,
  updated_at: "2026-09-01T10:00:00+08:00",
};

type Req = { url: URL; body: unknown };

/** The API as Parent A sees it. Anything else (e.g. Family B's ids) is a 404. */
export function parentRoutes(overrides: Record<string, unknown> = {}) {
  const routes: Record<string, unknown> = {
    "GET /api/me/": json(parentMe),
    "GET /api/families/": json(page([family])),
    "GET /api/sessions/": json(page(sessions)),
    "GET /api/attendance/": ({ url }: Req) => json(page(attendance[Number(url.searchParams.get("student"))] ?? [])),
    "GET /api/charges/": ({ url }: Req) => {
      const student = url.searchParams.get("student");
      return json(page(student ? charges.filter((c) => c.student === Number(student)) : charges));
    },
    "GET /api/invoices/": ({ url }: Req) => {
      if (url.searchParams.get("outstanding") === "1") return json(page([invoice]));
      if (url.searchParams.get("status") === "PAID") return json(page([paidInvoice]));
      if (url.searchParams.get("status")) return json(page([]));
      return json(page([invoice, paidInvoice]));
    },
    "GET /api/invoices/301/": json(invoice),
    "GET /api/payments/": json(page([payment])),
    "GET /api/receipts/": json(page([receipt])),
    "GET /api/receipts/701/": json(receipt),
    "GET /api/payment-info/": json(paymentInfo),
    "GET /api/payment-proofs/": json(page([])),
    "GET /api/competitions/": json(page([openCompetition, closedCompetition])),
    "GET /api/competitions/81/": json(openCompetition),
    "GET /api/competitions/82/": json(closedCompetition),
    "GET /api/competition-registrations/": ({ url }: Req) => {
      const competition = url.searchParams.get("competition");
      return json(page(competition ? registrations.filter((r) => r.competition === Number(competition)) : registrations));
    },
  };
  for (const id of [11, 12, 13]) {
    routes[`GET /api/students/${id}/`] = json(students[id]);
    routes[`GET /api/students/${id}/attendance-summary/`] = json(summaries[id]);
  }
  return { ...routes, ...overrides } as Record<string, never>;
}
