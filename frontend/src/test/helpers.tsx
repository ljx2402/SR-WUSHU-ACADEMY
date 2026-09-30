import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter } from "react-router";
import { vi } from "vitest";

import type { Me, Role } from "../api/types";
import { AppProviders, appRoutes, createQueryClient } from "../app/App";
import { createServices } from "../app/services";
import type { TokenStore } from "../auth/tokenStore";

/** In-memory token store (the browser one uses sessionStorage). */
export function memoryTokens(initial: string | null = null): TokenStore & { value: string | null } {
  const store = {
    value: initial,
    get: () => store.value,
    set: (token: string) => { store.value = token; },
    clear: () => { store.value = null; },
  };
  return store;
}

export function json(body: unknown, status = 200): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Handler = (request: { method: string; url: URL; body: unknown; headers: Record<string, string> }) =>
  Response | Promise<Response>;

/**
 * A fake `fetch`: routes "METHOD /path" to handlers. Unknown requests get a
 * 404, so a test never silently depends on an endpoint it did not declare.
 */
export function fakeFetch(routes: Record<string, Handler | Response | (() => Response)>) {
  return vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), "http://localhost");
    const method = (init.method ?? "GET").toUpperCase();
    const route = routes[`${method} ${url.pathname}`];
    if (!route) return json({ detail: "Not found." }, 404);
    if (route instanceof Response) return route.clone();
    const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body; // FormData for uploads
    return (route as Handler)({ method, url, body, headers: (init.headers ?? {}) as Record<string, string> });
  });
}

const CAPABILITIES: Record<Role, string[]> = {
  SUPER_ADMIN: [
    "students.view_all", "students.view_directory", "sessions.view_all", "attendance.view_all",
    "competition.registrations.view_all", "competition.manage", "finance.view_all", "payroll.view_all",
    "reports.students", "reports.finance", "reports.payroll",
    // A super admin holds every capability, including "own children" ones.
    "students.view_own_children", "sessions.view_own_children", "finance.view_own_children",
    "sessions.view_assigned", "attendance.take_assigned",
  ],
  // Mirrors apps/accounts/capabilities.py (front desk may take payments, so sees finance).
  ADMIN: ["students.view_all", "sessions.view_all", "attendance.view_all", "competition.manage",
          "competition.registrations.view_all", "finance.view_all", "reports.students", "reports.attendance",
          "reports.competitions"],
  FINANCE_ADMIN: ["finance.view_all", "payroll.view_all", "reports.finance", "reports.payroll",
                  "students.view_directory"],
  COACH: ["sessions.view_assigned", "attendance.take_assigned", "students.view_assigned", "payroll.view_own"],
  PARENT: ["students.view_own_children", "sessions.view_own_children", "attendance.view_own_children",
           "finance.view_own_children", "finance.proofs.upload_own", "competition.registrations.view_own_children",
           "competition.register_own_children"],
  STUDENT: ["students.view_self", "sessions.view_self", "attendance.view_self",
            "competition.registrations.view_self"],
};

export function makeMe(roles: Role[], extra: Partial<Me> = {}): Me {
  return {
    id: 1,
    username: "user1",
    name: "Test User",
    role: roles[0] ?? null,
    roles,
    capabilities: [...new Set(roles.flatMap((role) => CAPABILITIES[role]))].sort(),
    ...extra,
  };
}

const EMPTY_PAGE = { count: 0, next: null, previous: null, results: [] };

/** Default read endpoints used by the dashboard, all returning empty pages. */
export function dashboardRoutes() {
  return {
    "GET /api/sessions/": () => json(EMPTY_PAGE),
    "GET /api/students/": () => json(EMPTY_PAGE),
    "GET /api/invoices/": () => json(EMPTY_PAGE),
    "GET /api/payroll-runs/": () => json(EMPTY_PAGE),
  };
}

export function renderApp({ route = "/dashboard", token = "tok-123", routes = {} }: {
  route?: string;
  token?: string | null;
  routes?: Parameters<typeof fakeFetch>[0];
} = {}) {
  const tokens = memoryTokens(token);
  const fetchImpl = fakeFetch({ ...dashboardRoutes(), ...routes });
  const services = createServices({ fetchImpl: fetchImpl as unknown as typeof fetch, tokens, baseUrl: "",
                                    timeoutMs: 1000 });
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
  const router = createMemoryRouter(appRoutes, { initialEntries: [route] });
  const user = userEvent.setup();
  const utils = render(<AppProviders services={services} queryClient={queryClient} router={router} />);
  return { ...utils, user, router, tokens, fetchImpl };
}
