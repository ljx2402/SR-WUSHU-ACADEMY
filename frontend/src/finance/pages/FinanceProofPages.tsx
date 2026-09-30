import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";

import { download } from "../../api/download";
import { isApiError } from "../../api/errors";
import type { StaffPaymentProof } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { InvoiceStatusBadge } from "../../domain/finance";
import { formatDate, formatDateTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DefinitionList, LoadMore, Section } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { errorText } from "../../staff/components";
import { Button } from "../../ui/Button";
import { ConfirmDialog, Dialog } from "../../ui/Dialog";
import { DataTable } from "../../ui/DataTable";
import { SelectField, TextAreaField, TextField } from "../../ui/Field";
import { Alert } from "../../ui/primitives";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { FINANCE_CRUMBS, MoneyAmount, ProofIsNotPayment, ProofReviewStatus, awaitsPayment, financeKeys } from "../components";
import { useUrlFilters } from "../filters";

const CRUMBS = [...FINANCE_CRUMBS, { label: "Payment proofs", to: "/finance/payment-proofs" }];

export function FinanceProofsPage() {
  const { endpoints } = useServices();
  const { params, set } = useUrlFilters();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const status = params.get("status") ?? "PENDING_REVIEW";
  const query = { status: status === "ALL" ? undefined : status, search: params.get("search") || undefined,
                  start: params.get("start") || undefined, end: params.get("end") || undefined };
  const proofs = usePagedList<StaffPaymentProof>(financeKeys.proofs(query), (page, signal) => endpoints.staffProofs({ ...query, page }, signal));
  return (
    <>
      <PageHeader title="Payment proofs" crumbs={FINANCE_CRUMBS}
                  description="Parents' evidence of manual payments. Review each one; record the actual payment separately." />
      <form className="filter-bar" role="search" aria-label="Find payment proofs" onSubmit={(e) => { e.preventDefault(); set("search", search.trim()); }}>
        <SelectField label="Status" value={status} onChange={(e) => set("status", e.target.value)}>
          <option value="PENDING_REVIEW">Pending review</option>
          <option value="ACCEPTED">Accepted</option>
          <option value="REJECTED">Rejected</option>
          <option value="ALL">All</option>
        </SelectField>
        <TextField label="Search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
                   placeholder="Invoice no., family, student, bank reference" />
        <TextField label="Uploaded from" type="date" value={params.get("start") ?? ""} onChange={(e) => set("start", e.target.value)} />
        <TextField label="Uploaded to" type="date" value={params.get("end") ?? ""} onChange={(e) => set("end", e.target.value)} />
        <div className="filter-actions"><Button type="submit">Search</Button></div>
      </form>
      {proofs.isPending ? <LoadingState /> : proofs.isError ? <ErrorState error={proofs.error} onRetry={() => proofs.refetch()} /> : (
        <>
          <DataTable<StaffPaymentProof>
            caption="Payment proofs"
            rows={proofs.rows}
            rowKey={(p) => p.id}
            emptyMessage={status === "PENDING_REVIEW" ? "No proofs waiting for review." : "No payment proofs match."}
            className="staff-table"
            columns={[
              { key: "id", header: "Proof", render: (p) => <Link className="tap-link" to={`/finance/payment-proofs/${p.id}`}>#{p.id} · {p.invoice_number}</Link> },
              { key: "family", header: "Family", render: (p) => p.family_name },
              { key: "uploaded", header: "Uploaded", priority: "secondary", render: (p) => formatDateTime(p.uploaded_at) },
              { key: "paid", header: "Paid on", priority: "secondary", render: (p) => (p.payment_date ? formatDate(p.payment_date) : "—") },
              { key: "ref", header: "Bank ref.", priority: "secondary", render: (p) => p.reference || "—" },
              { key: "claimed", header: "Claimed", align: "end", render: (p) => (p.amount_claimed ? <MoneyAmount value={p.amount_claimed} /> : "—") },
              { key: "status", header: "Status", render: (p) => <ProofReviewStatus proof={p} /> },
              { key: "reviewer", header: "Reviewed", priority: "secondary", render: (p) => (p.reviewed_at
                ? `${p.reviewed_by_name ?? ""} ${formatDateTime(p.reviewed_at)}`.trim() : "—") },
            ]}
          />
          <LoadMore shown={proofs.rows.length} total={proofs.count} hasMore={!!proofs.hasNextPage}
                    loading={proofs.isFetchingNextPage} onMore={() => proofs.fetchNextPage()} />
        </>
      )}
    </>
  );
}

/**
 * Reviewing a proof. Accepting records no money and changes no invoice or
 * competition entry; rejecting needs a reason the parent will see. The file is
 * downloaded through the permission-checked API (never a storage URL).
 */
export function FinanceProofDetailPage() {
  const { proofId = "" } = useParams();
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const proof = useQuery({ queryKey: financeKeys.proof(proofId), queryFn: ({ signal }) => endpoints.staffProof(proofId, signal) });
  const payments = useQuery({
    queryKey: financeKeys.payments({ invoice: proof.data?.invoice, status: "VALID" }),
    queryFn: ({ signal }) => endpoints.staffPayments({ invoice: proof.data!.invoice, status: "VALID" }, signal),
    enabled: proof.data?.status === "PENDING_REVIEW",
  });
  const [open, setOpen] = useState<"accept" | "reject" | null>(null);
  const [link, setLink] = useState("");
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const statusRef = useRef<HTMLDivElement>(null);

  const review = useMutation({
    mutationFn: ({ name, text }: { name: "accept" | "reject"; text: string }) => (name === "accept"
      ? endpoints.acceptProof(Number(proofId), { note: text, payment: link ? Number(link) : null })
      : endpoints.rejectProof(Number(proofId), text)),
    onSuccess: async (_, { name }) => {
      setOpen(null);
      setDone(name === "accept" ? "Proof accepted. No payment was recorded by this." : "Proof rejected. The family will see the reason.");
      await queryClient.invalidateQueries({ queryKey: financeKeys.all });
      window.setTimeout(() => statusRef.current?.focus(), 0);
    },
    onError: (e) => {
      if (isApiError(e) && e.fieldErrors.reason) setReasonError(e.fieldErrors.reason);
      setError(errorText(e));
    },
  });

  if (proof.isPending) return (<><PageHeader title="Payment proof" crumbs={CRUMBS} /><LoadingState /></>);
  if (proof.isError) {
    return (<><PageHeader title="Payment proof" crumbs={CRUMBS} />
      {isApiError(proof.error) && proof.error.kind === "not_found" ? <NotFoundState message="Payment proof not found." />
        : <ErrorState error={proof.error} onRetry={() => proof.refetch()} />}</>);
  }
  const p = proof.data;
  const pending = p.status === "PENDING_REVIEW";
  const reviewer = can(me, "finance.proofs.review");
  const invoiceOpen = p.invoice_status === "ISSUED" || p.invoice_status === "PARTIALLY_PAID";

  async function onDownload() {
    setDownloadError(null);
    try {
      await download(() => endpoints.paymentProofFile(p.id), p.original_name);
    } catch (e) {
      setDownloadError(errorText(e));
    }
  }
  function onReject(e: FormEvent) {
    e.preventDefault();
    if (!reason.trim()) {
      setReasonError(["Give the reason; the family will see it."]);
      window.setTimeout(() => document.getElementById("reject-reason")?.focus(), 0);
      return;
    }
    review.mutate({ name: "reject", text: reason.trim() });
  }

  return (
    <>
      <PageHeader title={`Payment proof #${p.id}`} crumbs={CRUMBS} description={<ProofReviewStatus proof={p} />}
                  actions={reviewer && pending ? (
                    <>
                      <Button onClick={() => { setError(null); setDone(null); setOpen("accept"); }}>Accept</Button>
                      <Button variant="danger" onClick={() => { setError(null); setDone(null); setReasonError([]); setOpen("reject"); }}>Reject</Button>
                    </>
                  ) : null} />
      <div ref={statusRef} tabIndex={-1} role="status" className="status-slot">{done ? <Alert tone="success" title={done} /> : null}</div>
      <ProofIsNotPayment>
        {invoiceOpen && can(me, "finance.payments.record") ? (
          <>: <Link className="tap-link" to={`/finance/payments/new?invoice=${p.invoice}`}>record the payment for {p.invoice_number}</Link>.</>
        ) : null}
      </ProofIsNotPayment>
      {p.status === "REJECTED" ? <Alert tone="danger" title="Rejected"><p>Reason: {p.review_note}</p></Alert> : null}
      {awaitsPayment(p) ? <Alert tone="warning" title="Accepted — payment still requires recording" /> : null}

      <Section title="Proof">
        <DefinitionList items={[
          ["Invoice", <Link key="i" className="tap-link" to={`/finance/invoices/${p.invoice}`}>{p.invoice_number}</Link>],
          ["Invoice status", <InvoiceStatusBadge key="s" status={p.invoice_status} />],
          ["Balance due", <MoneyAmount key="b" value={p.invoice_balance_due} />],
          ["Family", p.family_name],
          ["Amount claimed", p.amount_claimed ? <MoneyAmount key="c" value={p.amount_claimed} /> : ""],
          ["Payment date", p.payment_date ? formatDate(p.payment_date) : ""],
          ["Bank reference", p.reference],
          ["Uploaded", `${formatDateTime(p.uploaded_at)}${p.uploaded_by_name ? ` by ${p.uploaded_by_name}` : ""}`],
          ["Reviewed", p.reviewed_at ? `${formatDateTime(p.reviewed_at)}${p.reviewed_by_name ? ` by ${p.reviewed_by_name}` : ""}` : "Not yet"],
          ["Review note", p.review_note],
          ["Linked payment", p.payment_number ? <Link key="p" className="tap-link" to={`/finance/payments/${p.payment}`}>{p.payment_number}</Link> : ""],
        ]} />
        {p.note ? <p className="prewrap"><strong>Note from the family:</strong> {p.note}</p> : null}
      </Section>
      <Section title="File">
        <p>{p.original_name} <span className="muted">({p.content_type}, {Math.max(1, Math.round(p.size / 1024))} KB)</span></p>
        <Button variant="secondary" onClick={onDownload}>Download proof</Button>
        {downloadError ? <Alert tone="danger" role="alert" title={downloadError} /> : null}
        <p className="muted">Downloaded through the academy system after a permission check; the file is never shown from a public link.</p>
      </Section>

      <ConfirmDialog open={open === "accept"} title="Accept payment proof" confirmLabel="Accept proof" tone="primary" askReason
                     reasonLabel="Note (optional)"
                     message={<>
                       <p>Accepting says the proof looks right. <strong>It does not record a payment</strong>, issue a receipt,
                         change the invoice or confirm a competition entry.</p>
                       {payments.data?.results.length ? (
                         <SelectField label="Link a payment already recorded (optional)" value={link} onChange={(e) => setLink(e.target.value)}>
                           <option value="">No payment linked</option>
                           {payments.data.results.map((pay) => <option key={pay.id} value={pay.id}>{pay.number} · {pay.amount}</option>)}
                         </SelectField>
                       ) : null}
                     </>}
                     busy={review.isPending} error={error} onCancel={() => setOpen(null)}
                     onConfirm={(text) => review.mutate({ name: "accept", text })} />
      <Dialog open={open === "reject"} title="Reject payment proof" onClose={() => setOpen(null)}
              footer={<>
                <Button variant="secondary" onClick={() => setOpen(null)} disabled={review.isPending}>Cancel</Button>
                <Button variant="danger" type="submit" form="reject-form" busy={review.isPending}>Reject proof</Button>
              </>}>
        <form id="reject-form" onSubmit={onReject} noValidate>
          <p>The family sees this reason in the Parent Portal. Nothing financial changes.</p>
          <TextAreaField id="reject-reason" label="Reason" required rows={3} value={reason} errors={reasonError}
                         hint="e.g. the amount does not match, the image is unreadable, wrong account"
                         onChange={(e) => { setReason(e.target.value); setReasonError([]); }} />
        </form>
        {error && !reasonError.length ? <Alert tone="danger" role="alert" title={error} /> : null}
      </Dialog>
    </>
  );
}
