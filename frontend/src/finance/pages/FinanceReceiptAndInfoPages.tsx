import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { Receipt } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { formatDateTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DefinitionList, LoadMore, Section } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { errorText } from "../../staff/components";
import { Button } from "../../ui/Button";
import { DataTable } from "../../ui/DataTable";
import { TextAreaField, TextField } from "../../ui/Field";
import { Alert } from "../../ui/primitives";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { ReceiptDocument } from "../ReceiptDocument";
import { FINANCE_CRUMBS, MoneyAmount, financeKeys } from "../components";
import { useUrlFilters } from "../filters";

const RECEIPT_CRUMBS = [...FINANCE_CRUMBS, { label: "Receipts", to: "/finance/receipts" }];

export function FinanceReceiptsPage() {
  const { endpoints } = useServices();
  const { params, set } = useUrlFilters();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const query = { search: params.get("search") || undefined, start: params.get("start") || undefined, end: params.get("end") || undefined };
  const receipts = usePagedList<Receipt>(financeKeys.receipts(query), (page, signal) => endpoints.staffReceipts({ ...query, page }, signal));
  return (
    <>
      <PageHeader title="Receipts" crumbs={FINANCE_CRUMBS}
                  description="Official receipts, issued automatically when a payment is recorded. They are never edited; a voided payment voids its receipt." />
      <form className="filter-bar" role="search" aria-label="Find receipts" onSubmit={(e) => { e.preventDefault(); set("search", search.trim()); }}>
        <TextField label="Search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
                   placeholder="Receipt or payment no., reference, invoice, payer" />
        <TextField label="Issued from" type="date" value={params.get("start") ?? ""} onChange={(e) => set("start", e.target.value)} />
        <TextField label="Issued to" type="date" value={params.get("end") ?? ""} onChange={(e) => set("end", e.target.value)} />
        <div className="filter-actions"><Button type="submit">Search</Button></div>
      </form>
      {receipts.isPending ? <LoadingState /> : receipts.isError ? <ErrorState error={receipts.error} onRetry={() => receipts.refetch()} /> : (
        <>
          <DataTable<Receipt>
            caption="Receipts"
            rows={receipts.rows}
            rowKey={(r) => r.id}
            emptyMessage="No receipts match."
            className="staff-table"
            columns={[
              { key: "no", header: "Receipt", render: (r) => <Link className="tap-link" to={`/finance/receipts/${r.id}`}>{r.number}</Link> },
              { key: "when", header: "Issued", render: (r) => formatDateTime(r.issued_at) },
              { key: "students", header: "Students", priority: "secondary", render: (r) => r.content.students.join(", ") },
              { key: "invoice", header: "Invoice", priority: "secondary", render: (r) => [...new Set(r.content.lines.map((l) => l.invoice_number))].join(", ") },
              { key: "status", header: "Status", render: (r) => (r.is_void ? "Voided" : "Valid") },
              { key: "total", header: "Total", align: "end", render: (r) => <MoneyAmount value={r.total} /> },
            ]}
          />
          <LoadMore shown={receipts.rows.length} total={receipts.count} hasMore={!!receipts.hasNextPage}
                    loading={receipts.isFetchingNextPage} onMore={() => receipts.fetchNextPage()} />
        </>
      )}
    </>
  );
}

/** The receipt exactly as issued (shared with the Parent Portal) and printed with the browser. */
export function FinanceReceiptDetailPage() {
  const { receiptId = "" } = useParams();
  const { endpoints } = useServices();
  const receipt = useQuery({ queryKey: financeKeys.receipt(receiptId), queryFn: ({ signal }) => endpoints.receipt(receiptId, signal) });
  if (receipt.isPending) return (<><PageHeader title="Receipt" crumbs={RECEIPT_CRUMBS} /><LoadingState /></>);
  if (receipt.isError) {
    return (<><PageHeader title="Receipt" crumbs={RECEIPT_CRUMBS} />
      {isApiError(receipt.error) && receipt.error.kind === "not_found" ? <NotFoundState message="Receipt not found." />
        : <ErrorState error={receipt.error} onRetry={() => receipt.refetch()} />}</>);
  }
  const r = receipt.data;
  return (
    <>
      <PageHeader title={`Receipt ${r.number}`} crumbs={RECEIPT_CRUMBS}
                  actions={<>
                    <Link className="btn btn-secondary no-print" to={`/finance/payments/${r.payment}`}>Payment</Link>
                    <Button variant="secondary" className="no-print" onClick={() => window.print()}>Print</Button>
                  </>} />
      <ReceiptDocument receipt={r} />
    </>
  );
}

const FIELDS = [
  ["bank_name", "Bank name"], ["account_name", "Account name"], ["account_number", "Account number"],
] as const;

/**
 * The academy's payment details that parents see (not coach or staff bank
 * details). Everyone with finance access can read them; only
 * `finance.payment_info.manage` changes them (PNG / JPG QR, validated and
 * stored privately by the backend; the change is audited).
 */
export function PaymentInfoPage() {
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const info = useQuery({ queryKey: financeKeys.paymentInfo, queryFn: ({ signal }) => endpoints.paymentInfo(signal) });
  const manage = can(me, "finance.payment_info.manage");
  const [values, setValues] = useState<Record<string, string>>({});
  const [qr, setQr] = useState<File | null>(null);
  const [removeQr, setRemoveQr] = useState(false);
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [saved, setSaved] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const statusRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (info.data) {
      setValues({ bank_name: info.data.bank_name, account_name: info.data.account_name, account_number: info.data.account_number,
                  instructions: info.data.instructions, reference_instructions: info.data.reference_instructions });
    }
  }, [info.data]);
  const save = useMutation({
    mutationFn: () => {
      const form = new FormData();
      for (const [key, value] of Object.entries(values)) form.append(key, value);
      if (qr) form.append("qr_code", qr);
      if (removeQr) form.append("remove_qr_code", "true");
      return endpoints.updatePaymentInfo(form);
    },
    onSuccess: async (data) => {
      queryClient.setQueryData(financeKeys.paymentInfo, data);
      setQr(null);
      setRemoveQr(false);
      if (fileRef.current) fileRef.current.value = "";
      setErrors({});
      setSaved(true);
      window.setTimeout(() => statusRef.current?.focus(), 0);
    },
    onError: (e) => {
      setSaved(false);
      setErrors(isApiError(e) && Object.keys(e.fieldErrors).length ? e.fieldErrors : { detail: [errorText(e)] });
    },
  });
  function onSubmit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }
  const header = <PageHeader title="Payment information" crumbs={FINANCE_CRUMBS}
                             description="How families pay the academy: shown to parents with each invoice. This is the academy's account, not a coach's or staff member's." />;
  if (info.isPending) return (<>{header}<LoadingState /></>);
  if (info.isError) return (<>{header}<ErrorState error={info.error} onRetry={() => info.refetch()} /></>);
  const d = info.data;
  if (!manage) {
    return (
      <>
        {header}
        <p className="muted">View only: changing it needs the payment-information permission.</p>
        <DefinitionList items={[
          ["Bank name", d.bank_name], ["Account name", d.account_name], ["Account number", d.account_number],
          ["Instructions", d.instructions], ["Reference", d.reference_instructions],
        ]} />
        {d.qr_code ? <figure className="payment-qr"><img src={d.qr_code} alt="Academy payment QR code" /></figure> : null}
      </>
    );
  }
  return (
    <>
      {header}
      <div ref={statusRef} tabIndex={-1} role="status" className="status-slot">{saved ? <Alert tone="success" title="Payment information saved." /> : null}</div>
      {errors.detail ? <Alert tone="danger" role="alert" title={errors.detail.join(" ")} /> : null}
      <form onSubmit={onSubmit} noValidate aria-label="Payment information" className="proof-form">
        {FIELDS.map(([name, label]) => (
          <TextField key={name} label={label} value={values[name] ?? ""} errors={errors[name]}
                     onChange={(e) => { setSaved(false); setValues((v) => ({ ...v, [name]: e.target.value })); }} />
        ))}
        <TextAreaField label="Payment instructions" rows={3} value={values.instructions ?? ""} errors={errors.instructions}
                       onChange={(e) => { setSaved(false); setValues((v) => ({ ...v, instructions: e.target.value })); }} />
        <TextField label="Reference instructions" value={values.reference_instructions ?? ""} errors={errors.reference_instructions}
                   hint="e.g. Use the invoice number as the payment reference."
                   onChange={(e) => { setSaved(false); setValues((v) => ({ ...v, reference_instructions: e.target.value })); }} />
        <Section title="QR code">
          {d.qr_code && !removeQr ? <figure className="payment-qr"><img src={d.qr_code} alt="Academy payment QR code" /></figure> : null}
          <div className={`field${errors.qr_code ? " field-invalid" : ""}`}>
            <label htmlFor="qr-file">New QR image (PNG or JPG)</label>
            <input id="qr-file" ref={fileRef} type="file" accept="image/png,image/jpeg"
                   aria-describedby={errors.qr_code ? "qr-error" : undefined} aria-invalid={errors.qr_code ? true : undefined}
                   onChange={(e) => { setSaved(false); setQr(e.target.files?.[0] ?? null); }} />
            {errors.qr_code ? <p id="qr-error" className="field-error">{errors.qr_code.join(" ")}</p> : null}
          </div>
          {d.qr_code ? (
            <label className="check-field"><input type="checkbox" checked={removeQr} onChange={(e) => setRemoveQr(e.target.checked)} /> Remove the current QR code</label>
          ) : null}
        </Section>
        <Button type="submit" busy={save.isPending}>Save payment information</Button>
      </form>
      {d.updated_at ? <p className="muted">Last updated {formatDateTime(d.updated_at)}.</p> : null}
    </>
  );
}
