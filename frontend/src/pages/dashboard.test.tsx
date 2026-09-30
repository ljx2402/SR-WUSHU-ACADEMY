import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { json, makeMe, renderApp } from "../test/helpers";

const page = <T,>(results: T[], count = results.length) => ({ count, next: null, previous: null, results });

function session(id: number, start: string, name: string, phase = "UPCOMING") {
  return { id, training_class: 1, class_name: name, date: "2026-10-01", start_time: start, end_time: "23:00:00",
           venue: "Hall A", status: "SCHEDULED", phase, notes: "", coaches: [] };
}

describe("dashboard", () => {
  it("shows staff KPIs from API counts, only for the user's capabilities", async () => {
    const { fetchImpl } = renderApp({
      routes: {
        "GET /api/me/": json(makeMe(["ADMIN"])),
        "GET /api/students/": ({ url }) => json(page([], url.searchParams.get("status") === "ACTIVE" ? 42 : 0)),
        "GET /api/sessions/": json(page([], 3)),
        "GET /api/invoices/": ({ url }) => json(page([], url.searchParams.get("outstanding") === "1" ? 7 : 0)),
      },
    });
    const kpis = await screen.findByRole("region", { name: "Key figures" });
    expect(await within(kpis).findByText("42")).toBeInTheDocument();
    expect(within(kpis).getByText("Active students")).toBeInTheDocument();
    expect(within(kpis).getByText("7")).toBeInTheDocument();
    expect(within(kpis).getByText("Invoices awaiting payment")).toBeInTheDocument();
    // ADMIN has no payroll capability: the widget is absent and never requested.
    expect(within(kpis).queryByText("Latest payroll period")).not.toBeInTheDocument();
    const paths = fetchImpl.mock.calls.map(([url]) => new URL(String(url), "http://x").pathname);
    expect(paths).not.toContain("/api/payroll-runs/");
  });

  it("lists sessions in start-time order with their status", async () => {
    renderApp({
      routes: {
        "GET /api/me/": json(makeMe(["COACH", "PARENT"], { classes: [{ id: 1, name: "Junior Taolu" }] })),
        "GET /api/sessions/": json(page([session(2, "19:00:00", "Senior Sanda"), session(1, "17:00:00", "Junior Taolu", "CANCELLED")])),
      },
    });
    const tables = await screen.findAllByRole("table", { name: /Sessions/ });
    const rows = within(tables[0]).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("17:00");
    expect(rows[0]).toHaveTextContent("Cancelled");
    expect(rows[1]).toHaveTextContent("Senior Sanda");
  });

  it("uses /api/me/ for the coach's classes and substitute sessions", async () => {
    renderApp({
      routes: {
        "GET /api/me/": json(makeMe(["COACH", "PARENT"], {
          classes: [{ id: 1, name: "Junior Taolu" }],
          substitute_sessions: [{ session: 77, access_ends_at: "2026-10-03T12:00:00Z" }],
        })),
      },
    });
    expect(await screen.findByText("You are covering 1 session as a substitute.")).toBeInTheDocument();
    expect(screen.getByText("Junior Taolu")).toBeInTheDocument();
  });

  it("shows a coach-and-parent's children (one family, several students)", async () => {
    renderApp({
      routes: {
        "GET /api/me/": json(makeMe(["COACH", "PARENT"], { children: [
          { id: 1, student_no: "S0001", full_name: "Lina" }, { id: 2, student_no: "S0002", full_name: "Adam" },
        ] })),
      },
    });
    const main = await screen.findByRole("main");
    const card = (await within(main).findByRole("heading", { name: "My family", level: 2 })).closest("section")!;
    expect(within(card).getByText("Lina")).toBeInTheDocument();
    expect(within(card).getByText("Adam")).toBeInTheDocument();
    expect(screen.queryByText(/billing contact|bill to/i)).not.toBeInTheDocument();
  });

  it("a failing widget shows the standard error, not the response", async () => {
    renderApp({
      routes: {
        "GET /api/me/": json(makeMe(["COACH", "PARENT"])),
        "GET /api/sessions/": () => new Response("<h1>Server Error (500)</h1>", { status: 500 }),
      },
    });
    const alerts = await screen.findAllByText("Something went wrong on our side. Please try again later.");
    expect(alerts.length).toBeGreaterThan(0);
    expect(screen.queryByText(/Server Error \(500\)/)).not.toBeInTheDocument();
  });

  it("finance admin sees the latest payroll period and no student count", async () => {
    const { fetchImpl } = renderApp({
      routes: {
        "GET /api/me/": json(makeMe(["FINANCE_ADMIN"])),
        "GET /api/payroll-runs/": json(page([{ id: 3, year: 2026, month: 9, period_start: "2026-09-01",
                                               period_end: "2026-09-30", status: "READY", issue_count: 0 }])),
      },
    });
    const kpis = await screen.findByRole("region", { name: "Key figures" });
    expect(await within(kpis).findByText("Ready for approval")).toBeInTheDocument();
    expect(within(kpis).getByText(/09\/2026/)).toBeInTheDocument();
    expect(within(kpis).queryByText("Active students")).not.toBeInTheDocument();
    const paths = fetchImpl.mock.calls.map(([url]) => new URL(String(url), "http://x").pathname);
    expect(paths).not.toContain("/api/students/");
  });

  it("marks widgets without a backend as not available", async () => {
    renderApp({ routes: { "GET /api/me/": json(makeMe(["STUDENT"])) } });
    expect(await screen.findByText(/Recent activity/)).toBeInTheDocument();
    expect(screen.getByText(/Not available yet/)).toBeInTheDocument();
  });
});
