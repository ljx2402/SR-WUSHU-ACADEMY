import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { PayrollExcluded, PayrollRun, Payslip } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { PayrollStatusBadge } from "../../domain/finance";
import { academyToday, formatDate, formatDateTime } from "../../domain/format";
import { MoneyAmount } from "../../finance/components";
import { PageHeader } from "../../layout/PageHeader";
import { DefinitionList, LoadMore, Section } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { AdminLink, errorText } from "../../staff/components";
import { Button } from "../../ui/Button";
import { DataTable } from "../../ui/DataTable";
import { ConfirmDialog } from "../../ui/Dialog";
import { SelectField, TextField } from "../../ui/Field";
import { Alert, Badge } from "../../ui/primitives";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { MONTH_NAMES, PAYROLL_CRUMBS, PAYROLL_ROOT, PayslipView, payrollKeys, periodName } from "../components";

/*
 * Payroll Staff Portal (Phase 6H) over the existing endpoints:
 * /api/payroll-runs/ (read), /calculate/ (payroll.prepare: FINANCE_ADMIN,
 * SUPER_ADMIN), /:id/finalize/ (payroll.finalize: SUPER_ADMIN only) and
 * /api/payslips/?run=&coach=. The backend decides who is paid, at which rate,
 * every total, and every refusal; this page shows them.
 */

function previousMonth() {
  const [y, m] = academyToday().split("-").map(Number);
  return m === 1 ? { year: y - 1, month: 12 } : { year: y, month: m - 1 };
}

export function PayrollPage() {
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const runs = usePagedList<PayrollRun>(payrollKeys.runs, (page, signal) => endpoints.staffPayrollRuns({ page }, signal));
  const [period, setPeriod] = useState(() => {
    const p = previousMonth();
    return { year: String(p.year), month: String(p.month) };
  });
  const [confirming, setConfirming] = useState(false);
  const calculate = useMutation({
    mutationFn: () => endpoints.calculatePayroll(Number(period.year), Number(period.month)),
    onSuccess: async (run) => {
      setConfirming(false);
      await queryClient.invalidateQueries({ queryKey: payrollKeys.all });
      navigate(`${PAYROLL_ROOT}/${run.id}`);
    },
  });
  const prepares = can(me, "payroll.prepare");
  function submit(e: FormEvent) { e.preventDefault(); calculate.reset(); setConfirming(true); }
  return (
    <>
      <PageHeader title="Payroll" crumbs={PAYROLL_CRUMBS.slice(0, 1)}
                  description="Coach pay per session, one period per calendar month. Finance calculates and reviews; only a super admin finalizes. A finalized payroll can never change." />
      {prepares ? (
        <Section title="Calculate a month">
          <form className="filter-bar" onSubmit={submit} aria-label="Calculate payroll">
            <SelectField label="Month" value={period.month} onChange={(e) => setPeriod({ ...period, month: e.target.value })}>
              {MONTH_NAMES.map((name, i) => <option key={name} value={i + 1}>{name}</option>)}
            </SelectField>
            <TextField label="Year" inputMode="numeric" value={period.year} maxLength={4}
                       onChange={(e) => setPeriod({ ...period, year: e.target.value })} />
            <div className="filter-actions"><Button type="submit">Calculate</Button></div>
          </form>
          <p className="muted">Calculating creates the month's period if needed, or recalculates it. Only coaching sessions
            that have ended are paid; rates and adjustments are managed in <AdminLink path="payroll/coachrate/">Django Admin</AdminLink>.</p>
        </Section>
      ) : null}
      <Section title="Payroll periods">
        {runs.isPending ? <LoadingState /> : runs.isError ? (
          <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
        ) : (
          <>
            <DataTable<PayrollRun>
              caption="Payroll periods"
              rows={runs.rows}
              rowKey={(r) => r.id}
              emptyMessage="No payroll periods yet."
              className="staff-table"
              columns={[
                { key: "period", header: "Period", render: (r) => (
                  <Link className="tap-link" to={`${PAYROLL_ROOT}/${r.id}`}>{periodName(r.year, r.month)}</Link>) },
                { key: "status", header: "Status", render: (r) => <PayrollStatusBadge status={r.status} /> },
                { key: "issues", header: "Issues", align: "end", render: (r) => (r.issue_count
                  ? <Badge tone="danger">{r.issue_count}</Badge> : "0") },
                { key: "calculated", header: "Calculated", priority: "secondary", render: (r) => formatDateTime(r.calculated_at) },
                { key: "finalized", header: "Finalized", priority: "secondary", render: (r) => formatDateTime(r.finalized_at) },
              ]}
            />
            <LoadMore shown={runs.rows.length} total={runs.count} hasMore={!!runs.hasNextPage}
                      loading={runs.isFetchingNextPage} onMore={() => runs.fetchNextPage()} />
          </>
        )}
      </Section>
      <ConfirmDialog
        open={confirming}
        title={`Calculate payroll for ${periodName(Number(period.year), Number(period.month))}?`}
        confirmLabel="Calculate"
        tone="primary"
        busy={calculate.isPending}
        error={calculate.error ? errorText(calculate.error) : null}
        message={<p>The period's payslips are (re)built from its sessions, coach assignments, rates and adjustments.
          A finalized period is never recalculated.</p>}
        onCancel={() => setConfirming(false)}
        onConfirm={() => calculate.mutate()}
      />
    </>
  );
}

function useRun(runId: string) {
  const { endpoints } = useServices();
  return useQuery({ queryKey: payrollKeys.run(runId), queryFn: ({ signal }) => endpoints.payrollRun(runId, signal) });
}

function RunNotFound({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return isApiError(error) && error.kind === "not_found"
    ? <NotFoundState message="Payroll period not found." /> : <ErrorState error={error} onRetry={onRetry} />;
}

export function PayrollRunPage() {
  const { runId = "" } = useParams();
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const run = useRun(runId);
  const payslips = usePagedList<Payslip>(payrollKeys.payslips({ run: runId }),
    (page, signal) => endpoints.payslips({ run: Number(runId), page }, signal), run.isSuccess);
  const [action, setAction] = useState<"calculate" | "finalize" | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const act = useMutation({
    mutationFn: (name: "calculate" | "finalize") => (name === "finalize"
      ? endpoints.finalizePayroll(Number(runId))
      : endpoints.calculatePayroll(run.data!.year, run.data!.month)),
    onSuccess: async (updated, name) => {
      setAction(null);
      setDone(name === "finalize" ? "Payroll finalized. It is now permanent." : "Payroll recalculated.");
      queryClient.setQueryData(payrollKeys.run(runId), updated);
      await queryClient.invalidateQueries({ queryKey: payrollKeys.all });
    },
  });

  if (run.isPending) return (<><PageHeader title="Payroll period" crumbs={PAYROLL_CRUMBS} /><LoadingState /></>);
  if (run.isError) {
    return (<><PageHeader title="Payroll period" crumbs={PAYROLL_CRUMBS} /><RunNotFound error={run.error} onRetry={() => run.refetch()} /></>);
  }
  const r = run.data;
  const locked = r.status === "FINALIZED";
  const prepares = can(me, "payroll.prepare");
  const finalizes = can(me, "payroll.finalize");
  return (
    <>
      <PageHeader title={`Payroll ${periodName(r.year, r.month)}`} crumbs={PAYROLL_CRUMBS}
                  actions={<span className="button-row">
                    {prepares && !locked ? <Button variant="secondary" onClick={() => { act.reset(); setAction("calculate"); }}>Recalculate</Button> : null}
                    {finalizes && r.status === "READY" ? <Button onClick={() => { act.reset(); setAction("finalize"); }}>Finalize</Button> : null}
                  </span>} />
      {done ? <Alert tone="success" role="status" title={done} /> : null}
      <DefinitionList items={[
        ["Period", `${formatDate(r.period_start)} – ${formatDate(r.period_end)}`],
        ["Status", <PayrollStatusBadge key="s" status={r.status} />],
        ["Unresolved issues", String(r.issue_count)],
        ["Calculated", r.calculated_at ? formatDateTime(r.calculated_at) : "Not yet"],
        ["Finalized", r.finalized_at ? formatDateTime(r.finalized_at) : "No"],
      ]} />
      {locked ? (
        <Alert tone="info" title="Finalized: this payroll is permanent.">
          <p>Its payslips, the sessions of this month and the rates it used can no longer change. Corrections go into a later month.</p>
        </Alert>
      ) : r.status === "DRAFT" && r.calculated_at ? (
        <Alert tone="warning" title={`${r.issue_count} unresolved issue${r.issue_count === 1 ? "" : "s"}: this payroll cannot be finalized.`}>
          <p>Add the missing per-session rates in <AdminLink path="payroll/coachrate/">Django Admin</AdminLink>, then recalculate.</p>
        </Alert>
      ) : r.status === "READY" ? (
        <Alert tone="info" title="Calculated: ready for review.">
          <p>{finalizes ? "Finalize once the month has ended and the payslips are correct; the system refuses if anything changed since the calculation."
            : "A super admin finalizes the payroll after the month has ended."}</p>
        </Alert>
      ) : null}

      <Section title="Payslips">
        {payslips.isPending ? <LoadingState /> : payslips.isError ? (
          <ErrorState error={payslips.error} onRetry={() => payslips.refetch()} />
        ) : (
          <>
            <DataTable<Payslip>
              caption={`Payslips for ${periodName(r.year, r.month)}`}
              rows={payslips.rows}
              rowKey={(p) => p.id}
              emptyMessage={r.calculated_at ? "No coach was paid in this period." : "Not calculated yet."}
              className="staff-table"
              columns={[
                { key: "coach", header: "Coach", render: (p) => (
                  <Link className="tap-link" to={`${PAYROLL_ROOT}/${r.id}/coach/${p.coach}`}>{p.coach_name}</Link>) },
                { key: "regular", header: "Regular", align: "end", render: (p) => p.regular_sessions },
                { key: "substitute", header: "Substitute", align: "end", render: (p) => p.substitute_sessions },
                { key: "gross", header: "Gross", align: "end", priority: "secondary", render: (p) => <MoneyAmount value={p.gross_pay} /> },
                { key: "deductions", header: "Deductions", align: "end", priority: "secondary", render: (p) => <MoneyAmount value={p.total_deductions} /> },
                { key: "net", header: "Net pay", align: "end", render: (p) => <MoneyAmount value={p.net_pay} /> },
                { key: "issues", header: "Issues", render: (p) => (p.lines.some((l) => l.issue)
                  ? <Badge tone="danger">Missing rate</Badge> : "—") },
              ]}
            />
            <LoadMore shown={payslips.rows.length} total={payslips.count} hasMore={!!payslips.hasNextPage}
                      loading={payslips.isFetchingNextPage} onMore={() => payslips.fetchNextPage()} />
          </>
        )}
      </Section>

      <Section title={`Not paid in this period (${r.excluded.length})`}>
        <DataTable<PayrollExcluded>
          caption="Coach assignments not paid, and why"
          rows={r.excluded}
          rowKey={(x) => `${x.slot}-${x.session}`}
          emptyMessage="Every coach assignment in the period was paid."
          className="staff-table"
          columns={[
            { key: "date", header: "Date", render: (x) => formatDate(x.date) },
            { key: "class", header: "Class", render: (x) => x.class },
            { key: "coach", header: "Coach", render: (x) => <>{x.coach} <span className="muted">({x.role === "SUBSTITUTE" ? "substitute" : "regular"})</span></> },
            { key: "reason", header: "Reason", render: (x) => x.reason },
          ]}
        />
      </Section>
      {r.notes ? <Section title="Notes"><p className="prewrap">{r.notes}</p></Section> : null}

      <ConfirmDialog
        open={!!action}
        title={action === "finalize" ? `Finalize payroll for ${periodName(r.year, r.month)}?` : "Recalculate this payroll?"}
        confirmLabel={action === "finalize" ? "Finalize permanently" : "Recalculate"}
        tone={action === "finalize" ? "danger" : "primary"}
        busy={act.isPending}
        error={act.error ? errorText(act.error) : null}
        message={action === "finalize" ? (
          <><p><strong>This cannot be undone.</strong> The payslips become permanent and coaches can see them. The month's
            sessions, substitutions and the rates used are locked.</p>
            <p>The system refuses if the month has not ended, if there are unresolved issues, or if anything changed since the
              last calculation.</p></>
        ) : <p>The payslips are rebuilt from the current sessions, coach assignments, rates and adjustments.</p>}
        onCancel={() => setAction(null)}
        onConfirm={() => action && act.mutate(action)}
      />
    </>
  );
}

export function PayrollCoachPage() {
  const { runId = "", coachId = "" } = useParams();
  const { endpoints } = useServices();
  const run = useRun(runId);
  const query = { run: Number(runId), coach: Number(coachId) };
  const payslip = useQuery({
    queryKey: payrollKeys.payslips(query),
    queryFn: ({ signal }) => endpoints.payslips(query, signal),
    enabled: run.isSuccess,
  });
  const crumbs = run.data ? [...PAYROLL_CRUMBS, { label: periodName(run.data.year, run.data.month), to: `${PAYROLL_ROOT}/${runId}` }]
    : PAYROLL_CRUMBS;
  if (run.isError) return (<><PageHeader title="Payslip" crumbs={crumbs} /><RunNotFound error={run.error} onRetry={() => run.refetch()} /></>);
  if (run.isPending || payslip.isPending) return (<><PageHeader title="Payslip" crumbs={crumbs} /><LoadingState /></>);
  if (payslip.isError) return (<><PageHeader title="Payslip" crumbs={crumbs} /><ErrorState error={payslip.error} onRetry={() => payslip.refetch()} /></>);
  const p = payslip.data.results[0];
  if (!p) return (<><PageHeader title="Payslip" crumbs={crumbs} /><NotFoundState message="This coach has no payslip in this period." /></>);
  return (
    <>
      <PageHeader title={`${p.coach_name}: ${periodName(p.year, p.month)}`} crumbs={crumbs} />
      <PayslipView payslip={p} />
    </>
  );
}
