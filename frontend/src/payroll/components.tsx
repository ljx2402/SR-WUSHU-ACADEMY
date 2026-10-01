import type { Payslip, PayslipLine } from "../api/types";
import { PayrollStatusBadge } from "../domain/finance";
import { MoneyAmount } from "../finance/components";
import { DefinitionList, Section } from "../parent/components";
import { DataTable } from "../ui/DataTable";
import { Alert, Badge } from "../ui/primitives";

/*
 * Payroll building blocks (Phase 6H). Every amount, count, rate, rule and
 * status shown here comes from the backend's payroll service
 * (apps/payroll/services.py); nothing is calculated in the browser.
 */

export const payrollKeys = {
  all: ["payroll"] as const,
  runs: ["payroll", "runs"] as const,
  run: (id: string) => ["payroll", "run", id] as const,
  payslips: (query: object) => ["payroll", "payslips", query] as const,
  payslip: (id: string) => ["payroll", "payslip", id] as const,
};

export const PAYROLL_ROOT = "/finance/payroll";
export const PAYROLL_CRUMBS = [{ label: "Finance dashboard", to: "/finance/dashboard" },
                               { label: "Payroll", to: PAYROLL_ROOT }];

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                "November", "December"];
export const MONTH_NAMES = MONTHS;

/** "October 2026" */
export function periodName(year: number, month: number) {
  return `${MONTHS[month - 1] ?? month} ${year}`;
}

/** The backend's line kinds (PayslipLine.Kind). */
export const LINE_KIND: Record<string, string> = {
  REGULAR_SESSION: "Regular session", SUBSTITUTE_SESSION: "Substitute session", MONTHLY: "Monthly salary (legacy)",
  ALLOWANCE: "Allowance", BONUS: "Bonus", DEDUCTION: "Deduction",
};

/** The backend's rate rules (payroll.services.Rule.LABELS): which rate priced the line. */
export const RATE_RULE: Record<string, string> = {
  CLASS_REGULAR: "Class per-session rate",
  GENERAL_REGULAR: "General per-session rate",
  CLASS_SUBSTITUTE: "Class substitute rate",
  GENERAL_SUBSTITUTE: "General substitute rate",
  SUBSTITUTE_USES_CLASS_REGULAR: "No substitute rate set: class per-session rate",
  SUBSTITUTE_USES_GENERAL_REGULAR: "No substitute rate set: general per-session rate",
  MISSING_RATE: "No rate set",
};

export const ISSUE_TEXT: Record<string, string> = { MISSING_RATE: "No per-session rate for this coach and session" };

/** A payslip as the backend calculated it: totals, then one line per paid session or adjustment. */
export function PayslipView({ payslip: p, showRules = true }: { payslip: Payslip; showRules?: boolean }) {
  const issues = p.lines.filter((line) => line.issue).length;
  return (
    <>
      <DefinitionList items={[
        ["Period", periodName(p.year, p.month)],
        ["Status", <PayrollStatusBadge key="s" status={p.run_status} />],
        ["Regular sessions", String(p.regular_sessions)],
        ["Substitute sessions", String(p.substitute_sessions)],
        ["Hours (information only)", p.hours],
        ["Gross pay", <MoneyAmount key="g" value={p.gross_pay} />],
        ["Deductions", <MoneyAmount key="d" value={p.total_deductions} />],
        ["Net pay", <strong key="n"><MoneyAmount value={p.net_pay} /></strong>],
      ]} />
      {issues ? (
        <Alert tone="warning" title={`${issues} line${issues === 1 ? " has" : "s have"} no rate and ${issues === 1 ? "is" : "are"} paid RM 0.`}>
          <p>The payroll cannot be finalized until a per-session rate is added (Django Admin) and the payroll is recalculated.</p>
        </Alert>
      ) : null}
      <Section title="Lines">
        <DataTable<PayslipLine & { _i: number }>
          caption="Payslip lines"
          rows={p.lines.map((line, i) => ({ ...line, _i: i }))}
          rowKey={(line) => line._i}
          emptyMessage="No lines."
          className="staff-table"
          columns={[
            { key: "kind", header: "Type", render: (line) => LINE_KIND[line.kind] ?? line.kind },
            { key: "description", header: "Session / description", render: (line) => (
              <>{line.description}{line.original_coach ? <><br /><span className="muted">Covering for {line.original_coach}</span></> : null}</>) },
            ...(showRules ? [{ key: "rule", header: "Rate used", priority: "secondary" as const,
                               render: (line: PayslipLine) => (line.rule ? RATE_RULE[line.rule] ?? line.rule : "—") }] : []),
            { key: "issue", header: "Issue", render: (line) => (line.issue
              ? <Badge tone="danger">{ISSUE_TEXT[line.issue] ?? line.issue}</Badge> : "—") },
            { key: "amount", header: "Amount", align: "end", render: (line) => <MoneyAmount value={line.amount} /> },
          ]}
        />
      </Section>
    </>
  );
}
