import type { ApiClient } from "./client";
import type { Me, Paginated, PayrollRunSummary, TokenResponse, TrainingSession } from "./types";

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

    sessions: (query: { start?: string; end?: string; date?: string }, signal?: AbortSignal) =>
      api.get<Paginated<TrainingSession>>("/api/sessions/", query, signal),

    /** Counts only (the list endpoints report `count`); page size is fixed by the backend. */
    count: async (path: string, query: Record<string, string> = {}, signal?: AbortSignal) =>
      (await api.get<Paginated<unknown>>(path, query, signal)).count,

    payrollRuns: (signal?: AbortSignal) =>
      api.get<Paginated<PayrollRunSummary>>("/api/payroll-runs/", undefined, signal),
  };
}

export type Endpoints = ReturnType<typeof endpoints>;
