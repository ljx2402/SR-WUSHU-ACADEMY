import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState, type FormEvent } from "react";

import { isApiError } from "../api/errors";
import type { Invoice, PaymentProof } from "../api/types";
import { useServices } from "../app/services";
import { Money, ProofStatusBadge } from "../domain/finance";
import { academyToday, formatDate, formatDateTime } from "../domain/format";
import { Button } from "../ui/Button";
import { DataTable } from "../ui/DataTable";
import { TextAreaField, TextField } from "../ui/Field";
import { Alert } from "../ui/primitives";
import { ErrorState, LoadingState } from "../ui/states";
import { DefinitionList, Section } from "./components";

/*
 * Paying an invoice (no online payment gateway):
 * the parent pays the academy manually (bank transfer, DuitNow QR, cash...),
 * then uploads PAYMENT PROOF. A proof is only evidence: it is "Pending review"
 * until academy staff check it. The invoice changes, and an OFFICIAL RECEIPT
 * is issued, only when staff record the actual payment. Nothing here marks
 * anything as paid.
 */

export const PROOF_MAX_BYTES = 5 * 1024 * 1024;
const PROOF_TYPES = [".pdf", ".jpg", ".jpeg", ".png"];
const MONEY_INPUT = /^\d{1,8}(\.\d{1,2})?$/;

export const paymentKeys = {
  info: ["parent", "payment-info"] as const,
  proofs: (invoice: number) => ["parent", "payment-proofs", invoice] as const,
};

export function PaymentInformation({ invoice }: { invoice: Invoice }) {
  const { endpoints } = useServices();
  const info = useQuery({
    queryKey: paymentKeys.info,
    queryFn: ({ signal }) => endpoints.paymentInfo(signal),
    staleTime: 10 * 60_000,
  });
  const [copied, setCopied] = useState(false);

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 3000);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Section title="How to pay">
      <p className="amount-due">Amount due <Money value={invoice.balance_due} /></p>
      {info.isPending ? <LoadingState /> : info.isError ? <ErrorState error={info.error} onRetry={() => info.refetch()} /> : (
        !info.data.configured ? (
          <Alert tone="warning">The academy has not published its payment details yet. Please ask the academy office
            how to pay.</Alert>
        ) : (
          <div className="payment-info">
            <div>
              <DefinitionList items={[
                ["Bank", info.data.bank_name],
                ["Account name", info.data.account_name],
                ["Account number", info.data.account_number ? (
                  <span className="copy-row">
                    <span className="account-number">{info.data.account_number}</span>
                    <Button variant="secondary" onClick={() => copy(info.data.account_number)}
                            aria-label="Copy account number">Copy</Button>
                  </span>
                ) : ""],
                ["Payment reference", <strong key="ref">{invoice.number}</strong>],
              ]} />
              <span className="visually-hidden" role="status">{copied ? "Account number copied." : ""}</span>
              {info.data.reference_instructions ? <p className="muted">{info.data.reference_instructions}</p> : null}
              {info.data.instructions ? <p className="prewrap">{info.data.instructions}</p> : null}
            </div>
            {info.data.qr_code ? (
              <figure className="payment-qr">
                <img src={info.data.qr_code} alt="Academy payment QR code" width={220} height={220} />
                <figcaption className="muted">Scan with your banking app.</figcaption>
              </figure>
            ) : null}
          </div>
        )
      )}
      <p className="muted">After paying, upload your proof of payment below. The academy checks it, records the
        payment and then issues the official receipt.</p>
    </Section>
  );
}

type Errors = Partial<Record<"file" | "payment_date" | "amount_claimed" | "reference" | "note", string[]>>;

export function ProofUploadForm({ invoice }: { invoice: Invoice }) {
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const fileId = useId();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [paymentDate, setPaymentDate] = useState("");
  const [reference, setReference] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const upload = useMutation({
    mutationFn: (form: FormData) => endpoints.uploadPaymentProof(form),
    onSuccess: async () => {
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      setPaymentDate(""); setReference(""); setAmount(""); setNote("");
      await queryClient.invalidateQueries({ queryKey: paymentKeys.proofs(invoice.id) });
    },
    onError: (error) => {
      if (isApiError(error) && error.kind === "validation") setErrors(error.fieldErrors as Errors);
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const found: Errors = {};
    if (!file) found.file = ["Choose the file with your proof of payment."];
    else if (!PROOF_TYPES.some((ext) => file.name.toLowerCase().endsWith(ext))) found.file = ["Upload a PDF, JPG or PNG file."];
    else if (file.size > PROOF_MAX_BYTES) found.file = ["The file is too large (maximum 5 MB)."];
    else if (file.size === 0) found.file = ["The file is empty."];
    if (paymentDate && paymentDate > academyToday()) found.payment_date = ["The payment date cannot be in the future."];
    if (amount && !MONEY_INPUT.test(amount.trim())) found.amount_claimed = ["Enter an amount like 220.00."];
    setErrors(found);
    upload.reset();
    if (Object.keys(found).length) {
      document.getElementById(found.file ? fileId : "proof-first-error")?.focus();
      return;
    }
    const form = new FormData();
    form.append("invoice", String(invoice.id));
    form.append("file", file!);
    if (paymentDate) form.append("payment_date", paymentDate);
    if (reference.trim()) form.append("reference", reference.trim());
    if (amount.trim()) form.append("amount_claimed", amount.trim());
    if (note.trim()) form.append("note", note.trim());
    upload.mutate(form);
  }

  const generalError = upload.isError && !(isApiError(upload.error) && Object.keys(upload.error.fieldErrors).length)
    ? (isApiError(upload.error) ? upload.error.userMessage : "Unable to connect. Please try again.") : null;

  return (
    <Section title="Upload payment proof">
      {upload.isSuccess ? (
        <Alert tone="info" title="Payment proof submitted">
          <p>Your payment proof has been submitted and is waiting for academy verification.</p>
        </Alert>
      ) : null}
      {generalError ? <Alert tone="danger">{generalError}</Alert> : null}
      <form onSubmit={onSubmit} noValidate aria-label="Upload payment proof" className="proof-form">
        <div className={`field${errors.file ? " field-invalid" : ""}`}>
          <label htmlFor={fileId}>Proof of payment <span className="required" aria-hidden="true">*</span></label>
          <p id={`${fileId}-hint`} className="field-hint">Screenshot or PDF of your transfer confirmation.
            PDF, JPG or PNG, up to 5 MB.</p>
          <input ref={fileInput} id={fileId} type="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
                 aria-invalid={errors.file ? true : undefined}
                 aria-describedby={`${fileId}-hint${errors.file ? ` ${fileId}-error` : ""}`}
                 onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {errors.file ? <p id={`${fileId}-error`} className="field-error">{errors.file.join(" ")}</p> : null}
        </div>
        <div className="form-grid">
          <TextField id="proof-first-error" label="Payment date" type="date" max={academyToday()} value={paymentDate}
                     errors={errors.payment_date} onChange={(e) => setPaymentDate(e.target.value)} />
          <TextField label="Amount paid (RM)" inputMode="decimal" placeholder={invoice.balance_due} value={amount}
                     errors={errors.amount_claimed} onChange={(e) => setAmount(e.target.value)} />
        </div>
        <TextField label="Bank / transfer reference" maxLength={100} value={reference} errors={errors.reference}
                   hint="The reference shown on your bank's confirmation, if any."
                   onChange={(e) => setReference(e.target.value)} />
        <TextAreaField label="Note for the academy (optional)" rows={2} maxLength={500} value={note}
                       errors={errors.note} onChange={(e) => setNote(e.target.value)} />
        <Button type="submit" busy={upload.isPending}>Upload payment proof</Button>
      </form>
    </Section>
  );
}

async function download(fetchFile: () => Promise<Blob>, name: string) {
  const blob = await fetchFile();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ProofHistory({ invoice }: { invoice: Invoice }) {
  const { endpoints } = useServices();
  const proofs = useQuery({
    queryKey: paymentKeys.proofs(invoice.id),
    queryFn: ({ signal }) => endpoints.paymentProofs({ invoice: invoice.id }, signal),
  });
  const [downloadError, setDownloadError] = useState<string | null>(null);
  if (proofs.isPending) return <LoadingState />;
  if (proofs.isError) return <ErrorState error={proofs.error} onRetry={() => proofs.refetch()} />;
  if (!proofs.data.results.length) return null;
  return (
    <Section title="Payment proofs you uploaded">
      <p className="muted">A proof is not a receipt. “Checked by the academy” means staff have seen it; the invoice
        and the official receipt are updated when the academy records the payment.</p>
      {downloadError ? <Alert tone="danger">{downloadError}</Alert> : null}
      <DataTable<PaymentProof>
        caption="Payment proofs for this invoice"
        rows={proofs.data.results}
        rowKey={(p) => p.id}
        columns={[
          { key: "uploaded", header: "Uploaded", render: (p) => formatDateTime(p.uploaded_at) },
          { key: "status", header: "Status", render: (p) => (
            <>
              <ProofStatusBadge status={p.status} />
              {p.status === "REJECTED" && p.review_note ? <p className="proof-reason">Reason: {p.review_note}</p> : null}
            </>
          ) },
          { key: "amount", header: "Amount paid", align: "end", render: (p) => (p.amount_claimed ? <Money value={p.amount_claimed} /> : "—") },
          { key: "date", header: "Payment date", render: (p) => (p.payment_date ? formatDate(p.payment_date) : "—"), priority: "secondary" },
          { key: "file", header: "File", render: (p) => (
            <Button variant="ghost" aria-label={`Download ${p.original_name}`} onClick={() => {
              setDownloadError(null);
              download(() => endpoints.paymentProofFile(p.id), p.original_name)
                .catch((error) => setDownloadError(isApiError(error) ? error.userMessage : "Unable to connect. Please try again."));
            }}>{p.original_name}</Button>
          ) },
        ]}
      />
    </Section>
  );
}
