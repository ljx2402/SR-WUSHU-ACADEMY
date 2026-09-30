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

export type FormFieldType =
  | "TEXT" | "LONG_TEXT" | "NUMBER" | "DATE" | "SINGLE_SELECT" | "MULTI_SELECT" | "YES_NO" | "EMAIL" | "PHONE";

/** One custom question of a competition's PUBLISHED registration form. */
export interface FormFieldDefinition {
  key: string;
  label: string;
  type: FormFieldType;
  required: boolean;
  help_text: string;
  placeholder: string;
  options: string[];
  max_length: number | null;
  min_value: string | null;
  max_value: string | null;
}

export interface RegistrationFormInfo {
  status: "DRAFT" | "PUBLISHED";
  version: number;
  published_at: string | null;
  fields: FormFieldDefinition[];
}

/** An answer as stored with the registration (label and type frozen at submission). */
export interface FormAnswer {
  key: string;
  label: string;
  type: FormFieldType;
  value: string | number | boolean | string[] | null;
  display: string;
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
  allow_parent_withdrawal: boolean;
  max_events_per_student: number | null;
  age_reference_date: string | null;
  description: string;
  rules: string;
  is_open: boolean;
  events: CompetitionEvent[];
  registration_form: RegistrationFormInfo;
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
  form_version: number | null;
  /** The family's own answers (not returned to coaches). */
  form_responses?: FormAnswer[];
}

/* ------------------------------------------------------------------ finance staff portal (Phase 6F) */

/** GET /api/finance/dashboard/ : totals and lists computed from the finance records. */
export interface FinanceDashboard {
  date: string;
  invoices: {
    unpaid: number; partially_paid: number; overdue: number; outstanding_total: string;
    competition_open: number; competition_outstanding: string;
    overdue_list: { id: number; number: string; family_name: string; kind: string; status: Invoice["status"];
                    due_date: string | null; total: string; balance_due: string }[];
  };
  payments: {
    today_count: number; today_total: string;
    recent: { id: number; number: string; family_name: string; amount: string; method: string; received_at: string;
              status: Payment["status"]; receipt_number: string | null }[];
  };
  receipts: { id: number; number: string; issued_at: string; payer_name: string; total: string; is_void: boolean }[];
  /** Only for payment-proof reviewers. */
  proofs?: {
    pending: number; accepted_recently: number; rejected_recently: number; accepted_awaiting_payment: number;
    oldest_pending: { id: number; invoice_number: string; family_name: string; amount_claimed: string | null;
                      uploaded_at: string }[];
  };
}

/** Invoice as finance staff receive it (lines carry the competition entry they pay for). */
export interface StaffInvoice extends Invoice {
  notes?: string;
  issued_at: string | null;
  items: (InvoiceItem & { competition_registration?: { id: number; competition_name: string; event_name: string;
                                                       status: string } | null })[];
}

export interface StaffPayment extends Payment {
  family: number;
  payer_name: string;
  notes?: string;
}

export interface Refund {
  id: number; number: string; payment: number; allocation: number; amount: string; method: string; reference: string;
  reason: string; refunded_at: string;
}

export interface StaffPaymentProof extends PaymentProof {
  reviewed_by_name?: string | null;
}

/* ------------------------------------------------------------------ staff portal (Phase 6E) */
/* Academy staff views. What each field holds depends on the caller's capabilities: the
 * backend returns the full record to `students.view_all` and a directory to finance. */

/** GET /api/students/ as staff with students.view_all: a minimal row (no IC, contacts or medical). */
export interface StaffStudentRow {
  id: number;
  student_no: string;
  full_name: string;
  chinese_name: string;
  gender?: "M" | "F";
  age?: number | null;
  status: StudentStatus;
  join_date?: string | null;
  family: number | null;
  family_name: string | null;
  current_classes?: { id: number; name: string }[];
  /** Directory rows (finance) carry guardian contacts instead. */
  guardians?: { name: string; relationship: string; phone: string }[];
}

export interface StaffGuardian {
  id: number;
  relationship: string;
  is_primary_contact: boolean;
  is_emergency_contact: boolean;
  parent: { id: number; full_name: string; ic_number: string; phone: string; alt_phone: string; email: string;
            address: string; occupation: string; is_active: boolean };
}

/** GET /api/students/:id/ as staff: the full record (students.view_all) or the directory (finance). */
export interface StaffStudent {
  id: number;
  student_no: string;
  full_name: string;
  chinese_name: string;
  status: StudentStatus;
  family: number | null;
  family_name: string | null;
  gender?: "M" | "F";
  date_of_birth?: string | null;
  age?: number | null;
  ic_number?: string;
  nationality?: string;
  school?: string;
  phone?: string;
  email?: string;
  address?: string;
  medical_notes?: string;
  join_date?: string | null;
  /** Full record: guardianships with the parent; directory: contacts only. */
  guardians?: (StaffGuardian | { name: string; relationship: string; phone: string })[];
  current_classes?: StudentEnrollment[];
}

export interface AuditEntry {
  id: number;
  timestamp: string;
  actor_name: string | null;
  action: string;
  object_repr: string;
  changes: Record<string, unknown>;
  reason: string;
}

export interface StudentHistory {
  status_history: { previous_status: string; status: string; effective_date: string; reason: string }[];
  enrollments: StudentEnrollment[];
  changes: AuditEntry[];
}

export interface ClassSchedule {
  id: number;
  weekday: number;
  weekday_name: string;
  start_time: string;
  end_time: string;
  venue: string;
  effective_from: string | null;
  effective_to: string | null;
}

/** GET /api/classes/ */
export interface TrainingClass {
  id: number;
  code: string;
  name: string;
  category: "SCHOOL" | "ADDITIONAL" | "ELITE";
  program: number;
  program_name: string;
  team: number | null;
  team_name: string | null;
  venue: string;
  capacity: number | null;
  description: string;
  is_active: boolean;
  schedules: ClassSchedule[];
  current_coaches: { id: number; full_name: string }[];
  /** Staff (classes.view_all) only. */
  active_students?: number;
}

export interface Program { id: number; code: string; name: string; is_active: boolean }

/** GET /api/coaches/ (bank details only for coaches.bank_details, never used here). */
export interface CoachRecord { id: number; full_name: string; phone: string; email: string; is_active: boolean }

export interface StaffSessionSlot extends SessionCoachSlot {
  access_starts_at: string | null;
  access_ends_at: string | null;
  authorized_at: string | null;
  revoked_at: string | null;
}

export interface SessionRef { id: number; class_name: string; date: string; start_time: string; end_time: string;
                              unmarked?: number }

/** GET /api/staff/dashboard/ : counts and alerts derived from the current state (no stored notifications). */
export interface StaffDashboard {
  date: string;
  counts: { sessions_today: number; in_progress: number; cancelled_today: number; substitute_covered_today: number;
            active_students?: number; active_classes?: number };
  alerts: { code: "attendance_open" | "attendance_locked" | "session_without_coach" | "class_without_coach";
            label: string; count: number; sessions?: SessionRef[]; classes?: { id: number; name: string }[] }[];
}

/* ------------------------------------------------------------------ student portal */
/* The student is always the signed-in user's own linked record (the backend
 * derives it from their StudentAccount; no student id is ever sent). */

/** GET /api/students/me/ : the basic training profile only (no IC, contacts, family or medical). */
export interface StudentSelfProfile {
  id: number;
  student_no: string;
  full_name: string;
  chinese_name: string;
  gender: "M" | "F";
  age: number | null;
  status: StudentStatus;
  join_date: string | null;
  current_classes: { class_name: string; category: string; team_name: string | null; start_date: string }[];
}

/** GET /api/students/me/sessions/ : when, where, coach names and the student's own attendance. */
export interface StudentSession {
  id: number;
  class_name: string;
  date: string;
  start_time: string;
  end_time: string;
  venue: string;
  status: "SCHEDULED" | "CANCELLED";
  phase: SessionPhase;
  coaches: string[];
  /** Own status once the session has started (UNMARKED if not marked); null before it or if cancelled. */
  my_attendance: "UNMARKED" | "PRESENT" | "ABSENT" | "LATE" | "EXCUSED" | null;
}

/** GET /api/students/me/attendance/ */
export interface StudentAttendanceResponse {
  summary: {
    percentage: string | null;
    present: number; late: number; absent: number; excused: number;
    expected: number; marked: number; unmarked: number;
  };
  sessions: { session: number; date: string; start_time: string; end_time: string; class_name: string;
              status: "UNMARKED" | "PRESENT" | "ABSENT" | "LATE" | "EXCUSED" }[];
}

/** GET /api/students/me/competitions/ : entry and result only (no fee, payment, invoice or form answers). */
export interface StudentCompetitionEntry {
  id: number;
  status: "PENDING" | "CONFIRMED" | "WITHDRAWN" | "REJECTED";
  registered_at: string;
  competition: { id: number; name: string; organiser: string; venue: string; start_date: string; end_date: string;
                 status: string; rules: string };
  event: { name: string; event_type: string; gender: string; min_age: number | null; max_age: number | null;
           weight_class: string };
  result: { placing: number | null; medal: string; score: string | null } | null;
}

/* ------------------------------------------------------------------ payment proofs */

/** GET /api/payment-info/ : how to pay the academy (read-only for parents). */
export interface AcademyPaymentInfo {
  configured: boolean;
  bank_name: string;
  account_name: string;
  account_number: string;
  instructions: string;
  reference_instructions: string;
  /** data: URI of the QR image, or null. */
  qr_code: string | null;
  updated_at: string | null;
}

export type PaymentProofStatus = "PENDING_REVIEW" | "ACCEPTED" | "REJECTED";

/** GET /api/payment-proofs/ : a parent's evidence of a manual payment (not a payment, not a receipt). */
export interface PaymentProof {
  id: number;
  family: number;
  family_name: string;
  invoice: number;
  invoice_number: string;
  invoice_status: Invoice["status"];
  invoice_balance_due: string;
  uploaded_by_name: string | null;
  uploaded_at: string;
  original_name: string;
  content_type: string;
  size: number;
  amount_claimed: string | null;
  payment_date: string | null;
  reference: string;
  note: string;
  status: PaymentProofStatus;
  reviewed_at: string | null;
  review_note: string;
  payment: number | null;
  payment_number: string | null;
}

/* ------------------------------------------------------------------ coach portal (Phase 6C) */

export type SheetState = "NOT_STARTED" | "OPEN" | "COMPLETE" | "LOCKED" | "CANCELLED";

/** GET /api/sessions/coaching/ : a session with the caller's role and the attendance state. */
export interface CoachingSession extends TrainingSession {
  my_role: { role: "REGULAR" | "SUBSTITUTE"; status: string | null; access_ends_at: string | null } | null;
  attendance: {
    state: SheetState;
    coach_edit_deadline: string;
    expected: number;
    marked: number;
    unmarked: number;
    percentage: string | null;
  };
}

/** GET /api/sessions/:id/roster/ as a coach receives it: training information only. The backend
 *  does not send medical notes or emergency contacts to coaches (no capability authorizes them). */
export interface RosterStudent {
  id: number;
  student_no: string;
  full_name: string;
  chinese_name: string;
  gender: "M" | "F";
  age: number | null;
  status: string;
}

export type AttendanceMark = "UNMARKED" | "PRESENT" | "LATE" | "ABSENT" | "EXCUSED";

/** GET /api/sessions/:id/attendance/ : the sheet of expected students. */
export interface AttendanceSheet {
  session: number;
  state: SheetState;
  coach_edit_deadline: string;
  summary: {
    expected: number;
    marked: number;
    unmarked: number;
    present: number;
    late: number;
    absent: number;
    excused: number;
    percentage: string | null;
  };
  sheet: { student: number; student_name: string; status: AttendanceMark; remarks: string }[];
  /** The saved records (ids for their change history; staff and coaches). */
  records?: (AttendanceRecord & { remarks: string; recorded_by_name: string | null })[];
}
