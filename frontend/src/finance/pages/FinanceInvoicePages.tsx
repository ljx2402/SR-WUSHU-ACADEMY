import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link, useParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { StaffInvoice } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { INVOICE_STATUS, InvoiceStatusBadge, PaymentStatusBadge, RegistrationStatusBadge } from "../../domain/finance";
import { formatDate, formatDateTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DefinitionList, LoadMore, Section } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { errorText } from "../../staff/components";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/Dialog";
import { DataTable } from "../../ui/DataTable";
import { SelectField, TextField } from "../../ui/Field";
import { Alert, Badge } from "../../ui/primitives";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { FINANCE_CRUMBS, INVOICE_KIND, MoneyAmount, ProofReviewStatus, financeKeys } from "../components";
import { useUrlFilters } from "../filters";

const studentsOn = (inv: StaffInvoice) => [...new Set(inv.items.map((i) => i.student_name))].join(", ");

export function FinanceInvoicesPage() {
  const { endpoints } = useServices();
  const { params, set } = useUrlFilters();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const query = {
    search: params.get("search") || undefined, status: params.get("status") || undefined,
    kind: params.get("kind") || undefined, start: params.get("start") || undefined, end: params.get("end") || undefined,
    overdue: params.get("overdue") ? (1 as const) : undefined,
  };
  const invoices = usePagedList<StaffInvoice>(financeKeys.invoices(query),
    (page, signal) => endpoints.staffInvoices({ ...query, page }, signal));
  function onSearch(e: FormEvent) { e.preventDefault(); set("search", search.trim()); }
  return (
    <>
      <PageHeader title="Invoices" crumbs={FINANCE_CRUMBS}
                  description="Family invoices: one invoice per family, with a line for each student's charge." />
      <form className="filter-bar" role="search" aria-label="Find invoices" onSubmit={onSearch}>
        <TextField label="Search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
                   placeholder="Invoice no., family, student name or no." />
        <SelectField label="Status" value={params.get("status") ?? ""} onChange={(e) => set("status", e.target.value)}>
          <option value="">All statuses</option>
          {Object.entries(INVOICE_STATUS).map(([value, s]) => <option key={value} value={value}>{s.label}</option>)}
        </SelectField>
        <SelectField label="Type" value={params.get("kind") ?? ""} onChange={(e) => set("kind", e.target.value)}>
          <option value="">All</option>
          {Object.entries(INVOICE_KIND).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </SelectField>
        <TextField label="Issued from" type="date" value={params.get("start") ?? ""} onChange={(e) => set("start", e.target.value)} />
        <TextField label="Issued to" type="date" value={params.get("end") ?? ""} onChange={(e) => set("end", e.target.value)} />
        <label className="check-field"><input type="checkbox" checked={!!params.get("overdue")}
          onChange={(e) => set("overdue", e.target.checked ? "1" : "")} /> Overdue only</label>
        <div className="filter-actions"><Button type="submit">Search</Button></div>
      </form>
      {invoices.isPending ? <LoadingState /> : invoices.isError ? (
        <ErrorState error={invoices.error} onRetry={() => invoices.refetch()} />
      ) : (
        <>
          <DataTable<StaffInvoice>
            caption="Invoices"
            rows={invoices.rows}
            rowKey={(i) => i.id}
            emptyMessage="No invoices match."
            className="staff-table"
            columns={[
              { key: "no", header: "Invoice", render: (i) => <Link className="tap-link" to={`/finance/invoices/${i.id}`}>{i.number ?? `Draft #${i.id}`}</Link> },
              { key: "family", header: "Family / students", render: (i) => <>{i.family_name || "—"}<br /><span className="muted">{studentsOn(i)}</span></> },
              { key: "status", header: "Status", render: (i) => <><InvoiceStatusBadge status={i.status} />{i.kind === "COMPETITION" ? <> <Badge tone="info">Competition</Badge></> : null}</> },
              { key: "issued", header: "Issued", priority: "secondary", render: (i) => (i.issue_date ? formatDate(i.issue_date) : "—") },
              { key: "due", header: "Due", priority: "secondary", render: (i) => (i.due_date ? formatDate(i.due_date) : "—") },
              { key: "total", header: "Total", align: "end", render: (i) => <MoneyAmount value={i.total} /> },
              { key: "paid", header: "Paid", align: "end", priority: "secondary", render: (i) => <MoneyAmount value={i.amount_paid} /> },
              { key: "due_amt", header: "Outstanding", align: "end", render: (i) => <MoneyAmount value={i.balance_due} /> },
            ]}
          />
          <LoadMore shown={invoices.rows.length} total={invoices.count} hasMore={!!invoices.hasNextPage}
                    loading={invoices.isFetchingNextPage} onMore={() => invoices.fetchNextPage()} />
        </>
      )}
    </>
  );
}

export function FinanceInvoiceDetailPage() {
  const { invoiceId = "" } = useParams();
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const invoice = useQuery({ queryKey: financeKeys.invoice(invoiceId), queryFn: ({ signal }) => endpoints.staffInvoice(invoiceId, signal) });
  const payments = useQuery({ queryKey: financeKeys.payments({ invoice: invoiceId }),
                              queryFn: ({ signal }) => endpoints.staffPayments({ invoice: Number(invoiceId) }, signal),
                              enabled: invoice.isSuccess });
  const proofs = useQuery({ queryKey: financeKeys.proofs({ invoice: invoiceId }),
                            queryFn: ({ signal }) => endpoints.staffProofs({ invoice: Number(invoiceId) }, signal),
                            enabled: invoice.isSuccess && can(me, "finance.proofs.review") });
  const [open, setOpen] = useState<"issue" | "void" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const action = useMutation({
    mutationFn: ({ name, reason }: { name: "issue" | "void"; reason: string }) =>
      (name === "issue" ? endpoints.issueInvoice(Number(invoiceId)) : endpoints.voidInvoice(Number(invoiceId), reason)),
    onSuccess: async (_, { name }) => {
      setOpen(null);
      setDone(name === "issue" ? "Invoice issued." : "Invoice voided.");
      await queryClient.invalidateQueries({ queryKey: financeKeys.all });
    },
    onError: (e) => setError(errorText(e)),
  });

  const crumbs = [...FINANCE_CRUMBS, { label: "Invoices", to: "/finance/invoices" }];
  if (invoice.isPending) return (<><PageHeader title="Invoice" crumbs={crumbs} /><LoadingState /></>);
  if (invoice.isError) {
    return (<><PageHeader title="Invoice" crumbs={crumbs} />
      {isApiError(invoice.error) && invoice.error.kind === "not_found" ? <NotFoundState message="Invoice not found." />
        : <ErrorState error={invoice.error} onRetry={() => invoice.refetch()} />}</>);
  }
  const inv = invoice.data;
  const manage = can(me, "finance.invoices.manage");
  const open4pay = inv.status === "ISSUED" || inv.status === "PARTIALLY_PAID";
  return (
    <>
      <PageHeader title={inv.number ? `Invoice ${inv.number}` : `Draft invoice #${inv.id}`} crumbs={crumbs}
                  description={<><InvoiceStatusBadge status={inv.status} /> {INVOICE_KIND[inv.kind] ?? inv.kind}</>}
                  actions={<>
                    {open4pay && can(me, "finance.payments.record") ? (
                      <Link className="btn btn-primary" to={`/finance/payments/new?invoice=${inv.id}`}>Record payment</Link>) : null}
                    {manage && inv.status === "DRAFT" ? <Button variant="secondary" onClick={() => { setError(null); setOpen("issue"); }}>Issue</Button> : null}
                    {manage && inv.status !== "VOID" ? <Button variant="danger" onClick={() => { setError(null); setOpen("void"); }}>Void</Button> : null}
                  </>} />
      <div role="status" className="status-slot">{done ? <Alert tone="success" title={done} /> : null}</div>
      {inv.status === "VOID" ? <Alert tone="danger" title="This invoice is void.">{inv.void_reason ? <p>Reason: {inv.void_reason}</p> : null}</Alert> : null}
      <Section title="Invoice">
        <DefinitionList items={[
          ["Family", inv.family_name], ["Students", studentsOn(inv)],
          ["Issued", inv.issue_date ? formatDate(inv.issue_date) : "Not issued"], ["Due", inv.due_date ? formatDate(inv.due_date) : ""],
          ["Total", <MoneyAmount key="t" value={inv.total} />], ["Paid", <MoneyAmount key="p" value={inv.amount_paid} />],
          ["Outstanding", <strong key="o"><MoneyAmount value={inv.balance_due} /></strong>],
          ...(Number(inv.amount_refunded) > 0 ? [["Refunded", <MoneyAmount key="r" value={inv.amount_refunded} />] as [string, ReactNode]] : []),
        ]} />
        {inv.notes ? <p className="prewrap muted">Staff note: {inv.notes}</p> : null}
      </Section>
      <Section title="Lines">
        <DataTable
          caption="Invoice lines"
          rows={inv.items}
          rowKey={(l) => l.id}
          columns={[
            { key: "student", header: "Student", render: (l) => `${l.student_name} (${l.student_no})` },
            { key: "desc", header: "Description", render: (l) => (
              <>{l.description}{l.competition_registration ? (
                <><br /><span className="muted">{l.competition_registration.competition_name} · {l.competition_registration.event_name}</span>{" "}
                  <RegistrationStatusBadge status={l.competition_registration.status} /></>) : null}</>) },
            { key: "amount", header: "Amount", align: "end", render: (l) => <MoneyAmount value={l.amount} /> },
            { key: "paid", header: "Paid", align: "end", render: (l) => <MoneyAmount value={l.amount_paid} /> },
          ]}
        />
      </Section>
      <Section title="Payments">
        {payments.data ? (
          <DataTable
            caption="Payments applied to this invoice"
            rows={payments.data.results}
            rowKey={(p) => p.id}
            emptyMessage="No payments recorded."
            columns={[
              { key: "no", header: "Payment", render: (p) => <Link className="tap-link" to={`/finance/payments/${p.id}`}>{p.number}</Link> },
              { key: "when", header: "Received", render: (p) => formatDateTime(p.received_at) },
              { key: "status", header: "Status", render: (p) => <PaymentStatusBadge status={p.status} /> },
              { key: "receipt", header: "Receipt", render: (p) => (p.receipt_id ? <Link className="tap-link" to={`/finance/receipts/${p.receipt_id}`}>{p.receipt_number}</Link> : "—") },
              { key: "applied", header: "Applied here", align: "end", render: (p) => (
                <MoneyAmount value={p.allocations.filter((a) => a.invoice === inv.id).reduce((t, a) => t + Number(a.amount), 0).toFixed(2)} />) },
            ]}
          />
        ) : payments.isError ? <ErrorState error={payments.error} onRetry={() => payments.refetch()} /> : <LoadingState />}
      </Section>
      {can(me, "finance.proofs.review") ? (
        <Section title="Payment proofs">
          {proofs.data ? (
            <DataTable
              caption="Payment proofs for this invoice"
              rows={proofs.data.results}
              rowKey={(p) => p.id}
              emptyMessage="No payment proofs uploaded."
              columns={[
                { key: "id", header: "Proof", render: (p) => <Link className="tap-link" to={`/finance/payment-proofs/${p.id}`}>#{p.id}</Link> },
                { key: "when", header: "Uploaded", render: (p) => formatDateTime(p.uploaded_at) },
                { key: "claimed", header: "Claimed", align: "end", render: (p) => (p.amount_claimed ? <MoneyAmount value={p.amount_claimed} /> : "—") },
                { key: "status", header: "Status", render: (p) => <ProofReviewStatus proof={p} /> },
              ]}
            />
          ) : <LoadingState />}
        </Section>
      ) : null}
      <ConfirmDialog open={open === "issue"} title="Issue invoice" confirmLabel="Issue" tone="primary"
                     message={<p>Issue this draft? Its number is assigned and its lines can no longer change.</p>}
                     busy={action.isPending} error={error} onCancel={() => setOpen(null)}
                     onConfirm={() => action.mutate({ name: "issue", reason: "" })} />
      <ConfirmDialog open={open === "void"} title="Void invoice" confirmLabel="Void invoice" requireReason
                     message={<p>Void {inv.number ?? "this draft"}? An invoice with payments cannot be voided; the academy system
                       checks this. Nothing is deleted.</p>}
                     busy={action.isPending} error={error} onCancel={() => setOpen(null)}
                     onConfirm={(reason) => action.mutate({ name: "void", reason })} />
    </>
  );
}
