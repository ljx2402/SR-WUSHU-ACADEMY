import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { InvoiceStatusBadge, PAYMENT_METHOD_LABELS, PaymentStatusBadge } from "../../domain/finance";
import { formatDate, formatDateTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { Button } from "../../ui/Button";
import { DataTable } from "../../ui/DataTable";
import { Alert, Card, KpiCard } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState } from "../../ui/states";
import { MoneyAmount, financeKeys } from "../components";

/** Operational finance overview: every number is the backend's (no new statuses). */
export function FinanceDashboardPage() {
  const me = useMe();
  const { endpoints } = useServices();
  const dashboard = useQuery({ queryKey: financeKeys.dashboard, queryFn: ({ signal }) => endpoints.financeDashboard(signal) });
  const header = (
    <PageHeader title="Finance dashboard" description="Invoices, payments, receipts and payment proofs. Payments are recorded manually."
                actions={can(me, "finance.payments.record") ? <Link className="btn btn-primary" to="/finance/payments/new">Record payment</Link> : null} />
  );
  if (dashboard.isPending) return (<>{header}<LoadingState /></>);
  if (dashboard.isError) return (<>{header}<ErrorState error={dashboard.error} onRetry={() => dashboard.refetch()} /></>);
  const d = dashboard.data;
  const proofs = d.proofs;
  return (
    <>
      {header}
      <section aria-label="Finance at a glance" className="grid grid-kpi">
        <KpiCard label="Outstanding" value={<MoneyAmount value={d.invoices.outstanding_total} />}
                 hint={`${d.invoices.unpaid} unpaid · ${d.invoices.partially_paid} partially paid`} />
        <KpiCard label="Overdue invoices" value={d.invoices.overdue} hint="Past their due date and not fully paid" />
        <KpiCard label="Payments today" value={d.payments.today_count} hint={`Total received today`} />
        {proofs ? <KpiCard label="Proofs to review" value={proofs.pending}
                           hint={`${proofs.accepted_recently} accepted · ${proofs.rejected_recently} rejected in 7 days`} /> : null}
      </section>
      <p className="muted">Received today: <MoneyAmount value={d.payments.today_total} /> · Competition invoices open:
        {" "}{d.invoices.competition_open} (<MoneyAmount value={d.invoices.competition_outstanding} /> due)</p>

      {proofs && (proofs.pending || proofs.accepted_awaiting_payment) ? (
        <Alert tone="warning" title="Needs attention">
          <ul className="plain-list">
            {proofs.pending ? <li><Link className="tap-link" to="/finance/payment-proofs?status=PENDING_REVIEW">
              {proofs.pending} payment proof{proofs.pending === 1 ? "" : "s"} waiting for review</Link></li> : null}
            {proofs.accepted_awaiting_payment ? <li><Link className="tap-link" to="/finance/payment-proofs?status=ACCEPTED">
              {proofs.accepted_awaiting_payment} accepted proof{proofs.accepted_awaiting_payment === 1 ? "" : "s"} with
              the payment still to be recorded</Link></li> : null}
          </ul>
        </Alert>
      ) : null}

      <div className="grid grid-2">
        {proofs ? (
          <Card title="Oldest proofs waiting" actions={<Link className="tap-link" to="/finance/payment-proofs">All proofs</Link>}>
            {proofs.oldest_pending.length ? (
              <ul className="plain-list entry-list">
                {proofs.oldest_pending.map((p) => (
                  <li key={p.id}><Link className="tap-link" to={`/finance/payment-proofs/${p.id}`}>
                    {p.invoice_number} · {p.family_name}</Link>
                    <span className="muted"> · {p.amount_claimed ? <MoneyAmount value={p.amount_claimed} /> : "no amount"} ·
                      {" "}{formatDateTime(p.uploaded_at)}</span></li>
                ))}
              </ul>
            ) : <EmptyState message="No proofs waiting for review." />}
          </Card>
        ) : null}
        <Card title="Overdue invoices" actions={<Link className="tap-link" to="/finance/invoices?overdue=1">All overdue</Link>}>
          {d.invoices.overdue_list.length ? (
            <ul className="plain-list entry-list">
              {d.invoices.overdue_list.map((i) => (
                <li key={i.id}><Link className="tap-link" to={`/finance/invoices/${i.id}`}>{i.number} · {i.family_name}</Link>
                  <span className="muted"> · due {i.due_date ? formatDate(i.due_date) : "—"} · </span>
                  <MoneyAmount value={i.balance_due} /> <InvoiceStatusBadge status={i.status} /></li>
              ))}
            </ul>
          ) : <EmptyState message="No overdue invoices." />}
        </Card>
        <Card title="Recent payments" className="span-all" actions={<Link className="tap-link" to="/finance/payments">All payments</Link>}>
          <DataTable
            caption="Recent payments"
            rows={d.payments.recent}
            rowKey={(p) => p.id}
            emptyMessage="No payments yet."
            columns={[
              { key: "no", header: "Payment", render: (p) => <Link className="tap-link" to={`/finance/payments/${p.id}`}>{p.number}</Link> },
              { key: "family", header: "Family", render: (p) => p.family_name },
              { key: "when", header: "Received", priority: "secondary", render: (p) => formatDateTime(p.received_at) },
              { key: "method", header: "Method", priority: "secondary", render: (p) => PAYMENT_METHOD_LABELS[p.method] ?? p.method },
              { key: "status", header: "Status", render: (p) => <PaymentStatusBadge status={p.status} /> },
              { key: "receipt", header: "Receipt", render: (p) => p.receipt_number ?? "—" },
              { key: "amount", header: "Amount", align: "end", render: (p) => <MoneyAmount value={p.amount} /> },
            ]}
          />
        </Card>
        <Card title="Recent receipts" className="span-all" actions={<Link className="tap-link" to="/finance/receipts">All receipts</Link>}>
          <DataTable
            caption="Recent receipts"
            rows={d.receipts}
            rowKey={(r) => r.id}
            emptyMessage="No receipts yet."
            columns={[
              { key: "no", header: "Receipt", render: (r) => <Link className="tap-link" to={`/finance/receipts/${r.id}`}>{r.number}</Link> },
              { key: "when", header: "Issued", render: (r) => formatDateTime(r.issued_at) },
              { key: "void", header: "Status", render: (r) => (r.is_void ? "Voided" : "Valid") },
              { key: "total", header: "Total", align: "end", render: (r) => <MoneyAmount value={r.total} /> },
            ]}
          />
        </Card>
      </div>
      <p className="no-print"><Button variant="ghost" onClick={() => dashboard.refetch()}>Refresh</Button></p>
    </>
  );
}
