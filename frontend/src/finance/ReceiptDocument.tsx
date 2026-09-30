import type { Receipt } from "../api/types";
import { Money } from "../domain/finance";
import { formatDateTime, formatPeriod } from "../domain/format";
import { DefinitionList } from "../parent/components";
import { DataTable } from "../ui/DataTable";
import { Alert } from "../ui/primitives";

/**
 * The official receipt exactly as issued (the backend's frozen `content`),
 * shared by the Parent Portal and the finance staff portal and printed with
 * the browser's print. It never recomputes anything.
 */
export function ReceiptDocument({ receipt: r }: { receipt: Receipt }) {
  const c = r.content;
  return (
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
  );
}
