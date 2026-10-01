import type { PayrollRun, Payslip } from "../../api/types";
import { json, makeMe } from "../../test/helpers";

export const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });

export const financeMe = makeMe(["FINANCE_ADMIN"], { name: "Finance Officer" });
export const superMe = makeMe(["SUPER_ADMIN"], { name: "Owner" });
export const coachMe = makeMe(["COACH"], { name: "Coach Lim" });

export const readyRun: PayrollRun = {
  id: 12, year: 2026, month: 9, period_start: "2026-09-01", period_end: "2026-09-30", status: "READY", issue_count: 0,
  calculated_at: "2026-10-01T09:00:00+08:00", finalized_at: null, notes: "",
  excluded: [
    { slot: 501, session: 901, coach: "Coach Wong", date: "2026-09-12", class: "Junior Taolu", role: "REGULAR",
      reason: "Replaced by an authorized substitute" },
    { slot: 502, session: 902, coach: "Coach Lim", date: "2026-09-19", class: "Junior Taolu", role: "REGULAR",
      reason: "Session cancelled" },
  ],
};
export const draftRun: PayrollRun = { ...readyRun, id: 13, month: 10, period_start: "2026-10-01", period_end: "2026-10-31",
  status: "DRAFT", issue_count: 1, excluded: [] };
export const finalRun: PayrollRun = { ...readyRun, id: 11, month: 8, period_start: "2026-08-01", period_end: "2026-08-31",
  status: "FINALIZED", finalized_at: "2026-09-02T10:00:00+08:00", excluded: [] };

export const limPayslip: Payslip = {
  id: 71, year: 2026, month: 9, run_status: "READY", coach: 3, coach_name: "Coach Lim", regular_sessions: 1,
  substitute_sessions: 1, hours: "3.00", gross_pay: "230.00", total_deductions: "20.00", net_pay: "210.00",
  lines: [
    { kind: "REGULAR_SESSION", description: "Junior Taolu 05/09 17:00 (RM 80.00 per session; general per-session rate)",
      session: 900, slot: 500, original_coach: null, rate: "80.00", rule: "GENERAL_REGULAR", issue: "", amount: "80.00" },
    { kind: "SUBSTITUTE_SESSION", description: "Substitute for Coach Wong: Junior Taolu 12/09 17:00 (RM 100.00 per session; general substitute rate)",
      session: 901, slot: 503, original_coach: "Coach Wong", rate: "100.00", rule: "GENERAL_SUBSTITUTE", issue: "", amount: "100.00" },
    { kind: "ALLOWANCE", description: "Transport", session: null, slot: null, original_coach: null, rate: "50.00", rule: "",
      issue: "", amount: "50.00" },
    { kind: "DEDUCTION", description: "Advance", session: null, slot: null, original_coach: null, rate: "20.00", rule: "",
      issue: "", amount: "-20.00" },
  ],
};
export const wongPayslip: Payslip = {
  ...limPayslip, id: 72, coach: 4, coach_name: "Coach Wong", regular_sessions: 1, substitute_sessions: 0, hours: "2.00",
  gross_pay: "70.00", total_deductions: "0.00", net_pay: "70.00",
  lines: [{ ...limPayslip.lines[0], description: "Senior Sanda 06/09 19:00 (RM 70.00 per session; class per-session rate)",
            rate: "70.00", rule: "CLASS_REGULAR", amount: "70.00" }],
};
export const missingRatePayslip: Payslip = {
  ...wongPayslip, id: 73, month: 10, run_status: "DRAFT", gross_pay: "0.00", net_pay: "0.00",
  lines: [{ ...wongPayslip.lines[0], description: "Senior Sanda 03/10 19:00 (NO RATE SET; NO RATE SET)", rate: "0.00",
            rule: "MISSING_RATE", issue: "MISSING_RATE", amount: "0.00" }],
};
export const coachOwnPayslip: Payslip = { ...limPayslip, id: 61, month: 8, run_status: "FINALIZED" };

type Req = { url: URL };

export function payrollRoutes(overrides: Record<string, unknown> = {}) {
  return {
    "GET /api/me/": json(financeMe),
    "GET /api/payroll-runs/": json(page([draftRun, readyRun, finalRun])),
    "GET /api/payroll-runs/12/": json(readyRun),
    "GET /api/payroll-runs/13/": json(draftRun),
    "GET /api/payroll-runs/11/": json(finalRun),
    "GET /api/payslips/": ({ url }: Req) => {
      const run = url.searchParams.get("run");
      const coach = url.searchParams.get("coach");
      let rows = run === "13" ? [missingRatePayslip] : run === "11" ? [] : [limPayslip, wongPayslip];
      if (coach) rows = rows.filter((p) => String(p.coach) === coach);
      return json(page(rows));
    },
    "POST /api/payroll-runs/calculate/": json(readyRun),
    "POST /api/payroll-runs/12/finalize/": json({ ...readyRun, status: "FINALIZED", finalized_at: "2026-10-01T10:00:00+08:00" }),
    ...overrides,
  } as unknown as Record<string, never>;
}

export function coachPayslipRoutes(overrides: Record<string, unknown> = {}) {
  return {
    "GET /api/me/": json(coachMe),
    "GET /api/payslips/": json(page([coachOwnPayslip])),
    "GET /api/payslips/61/": json(coachOwnPayslip),
    ...overrides,
  } as unknown as Record<string, never>;
}
