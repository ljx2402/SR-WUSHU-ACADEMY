import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
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
import { TextField } from "../../ui/Field";
import { Alert, Card } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { CompetitionStatusBadge, DefinitionList, LoadMore, Section } from "../components";
import { useSelectedStudent } from "../ParentContext";
import { parentKeys, usePagedList } from "../queries";

/*
 * Competitions. The flow is the backend's: choose child and event → register
 * → a competition invoice is issued (due today) and the entry is "Awaiting
 * payment" → the academy records the payment → the entry becomes CONFIRMED
 * automatically. There is no approval step and no online payment. Fees are
 * generally non-refundable; parents cannot request refunds here.
 */

const CRUMBS = [{ label: "Overview", to: "/parent/dashboard" }];
const COMP_CRUMBS = [...CRUMBS, { label: "Competitions", to: "/parent/competitions" }];
const GENDER: Record<string, string> = { M: "Male", F: "Female", OPEN: "Open / mixed" };

function ageText(event: CompetitionEvent) {
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

function useCompetition(id: string) {
  const { endpoints } = useServices();
  return useQuery({
    queryKey: parentKeys.competition(id),
    queryFn: ({ signal }) => endpoints.competition(id, signal),
  });
}

function registrationBlocker(competition: Competition, canRegister: boolean): string | null {
  if (!canRegister) return "Your account cannot register children for competitions.";
  if (!competition.allow_parent_registration) return "Registration for this competition is handled by the academy.";
  if (!competition.is_open) return "Registration for this competition is closed.";
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
  const canWithdraw = canRegister && c.is_open;
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
      {blocker && canRegister ? <Alert tone="info">{blocker}</Alert> : null}

      <Section title="Events">
        <DataTable caption={`Events at ${c.name}`} rows={c.events} rowKey={(e) => e.id} columns={eventColumns()}
                   emptyMessage="No events have been announced yet." />
        <p className="muted">Age and gender rules are checked by the academy system when you register.</p>
      </Section>

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

export function CompetitionRegisterPage() {
  const { competitionId = "" } = useParams();
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const { children, selected, select } = useSelectedStudent();
  const competition = useCompetition(competitionId);
  const [eventId, setEventId] = useState<number | null>(null);
  const [notes, setNotes] = useState("");
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [confirming, setConfirming] = useState(false);
  const childFieldId = useId();
  const register = useMutation({
    mutationFn: (body: { student: number; event: number; notes: string }) => endpoints.register(body),
    onSuccess: async () => {
      setConfirming(false);
      await queryClient.invalidateQueries({ queryKey: parentKeys.all });
    },
    onError: (error) => {
      setConfirming(false);
      if (isApiError(error) && error.kind === "validation") setErrors(error.fieldErrors);
    },
  });

  if (competition.isPending) return <LoadingState />;
  if (competition.isError) {
    if (isApiError(competition.error) && competition.error.kind === "not_found") {
      return (<><PageHeader title="Competition not found" crumbs={COMP_CRUMBS} /><NotFoundState /></>);
    }
    return <ErrorState error={competition.error} onRetry={() => competition.refetch()} />;
  }
  const c = competition.data;
  const crumbs = [...COMP_CRUMBS, { label: c.name, to: `/parent/competitions/${c.id}` }];
  const blocker = registrationBlocker(c, can(me, "competition.register_own_children"));
  const child = children.find((ch) => ch.id === selected) ?? null;
  const event = c.events.find((e) => e.id === eventId) ?? null;

  if (register.isSuccess) {
    const r = register.data;
    return (
      <>
        <PageHeader title="Registration received" crumbs={crumbs} />
        <Alert tone={r.status === "CONFIRMED" ? "success" : "info"}
               title={`${r.student_name} – ${r.event_name}`}>
          <p>Status: <RegistrationStatusBadge status={r.status} /></p>
          {r.status === "PENDING" && r.invoice ? (
            <>
              <p>Invoice <Link to={`/parent/finance/invoices/${r.invoice.id}`}>{r.invoice.number}</Link> has been issued
                with <Money value={r.invoice.balance_due} /> due today.</p>
              <p>Pay the academy using the details on the invoice, then upload your proof of payment there. The
                academy checks it and records the payment; the entry is confirmed automatically once the invoice is
                paid in full. The app does not take payments itself.</p>
              <p><Link className="btn btn-primary" to={`/parent/finance/invoices/${r.invoice.id}`}>Pay and upload proof</Link></p>
            </>
          ) : r.status === "CONFIRMED" ? <p>This event has no fee, so the entry is confirmed.</p> : null}
        </Alert>
        <p><Link to={`/parent/competitions/${c.id}`}>Back to {c.name}</Link></p>
      </>
    );
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const found: Record<string, string[]> = {};
    if (!child) found.student = ["Choose a child."];
    if (!event) found.event = ["Choose an event."];
    setErrors(found);
    register.reset();
    if (Object.keys(found).length) {
      document.getElementById(found.student ? childFieldId : "event-choices")?.focus();
      return;
    }
    setConfirming(true);
  }

  return (
    <>
      <PageHeader title={`Register for ${c.name}`} crumbs={crumbs} />
      {blocker ? <Alert tone="info">{blocker}</Alert> : !children.length ? <EmptyState message="No children found." /> : (
        <form onSubmit={onSubmit} noValidate aria-label="Competition registration" className="register-form">
          {register.isError && !(isApiError(register.error) && Object.keys(register.error.fieldErrors).length) ? (
            <Alert tone="danger" title="The registration was not accepted.">
              <p>{isApiError(register.error) ? register.error.userMessage : "Unable to connect. Please try again."}</p>
            </Alert>
          ) : null}
          <div className={`field${errors.student ? " field-invalid" : ""}`}>
            <label htmlFor={childFieldId}>1. Child <span className="required" aria-hidden="true">*</span></label>
            <select id={childFieldId} value={selected === null || selected === "all" ? "" : selected}
                    aria-invalid={errors.student ? true : undefined}
                    aria-describedby={errors.student ? `${childFieldId}-error` : undefined}
                    onChange={(e) => e.target.value && select(Number(e.target.value))}>
              <option value="" disabled>Choose a child</option>
              {children.map((ch) => <option key={ch.id} value={ch.id}>{ch.full_name}</option>)}
            </select>
            {errors.student ? <p id={`${childFieldId}-error`} className="field-error">{errors.student.join(" ")}</p> : null}
          </div>

          <fieldset id="event-choices" tabIndex={-1} className={`field choices${errors.event ? " field-invalid" : ""}`}
                    aria-describedby={errors.event ? "event-error" : undefined}>
            <legend>2. Event <span className="required" aria-hidden="true">*</span></legend>
            {c.events.length ? c.events.map((ev) => (
              <label key={ev.id} className="choice">
                <input type="radio" name="event" value={ev.id} checked={eventId === ev.id}
                       onChange={() => setEventId(ev.id)} />
                <span>
                  <strong>{ev.name}</strong>{" "}
                  <span className="muted">{GENDER[ev.gender] ?? ev.gender} · {ageText(ev)}{ev.weight_class ? ` · ${ev.weight_class}` : ""}</span>
                  <br />Fee <Money value={ev.fee} />
                </span>
              </label>
            )) : <p className="muted">No events have been announced yet.</p>}
            {errors.event ? <p id="event-error" className="field-error">{errors.event.join(" ")}</p> : null}
          </fieldset>

          <TextField label="3. Note for the academy (optional)" value={notes} maxLength={255}
                     errors={errors.notes} onChange={(e) => setNotes(e.target.value)} />

          <Alert tone="info" title="Payment is required to confirm the entry">
            <p>Registering issues a competition invoice for the event fee, due today. Pay the academy, then upload
              your proof of payment on the invoice. The entry stays “Awaiting payment” until the academy has recorded
              full payment, and is then confirmed automatically. Competition fees are generally non-refundable.</p>
          </Alert>
          <Button type="submit" busy={register.isPending}>Review and register</Button>
        </form>
      )}

      <ConfirmDialog
        open={confirming}
        title="Confirm registration"
        tone="primary"
        confirmLabel="Register"
        busy={register.isPending}
        message={child && event ? (
          <>
            <p>Register <strong>{child.full_name}</strong> for <strong>{event.name}</strong> at {c.name}.</p>
            <p>An invoice for <Money value={event.fee} /> will be issued, due today. The entry is confirmed once it is
              paid in full. Competition fees are generally non-refundable.</p>
          </>
        ) : null}
        onCancel={() => setConfirming(false)}
        onConfirm={() => child && event && register.mutate({ student: child.id, event: event.id, notes: notes.trim() })}
      />
    </>
  );
}
