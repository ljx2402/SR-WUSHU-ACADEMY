import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError, isApiError } from "../../api/errors";
import type { Competition, CompetitionEvent, CompetitionRegistration } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { ChargeStatusBadge, Money, RegistrationStatusBadge } from "../../domain/finance";
import { formatDate, formatPeriod } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { Button } from "../../ui/Button";
import { DataTable, type Column } from "../../ui/DataTable";
import { ConfirmDialog } from "../../ui/Dialog";
import { Alert, Card } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { CompetitionStatusBadge, DefinitionList, LoadMore, Section } from "../components";
import { AnswerList } from "../DynamicForm";
import { parentKeys, usePagedList } from "../queries";

/*
 * Competitions. The flow is the backend's: choose child and event → register
 * → a competition invoice is issued (due today) and the entry is "Awaiting
 * payment" → the academy records the payment → the entry becomes CONFIRMED
 * automatically. There is no approval step and no online payment. Fees are
 * generally non-refundable; parents cannot request refunds here.
 */

const CRUMBS = [{ label: "Overview", to: "/parent/dashboard" }];
export const COMP_CRUMBS = [...CRUMBS, { label: "Competitions", to: "/parent/competitions" }];
export const GENDER: Record<string, string> = { M: "Male", F: "Female", OPEN: "Open / mixed" };

export function ageText(event: CompetitionEvent) {
  if (event.min_age !== null && event.max_age !== null) return `Ages ${event.min_age}–${event.max_age}`;
  if (event.min_age !== null) return `Age ${event.min_age}+`;
  if (event.max_age !== null) return `Up to age ${event.max_age}`;
  return "All ages";
}

const REGISTRATION_COLUMNS: Column<CompetitionRegistration>[] = [
  { key: "child", header: "Child", render: (r) => r.student_name },
  { key: "comp", header: "Competition", render: (r) => <Link to={`/parent/competitions/${r.competition}`}>{r.competition_name}</Link> },
  { key: "event", header: "Event", render: (r) => r.event_name },
  { key: "status", header: "Entry", render: (r) => <RegistrationStatusBadge status={r.status} /> },
  { key: "fee", header: "Fee", align: "end", render: (r) => (r.fee ? <Money value={r.fee} /> : "Free") },
  { key: "payment", header: "Payment", render: (r) => (r.fee_status ? <ChargeStatusBadge status={r.fee_status} /> : "—") },
  { key: "invoice", header: "Invoice", render: (r) => (r.invoice
    ? <Link to={`/parent/finance/invoices/${r.invoice.id}`}>{r.invoice.number}</Link> : "—"), priority: "secondary" },
];

function eventColumns(): Column<CompetitionEvent>[] {
  return [
    { key: "name", header: "Event", render: (e) => e.name },
    { key: "gender", header: "Gender", render: (e) => GENDER[e.gender] ?? e.gender },
    { key: "age", header: "Age", render: ageText },
    { key: "weight", header: "Weight class", render: (e) => e.weight_class || "—", priority: "secondary" },
    { key: "fee", header: "Fee", align: "end", render: (e) => <Money value={e.fee} /> },
  ];
}

export function CompetitionsPage() {
  const { endpoints } = useServices();
  const competitions = usePagedList<Competition>(parentKeys.competitions,
    (page, signal) => endpoints.competitions({ page }, signal));
  const registrations = usePagedList<CompetitionRegistration>(parentKeys.registrations({}),
    (page, signal) => endpoints.registrations({ page }, signal));
  const open = competitions.rows.filter((c) => c.is_open);
  const others = competitions.rows.filter((c) => !c.is_open);
  return (
    <>
      <PageHeader title="Competitions" crumbs={CRUMBS}
                  description="Entry fees are paid when you register: the entry is confirmed once its invoice is paid in full. Competition fees are generally non-refundable." />
      <Section title="Your children’s entries">
        {registrations.isPending ? <LoadingState /> : registrations.isError ? (
          <ErrorState error={registrations.error} onRetry={() => registrations.refetch()} />
        ) : (
          <>
            <DataTable caption="Competition entries" rows={registrations.rows} rowKey={(r) => r.id}
                       columns={REGISTRATION_COLUMNS} emptyMessage="No competition entries yet." />
            <LoadMore shown={registrations.rows.length} total={registrations.count}
                      hasMore={!!registrations.hasNextPage} loading={registrations.isFetchingNextPage}
                      onMore={() => registrations.fetchNextPage()} />
          </>
        )}
      </Section>
      {competitions.isPending ? <LoadingState /> : competitions.isError ? (
        <ErrorState error={competitions.error} onRetry={() => competitions.refetch()} />
      ) : (
        <>
          <Section title="Open for registration">
            {open.length ? <CompetitionCards list={open} /> : <EmptyState message="No upcoming competitions." />}
          </Section>
          {others.length ? (
            <Section title="Other competitions"><CompetitionCards list={others} /></Section>
          ) : null}
          <LoadMore shown={competitions.rows.length} total={competitions.count} hasMore={!!competitions.hasNextPage}
                    loading={competitions.isFetchingNextPage} onMore={() => competitions.fetchNextPage()} />
        </>
      )}
    </>
  );
}

function CompetitionCards({ list }: { list: Competition[] }) {
  return (
    <div className="grid grid-cards">
      {list.map((c) => (
        <Card key={c.id} as="article" title={<Link to={`/parent/competitions/${c.id}`}>{c.name}</Link>}
              actions={<CompetitionStatusBadge status={c.status} />}>
          <DefinitionList items={[
            ["Dates", formatPeriod(c.start_date, c.end_date)],
            ["Venue", c.venue],
            ["Register by", formatDate(c.registration_deadline)],
            ["Events", String(c.events.length)],
          ]} />
        </Card>
      ))}
    </div>
  );
}

export function useCompetition(id: string) {
  const { endpoints } = useServices();
  return useQuery({
    queryKey: parentKeys.competition(id),
    queryFn: ({ signal }) => endpoints.competition(id, signal),
  });
}

export function registrationBlocker(competition: Competition, canRegister: boolean): string | null {
  if (!canRegister) return "Your account cannot register children for competitions.";
  if (!competition.allow_parent_registration) return "Registration for this competition is handled by the academy.";
  if (!competition.is_open) return "Registration for this competition is closed.";
  if (competition.registration_form.status !== "PUBLISHED") {
    return "The registration form for this competition is not available yet.";
  }
  return null;
}

export function CompetitionDetailPage() {
  const { competitionId = "" } = useParams();
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const competition = useCompetition(competitionId);
  const registrations = useQuery({
    queryKey: parentKeys.registrations({ competition: competitionId }),
    queryFn: ({ signal }) => endpoints.registrations({ competition: Number(competitionId) }, signal),
    enabled: competition.isSuccess,
  });
  const [withdrawing, setWithdrawing] = useState<CompetitionRegistration | null>(null);
  const withdraw = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => endpoints.withdraw(id, reason),
    onSuccess: async () => {
      setWithdrawing(null);
      await queryClient.invalidateQueries({ queryKey: parentKeys.all });
    },
  });

  if (competition.isPending) return <LoadingState />;
  if (competition.isError) {
    if (isApiError(competition.error) && competition.error.kind === "not_found") {
      return (<><PageHeader title="Competition not found" crumbs={COMP_CRUMBS.slice(0, 2)} /><NotFoundState /></>);
    }
    return <ErrorState error={competition.error} onRetry={() => competition.refetch()} />;
  }
  const c = competition.data;
  const canRegister = can(me, "competition.register_own_children");
  const blocker = registrationBlocker(c, canRegister);
  // The competition decides whether parents may withdraw; the backend enforces it.
  const canWithdraw = canRegister && c.is_open && c.allow_parent_withdrawal;
  return (
    <>
      <PageHeader title={c.name} crumbs={COMP_CRUMBS}
                  actions={!blocker ? <Link className="btn btn-primary" to={`/parent/competitions/${c.id}/register`}>Register a child</Link> : null} />
      <p><CompetitionStatusBadge status={c.status} /></p>
      <DefinitionList items={[
        ["Organiser", c.organiser],
        ["Dates", formatPeriod(c.start_date, c.end_date)],
        ["Venue", c.venue],
        ["Registration deadline", formatDate(c.registration_deadline)],
        ["Events per child", c.max_events_per_student ? `Up to ${c.max_events_per_student}` : "No limit"],
        ["Ages counted on", c.age_reference_date ? formatDate(c.age_reference_date) : formatDate(c.start_date)],
      ]} />
      {c.description ? <p className="prewrap">{c.description}</p> : null}
      {c.rules ? (
        <Section title="Rules"><p className="prewrap">{c.rules}</p></Section>
      ) : null}
      {blocker && canRegister ? <Alert tone="info">{blocker}</Alert> : null}

      <Section title="Events">
        <DataTable caption={`Events at ${c.name}`} rows={c.events} rowKey={(e) => e.id} columns={eventColumns()}
                   emptyMessage="No events have been announced yet." />
        <p className="muted">Age and gender rules are checked by the academy system when you register.</p>
      </Section>
      {canRegister && !c.allow_parent_withdrawal ? (
        <p className="muted">Withdrawal from this competition is handled by the academy.</p>
      ) : null}

      <Section title="Your children’s entries">
        {registrations.isPending ? <LoadingState /> : registrations.isError ? (
          <ErrorState error={registrations.error} onRetry={() => registrations.refetch()} />
        ) : (
          <DataTable
            caption="Your children’s entries in this competition"
            rows={registrations.data.results}
            rowKey={(r) => r.id}
            emptyMessage="None of your children are entered yet."
            columns={[
              ...REGISTRATION_COLUMNS.filter((col) => col.key !== "comp"),
              { key: "answers", header: "Form answers", render: (r) => (r.form_responses?.length ? (
                <details className="answers">
                  <summary>View answers</summary>
                  <AnswerList answers={r.form_responses} />
                </details>
              ) : "—"), priority: "secondary" },
              { key: "actions", header: "Actions", render: (r) => (canWithdraw && (r.status === "PENDING" || r.status === "CONFIRMED") && !r.result
                ? <Button variant="ghost" onClick={() => { withdraw.reset(); setWithdrawing(r); }}>Withdraw</Button> : "—") },
            ]}
          />
        )}
      </Section>

      <ConfirmDialog
        open={!!withdrawing}
        title="Withdraw this entry?"
        confirmLabel="Withdraw entry"
        busy={withdraw.isPending}
        error={withdraw.error ? (withdraw.error instanceof ApiError ? withdraw.error.userMessage : "Unable to connect. Please try again.") : null}
        reasonLabel="Reason (optional)"
        message={withdrawing ? (
          <>
            <p>{withdrawing.student_name} will be withdrawn from {withdrawing.event_name}.</p>
            {withdrawing.fee_status === "PAID" || withdrawing.fee_status === "PARTIAL" ? (
              <p><strong>Fees already paid are not refunded.</strong> Competition fees are non-refundable; any
                exception is decided by the academy.</p>
            ) : <p>The unpaid competition invoice for this entry will be cancelled.</p>}
          </>
        ) : null}
        onCancel={() => setWithdrawing(null)}
        onConfirm={(reason) => withdrawing && withdraw.mutate({ id: withdrawing.id, reason })}
        askReason
      />
    </>
  );
}
