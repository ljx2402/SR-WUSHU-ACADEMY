import type { ApiClient } from "./client";
import type { QueryValue } from "./client";
import type {
  AcademyPaymentInfo, AttendanceMark, AttendanceSheet, CoachingSession, PaymentProof, RosterStudent,
  AttendanceRecord, AttendanceSummaryResponse, Charge, Competition, CompetitionRegistration, Family, Invoice, Me,
  OwnStudent, Paginated, Payment, PayrollRunSummary, Receipt, StudentAttendanceResponse, StudentCompetitionEntry,
  StudentSelfProfile, StudentSession, TokenResponse, TrainingSession,
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
    /** Parent withdrawal while registration is open. Paid fees are not refunded. */
    withdraw: (id: number, reason: string) =>
      api.post<CompetitionRegistration>(`/api/competition-registrations/${id}/withdraw/`, { reason }),
  };
}

export type Endpoints = ReturnType<typeof endpoints>;
