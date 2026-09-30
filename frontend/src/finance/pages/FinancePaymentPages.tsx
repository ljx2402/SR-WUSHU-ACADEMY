import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { StaffInvoice, StaffPayment } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { PAYMENT_METHOD_LABELS, PaymentStatusBadge } from "../../domain/finance";
import { formatDateTime, isPositiveMoney, sumMoney } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DefinitionList, LoadMore, Section } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { ActionDialog, errorText, useDialog } from "../../staff/components";
import { Button } from "../../ui/Button";
import { ConfirmDialog, Dialog } from "../../ui/Dialog";
import { DataTable } from "../../ui/DataTable";
import { SelectField, TextAreaField, TextField } from "../../ui/Field";
import { Alert } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { FINANCE_CRUMBS, MoneyAmount, financeKeys, newIdempotencyKey } from "../components";
import { useUrlFilters } from "../filters";

const CRUMBS = [...FINANCE_CRUMBS, { label: "Payments", to: "/finance/payments" }];

function useMethods() {
  const { endpoints } = useServices();
  return useQuery({ queryKey: financeKeys.methods, queryFn: ({ signal }) => endpoints.paymentMethods(signal), staleTime: Infinity });
}

export function FinancePaymentsPage() {
  const me = useMe();
  const { endpoints } = useServices();
  const { params, set } = useUrlFilters();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const query = { search: params.get("search") || undefined, status: params.get("status") || undefined,
                  start: params.get("start") || undefined, end: params.get("end") || undefined };
  const payments = usePagedList<StaffPayment>(financeKeys.payments(query),
    (page, signal) => endpoints.staffPayments({ ...query, page }, signal));
  return (
    <>
      <PageHeader title="Payments" crumbs={FINANCE_CRUMBS} description="Money received, recorded manually by staff. Each payment issues an official receipt."
                  actions={can(me, "finance.payments.record") ? <Link className="btn btn-primary" to="/finance/payments/new">Record payment</Link> : null} />
      <form className="filter-bar" role="search" aria-label="Find payments" onSubmit={(e) => { e.preventDefault(); set("search", search.trim()); }}>
        <TextField label="Search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
                   placeholder="Payment or receipt no., reference, family, invoice" />
        <SelectField label="Status" value={params.get("status") ?? ""} onChange={(e) => set("status", e.target.value)}>
          <option value="">All</option><option value="VALID">Recorded</option><option value="VOIDED">Voided</option>
        </SelectField>
        <TextField label="Received from" type="date" value={params.get("start") ?? ""} onChange={(e) => set("start", e.target.value)} />
        <TextField label="Received to" type="date" value={params.get("end") ?? ""} onChange={(e) => set("end", e.target.value)} />
        <div className="filter-actions"><Button type="submit">Search</Button></div>
      </form>
      {payments.isPending ? <LoadingState /> : payments.isError ? <ErrorState error={payments.error} onRetry={() => payments.refetch()} /> : (
        <>
          <DataTable<StaffPayment>
            caption="Payments"
            rows={payments.rows}
            rowKey={(p) => p.id}
            emptyMessage="No payments match."
            className="staff-table"
            columns={[
              { key: "no", header: "Payment", render: (p) => <Link className="tap-link" to={`/finance/payments/${p.id}`}>{p.number}</Link> },
              { key: "invoices", header: "Invoices", render: (p) => [...new Set(p.allocations.map((a) => a.invoice_number))].join(", ") },
              { key: "when", header: "Received", priority: "secondary", render: (p) => formatDateTime(p.received_at) },
              { key: "method", header: "Method", priority: "secondary", render: (p) => PAYMENT_METHOD_LABELS[p.method] ?? p.method },
              { key: "ref", header: "Reference", priority: "secondary", render: (p) => p.reference || "—" },
              { key: "status", header: "Status", render: (p) => <PaymentStatusBadge status={p.status} /> },
              { key: "receipt", header: "Receipt", render: (p) => p.receipt_number ?? "—" },
              { key: "amount", header: "Amount", align: "end", render: (p) => <MoneyAmount value={p.amount} /> },
            ]}
          />
          <LoadMore shown={payments.rows.length} total={payments.count} hasMore={!!payments.hasNextPage}
                    loading={payments.isFetchingNextPage} onMore={() => payments.fetchNextPage()} />
        </>
      )}
    </>
  );
}

/** Local date-time for the "received" input (the backend reads it in the academy time zone). */
function nowLocal() {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * Manual payment through the existing service: one family, one or more of its
 * issued invoices, fully allocated (no overpayment or credit). The backend
 * spreads each amount over the invoice's lines, updates the invoice, issues the
 * receipt and confirms a paid competition entry; this page only collects input.
 */
export function RecordPaymentPage() {
  const { endpoints } = useServices();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { params } = useUrlFilters();
  const methods = useMethods();
  const [idempotencyKey] = useState(newIdempotencyKey);
  const [lookup, setLookup] = useState("");
  const [lookupQuery, setLookupQuery] = useState("");
  const [familyId, setFamilyId] = useState<number | null>(null);
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  const [form, setForm] = useState({ method: "", received_at: nowLocal(), payer_name: "", reference: "", notes: "" });
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const preset = params.get("invoice");
  const presetInvoice = useQuery({ queryKey: financeKeys.invoice(preset ?? ""), enabled: !!preset,
                                   queryFn: ({ signal }) => endpoints.staffInvoice(preset!, signal) });
  const family = familyId ?? presetInvoice.data?.family ?? null;
  const found = useQuery({ queryKey: financeKeys.invoices({ search: lookupQuery, outstanding: 1 }), enabled: !!lookupQuery && family === null,
                           queryFn: ({ signal }) => endpoints.staffInvoices({ search: lookupQuery, outstanding: 1 }, signal) });
  const open = useQuery({ queryKey: financeKeys.invoices({ family, outstanding: 1 }), enabled: family !== null,
                          queryFn: ({ signal }) => endpoints.staffInvoices({ family: family!, outstanding: 1 }, signal) });

  // Pre-fill the chosen invoice with its balance (the backend refuses more than the balance).
  const openInvoices = useMemo(() => open.data?.results ?? [], [open.data]);
  const initial = presetInvoice.data;
  if (initial && amounts[initial.id] === undefined && openInvoices.some((i) => i.id === initial.id)) {
    setAmounts((a) => ({ ...a, [initial.id]: initial.balance_due }));
  }
  const chosen = Object.entries(amounts).filter(([, amount]) => amount.trim() !== "");
  const total = sumMoney(chosen.map(([, amount]) => amount.trim()));

  const save = useMutation({
    mutationFn: () => endpoints.recordPayment({
      amount: total, method: form.method, received_at: form.received_at || undefined, payer_name: form.payer_name.trim(),
      reference: form.reference.trim(), notes: form.notes.trim(),
      allocations: chosen.map(([invoice, amount]) => ({ invoice: Number(invoice), amount: amount.trim() })),
    }, idempotencyKey),
    onSuccess: async (payment) => {
      await queryClient.invalidateQueries({ queryKey: financeKeys.all });
      navigate(`/finance/payments/${payment.id}`, { state: { recorded: true } });
    },
    onError: (e) => {
      setConfirming(false);
      if (isApiError(e) && Object.keys(e.fieldErrors).length) setErrors(e.fieldErrors);
      setError(errorText(e));
      window.setTimeout(() => document.getElementById("payment-error")?.focus(), 0);
    },
  });

  function review(e: FormEvent) {
    e.preventDefault();
    const problems: Record<string, string[]> = {};
    if (!form.method) problems.method = ["Choose how the money was received."];
    if (!chosen.length || !isPositiveMoney(total)) problems.allocations = ["Enter the amount received for at least one invoice."];
    if (chosen.some(([, amount]) => !isPositiveMoney(amount.trim()))) problems.allocations = ["Amounts must be positive, e.g. 120.00."];
    setErrors(problems);
    setError(null);
    if (Object.keys(problems).length) {
      window.setTimeout(() => document.querySelector<HTMLElement>("[aria-invalid='true']")?.focus(), 0);
      return;
    }
    setConfirming(true);
  }

  const familyName = openInvoices[0]?.family_name ?? initial?.family_name ?? "";
  return (
    <>
      <PageHeader title="Record payment" crumbs={CRUMBS}
                  description="Record money the academy has received (cash, transfer, cheque …). A payment proof is not a payment: record the payment here after checking the money has arrived." />
      <div id="payment-error" tabIndex={-1}>{error ? <Alert tone="danger" role="alert" title={error} /> : null}</div>
      {family === null ? (
        <Section title="1. Find the invoice">
          <form className="filter-bar" role="search" aria-label="Find an open invoice"
                onSubmit={(e) => { e.preventDefault(); setLookupQuery(lookup.trim()); }}>
            <TextField label="Invoice, family or student" type="search" value={lookup} onChange={(e) => setLookup(e.target.value)} />
            <div className="filter-actions"><Button type="submit">Find</Button></div>
          </form>
          {found.isFetching ? <LoadingState /> : found.data ? (
            found.data.results.length ? (
              <ul className="plain-list entry-list">
                {found.data.results.map((i) => (
                  <li key={i.id}>
                    <Button variant="secondary" onClick={() => { setFamilyId(i.family); setAmounts({ [i.id]: i.balance_due }); }}>
                      {i.number} · {i.family_name} · <MoneyAmount value={i.balance_due} /> due</Button>
                  </li>
                ))}
              </ul>
            ) : <EmptyState message="No open invoice matches." />
          ) : null}
        </Section>
      ) : (
        <form onSubmit={review} noValidate aria-label="Payment" className="register-form">
          <Section title={`1. Invoices of ${familyName || "the family"}`}>
            {open.isPending ? <LoadingState /> : (
              <fieldset className="choices" aria-invalid={errors.allocations ? true : undefined}
                        aria-describedby={errors.allocations ? "alloc-error" : undefined}>
                <legend>Amount received for each open invoice</legend>
                {openInvoices.map((i: StaffInvoice) => (
                  <div key={i.id} className="alloc-row">
                    <TextField label={`${i.number} (${[...new Set(i.items.map((l) => l.student_name))].join(", ")}) — ${i.balance_due} due`}
                               inputMode="decimal" value={amounts[i.id] ?? ""} placeholder="0.00"
                               onChange={(e) => setAmounts((a) => ({ ...a, [i.id]: e.target.value }))} />
                  </div>
                ))}
                {!openInvoices.length ? <EmptyState message="This family has no invoice open for payment." /> : null}
                {errors.allocations ? <p id="alloc-error" className="field-error">{errors.allocations.join(" ")}</p> : null}
              </fieldset>
            )}
            <p className="fee-line">Total received: <strong><MoneyAmount value={total || "0"} /></strong></p>
            <p className="muted">Paying less than the balance leaves the invoice partially paid. Overpayment is not accepted.</p>
          </Section>
          <Section title="2. Payment details">
            <SelectField label="Payment method" required value={form.method} errors={errors.method}
                         onChange={(e) => setForm((f) => ({ ...f, method: e.target.value }))}>
              <option value="">Choose…</option>
              {(methods.data ?? []).map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </SelectField>
            <TextField label="Received" type="datetime-local" value={form.received_at} errors={errors.received_at}
                       onChange={(e) => setForm((f) => ({ ...f, received_at: e.target.value }))} />
            <TextField label="Paid by (optional)" value={form.payer_name} onChange={(e) => setForm((f) => ({ ...f, payer_name: e.target.value }))} />
            <TextField label="Reference (optional)" hint="Bank reference, cheque number …" value={form.reference}
                       onChange={(e) => setForm((f) => ({ ...f, reference: e.target.value }))} />
            <TextAreaField label="Staff note (optional)" rows={2} value={form.notes}
                           onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))} />
          </Section>
          <div className="attendance-submit">
            <Button type="submit">Review payment</Button>{" "}
            <Button variant="ghost" onClick={() => { setFamilyId(null); setAmounts({}); navigate("/finance/payments/new", { replace: true }); }}>
              Choose another family</Button>
          </div>
        </form>
      )}
      <Dialog open={confirming} title="Confirm payment" onClose={() => setConfirming(false)}
              footer={<>
                <Button variant="secondary" onClick={() => setConfirming(false)} disabled={save.isPending}>Back</Button>
                <Button onClick={() => save.mutate()} busy={save.isPending}>Record payment</Button>
              </>}>
        <p>Record <strong><MoneyAmount value={total} /></strong> received from {familyName} by
          {" "}{methods.data?.find((m) => m.value === form.method)?.label ?? form.method}?</p>
        <ul className="plain-list">
          {chosen.map(([id, amount]) => {
            const inv = openInvoices.find((i) => i.id === Number(id));
            return <li key={id}>{inv?.number}: <MoneyAmount value={amount} /></li>;
          })}
        </ul>
        <p className="muted">An official receipt is issued. Recorded payments cannot be edited; only voided with a reason.</p>
      </Dialog>
    </>
  );
}

type PaymentDialog = "void" | "refund";

export function FinancePaymentDetailPage() {
  const { paymentId = "" } = useParams();
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const methods = useMethods();
  const payment = useQuery({ queryKey: financeKeys.payment(paymentId), queryFn: ({ signal }) => endpoints.staffPayment(paymentId, signal) });
  const refunds = useQuery({ queryKey: [...financeKeys.payment(paymentId), "refunds"], enabled: payment.isSuccess,
                             queryFn: ({ signal }) => endpoints.refunds({ payment: Number(paymentId) }, signal) });
  const dialog = useDialog<PaymentDialog>();
  const [form, setForm] = useState<Record<string, string>>({});
  const [done, setDone] = useState<string | null>(null);
  const action = useMutation({
    mutationFn: async ({ name, reason }: { name: PaymentDialog; reason: string }) => {
      if (name === "void") return endpoints.voidPayment(Number(paymentId), reason);
      return endpoints.refundPayment(Number(paymentId), { allocation: Number(form.allocation), amount: (form.amount ?? "").trim(),
        reason, method: form.method || "BANK_TRANSFER", reference: (form.reference ?? "").trim() });
    },
    onSuccess: async (_, { name }) => {
      dialog.close();
      setDone(name === "void" ? "Payment voided; its receipt is marked void." : "Refund recorded.");
      await queryClient.invalidateQueries({ queryKey: financeKeys.all });
    },
    onError: (e) => dialog.setError(errorText(e)),
  });
  const field = (name: string) => ({ value: form[name] ?? "", onChange: (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [name]: e.target.value })) });

  if (payment.isPending) return (<><PageHeader title="Payment" crumbs={CRUMBS} /><LoadingState /></>);
  if (payment.isError) {
    return (<><PageHeader title="Payment" crumbs={CRUMBS} />
      {isApiError(payment.error) && payment.error.kind === "not_found" ? <NotFoundState message="Payment not found." />
        : <ErrorState error={payment.error} onRetry={() => payment.refetch()} />}</>);
  }
  const p = payment.data;
  const valid = p.status === "VALID";
  return (
    <>
      <PageHeader title={`Payment ${p.number}`} crumbs={CRUMBS} description={<PaymentStatusBadge status={p.status} />}
                  actions={<>
                    {valid && can(me, "finance.refunds.record") ? <Button variant="secondary" onClick={() => { setDone(null); setForm({ method: "BANK_TRANSFER" }); dialog.show("refund"); }}>Exceptional refund</Button> : null}
                    {valid && can(me, "finance.payments.void") ? <Button variant="danger" onClick={() => { setDone(null); dialog.show("void"); }}>Void payment</Button> : null}
                  </>} />
      <div role="status" className="status-slot">{done ? <Alert tone="success" title={done} /> : null}</div>
      <Section title="Payment">
        <DefinitionList items={[
          ["Amount", <MoneyAmount key="a" value={p.amount} />], ["Method", PAYMENT_METHOD_LABELS[p.method] ?? p.method],
          ["Received", formatDateTime(p.received_at)], ["Reference", p.reference], ["Paid by", p.payer_name],
          ["Receipt", p.receipt_id ? <Link key="r" className="tap-link" to={`/finance/receipts/${p.receipt_id}`}>{p.receipt_number}</Link> : ""],
        ]} />
        {p.notes ? <p className="prewrap muted">Staff note: {p.notes}</p> : null}
      </Section>
      <Section title="Applied to">
        <DataTable
          caption="Where this payment was applied"
          rows={p.allocations}
          rowKey={(a) => a.id}
          columns={[
            { key: "invoice", header: "Invoice", render: (a) => <Link className="tap-link" to={`/finance/invoices/${a.invoice}`}>{a.invoice_number}</Link> },
            { key: "student", header: "Student", render: (a) => a.student_name },
            { key: "desc", header: "Line", priority: "secondary", render: (a) => a.description },
            { key: "amount", header: "Amount", align: "end", render: (a) => <MoneyAmount value={a.amount} /> },
          ]}
        />
      </Section>
      {refunds.data?.results.length ? (
        <Section title="Refunds">
          <DataTable
            caption="Refunds against this payment"
            rows={refunds.data.results}
            rowKey={(r) => r.id}
            columns={[
              { key: "no", header: "Refund", render: (r) => r.number },
              { key: "when", header: "Date", render: (r) => formatDateTime(r.refunded_at) },
              { key: "reason", header: "Reason", render: (r) => r.reason },
              { key: "amount", header: "Amount", align: "end", render: (r) => <MoneyAmount value={r.amount} /> },
            ]}
          />
        </Section>
      ) : null}
      <ConfirmDialog open={dialog.open === "void"} title="Void payment" confirmLabel="Void payment" requireReason
                     message={<p>Void {p.number}? Its receipt is marked void and the invoices re-open by the amount applied.
                       Nothing is deleted. A payment with refunds cannot be voided.</p>}
                     busy={action.isPending} error={dialog.error} onCancel={dialog.close}
                     onConfirm={(reason) => action.mutate({ name: "void", reason })} />
      <ActionDialog open={dialog.open === "refund"} title="Exceptional refund" submitLabel="Record refund" tone="danger"
                    busy={action.isPending} error={dialog.error} onClose={dialog.close}
                    onSubmit={() => action.mutate({ name: "refund", reason: (form.reason ?? "").trim() })}>
        <p className="muted">Fees (including competition fees) are normally not refundable. Record a refund only as an authorized
          exception: the payment and its receipt stay unchanged and the refund is its own record.</p>
        <SelectField label="Line" required {...field("allocation")}>
          <option value="">Choose the line refunded</option>
          {p.allocations.map((a) => <option key={a.id} value={a.id}>{a.invoice_number} · {a.student_name} · {a.amount}</option>)}
        </SelectField>
        <TextField label="Amount" required inputMode="decimal" {...field("amount")} />
        <SelectField label="Refund method" {...field("method")}>
          {(methods.data ?? []).map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </SelectField>
        <TextField label="Reference (optional)" {...field("reference")} />
        <TextAreaField label="Reason" required rows={2} hint="Required. Stored in the audit log." {...field("reason")} />
      </ActionDialog>
    </>
  );
}

