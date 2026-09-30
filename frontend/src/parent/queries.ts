import { keepPreviousData, useInfiniteQuery, useQueries, useQuery } from "@tanstack/react-query";

import { allPages } from "../api/endpoints";
import type { Paginated } from "../api/types";
import { useServices } from "../app/services";

/**
 * Data hooks for the Parent Portal. Query keys start with "parent" so a
 * sign-out (which clears the whole cache) and mutations (which invalidate by
 * prefix) behave predictably. Every request goes to an endpoint the backend
 * scopes to the signed-in parent's own children and families.
 */

export const parentKeys = {
  all: ["parent"] as const,
  student: (id: number | string) => ["parent", "student", String(id)] as const,
  summary: (id: number) => ["parent", "attendance-summary", id] as const,
  attendance: (id: number) => ["parent", "attendance", id] as const,
  families: ["parent", "families"] as const,
  sessions: (start: string, end: string) => ["parent", "sessions", start, end] as const,
  charges: (query: object) => ["parent", "charges", query] as const,
  invoices: (query: object) => ["parent", "invoices", query] as const,
  invoice: (id: string) => ["parent", "invoice", id] as const,
  payments: ["parent", "payments"] as const,
  allPayments: ["parent", "payments", "all"] as const,
  receipts: ["parent", "receipts"] as const,
  receipt: (id: string) => ["parent", "receipt", id] as const,
  competitions: ["parent", "competitions"] as const,
  competition: (id: string) => ["parent", "competition", id] as const,
  registrations: (query: object) => ["parent", "registrations", query] as const,
};

export function useStudent(id: number | string | undefined) {
  const { endpoints } = useServices();
  return useQuery({
    queryKey: parentKeys.student(id ?? ""),
    queryFn: ({ signal }) => endpoints.student(id!, signal),
    enabled: id !== undefined && id !== "",
  });
}

/** Details of several children at once, sharing the cache with useStudent. */
export function useStudents(ids: number[]) {
  const { endpoints } = useServices();
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: parentKeys.student(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => endpoints.student(id, signal),
    })),
  });
}

export function useAttendanceSummaries(ids: number[]) {
  const { endpoints } = useServices();
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: parentKeys.summary(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => endpoints.attendanceSummary(id, {}, signal),
    })),
  });
}

export function useFamilies() {
  const { endpoints } = useServices();
  return useQuery({
    queryKey: parentKeys.families,
    queryFn: ({ signal }) => endpoints.families(signal),
    staleTime: 5 * 60_000,
  });
}

/** Sessions in a date range (bounded range, so every page is fetched). */
export function useSessionsRange(start: string, end: string, enabled = true) {
  const { endpoints } = useServices();
  return useQuery({
    queryKey: parentKeys.sessions(start, end),
    queryFn: ({ signal }) => allPages((page) => endpoints.sessions({ start, end, page }, signal), 5),
    placeholderData: keepPreviousData,
    enabled,
  });
}

/**
 * A "load more" list over a paginated endpoint. Its cache entry holds pages,
 * so its key gets a "paged" suffix and never collides with a plain query for
 * the same endpoint (which holds a single page).
 */
export function usePagedList<T>(key: readonly unknown[], fetchPage: (page: number, signal: AbortSignal) =>
  Promise<Paginated<T>>, enabled = true) {
  const query = useInfiniteQuery({
    queryKey: [...key, "paged"],
    queryFn: ({ pageParam, signal }) => fetchPage(pageParam, signal),
    initialPageParam: 1,
    getNextPageParam: (last, pages) => (last.next ? pages.length + 1 : undefined),
    enabled,
  });
  const rows = query.data?.pages.flatMap((page) => page.results) ?? [];
  const count = query.data?.pages[0]?.count ?? 0;
  return { ...query, rows, count };
}
