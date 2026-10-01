import type { ApiClient } from "./client";
import type { QueryValue } from "./client";
import type {
  AcademyPaymentInfo, AttendanceMark, AttendanceSheet, CoachingSession, PaymentProof, RosterStudent,
  AttendanceRecord, AttendanceSummaryResponse, Charge, Competition, CompetitionRegistration, Family, Invoice, Me,
  OwnStudent, Paginated, Payment, PayrollRunSummary, Receipt, StudentAttendanceResponse, StudentCompetitionEntry,
  StudentSelfProfile, StudentSession, TokenResponse, TrainingSession, StaffDashboard, StaffStudent, StaffStudentRow,
  StudentHistory, TrainingClass, Program, CoachRecord, AuditEntry, StudentEnrollment, StaffSessionSlot,
  FinanceDashboard, StaffInvoice, StaffPayment, StaffPaymentProof, Refund,
  StaffCompetition, CompetitionInput, CompetitionEvent, CompetitionEventInput, CompetitionSummary,
  RegistrationFormFieldRow, RegistrationFormFieldInput, StaffRegistrationForm, CompetitionResultRow,
  CompetitionResultInput,
} from "./types";

type Query = Record<string, QueryValue>;

/** Pages of a list endpoint, following `next` until done (bounded: never "the whole database"). */
export async function allPages<T>(fetchPage: (page: number) => Promise<Paginated<T>>, maxPages = 10) {
  const rows: T[] = [];
  let count = 0;
  for (let page = 1; page <= maxPages; page += 1) {
    const data = await fetchPage(page);
    rows.push(...data.results);
    count = data.count;
    if (!data.next) return { rows, count, complete: true };
  }
  return { rows, count, complete: rows.length >= count };
}

/**
 * Typed wrappers for the backend endpoints the app uses. Pages call these
 * (through TanStack Query), never `fetch` directly.
 */
export function endpoints(api: ApiClient) {
  return {
    /** POST /api/auth/token/: a fresh token; the previous one is revoked by the backend. */
    signIn: (username: string, password: string) =>
      api.request<TokenResponse>("/api/auth/token/", {
        method: "POST",
        body: { username, password },
        anonymous: true,
      }),
    /** POST /api/auth/logout/: revokes the token. */
    signOut: () => api.post<void>("/api/auth/logout/"),
    me: (signal?: AbortSignal) => api.get<Me>("/api/me/", undefined, signal),

    sessions: (query: { start?: string; end?: string; date?: string; class?: number; page?: number },
               signal?: AbortSignal) =>
      api.get<Paginated<TrainingSession>>("/api/sessions/", query, signal),

    /** Counts only (the list endpoints report `count`); page size is fixed by the backend. */
    count: async (path: string, query: Record<string, string> = {}, signal?: AbortSignal) =>
      (await api.get<Paginated<unknown>>(path, query, signal)).count,

    payrollRuns: (signal?: AbortSignal) =>
      api.get<Paginated<PayrollRunSummary>>("/api/payroll-runs/", undefined, signal),

    /* Parent portal. Every endpoint is scoped by the backend to the caller's own
       children and families; ids from URLs are never trusted on the client. */
    student: (id: number | string, signal?: AbortSignal) =>
      api.get<OwnStudent>(`/api/students/${encodeURIComponent(id)}/`, undefined, signal),
    attendanceSummary: (id: number | string, query: Query = {}, signal?: AbortSignal) =>
      api.get<AttendanceSummaryResponse>(`/api/students/${encodeURIComponent(id)}/attendance-summary/`, query, signal),
    attendance: (query: { student?: number; start?: string; end?: string; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<AttendanceRecord>>("/api/attendance/", query, signal),
    families: (signal?: AbortSignal) => api.get<Paginated<Family>>("/api/families/", undefined, signal),
    charges: (query: { student?: number; outstanding?: 1; status?: string; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<Charge>>("/api/charges/", query, signal),
    invoices: (query: { status?: string; outstanding?: 1; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<Invoice>>("/api/invoices/", query, signal),
    invoice: (id: number | string, signal?: AbortSignal) =>
      api.get<Invoice>(`/api/invoices/${encodeURIComponent(id)}/`, undefined, signal),
    payments: (query: { page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<Payment>>("/api/payments/", query, signal),
    receipts: (query: { page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<Receipt>>("/api/receipts/", query, signal),
    receipt: (id: number | string, signal?: AbortSignal) =>
      api.get<Receipt>(`/api/receipts/${encodeURIComponent(id)}/`, undefined, signal),
    competitions: (query: { page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<Competition>>("/api/competitions/", query, signal),
    competition: (id: number | string, signal?: AbortSignal) =>
      api.get<Competition>(`/api/competitions/${encodeURIComponent(id)}/`, undefined, signal),
    registrations: (query: { competition?: number; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<CompetitionRegistration>>("/api/competition-registrations/", query, signal),
    /** Creates a PENDING registration and an issued competition invoice (paid at the academy). */
    register: (body: { student: number; event: number; competition?: number; notes?: string;
                       responses?: Record<string, unknown> }) =>
      api.post<CompetitionRegistration>("/api/competition-registrations/", body),
    /* Finance staff portal (Phase 6F). Thin wrappers over the existing finance endpoints;
       every rule (issued invoices immutable, allocation, receipts, proof review) is the backend's. */
    financeDashboard: (signal?: AbortSignal) => api.get<FinanceDashboard>("/api/finance/dashboard/", undefined, signal),
    staffInvoices: (query: { search?: string; status?: string; kind?: string; student?: number; family?: number;
                             start?: string; end?: string; outstanding?: 1; overdue?: 1; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<StaffInvoice>>("/api/invoices/", query, signal),
    staffInvoice: (id: number | string, signal?: AbortSignal) =>
      api.get<StaffInvoice>(`/api/invoices/${encodeURIComponent(id)}/`, undefined, signal),
    issueInvoice: (id: number, dueDate?: string) =>
      api.post<StaffInvoice>(`/api/invoices/${id}/issue/`, dueDate ? { due_date: dueDate } : {}),
    voidInvoice: (id: number, reason: string) => api.post<StaffInvoice>(`/api/invoices/${id}/void/`, { reason }),
    staffPayments: (query: { search?: string; family?: number; invoice?: number; status?: string; start?: string;
                             end?: string; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<StaffPayment>>("/api/payments/", query, signal),
    staffPayment: (id: number | string, signal?: AbortSignal) =>
      api.get<StaffPayment>(`/api/payments/${encodeURIComponent(id)}/`, undefined, signal),
    paymentMethods: (signal?: AbortSignal) =>
      api.get<{ value: string; label: string }[]>("/api/payments/methods/", undefined, signal),
    /** Manual payment through the existing service (allocation, receipt, invoice status are the backend's). */
    recordPayment: (body: { amount: string; method: string; payer_name?: string; reference?: string; received_at?: string;
                            notes?: string; allocations: { invoice: number; amount: string }[] }, idempotencyKey: string) =>
      api.request<StaffPayment>("/api/payments/", { method: "POST", body, idempotencyKey }),
    voidPayment: (id: number, reason: string) => api.post<StaffPayment>(`/api/payments/${id}/void/`, { reason }),
    refundPayment: (id: number, body: { allocation: number; amount: string; reason: string; method: string; reference?: string }) =>
      api.post<Refund>(`/api/payments/${id}/refund/`, body),
    refunds: (query: { payment?: number; page?: number }, signal?: AbortSignal) => api.get<Paginated<Refund>>("/api/refunds/", query, signal),
    staffReceipts: (query: { search?: string; family?: number; invoice?: number; start?: string; end?: string;
                             page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<Receipt>>("/api/receipts/", query, signal),
    staffProofs: (query: { search?: string; status?: string; invoice?: number; family?: number; student?: number;
                           start?: string; end?: string; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<StaffPaymentProof>>("/api/payment-proofs/", query, signal),
    staffProof: (id: number | string, signal?: AbortSignal) =>
      api.get<StaffPaymentProof>(`/api/payment-proofs/${encodeURIComponent(id)}/`, undefined, signal),
    /** Records no money: the payment is recorded separately (and may be linked here). */
    acceptProof: (id: number, body: { note?: string; payment?: number | null }) =>
      api.post<StaffPaymentProof>(`/api/payment-proofs/${id}/accept/`, body),
    rejectProof: (id: number, reason: string) => api.post<StaffPaymentProof>(`/api/payment-proofs/${id}/reject/`, { reason }),
    /** Multipart: bank fields and an optional PNG/JPG QR (validated by the backend). */
    updatePaymentInfo: (form: FormData) =>
      api.request<AcademyPaymentInfo>("/api/payment-info/", { method: "PATCH", body: form, timeoutMs: 60_000 }),

    /* Staff portal (Phase 6E). The backend checks every capability and runs every change
       through its services (reasons, lifecycle rules, audit); these are thin wrappers. */
    staffDashboard: (signal?: AbortSignal) => api.get<StaffDashboard>("/api/staff/dashboard/", undefined, signal),
    staffStudents: (query: { search?: string; status?: string; class?: number; family?: number; page?: number },
                    signal?: AbortSignal) => api.get<Paginated<StaffStudentRow>>("/api/students/", query, signal),
    staffStudent: (id: number | string, signal?: AbortSignal) =>
      api.get<StaffStudent>(`/api/students/${encodeURIComponent(id)}/`, undefined, signal),
    studentHistory: (id: number | string, signal?: AbortSignal) =>
      api.get<StudentHistory>(`/api/students/${encodeURIComponent(id)}/history/`, undefined, signal),
    updateStudent: (id: number, body: Partial<Pick<StaffStudent, "full_name" | "chinese_name" | "gender" |
                    "date_of_birth" | "school">>) =>
      api.request<StaffStudent>(`/api/students/${id}/`, { method: "PATCH", body }),
    changeStudentStatus: (id: number, status: string, reason: string) =>
      api.post<StaffStudent>(`/api/students/${id}/change-status/`, { status, reason }),
    enroll: (student: number, trainingClass: number, startDate?: string) =>
      api.post<StudentEnrollment>("/api/enrollments/", { student, training_class: trainingClass,
                                                         ...(startDate ? { start_date: startDate } : {}) }),
    endEnrollment: (id: number, reason: string, endDate?: string) =>
      api.post<StudentEnrollment>(`/api/enrollments/${id}/end/`, { reason, ...(endDate ? { end_date: endDate } : {}) }),
    classes: (query: { active?: "1" | "0"; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<TrainingClass>>("/api/classes/", query, signal),
    trainingClass: (id: number | string, signal?: AbortSignal) =>
      api.get<TrainingClass>(`/api/classes/${encodeURIComponent(id)}/`, undefined, signal),
    classRoster: (id: number | string, signal?: AbortSignal) =>
      api.get<{ id: number; student_no: string; full_name: string; chinese_name: string; gender: string;
                age: number | null; status: string }[]>(`/api/classes/${encodeURIComponent(id)}/students/`, undefined, signal),
    createClass: (body: { code: string; name: string; category: string; program: number; venue?: string;
                          capacity?: number | null; description?: string }) =>
      api.post<TrainingClass>("/api/classes/", body),
    updateClass: (id: number, body: Partial<Pick<TrainingClass, "name" | "venue" | "capacity" | "description" |
                  "is_active" | "category">>) =>
      api.request<TrainingClass>(`/api/classes/${id}/`, { method: "PATCH", body }),
    programs: (signal?: AbortSignal) => api.get<Program[]>("/api/programs/", undefined, signal),
    coaches: (query: { page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<CoachRecord>>("/api/coaches/", query, signal),
    staffSessions: (query: { date?: string; start?: string; end?: string; class?: number; coach?: number;
                             status?: string; order?: "asc"; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<CoachingSession>>("/api/sessions/coaching/", query, signal),
    cancelSession: (id: number, reason: string) => api.post<TrainingSession>(`/api/sessions/${id}/cancel/`, { reason }),
    reinstateSession: (id: number, reason: string) =>
      api.post<TrainingSession>(`/api/sessions/${id}/reinstate/`, { reason }),
    rescheduleSession: (id: number, body: { date?: string; start_time?: string; end_time?: string; reason: string }) =>
      api.post<TrainingSession>(`/api/sessions/${id}/reschedule/`, body),
    reassignCoach: (id: number, body: { from_coach: number; to_coach: number; reason: string }) =>
      api.post<StaffSessionSlot>(`/api/sessions/${id}/reassign-coach/`, body),
    assignSubstitute: (id: number, body: { substitute: number; replaces?: number; reason: string }) =>
      api.post<StaffSessionSlot>(`/api/sessions/${id}/assign-substitute/`, body),
    revokeSubstitute: (id: number, body: { substitute: number; reason: string }) =>
      api.post<StaffSessionSlot>(`/api/sessions/${id}/revoke-substitute/`, body),
    attendanceHistory: (recordId: number, signal?: AbortSignal) =>
      api.get<AuditEntry[]>(`/api/attendance/${recordId}/history/`, undefined, signal),

    /* Student portal: always the signed-in student's own record (no student id is sent). */
    myProfile: (signal?: AbortSignal) => api.get<StudentSelfProfile>("/api/students/me/", undefined, signal),
    mySessions: (query: { view?: "today" | "upcoming" | "past" | "cancelled"; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<StudentSession>>("/api/students/me/sessions/", query, signal),
    mySession: (id: number | string, signal?: AbortSignal) =>
      api.get<StudentSession>(`/api/students/me/sessions/${encodeURIComponent(id)}/`, undefined, signal),
    myAttendance: (signal?: AbortSignal) =>
      api.get<StudentAttendanceResponse>("/api/students/me/attendance/", undefined, signal),
    myCompetitions: (signal?: AbortSignal) =>
      api.get<StudentCompetitionEntry[]>("/api/students/me/competitions/", undefined, signal),
    /* Coach portal. Scope comes from the signed-in coach (never from ids sent). */
    coachingSessions: (query: { date?: string; start?: string; end?: string; status?: string; order?: "asc";
                                page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<CoachingSession>>("/api/sessions/coaching/", query, signal),
    session: (id: number | string, signal?: AbortSignal) =>
      api.get<TrainingSession>(`/api/sessions/${encodeURIComponent(id)}/`, undefined, signal),
    roster: (id: number | string, signal?: AbortSignal) =>
      api.get<RosterStudent[]>(`/api/sessions/${encodeURIComponent(id)}/roster/`, undefined, signal),
    attendanceSheet: (id: number | string, signal?: AbortSignal) =>
      api.get<AttendanceSheet>(`/api/sessions/${encodeURIComponent(id)}/attendance/`, undefined, signal),
    /** All or nothing (the backend checks roster, window, reason and authorization). */
    submitAttendance: (id: number | string, body: {
      records: { student: number; status: AttendanceMark; remarks: string }[]; reason: string }) =>
      api.post<unknown>(`/api/sessions/${encodeURIComponent(id)}/attendance/`, body),

    /** The academy's bank details, QR code and instructions. */
    paymentInfo: (signal?: AbortSignal) => api.get<AcademyPaymentInfo>("/api/payment-info/", undefined, signal),
    paymentProofs: (query: { invoice?: number; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<PaymentProof>>("/api/payment-proofs/", query, signal),
    /** Multipart upload; the backend checks the invoice is the family's own and open. */
    uploadPaymentProof: (form: FormData) =>
      api.request<PaymentProof>("/api/payment-proofs/", { method: "POST", body: form, timeoutMs: 60_000 }),
    /** The uploaded file (permission-checked download). */
    paymentProofFile: (id: number) =>
      api.request<Blob>(`/api/payment-proofs/${id}/file/`, { responseType: "blob", timeoutMs: 60_000 }),
    /* Competition staff portal (Phase 6G). Thin wrappers over the existing competition endpoints;
       every rule (eligibility, payment before confirmation, results only for confirmed paid entries,
       frozen form versions) is the backend's. */
    staffCompetitions: (query: { status?: string; search?: string; page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<StaffCompetition>>("/api/competitions/", query, signal),
    staffCompetition: (id: number | string, signal?: AbortSignal) =>
      api.get<StaffCompetition>(`/api/competitions/${encodeURIComponent(id)}/`, undefined, signal),
    createCompetition: (body: CompetitionInput) => api.post<StaffCompetition>("/api/competitions/", body),
    updateCompetition: (id: number, body: Partial<CompetitionInput>) =>
      api.request<StaffCompetition>(`/api/competitions/${id}/`, { method: "PATCH", body }),
    competitionSummary: (id: number | string, signal?: AbortSignal) =>
      api.get<CompetitionSummary>(`/api/competitions/${encodeURIComponent(id)}/summary/`, undefined, signal),
    createCompetitionEvent: (body: CompetitionEventInput) => api.post<CompetitionEvent>("/api/competition-events/", body),
    updateCompetitionEvent: (id: number, body: Partial<CompetitionEventInput>) =>
      api.request<CompetitionEvent>(`/api/competition-events/${id}/`, { method: "PATCH", body }),
    registrationForm: (id: number | string, signal?: AbortSignal) =>
      api.get<StaffRegistrationForm>(`/api/competitions/${encodeURIComponent(id)}/form/`, undefined, signal),
    publishForm: (id: number) => api.post<StaffRegistrationForm>(`/api/competitions/${id}/publish-form/`),
    unpublishForm: (id: number) => api.post<StaffRegistrationForm>(`/api/competitions/${id}/unpublish-form/`),
    reorderForm: (id: number, keys: string[]) =>
      api.post<StaffRegistrationForm>(`/api/competitions/${id}/reorder-form/`, { keys }),
    createFormField: (body: RegistrationFormFieldInput) =>
      api.post<RegistrationFormFieldRow>("/api/competition-form-fields/", body),
    updateFormField: (id: number, body: Partial<RegistrationFormFieldInput>) =>
      api.request<RegistrationFormFieldRow>(`/api/competition-form-fields/${id}/`, { method: "PATCH", body }),
    deleteFormField: (id: number) => api.request<void>(`/api/competition-form-fields/${id}/`, { method: "DELETE" }),
    staffRegistrations: (query: { competition?: number; event?: number; student?: number; status?: string;
                                  payment?: string; result?: string; search?: string; start?: string; end?: string;
                                  page?: number }, signal?: AbortSignal) =>
      api.get<Paginated<CompetitionRegistration>>("/api/competition-registrations/", query, signal),
    staffRegistration: (id: number | string, signal?: AbortSignal) =>
      api.get<CompetitionRegistration>(`/api/competition-registrations/${encodeURIComponent(id)}/`, undefined, signal),
    /** Never confirms an unpaid entry (the backend refuses). */
    confirmRegistration: (id: number) => api.post<CompetitionRegistration>(`/api/competition-registrations/${id}/confirm/`),
    rejectRegistration: (id: number, reason: string) =>
      api.post<CompetitionRegistration>(`/api/competition-registrations/${id}/reject/`, { reason }),
    createResult: (body: CompetitionResultInput & { registration: number }) =>
      api.post<CompetitionResultRow>("/api/competition-results/", body),
    updateResult: (id: number, body: CompetitionResultInput) =>
      api.request<CompetitionResultRow>(`/api/competition-results/${id}/`, { method: "PATCH", body }),
    /** Parent withdrawal while registration is open. Paid fees are not refunded. */
    withdraw: (id: number, reason: string) =>
      api.post<CompetitionRegistration>(`/api/competition-registrations/${id}/withdraw/`, { reason }),
  };
}

export type Endpoints = ReturnType<typeof endpoints>;
