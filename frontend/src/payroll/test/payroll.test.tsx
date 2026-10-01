import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { json, makeMe, renderApp } from "../../test/helpers";
import { coachPayslipRoutes, payrollRoutes, readyRun, superMe } from "./fixtures";

type Fetch = ReturnType<typeof renderApp>["fetchImpl"];
type Call = { url: URL; method: string; body: Record<string, unknown> | undefined };
function calls(fetchImpl: Fetch): Call[] {
  return fetchImpl.mock.calls.map(([url, init]) => {
    const i = (init ?? {}) as RequestInit;
    return { url: new URL(String(url), "http://x"), method: i.method ?? "GET",
             body: typeof i.body === "string" ? JSON.parse(i.body) : undefined };
  });
}
const writes = (f: Fetch) => calls(f).filter((c) => c.method !== "GET");
const asSuper = () => payrollRoutes({ "GET /api/me/": json(superMe) });

describe("payroll periods", () => {
  it("lists periods with the backend's statuses and issues, in the Finance menu", async () => {
    renderApp({ route: "/finance/payroll", routes: payrollRoutes() });
    const table = await screen.findByRole("table", { name: "Payroll periods" });
    expect(within(table).getByRole("link", { name: "September 2026" })).toHaveAttribute("href", "/finance/payroll/12");
    expect(within(table).getByText("September 2026").closest("tr")).toHaveTextContent("Ready for approval");
    expect(within(table).getByText("October 2026").closest("tr")).toHaveTextContent("Draft");
    expect(within(table).getByText("August 2026").closest("tr")).toHaveTextContent("Finalized");
    const finance = screen.getByRole("complementary", { name: "Sidebar" }).querySelector("[data-portal='finance']") as HTMLElement;
    expect(within(finance).getByRole("link", { name: "Payroll" })).toHaveAttribute("href", "/finance/payroll");
  });

  it("calculates a month through the backend service, after confirmation", async () => {
    const { user, fetchImpl, router } = renderApp({ route: "/finance/payroll", routes: payrollRoutes() });
    await screen.findByRole("table", { name: "Payroll periods" });
    await user.selectOptions(screen.getByLabelText("Month"), "9");
    await user.clear(screen.getByLabelText("Year"));
    await user.type(screen.getByLabelText("Year"), "2026");
    await user.click(screen.getByRole("button", { name: "Calculate" }));
    const dialog = screen.getByRole("dialog", { name: "Calculate payroll for September 2026?" });
    expect(writes(fetchImpl)).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "Calculate" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/finance/payroll/12"));
    expect(writes(fetchImpl)).toEqual([expect.objectContaining({ method: "POST", body: { year: 2026, month: 9 } })]);
    expect(writes(fetchImpl)[0].url.pathname).toBe("/api/payroll-runs/calculate/");
  });

  it("shows the backend's refusal to recalculate a finalized month", async () => {
    const { user } = renderApp({ route: "/finance/payroll", routes: payrollRoutes({
      "POST /api/payroll-runs/calculate/": json({ detail: "This payroll is finalized and cannot be recalculated." }, 400) }) });
    await screen.findByRole("table", { name: "Payroll periods" });
    await user.click(screen.getByRole("button", { name: "Calculate" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Calculate" }));
    expect(await screen.findByText("This payroll is finalized and cannot be recalculated.")).toBeInTheDocument();
  });

  it("shows an error with retry when periods fail to load", async () => {
    renderApp({ route: "/finance/payroll", routes: payrollRoutes({ "GET /api/payroll-runs/": json({ detail: "x" }, 500) }) });
    expect(await screen.findByRole("button", { name: /Try again/i })).toBeInTheDocument();
  });
});

describe("payroll period", () => {
  it("shows status, payslips per coach from the backend and every unpaid assignment with its reason", async () => {
    const { fetchImpl } = renderApp({ route: "/finance/payroll/12", routes: payrollRoutes() });
    expect(await screen.findByRole("heading", { name: "Payroll September 2026", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Calculated: ready for review.")).toBeInTheDocument();
    const payslips = await screen.findByRole("table", { name: "Payslips for September 2026" });
    const lim = within(payslips).getByRole("link", { name: "Coach Lim" });
    expect(lim).toHaveAttribute("href", "/finance/payroll/12/coach/3");
    expect(lim.closest("tr")).toHaveTextContent("RM 210.00");
    const sent = calls(fetchImpl).find((c) => c.url.pathname === "/api/payslips/")!;
    expect(sent.url.searchParams.get("run")).toBe("12");
    const excluded = screen.getByRole("table", { name: "Coach assignments not paid, and why" });
    expect(within(excluded).getByText("Replaced by an authorized substitute")).toBeInTheDocument();
    expect(within(excluded).getByText("Session cancelled")).toBeInTheDocument();
    // FINANCE_ADMIN recalculates; only a super admin finalizes.
    expect(screen.getByRole("button", { name: "Recalculate" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Finalize" })).not.toBeInTheDocument();
    expect(screen.getByText(/A super admin finalizes the payroll/)).toBeInTheDocument();
    expect(screen.getByRole("main").textContent).not.toMatch(/bank|account no|EPF|SOCSO/i);
  });

  it("a draft with missing rates explains why it cannot be finalized", async () => {
    renderApp({ route: "/finance/payroll/13", routes: asSuper() });
    expect(await screen.findByText("1 unresolved issue: this payroll cannot be finalized.")).toBeInTheDocument();
    expect(await screen.findByText("Missing rate")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Finalize" })).not.toBeInTheDocument();
  });

  it("super admin finalizes after a permanent-action confirmation", async () => {
    let finalized = false;
    const { user, fetchImpl } = renderApp({ route: "/finance/payroll/12", routes: payrollRoutes({
      "GET /api/me/": json(superMe),
      "GET /api/payroll-runs/12/": () => json(finalized ? { ...readyRun, status: "FINALIZED", finalized_at: "2026-10-01T10:00:00+08:00" } : readyRun),
      "POST /api/payroll-runs/12/finalize/": () => {
        finalized = true;
        return json({ ...readyRun, status: "FINALIZED", finalized_at: "2026-10-01T10:00:00+08:00" });
      },
    }) });
    await user.click(await screen.findByRole("button", { name: "Finalize" }));
    const dialog = screen.getByRole("dialog", { name: "Finalize payroll for September 2026?" });
    expect(dialog).toHaveTextContent("This cannot be undone.");
    await user.click(within(dialog).getByRole("button", { name: "Finalize permanently" }));
    expect(await screen.findByText("Payroll finalized. It is now permanent.")).toBeInTheDocument();
    expect(writes(fetchImpl).map((c) => c.url.pathname)).toEqual(["/api/payroll-runs/12/finalize/"]);
    expect(screen.getByText("Finalized: this payroll is permanent.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Recalculate" })).not.toBeInTheDocument();
  });

  it("shows the backend's refusal to finalize (month not over or changed since calculation)", async () => {
    const { user } = renderApp({ route: "/finance/payroll/12", routes: payrollRoutes({
      "GET /api/me/": json(superMe),
      "POST /api/payroll-runs/12/finalize/": json({ detail: "Sessions, substitutes, rates or adjustments changed since this payroll was calculated; recalculate and review it before finalizing." }, 400),
    }) });
    await user.click(await screen.findByRole("button", { name: "Finalize" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Finalize permanently" }));
    expect(await screen.findByText(/changed since this payroll was calculated/)).toBeInTheDocument();
  });

  it("recalculates the period's month", async () => {
    const { user, fetchImpl } = renderApp({ route: "/finance/payroll/12", routes: payrollRoutes() });
    await user.click(await screen.findByRole("button", { name: "Recalculate" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Recalculate" }));
    expect(await screen.findByText("Payroll recalculated.")).toBeInTheDocument();
    expect(writes(fetchImpl)[0]).toMatchObject({ method: "POST", body: { year: 2026, month: 9 } });
  });

  it("a finalized period is read only", async () => {
    renderApp({ route: "/finance/payroll/11", routes: asSuper() });
    expect(await screen.findByText("Finalized: this payroll is permanent.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Recalculate" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Finalize" })).not.toBeInTheDocument();
  });

  it("an unknown period is not found", async () => {
    renderApp({ route: "/finance/payroll/99", routes: payrollRoutes() });
    expect(await screen.findByText("Payroll period not found.")).toBeInTheDocument();
  });
});

describe("coach payslip (staff)", () => {
  it("asks the backend for that run and coach and shows its totals and lines as calculated", async () => {
    const { fetchImpl } = renderApp({ route: "/finance/payroll/12/coach/3", routes: payrollRoutes() });
    expect(await screen.findByRole("heading", { name: "Coach Lim: September 2026", level: 1 })).toBeInTheDocument();
    const sent = calls(fetchImpl).find((c) => c.url.pathname === "/api/payslips/")!;
    expect(Object.fromEntries(sent.url.searchParams)).toEqual({ run: "12", coach: "3" });
    expect(screen.getByText("Net pay").nextElementSibling).toHaveTextContent("RM 210.00");
    expect(screen.getByText("Deductions").nextElementSibling).toHaveTextContent("RM 20.00");
    const lines = screen.getByRole("table", { name: "Payslip lines" });
    const sub = within(lines).getByText(/Substitute for Coach Wong/).closest("tr")!;
    expect(sub).toHaveTextContent("Substitute session");
    expect(sub).toHaveTextContent("Covering for Coach Wong");
    expect(sub).toHaveTextContent("General substitute rate");
    expect(sub).toHaveTextContent("RM 100.00");
    expect(within(lines).getByText("Advance").closest("tr")).toHaveTextContent("Deduction");
  });

  it("a missing rate is flagged, never shown as a normal payment", async () => {
    renderApp({ route: "/finance/payroll/13/coach/4", routes: payrollRoutes() });
    expect(await screen.findByText("1 line has no rate and is paid RM 0.")).toBeInTheDocument();
    expect(screen.getByText("No per-session rate for this coach and session")).toBeInTheDocument();
  });

  it("a coach without a payslip in the period is not found", async () => {
    renderApp({ route: "/finance/payroll/11/coach/3", routes: payrollRoutes() });
    expect(await screen.findByText("This coach has no payslip in this period.")).toBeInTheDocument();
  });
});

describe("access", () => {
  it.each([["ADMIN"], ["COACH"], ["PARENT"], ["STUDENT"]] as const)(
    "%s is denied the payroll pages without any payroll request", async (role) => {
      for (const route of ["/finance/payroll", "/finance/payroll/12", "/finance/payroll/12/coach/3"]) {
        const { fetchImpl, unmount } = renderApp({ route, routes: payrollRoutes({ "GET /api/me/": json(makeMe([role])) }) });
        expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
        expect(calls(fetchImpl).filter((c) => /payroll|payslip/.test(c.url.pathname))).toEqual([]);
        unmount();
      }
    });

  it("the old staff payroll placeholder is retired", async () => {
    renderApp({ route: "/staff/payroll", routes: payrollRoutes() });
    expect(await screen.findByTestId("not-found")).toBeInTheDocument();
  });
});

describe("coach: my payslips", () => {
  it("lists the coach's finalized payslips from the API without any id of their own", async () => {
    const { fetchImpl } = renderApp({ route: "/coach/payslips", routes: coachPayslipRoutes() });
    const table = await screen.findByRole("table", { name: "My payslips" });
    const row = within(table).getByRole("link", { name: "August 2026" });
    expect(row).toHaveAttribute("href", "/coach/payslips/61");
    expect(row.closest("tr")).toHaveTextContent("RM 210.00");
    const sent = calls(fetchImpl).find((c) => c.url.pathname === "/api/payslips/")!;
    expect(sent.url.searchParams.get("coach")).toBeNull();
    expect(sent.url.searchParams.get("run")).toBeNull();
    const coaching = screen.getByRole("complementary", { name: "Sidebar" }).querySelector("[data-portal='coach']") as HTMLElement;
    expect(within(coaching).getByRole("link", { name: "My payslips" })).toHaveAttribute("href", "/coach/payslips");
  });

  it("shows a payslip's lines and totals without bank details", async () => {
    renderApp({ route: "/coach/payslips/61", routes: coachPayslipRoutes() });
    expect(await screen.findByRole("heading", { name: "Payslip August 2026", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Finalized")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Payslip lines" })).toHaveTextContent("Covering for Coach Wong");
    expect(screen.getByRole("main").textContent).not.toMatch(/bank|account no|EPF|SOCSO/i);
  });

  it("another coach's (or an unfinalized) payslip is not found", async () => {
    renderApp({ route: "/coach/payslips/72", routes: coachPayslipRoutes() });
    expect(await screen.findByText("Payslip not found.")).toBeInTheDocument();
  });

  it("an empty list says so", async () => {
    renderApp({ route: "/coach/payslips", routes: coachPayslipRoutes({ "GET /api/payslips/": json({ count: 0, next: null, previous: null, results: [] }) }) });
    expect(await screen.findByText("No finalized payslips yet.")).toBeInTheDocument();
  });

  it.each([["PARENT"], ["STUDENT"], ["FINANCE_ADMIN"]] as const)("%s cannot open My payslips", async (role) => {
    renderApp({ route: "/coach/payslips", routes: coachPayslipRoutes({ "GET /api/me/": json(makeMe([role])) }) });
    expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
  });
});
