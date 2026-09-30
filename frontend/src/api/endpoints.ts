import type { ApiClient } from "./client";
import type { QueryValue } from "./client";
import type {
  AttendanceRecord, AttendanceSummaryResponse, Charge, Competition, CompetitionRegistration, Family, Invoice, Me,
  OwnStudent, Paginated, Payment, PayrollRunSummary, Receipt, TokenResponse, TrainingSession,
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
    register: (body: { student: number; event: number; notes?: string }) =>
      api.post<CompetitionRegistration>("/api/competition-registrations/", body),
    /** Parent withdrawal while registration is open. Paid fees are not refunded. */
    withdraw: (id: number, reason: string) =>
      api.post<CompetitionRegistration>(`/api/competition-registrations/${id}/withdraw/`, { reason }),
  };
}

export type Endpoints = ReturnType<typeof endpoints>;
