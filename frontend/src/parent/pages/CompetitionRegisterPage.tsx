import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { CompetitionRegistration, FormFieldDefinition } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { Money, RegistrationStatusBadge } from "../../domain/finance";
import { formatDate } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/Dialog";
import { TextField } from "../../ui/Field";
import { Alert } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import {
  AnswerList, DynamicForm, describeAnswer, toResponses, validateAnswers, type Answers, type AnswerValue,
  type FieldErrors,
} from "../DynamicForm";
import { useSelectedStudent } from "../ParentContext";
import { PaymentInformation } from "../PaymentSection";
import { parentKeys } from "../queries";
import { COMP_CRUMBS, GENDER, ageText, registrationBlocker, useCompetition } from "./CompetitionPages";

/*
 * Registering a child. The questions come from the competition's PUBLISHED
 * registration form (configured by academy staff per competition); nothing
 * competition-specific is written here. Flow: competition → child → event
 * (fee shown) → the competition's questions → confirm → PENDING ("Awaiting
 * payment") with an issued invoice → pay manually and upload proof → the
 * academy records the payment → CONFIRMED when paid in full.
 */
export function CompetitionRegisterPage() {
  const { competitionId = "" } = useParams();
  const me = useMe();
  const navigate = useNavigate();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const { children, selected, select } = useSelectedStudent();
  const competition = useCompetition(competitionId);
  const openCompetitions = useQuery({
    queryKey: [...parentKeys.competitions, "first"],
    queryFn: ({ signal }) => endpoints.competitions({}, signal),
  });
  const [eventId, setEventId] = useState<number | null>(null);
  const [answers, setAnswers] = useState<Answers>({});
  const [notes, setNotes] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [confirming, setConfirming] = useState(false);
  const childFieldId = useId();
  const competitionFieldId = useId();
  const register = useMutation({
    mutationFn: (body: Parameters<typeof endpoints.register>[0]) => endpoints.register(body),
    onSuccess: async () => {
      setConfirming(false);
      await queryClient.invalidateQueries({ queryKey: parentKeys.all });
    },
    onError: (error) => {
      setConfirming(false);
      if (isApiError(error) && error.kind === "validation") {
        setErrors(error.fieldErrors);
        const first = Object.keys(error.fieldErrors)[0];
        // After the dialog has closed (it returns focus to its opener first).
        if (first?.startsWith("responses.")) {
          window.setTimeout(() => document.getElementById(`response-${first.slice(10)}`)?.focus(), 0);
        }
      }
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
  const fields: FormFieldDefinition[] = c.registration_form.fields;
  const otherOpen = (openCompetitions.data?.results ?? []).filter((x) => x.is_open && x.allow_parent_registration);

  if (register.isSuccess) return <RegistrationDone registration={register.data} competitionName={c.name}
                                                    competitionId={c.id} crumbs={crumbs} />;

  function setAnswer(key: string, value: AnswerValue) {
    setAnswers((current) => ({ ...current, [key]: value }));
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const found: FieldErrors = { ...validateAnswers(fields, answers) };
    if (!child) found.student = ["Choose a child."];
    if (!event) found.event = ["Choose an event."];
    setErrors(found);
    register.reset();
    const keys = Object.keys(found);
    if (keys.length) {
      const target = found.student ? childFieldId : found.event ? "event-choices"
        : `response-${keys.find((k) => k.startsWith("responses."))!.slice(10)}`;
      document.getElementById(target)?.focus();
      return;
    }
    setConfirming(true);
  }

  const generalError = register.isError && !(isApiError(register.error) && Object.keys(register.error.fieldErrors).length)
    ? (isApiError(register.error) ? register.error.userMessage : "Unable to connect. Please try again.") : null;
  const otherErrors = Object.entries(errors).filter(([k]) => !k.startsWith("responses.") && !["student", "event", "notes"].includes(k));

  return (
    <>
      <PageHeader title={`Register for ${c.name}`} crumbs={crumbs} />
      {otherOpen.length > 1 ? (
        <div className="field register-competition">
          <label htmlFor={competitionFieldId}>Competition</label>
          <select id={competitionFieldId} value={c.id}
                  onChange={(e) => navigate(`/parent/competitions/${e.target.value}/register`)}>
            {otherOpen.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select>
        </div>
      ) : null}
      <p className="muted">{formatDate(c.start_date)} · {c.venue || "Venue to be announced"} · Register by{" "}
        {formatDate(c.registration_deadline)}{c.max_events_per_student ? ` · Up to ${c.max_events_per_student} events per child` : ""}</p>
      {c.rules ? <details className="rules"><summary>Competition rules</summary><p className="prewrap">{c.rules}</p></details> : null}

      {blocker ? <Alert tone="info">{blocker}</Alert> : !children.length ? <EmptyState message="No children found." /> : (
        <form onSubmit={onSubmit} noValidate aria-label="Competition registration" className="register-form">
          {generalError ? (
            <Alert tone="danger" title="The registration was not accepted."><p>{generalError}</p></Alert>
          ) : null}
          {otherErrors.length ? (
            <Alert tone="danger" title="The registration was not accepted.">
              {otherErrors.map(([k, msgs]) => <p key={k}>{(msgs ?? []).join(" ")}</p>)}
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

          {fields.length ? (
            <section aria-labelledby="questions-heading" className="form-questions">
              <h2 id="questions-heading">3. {c.name}: registration questions</h2>
              <DynamicForm fields={fields} answers={answers} errors={errors} onChange={setAnswer} />
            </section>
          ) : null}

          <TextField label={`${fields.length ? "4" : "3"}. Note for the academy (optional)`} value={notes}
                     maxLength={255} errors={errors.notes} onChange={(e) => setNotes(e.target.value)} />

          <p className="fee-line" aria-live="polite">
            {event ? <>Fee for this entry: <strong><Money value={event.fee} /></strong></> : "Choose an event to see its fee."}
          </p>
          <Alert tone="info" title="Payment is required to confirm the entry">
            <p>Registering issues a competition invoice for the event fee, due today. Pay the academy, then upload
              your proof of payment on the invoice. The entry stays “Awaiting payment” until the academy has recorded
              full payment, and is then confirmed automatically. Refunds follow the academy’s rules for this
              competition.</p>
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
            {fields.length ? (
              <dl className="answer-list">
                {fields.map((f) => (
                  <div key={f.key}><dt>{f.label}</dt><dd className="prewrap">{describeAnswer(f, answers[f.key])}</dd></div>
                ))}
              </dl>
            ) : null}
            <p>An invoice for <Money value={event.fee} /> will be issued, due today. The entry is confirmed once it is
              paid in full.</p>
          </>
        ) : null}
        onCancel={() => setConfirming(false)}
        onConfirm={() => child && event && register.mutate({
          student: child.id, event: event.id, competition: c.id, notes: notes.trim(),
          responses: toResponses(fields, answers),
        })}
      />
    </>
  );
}

function RegistrationDone({ registration: r, competitionName, competitionId, crumbs }: {
  registration: CompetitionRegistration;
  competitionName: string;
  competitionId: number;
  crumbs: { label: string; to?: string }[];
}) {
  const { endpoints } = useServices();
  const invoice = useQuery({
    queryKey: parentKeys.invoice(String(r.invoice?.id ?? "")),
    queryFn: ({ signal }) => endpoints.invoice(r.invoice!.id, signal),
    enabled: !!r.invoice,
  });
  return (
    <>
      <PageHeader title="Registration received" crumbs={crumbs} />
      <Alert tone={r.status === "CONFIRMED" ? "success" : "info"} title={`${r.student_name} – ${r.event_name}`}>
        <p>Status: <RegistrationStatusBadge status={r.status} /></p>
        {r.status === "PENDING" && r.invoice ? (
          <>
            <p>Invoice <Link to={`/parent/finance/invoices/${r.invoice.id}`}>{r.invoice.number}</Link> has been issued
              with <Money value={r.invoice.balance_due} /> due today.</p>
            <p>Pay the academy using the details below, then upload your proof of payment on the invoice. The
              academy checks it and records the payment; the entry is confirmed automatically once the invoice is
              paid in full. The app does not take payments itself.</p>
            <p><Link className="btn btn-primary" to={`/parent/finance/invoices/${r.invoice.id}`}>Pay and upload proof</Link></p>
          </>
        ) : r.status === "CONFIRMED" ? <p>This event has no fee, so the entry is confirmed.</p> : null}
      </Alert>
      {r.form_responses?.length ? (
        <section aria-labelledby="answers-heading" className="page-section">
          <h2 id="answers-heading">Your answers</h2>
          <AnswerList answers={r.form_responses} />
        </section>
      ) : null}
      {r.status === "PENDING" && invoice.data ? <PaymentInformation invoice={invoice.data} uploadHere={false} /> : null}
      <p><Link to={`/parent/competitions/${competitionId}`}>Back to {competitionName}</Link></p>
    </>
  );
}
