import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { allPages } from "../../api/endpoints";
import { isApiError } from "../../api/errors";
import type { Charge, Invoice, InvoiceItem, Payment, Receipt } from "../../api/types";
import { useServices } from "../../app/services";
import {
  ChargeStatusBadge, FEE_TYPE_LABELS, InvoiceStatusBadge, Money, PAYMENT_METHOD_LABELS, PaymentStatusBadge,
} from "../../domain/finance";
import { formatDate, formatDateTime, formatPeriod, isPositiveMoney, sumMoney } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { Button } from "../../ui/Button";
import { DataTable, type Column } from "../../ui/DataTable";
import { Alert, Badge } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { DefinitionList, FinanceNav, LoadMore, Section, StudentSelector } from "../components";
import { useSelectedStudent } from "../ParentContext";
import { parentKeys, useFamilies, usePagedList } from "../queries";

/*
 * Family finance, read-only for parents. The model, from the backend:
 * one family → several children → charges → family invoice(s) → payments →
 * receipts. Payments are recorded by the academy office; there is no online
 * payment and no refund action for parents. There is no billing contact.
 */

const CRUMBS = [{ label: "Overview", to: "/parent/dashboard" }];
const FINANCE_CRUMBS = [...CRUMBS, { label: "Family finance", to: "/parent/finance" }];

function studentsOn(invoice: Invoice) {
  return [...new Set(invoice.items.map((item) => item.student_name))].join(", ") || "—";
}

const INVOICE_COLUMNS: Column<Invoice>[] = [
  { key: "number", header: "Invoice", render: (i) => <Link to={`/parent/finance/invoices/${i.id}`}>{i.number}</Link> },
  { key: "students", header: "Children", render: studentsOn, priority: "secondary" },
  { key: "issued", header: "Issued", render: (i) => (i.issue_date ? formatDate(i.issue_date) : "—") },
  { key: "due", header: "Due", render: (i) => (i.due_date ? formatDate(i.due_date) : "—") },
  { key: "status", header: "Status", render: (i) => <InvoiceStatusBadge status={i.status} /> },
  { key: "total", header: "Total", align: "end", render: (i) => <Money value={i.total} /> },
  { key: "paid", header: "Paid", align: "end", render: (i) => <Money value={i.amount_paid} />, priority: "secondary" },
  // A void invoice is cancelled: nothing is due on it, whatever its stored balance.
  { key: "balance", header: "Balance due", align: "end",
    render: (i) => (i.status === "VOID" ? "—" : <Money value={i.balance_due} />) },
];

const CHARGE_COLUMNS: Column<Charge>[] = [
  { key: "student", header: "Child", render: (c) => c.student_name },
  { key: "desc", header: "Description", render: (c) => c.description },
  { key: "period", header: "Period", render: (c) => formatPeriod(c.period_start, c.period_end), priority: "secondary" },
  { key: "amount", header: "Amount", align: "end", render: (c) => <Money value={c.amount} /> },
  { key: "balance", header: "Unpaid", align: "end", render: (c) => <Money value={c.balance} /> },
  { key: "status", header: "Status", render: (c) => <ChargeStatusBadge status={c.status} /> },
  { key: "invoice", header: "Invoice", render: (c) => c.invoice_number ?? "Not invoiced yet", priority: "secondary" },
];

function paymentColumns(): Column<Payment>[] {
  return [
    { key: "date", header: "Date", render: (p) => formatDateTime(p.received_at) },
    { key: "number", header: "Payment", render: (p) => p.number },
    { key: "method", header: "Method", render: (p) => PAYMENT_METHOD_LABELS[p.method] ?? p.method },
    { key: "ref", header: "Reference", render: (p) => p.reference || "—", priority: "secondary" },
    { key: "applied", header: "Applied to", render: (p) => (
      <ul className="plain-list">
        {p.allocations.map((a) => (
          <li key={a.id}>{a.invoice_number} · {a.student_name}: <Money value={a.amount} /></li>
        ))}
      </ul>
    ), priority: "secondary" },
    { key: "amount", header: "Amount", align: "end", render: (p) => <Money value={p.amount} /> },
    { key: "status", header: "Status", render: (p) => <PaymentStatusBadge status={p.status} /> },
    { key: "receipt", header: "Receipt", render: (p) => (p.receipt_id
      ? <Link to={`/parent/finance/receipts/${p.receipt_id}`}>{p.receipt_number}</Link> : "—") },
  ];
}

function FamilyHeading() {
  const families = useFamilies();
  if (!families.data?.results.length) return null;
  return (
    <div className="family-context">
      {families.data.results.map((family) => (
        <div key={family.id}>
          <p className="family-context-name">{family.name}</p>
          <p className="family-context-students">Students: {family.students.map((s) => s.full_name).join(", ")}</p>
        </div>
      ))}
    </div>
  );
}

export function FinanceOverviewPage() {
  const { endpoints } = useServices();
  const { selected } = useSelectedStudent({ allowAll: true });
  const outstanding = usePagedList<Invoice>(parentKeys.invoices({ outstanding: 1 }),
    (page, signal) => endpoints.invoices({ outstanding: 1, page }, signal));
  const studentFilter = typeof selected === "number" ? selected : undefined;
  const charges = usePagedList<Charge>(parentKeys.charges({ student: studentFilter ?? "all" }),
    (page, signal) => endpoints.charges({ student: studentFilter, page }, signal));
  return (
    <>
      <PageHeader title="Family finance" crumbs={CRUMBS}
                  description="Your family receives invoices that can include charges for several children. Payments are made at the academy office and recorded by the academy; the app does not take payments." />
      <FinanceNav />
      <FamilyHeading />
      <Section title="Invoices awaiting payment">
        {outstanding.isPending ? <LoadingState /> : outstanding.isError ? (
          <ErrorState error={outstanding.error} onRetry={() => outstanding.refetch()} />
        ) : (
          <>
            <DataTable caption="Invoices awaiting payment" rows={outstanding.rows} rowKey={(i) => i.id}
                       columns={INVOICE_COLUMNS} emptyMessage="No invoices awaiting payment." />
            <LoadMore shown={outstanding.rows.length} total={outstanding.count} hasMore={!!outstanding.hasNextPage}
                      loading={outstanding.isFetchingNextPage} onMore={() => outstanding.fetchNextPage()} />
          </>
        )}
      </Section>
      <Section title="Charges" actions={<StudentSelector allowAll label="Child" />}>
        <p className="muted">Each fee for a child. Charges are collected into the family’s invoices.</p>
        {charges.isPending ? <LoadingState /> : charges.isError ? (
          <ErrorState error={charges.error} onRetry={() => charges.refetch()} />
        ) : (
          <>
            <DataTable caption="Charges" rows={charges.rows} rowKey={(c) => c.id} columns={CHARGE_COLUMNS}
                       emptyMessage="No charges found." />
            <LoadMore shown={charges.rows.length} total={charges.count} hasMore={!!charges.hasNextPage}
                      loading={charges.isFetchingNextPage} onMore={() => charges.fetchNextPage()} />
          </>
        )}
      </Section>
    </>
  );
}

const INVOICE_FILTERS = [
  { id: "all", label: "All", query: {} },
  { id: "outstanding", label: "Awaiting payment", query: { outstanding: 1 as const } },
  { id: "paid", label: "Paid", query: { status: "PAID" } },
  { id: "void", label: "Void", query: { status: "VOID" } },
];

export function InvoicesPage() {
  const { endpoints } = useServices();
  const [filter, setFilter] = useState("all");
  const query = INVOICE_FILTERS.find((f) => f.id === filter)!.query;
  const invoices = usePagedList<Invoice>(parentKeys.invoices(query),
    (page, signal) => endpoints.invoices({ ...query, page }, signal));
  return (
    <>
      <PageHeader title="Invoices" crumbs={FINANCE_CRUMBS} />
      <FinanceNav />
      <fieldset className="segmented">
        <legend>Show</legend>
        {INVOICE_FILTERS.map((f) => (
          <label key={f.id}>
            <input type="radio" name="invoice-filter" value={f.id} checked={filter === f.id}
                   onChange={() => setFilter(f.id)} />
            <span>{f.label}</span>
          </label>
        ))}
      </fieldset>
      {invoices.isPending ? <LoadingState /> : invoices.isError ? (
        <ErrorState error={invoices.error} onRetry={() => invoices.refetch()} />
      ) : (
        <>
          <DataTable caption="Family invoices" rows={invoices.rows} rowKey={(i) => i.id} columns={INVOICE_COLUMNS}
                     emptyMessage="No invoices available." />
          <LoadMore shown={invoices.rows.length} total={invoices.count} hasMore={!!invoices.hasNextPage}
                    loading={invoices.isFetchingNextPage} onMore={() => invoices.fetchNextPage()} />
        </>
      )}
    </>
  );
}

function groupByStudent(items: InvoiceItem[]) {
  const groups = new Map<string, InvoiceItem[]>();
  for (const item of items) {
    const key = `${item.student_name}${item.student_no ? ` (${item.student_no})` : ""}`;
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return [...groups.entries()];
}

export function InvoiceDetailPage() {
  const { invoiceId = "" } = useParams();
  const { endpoints } = useServices();
  const invoice = useQuery({
    queryKey: parentKeys.invoice(invoiceId),
    queryFn: ({ signal }) => endpoints.invoice(invoiceId, signal),
  });
  // Payments applied to this invoice (a family's payment history is short; bounded to 5 pages).
  const payments = useQuery({
    queryKey: parentKeys.allPayments,
    queryFn: ({ signal }) => allPages((page) => endpoints.payments({ page }, signal), 5),
    enabled: invoice.isSuccess,
  });

  if (invoice.isPending) return <LoadingState />;
  if (invoice.isError) {
    if (isApiError(invoice.error) && invoice.error.kind === "not_found") {
      return (<><PageHeader title="Invoice not found" crumbs={FINANCE_CRUMBS} /><NotFoundState /></>);
    }
    return <ErrorState error={invoice.error} onRetry={() => invoice.refetch()} />;
  }
  const inv = invoice.data;
  const applied = (payments.data?.rows ?? []).flatMap((p) =>
    p.allocations.filter((a) => a.invoice === inv.id).map((a) => ({ payment: p, allocation: a })));
  return (
    <>
      <PageHeader title={`Invoice ${inv.number}`}
                  crumbs={[...FINANCE_CRUMBS, { label: "Invoices", to: "/parent/finance/invoices" }]}
                  actions={<Button variant="secondary" className="no-print" onClick={() => window.print()}>Print</Button>} />
      <div className="document">
        <p><InvoiceStatusBadge status={inv.status} /></p>
        {inv.status === "VOID" ? (
          <Alert tone="warning" title="This invoice was cancelled (void).">
            {inv.void_reason ? <p>Reason: {inv.void_reason}</p> : null}
          </Alert>
        ) : null}
        <DefinitionList items={[
          ["Family", inv.family_name],
          ["Issued", inv.issue_date ? formatDate(inv.issue_date) : ""],
          ["Due", inv.due_date ? formatDate(inv.due_date) : ""],
        ]} />
        <Section title="Charges on this invoice">
          {!inv.items.length ? <EmptyState message="No records found." /> : groupByStudent(inv.items).map(([student, items]) => (
            <div key={student} className="invoice-student">
              <h3>{student}</h3>
              <DataTable
                caption={`Charges for ${student}`}
                rows={items}
                rowKey={(item) => item.id}
                columns={[
                  { key: "desc", header: "Description", render: (i) => i.description },
                  { key: "type", header: "Type", render: (i) => FEE_TYPE_LABELS[i.fee_type] ?? i.fee_type, priority: "secondary" },
                  { key: "period", header: "Period", render: (i) => formatPeriod(i.period_start, i.period_end) },
                  { key: "amount", header: "Amount", align: "end", render: (i) => <Money value={i.amount} /> },
                ]}
              />
              {items.length > 1 ? (
                <p className="subtotal">Subtotal for {student.split(" (")[0]}: <Money value={sumMoney(items.map((i) => i.amount))} /></p>
              ) : null}
            </div>
          ))}
        </Section>
        <dl className="totals">
          {isPositiveMoney(inv.discount_total) ? (
            <><div><dt>Subtotal</dt><dd><Money value={inv.subtotal} /></dd></div>
              <div><dt>Discount</dt><dd><Money value={`-${inv.discount_total}`} /></dd></div></>
          ) : null}
          <div className="totals-grand"><dt>Total</dt><dd><Money value={inv.total} /></dd></div>
          <div><dt>Paid</dt><dd><Money value={inv.amount_paid} /></dd></div>
          {inv.status === "VOID" ? (
            <div className="totals-grand"><dt>Balance due</dt><dd>Nothing (cancelled)</dd></div>
          ) : <div className="totals-grand"><dt>Balance due</dt><dd><Money value={inv.balance_due} /></dd></div>}
          {isPositiveMoney(inv.amount_refunded) ? (
            <div><dt>Refunded by the academy</dt><dd><Money value={inv.amount_refunded} /></dd></div>
          ) : null}
        </dl>
        {isPositiveMoney(inv.balance_due) && inv.status !== "VOID" ? (
          <Alert tone="info" title="How to pay">
            <p>Please pay at the academy office or as the academy has advised. Payments are recorded by the academy,
              and a receipt appears here once they have been recorded.</p>
          </Alert>
        ) : null}
        <Section title="Payments applied">
          {payments.isPending ? <LoadingState /> : payments.isError ? <ErrorState error={payments.error} /> : (
            <DataTable
              caption="Payments applied to this invoice"
              rows={applied}
              rowKey={(r) => r.allocation.id}
              emptyMessage="No payments recorded for this invoice."
              columns={[
                { key: "date", header: "Date", render: (r) => formatDateTime(r.payment.received_at) },
                { key: "student", header: "Child", render: (r) => r.allocation.student_name },
                { key: "amount", header: "Amount", align: "end", render: (r) => <Money value={r.allocation.amount} /> },
                { key: "status", header: "Status", render: (r) => <PaymentStatusBadge status={r.payment.status} /> },
                { key: "receipt", header: "Receipt", render: (r) => (r.payment.receipt_id
                  ? <Link to={`/parent/finance/receipts/${r.payment.receipt_id}`}>{r.payment.receipt_number}</Link> : "—") },
              ]}
            />
          )}
        </Section>
      </div>
    </>
  );
}

export function PaymentsPage() {
  const { endpoints } = useServices();
  const payments = usePagedList<Payment>(parentKeys.payments, (page, signal) => endpoints.payments({ page }, signal));
  return (
    <>
      <PageHeader title="Payments" crumbs={FINANCE_CRUMBS}
                  description="Payments the academy has recorded for your family, newest first." />
      <FinanceNav />
      {payments.isPending ? <LoadingState /> : payments.isError ? (
        <ErrorState error={payments.error} onRetry={() => payments.refetch()} />
      ) : (
        <>
          <DataTable caption="Family payments" rows={payments.rows} rowKey={(p) => p.id} columns={paymentColumns()}
                     emptyMessage="No payments recorded." />
          <LoadMore shown={payments.rows.length} total={payments.count} hasMore={!!payments.hasNextPage}
                    loading={payments.isFetchingNextPage} onMore={() => payments.fetchNextPage()} />
        </>
      )}
    </>
  );
}

export function ReceiptsPage() {
  const { endpoints } = useServices();
  const receipts = usePagedList<Receipt>(parentKeys.receipts, (page, signal) => endpoints.receipts({ page }, signal));
  return (
    <>
      <PageHeader title="Receipts" crumbs={FINANCE_CRUMBS} description="Official receipts for your family’s payments." />
      <FinanceNav />
      {receipts.isPending ? <LoadingState /> : receipts.isError ? (
        <ErrorState error={receipts.error} onRetry={() => receipts.refetch()} />
      ) : (
        <>
          <DataTable
            caption="Receipts"
            rows={receipts.rows}
            rowKey={(r) => r.id}
            emptyMessage="No receipts yet."
            columns={[
              { key: "number", header: "Receipt", render: (r) => <Link to={`/parent/finance/receipts/${r.id}`}>{r.number}</Link> },
              { key: "issued", header: "Issued", render: (r) => formatDateTime(r.issued_at) },
              { key: "students", header: "Children", render: (r) => r.content.students.join(", "), priority: "secondary" },
              { key: "total", header: "Amount", align: "end", render: (r) => <Money value={r.total} /> },
              { key: "status", header: "Status", render: (r) => (r.is_void ? <Badge tone="danger">Void</Badge> : <Badge tone="success">Valid</Badge>) },
            ]}
          />
          <LoadMore shown={receipts.rows.length} total={receipts.count} hasMore={!!receipts.hasNextPage}
                    loading={receipts.isFetchingNextPage} onMore={() => receipts.fetchNextPage()} />
        </>
      )}
    </>
  );
}

/**
 * The receipt exactly as issued (the frozen content from /api/receipts/:id/,
 * scoped to the parent's family by the backend), printable from the browser.
 * The Django print page is not used: it needs a staff session.
 */
export function ReceiptDetailPage() {
  const { receiptId = "" } = useParams();
  const { endpoints } = useServices();
  const receipt = useQuery({
    queryKey: parentKeys.receipt(receiptId),
    queryFn: ({ signal }) => endpoints.receipt(receiptId, signal),
  });
  if (receipt.isPending) return <LoadingState />;
  if (receipt.isError) {
    if (isApiError(receipt.error) && receipt.error.kind === "not_found") {
      return (<><PageHeader title="Receipt not found" crumbs={FINANCE_CRUMBS} /><NotFoundState /></>);
    }
    return <ErrorState error={receipt.error} onRetry={() => receipt.refetch()} />;
  }
  const r = receipt.data;
  const c = r.content;
  return (
    <>
      <PageHeader title={`Receipt ${r.number}`}
                  crumbs={[...FINANCE_CRUMBS, { label: "Receipts", to: "/parent/finance/receipts" }]}
                  actions={<Button variant="secondary" className="no-print" onClick={() => window.print()}>Print</Button>} />
      <article className="document receipt" aria-label={`Official receipt ${r.number}`}>
        {r.is_void ? (
          <Alert tone="danger" title="This receipt has been voided.">
            {r.void_reason ? <p>Reason: {r.void_reason}</p> : null}
          </Alert>
        ) : null}
        <header className="receipt-header">
          <p className="receipt-academy">{c.academy.name}</p>
          {c.academy.registration_no ? <p className="muted">{c.academy.registration_no}</p> : null}
          {c.academy.address ? <p className="muted prewrap">{c.academy.address}</p> : null}
          <p className="muted">{[c.academy.phone, c.academy.email].filter(Boolean).join(" · ")}</p>
          <h2>Official receipt</h2>
        </header>
        <DefinitionList items={[
          ["Receipt no.", c.number],
          ["Issued", formatDateTime(c.issued_at)],
          ["Payment date", formatDateTime(c.payment_date)],
          ["Payment method", c.payment_method],
          ["Reference", c.reference],
          ["Received from", c.payer_reference],
          ["Students", c.students.join(", ")],
        ]} />
        <DataTable
          caption="Items paid"
          rows={c.lines.map((line, index) => ({ ...line, key: index }))}
          rowKey={(line) => line.key}
          columns={[
            { key: "invoice", header: "Invoice", render: (l) => l.invoice_number },
            { key: "student", header: "Student", render: (l) => l.student_name },
            { key: "desc", header: "Description", render: (l) => l.description },
            { key: "period", header: "Period", render: (l) => formatPeriod(l.period_start, l.period_end), priority: "secondary" },
            { key: "paid", header: "Paid", align: "end", render: (l) => <Money value={l.amount_paid} /> },
          ]}
        />
        <dl className="totals">
          <div className="totals-grand"><dt>Total received</dt><dd><Money value={c.total} /></dd></div>
        </dl>
        {c.issued_by ? <p className="muted">Issued by {c.issued_by}</p> : null}
      </article>
    </>
  );
}
