import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { CompetitionRegistration, StaffCompetition, StaffPaymentProof } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { ChargeStatusBadge, Money, REGISTRATION_STATUS, RegistrationStatusBadge } from "../../domain/finance";
import { formatDate, formatDateTime } from "../../domain/format";
import { ProofIsNotPayment, ProofReviewStatus } from "../../finance/components";
import { useUrlFilters } from "../../finance/filters";
import { DefinitionList, LoadMore, Section } from "../../parent/components";
import { AnswerList } from "../../parent/DynamicForm";
import { usePagedList } from "../../parent/queries";
import { errorText } from "../../staff/components";
import { Button } from "../../ui/Button";
import { DataTable } from "../../ui/DataTable";
import { ConfirmDialog } from "../../ui/Dialog";
import { SelectField, TextField } from "../../ui/Field";
import { Alert, Badge } from "../../ui/primitives";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { COMP_ROOT, MEDAL, PAYMENT_FILTER, competitionKeys } from "../components";
import { CompetitionFrame, WithCompetition } from "./StaffCompetitionDetailPage";

/*
 * Participants: the competition's registrations, filtered and paginated by
 * the backend (/api/competition-registrations/?competition=&status=&event=&
 * payment=&result=&search=&start=&end=). Payment is finance's: the entry shows
 * the fee's state and links to the invoice; it is never marked paid here.
 */

export function resultText(result: CompetitionRegistration["result"]) {
  if (!result) return "—";
  return [result.placing ? `Placing ${result.placing}` : null, MEDAL[result.medal] ?? null,
          result.score ? `Score ${result.score}` : null].filter(Boolean).join(" · ") || "Recorded";
}

export function ParticipantsPage() {
  return <WithCompetition title="Participants">{(c) => <Participants competition={c} />}</WithCompetition>;
}

function Participants({ competition: c }: { competition: StaffCompetition }) {
  const { endpoints } = useServices();
  const { params, set } = useUrlFilters();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const query = {
    competition: c.id, search: params.get("search") || undefined, status: params.get("status") || undefined,
    event: params.get("event") ? Number(params.get("event")) : undefined, payment: params.get("payment") || undefined,
    result: params.get("result") || undefined, start: params.get("start") || undefined, end: params.get("end") || undefined,
  };
  const list = usePagedList<CompetitionRegistration>(competitionKeys.registrations(query),
    (page, signal) => endpoints.staffRegistrations({ ...query, page }, signal));
  function onSearch(e: FormEvent) { e.preventDefault(); set("search", search.trim()); }
  return (
    <CompetitionFrame competition={c} title="Participants">
      <form className="filter-bar" role="search" aria-label="Find participants" onSubmit={onSearch}>
        <TextField label="Student" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
                   placeholder="Name or student no." />
        <SelectField label="Event" value={params.get("event") ?? ""} onChange={(e) => set("event", e.target.value)}>
          <option value="">All events</option>
          {c.events.map((ev) => <option key={ev.id} value={ev.id}>{ev.name}</option>)}
        </SelectField>
        <SelectField label="Entry status" value={params.get("status") ?? ""} onChange={(e) => set("status", e.target.value)}>
          <option value="">All statuses</option>
          {Object.entries(REGISTRATION_STATUS).map(([value, s]) => <option key={value} value={value}>{s.label}</option>)}
        </SelectField>
        <SelectField label="Fee" value={params.get("payment") ?? ""} onChange={(e) => set("payment", e.target.value)}>
          <option value="">All</option>
          {Object.entries(PAYMENT_FILTER).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </SelectField>
        <SelectField label="Result" value={params.get("result") ?? ""} onChange={(e) => set("result", e.target.value)}>
          <option value="">All</option>
          <option value="yes">Result recorded</option>
          <option value="no">No result yet</option>
        </SelectField>
        <TextField label="Registered from" type="date" value={params.get("start") ?? ""} onChange={(e) => set("start", e.target.value)} />
        <TextField label="Registered to" type="date" value={params.get("end") ?? ""} onChange={(e) => set("end", e.target.value)} />
        <div className="filter-actions"><Button type="submit">Search</Button></div>
      </form>
      {list.isPending ? <LoadingState /> : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : (
        <>
          <p className="muted" role="status">{list.count} {list.count === 1 ? "entry" : "entries"}</p>
          <DataTable<CompetitionRegistration>
            caption={`Participants in ${c.name}`}
            rows={list.rows}
            rowKey={(r) => r.id}
            emptyMessage="No entries match."
            className="staff-table"
            columns={[
              { key: "student", header: "Student", render: (r) => (
                <Link className="tap-link" to={`${COMP_ROOT}/${c.id}/participants/${r.id}`}>{r.student_name}</Link>) },
              { key: "event", header: "Event", render: (r) => r.event_name },
              { key: "status", header: "Entry", render: (r) => <RegistrationStatusBadge status={r.status} /> },
              { key: "fee", header: "Fee", align: "end", render: (r) => (r.fee ? <Money value={r.fee} className="nowrap" /> : "Free") },
              { key: "payment", header: "Payment", render: (r) => (r.fee_status ? <ChargeStatusBadge status={r.fee_status} /> : "—") },
              { key: "form", header: "Form", priority: "secondary", render: (r) => (r.form_version ? `v${r.form_version}` : "—") },
              { key: "result", header: "Result", priority: "secondary", render: (r) => resultText(r.result) },
              { key: "registered", header: "Registered", priority: "secondary", render: (r) => formatDate(r.registered_at.slice(0, 10)) },
            ]}
          />
          <LoadMore shown={list.rows.length} total={list.count} hasMore={!!list.hasNextPage}
                    loading={list.isFetchingNextPage} onMore={() => list.fetchNextPage()} />
        </>
      )}
    </CompetitionFrame>
  );
}

export function ParticipantDetailPage() {
  return <WithCompetition title="Participant">{(c) => <Participant competition={c} />}</WithCompetition>;
}

type Action = "confirm" | "reject" | "withdraw";

function Participant({ competition: c }: { competition: StaffCompetition }) {
  const { registrationId = "" } = useParams();
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const reg = useQuery({ queryKey: competitionKeys.registration(registrationId),
                         queryFn: ({ signal }) => endpoints.staffRegistration(registrationId, signal) });
  const invoiceId = reg.data?.invoice?.id;
  const reviewsProofs = can(me, "finance.proofs.review");
  const proofs = useQuery({
    queryKey: competitionKeys.proofs(invoiceId ?? 0),
    queryFn: ({ signal }) => endpoints.staffProofs({ invoice: invoiceId }, signal),
    enabled: !!invoiceId && reviewsProofs,
  });
  const [action, setAction] = useState<Action | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const run = useMutation({
    mutationFn: ({ name, reason }: { name: Action; reason: string }) => {
      const id = Number(registrationId);
      if (name === "confirm") return endpoints.confirmRegistration(id);
      if (name === "reject") return endpoints.rejectRegistration(id, reason);
      return endpoints.withdraw(id, reason);
    },
    onSuccess: async (updated, { name }) => {
      setAction(null);
      setDone(name === "confirm" ? "Entry confirmed." : name === "reject" ? "Entry rejected." : "Entry withdrawn.");
      queryClient.setQueryData(competitionKeys.registration(registrationId), updated);
      await queryClient.invalidateQueries({ queryKey: competitionKeys.all });
    },
  });

  if (reg.isPending) return <CompetitionFrame competition={c} title="Participant"><LoadingState /></CompetitionFrame>;
  if (reg.isError || reg.data.competition !== c.id) {
    return (
      <CompetitionFrame competition={c} title="Participant">
        {reg.isError && !(isApiError(reg.error) && reg.error.kind === "not_found")
          ? <ErrorState error={reg.error} onRetry={() => reg.refetch()} />
          : <NotFoundState message="This entry was not found in this competition." />}
      </CompetitionFrame>
    );
  }
  const r = reg.data;
  const active = r.status === "PENDING" || r.status === "CONFIRMED";
  const manages = can(me, "competition.registrations.manage");
  const paidFee = r.fee_status === "PAID" || r.fee_status === "PARTIAL";
  return (
    <CompetitionFrame competition={c} title={r.student_name}>
      {done ? <Alert tone="success" role="status" title={done} /> : null}
      <DefinitionList items={[
        ["Student", can(me, "students.view_all") ? <Link className="tap-link" to={`/staff/students/${r.student}`}>{r.student_name}</Link> : r.student_name],
        ["Event", r.event_name],
        ["Entry status", <RegistrationStatusBadge key="s" status={r.status} />],
        ["Registered", formatDateTime(r.registered_at)],
        ["Form version", r.form_version ? `Version ${r.form_version}` : "—"],
      ]} />

      <Section title="Fee and payment">
        <p className="muted">Payment is recorded by finance. An entry awaiting payment is confirmed automatically once its invoice is paid.</p>
        <DefinitionList items={[
          ["Fee", r.fee ? <Money key="f" value={r.fee} /> : "Free entry"],
          ["Payment", r.fee_status ? <ChargeStatusBadge key="p" status={r.fee_status} /> : "—"],
          ["Invoice", r.invoice ? (can(me, "finance.view_all")
            ? <Link className="tap-link" to={`/finance/invoices/${r.invoice.id}`}>{r.invoice.number}</Link> : r.invoice.number) : "—"],
          ["Invoice balance", r.invoice ? <Money key="b" value={r.invoice.balance_due} /> : "—"],
        ]} />
        {reviewsProofs && invoiceId ? (
          proofs.isPending ? <LoadingState label="Loading payment proofs…" /> : proofs.isError ? (
            <ErrorState error={proofs.error} onRetry={() => proofs.refetch()} />
          ) : proofs.data.results.length ? (
            <>
              <ProofIsNotPayment />
              <DataTable<StaffPaymentProof>
                caption="Payment proofs for this invoice"
                rows={proofs.data.results}
                rowKey={(p) => p.id}
                className="staff-table"
                columns={[
                  { key: "uploaded", header: "Uploaded", render: (p) => (
                    <Link className="tap-link" to={`/finance/payment-proofs/${p.id}`}>{formatDateTime(p.uploaded_at)}</Link>) },
                  { key: "amount", header: "Claimed", align: "end", render: (p) => <Money value={p.amount_claimed} /> },
                  { key: "status", header: "Review", render: (p) => <ProofReviewStatus proof={p} /> },
                ]}
              />
            </>
          ) : <p className="muted">No payment proofs uploaded for this invoice.</p>
        ) : null}
      </Section>

      <Section title="Registration form answers">
        {r.form_responses?.length ? (
          <>
            <p className="muted">As submitted with form version {r.form_version}: later form changes never alter these answers.</p>
            <AnswerList answers={r.form_responses} />
          </>
        ) : <p className="muted">No custom questions were answered{r.form_version ? ` (form version ${r.form_version})` : ""}.</p>}
        {r.notes ? <DefinitionList items={[["Notes", <span key="n" className="prewrap">{r.notes}</span>]]} /> : null}
      </Section>

      <Section title="Result">
        {r.result ? (
          <DefinitionList items={[
            ["Placing", r.result.placing ?? "—"], ["Medal", MEDAL[r.result.medal] ?? "—"], ["Score", r.result.score ?? "—"],
            ["Staff remarks", r.result.remarks ? <span key="r" className="prewrap">{r.result.remarks}</span> : "—"],
          ]} />
        ) : <p className="muted">No result recorded.</p>}
        <Link className="tap-link" to={`${COMP_ROOT}/${c.id}/results`}>Go to results</Link>
      </Section>

      {manages && active && !r.result ? (
        <Section title="Entry actions">
          <div className="button-row">
            {r.status === "PENDING" ? (
              <Button variant="secondary" onClick={() => { run.reset(); setAction("confirm"); }}>Confirm entry</Button>
            ) : null}
            <Button variant="secondary" onClick={() => { run.reset(); setAction("withdraw"); }}>Withdraw</Button>
            <Button variant="danger" onClick={() => { run.reset(); setAction("reject"); }}>Reject</Button>
          </div>
          {r.status === "PENDING" ? <p className="muted">Confirmation is refused while the fee is unpaid.</p> : null}
        </Section>
      ) : manages && r.result && active ? <p className="muted">An entry with a recorded result cannot be withdrawn or rejected.</p> : null}

      <ConfirmDialog
        open={!!action}
        title={action === "confirm" ? "Confirm this entry?" : action === "reject" ? "Reject this entry?" : "Withdraw this entry?"}
        confirmLabel={action === "confirm" ? "Confirm entry" : action === "reject" ? "Reject entry" : "Withdraw entry"}
        tone={action === "confirm" ? "primary" : "danger"}
        requireReason={action !== "confirm"}
        busy={run.isPending}
        error={run.error ? errorText(run.error) : null}
        message={action === "confirm" ? <p>{r.student_name} will be confirmed for {r.event_name}. Unpaid entries cannot be confirmed.</p> : (
          <>
            <p>{r.student_name} will be {action === "reject" ? "rejected from" : "withdrawn from"} {r.event_name}.</p>
            {paidFee ? <p><strong>Fees already paid are not refunded automatically.</strong> Any exceptional refund is a separate finance decision.</p>
              : r.fee ? <p>The unpaid competition invoice for this entry will be voided.</p> : null}
          </>
        )}
        onCancel={() => setAction(null)}
        onConfirm={(reason) => action && run.mutate({ name: action, reason })}
      />
      <p><Badge tone="neutral">Entry #{r.id}</Badge></p>
    </CompetitionFrame>
  );
}
