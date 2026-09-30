/**
 * Shapes of the Django REST API responses used by the app.
 * Money and percentages arrive as decimal strings ("1234.50"): never parse
 * them to floats for arithmetic.
 */

export type Role = "SUPER_ADMIN" | "ADMIN" | "FINANCE_ADMIN" | "COACH" | "PARENT" | "STUDENT";

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface PersonRef {
  id: number;
  student_no?: string;
  full_name: string;
}

/** GET /api/me/ */
export interface Me {
  id: number;
  username: string;
  name: string;
  role: Role | null;
  roles: Role[];
  capabilities: string[];
  parent?: { id: number; full_name: string; phone?: string; alt_phone?: string; email?: string; address?: string };
  children?: PersonRef[];
  coach?: { id: number; full_name: string };
  classes?: { id: number; name: string }[];
  substitute_sessions?: { session: number; access_ends_at: string | null }[];
  student?: PersonRef;
}

export interface TokenResponse {
  token: string;
}

export type SessionPhase = "UPCOMING" | "IN_PROGRESS" | "COMPLETED" | "CANCELLED";

export interface SessionCoachSlot {
  id: number;
  coach: number;
  coach_name: string;
  role: "REGULAR" | "SUBSTITUTE";
  status: string;
  replaces: number | null;
}

/** GET /api/sessions/ */
export interface TrainingSession {
  id: number;
  training_class: number;
  class_name: string;
  date: string; // YYYY-MM-DD (academy time zone)
  start_time: string; // HH:MM:SS
  end_time: string;
  venue: string;
  status: "SCHEDULED" | "CANCELLED";
  phase: SessionPhase;
  notes: string;
  coaches: SessionCoachSlot[];
}

export interface PayrollRunSummary {
  id: number;
  year: number;
  month: number;
  period_start: string;
  period_end: string;
  status: "DRAFT" | "READY" | "FINALIZED";
  issue_count: number;
}

/* ------------------------------------------------------------------ parent portal (Phase 6B) */

export type StudentStatus = "ACTIVE" | "ON_LEAVE" | "SUSPENDED" | "WITHDRAWN" | "GRADUATED";

/** GET /api/students/ and /api/students/:id/ (OwnStudentSerializer, a parent's own child). */
export interface StudentEnrollment {
  id: number;
  training_class: number;
  class_name: string;
  category: string;
  team_name: string | null;
  coach_name: string | null;
  start_date: string;
  end_date: string | null;
}

export interface OwnStudent {
  id: number;
  student_no: string;
  full_name: string;
  chinese_name: string;
  gender: "M" | "F";
  date_of_birth: string | null;
  age: number | null;
  ic_number?: string;
  nationality?: string;
  school?: string;
  phone?: string;
  email?: string;
  address?: string;
  medical_notes?: string;
  join_date: string | null;
  status: StudentStatus;
  family: number | null;
  family_name: string | null;
  guardians?: { name: string; relationship: string; phone: string }[];
  current_classes?: StudentEnrollment[];
}

/** GET /api/students/:id/attendance-summary/ */
export interface AttendanceSummaryResponse {
  student: number;
  percentage: string | null;
  present: number;
  late: number;
  absent: number;
  excused: number;
  marked: number;
  attended: number;
  expected: number;
  unmarked: number;
}

/** GET /api/attendance/ */
export interface AttendanceRecord {
  id: number;
  session: number;
  session_date: string;
  class_name: string;
  student: number;
  student_name: string;
  status: "UNMARKED" | "PRESENT" | "ABSENT" | "LATE" | "EXCUSED";
}

/** GET /api/families/ (no billing contact: a family is the students it contains). */
export interface Family {
  id: number;
  name: string;
  is_active: boolean;
  students: { id: number; student_no: string; full_name: string }[];
}

/** GET /api/charges/ */
export interface Charge {
  id: number;
  student: number;
  student_name: string;
  fee_type: string;
  description: string;
  period_start: string | null;
  period_end: string | null;
  amount: string;
  amount_paid: string;
  balance: string;
  due_date: string | null;
  status: "UNPAID" | "PARTIAL" | "PAID" | "WAIVED" | "CANCELLED";
  invoice_number: string | null;
}

export interface InvoiceItem {
  id: number;
  student: number | null;
  student_no: string;
  student_name: string;
  description: string;
  fee_type: string;
  period_start: string | null;
  period_end: string | null;
  quantity: string;
  unit_amount: string;
  discount: string;
  amount: string;
  amount_paid: string;
  is_active: boolean;
}

/** GET /api/invoices/ */
export interface Invoice {
  id: number;
  number: string;
  family: number;
  family_name: string;
  kind: string;
  status: "DRAFT" | "ISSUED" | "PARTIALLY_PAID" | "PAID" | "VOID";
  currency: string;
  issue_date: string | null;
  due_date: string | null;
  subtotal: string;
  discount_total: string;
  total: string;
  amount_paid: string;
  balance_due: string;
  amount_refunded: string;
  voided_at: string | null;
  void_reason: string;
  items: InvoiceItem[];
}

export interface PaymentAllocation {
  id: number;
  invoice: number;
  invoice_number: string;
  student: number | null;
  student_name: string;
  description: string;
  amount: string;
}

/** GET /api/payments/ */
export interface Payment {
  id: number;
  number: string;
  amount: string;
  method: string;
  reference: string;
  received_at: string;
  status: "VALID" | "VOIDED";
  allocations: PaymentAllocation[];
  receipt_id: number | null;
  receipt_number: string | null;
}

export interface ReceiptLine {
  invoice_number: string;
  student_no: string;
  student_name: string;
  description: string;
  fee_type: string;
  period_start: string | null;
  period_end: string | null;
  line_amount: string;
  amount_paid: string;
}

/** GET /api/receipts/ (content is the receipt exactly as issued). */
export interface Receipt {
  id: number;
  number: string;
  payment: number;
  issued_at: string;
  total: string;
  is_void: boolean;
  void_reason: string | null;
  content: {
    academy: { name: string; registration_no: string; address: string; phone: string; email: string };
    number: string;
    payment_number: string;
    issued_at: string;
    currency: string;
    students: string[];
    payer_reference: string;
    payment_date: string;
    payment_method: string;
    reference: string;
    lines: ReceiptLine[];
    total: string;
    issued_by: string;
  };
}

export interface CompetitionEvent {
  id: number;
  competition: number;
  event_type: string;
  name: string;
  gender: "M" | "F" | "OPEN";
  min_age: number | null;
  max_age: number | null;
  weight_class: string;
  fee: string;
  max_entries: number | null;
}

/** GET /api/competitions/ (drafts are never returned to parents). */
export interface Competition {
  id: number;
  name: string;
  organiser: string;
  venue: string;
  start_date: string;
  end_date: string;
  registration_deadline: string;
  status: "OPEN" | "CLOSED" | "COMPLETED" | "CANCELLED" | "DRAFT";
  allow_parent_registration: boolean;
  max_events_per_student: number | null;
  age_reference_date: string | null;
  description: string;
  is_open: boolean;
  events: CompetitionEvent[];
}

/** GET /api/competition-registrations/ */
export interface CompetitionRegistration {
  id: number;
  competition: number;
  competition_name: string;
  event: number;
  event_name: string;
  student: number;
  student_name: string;
  status: "PENDING" | "CONFIRMED" | "WITHDRAWN" | "REJECTED";
  registered_at: string;
  notes: string;
  fee: string | null;
  fee_status: Charge["status"] | null;
  invoice: { id: number; number: string; balance_due: string } | null;
  result: { placing: number | null; medal: string; score: string | null; remarks: string } | null;
}
