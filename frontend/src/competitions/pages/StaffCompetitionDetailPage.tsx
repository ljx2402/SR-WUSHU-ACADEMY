import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { CompetitionEvent, CompetitionEventInput, StaffCompetition } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { Money } from "../../domain/finance";
import { formatDate, formatDateTime, formatPeriod } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { CompetitionStatusBadge, DefinitionList, Section } from "../../parent/components";
import { ActionDialog, errorText } from "../../staff/components";
import { Button } from "../../ui/Button";
import { DataTable } from "../../ui/DataTable";
import { SelectField, TextField } from "../../ui/Field";
import { Alert, Badge, KpiCard } from "../../ui/primitives";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import {
  COMP_CRUMBS, COMP_ROOT, CompetitionNav, EVENT_TYPE, GENDER, ageText, competitionKeys, fieldErrorsOf, intOrNull,
  useStaffCompetition,
} from "../components";

/** Shared page frame for one competition: header, status and sub-navigation. */
export function CompetitionFrame({ competition, title, children, actions }: {
  competition: StaffCompetition; title?: string; children: ReactNode; actions?: ReactNode;
}) {
  const crumbs = title ? [...COMP_CRUMBS, { label: competition.name, to: `${COMP_ROOT}/${competition.id}` }] : COMP_CRUMBS;
  return (
    <>
      <PageHeader title={title ?? competition.name} crumbs={crumbs} actions={actions} />
      <p className="badge-row"><CompetitionStatusBadge status={competition.status} />
        {competition.registration_form.status === "PUBLISHED"
          ? <Badge tone="success">Form v{competition.registration_form.version} published</Badge>
          : <Badge tone="neutral">Registration form not published</Badge>}</p>
      <CompetitionNav id={competition.id} />
      {children}
    </>
  );
}

/** Loading / not-found / error handling shared by every page of one competition. */
export function WithCompetition({ title, children }: {
  title: string; children: (competition: StaffCompetition) => ReactNode;
}) {
  const { competitionId = "" } = useParams();
  const competition = useStaffCompetition(competitionId);
  if (competition.isPending) return (<><PageHeader title={title} crumbs={COMP_CRUMBS} /><LoadingState /></>);
  if (competition.isError) {
    return (<><PageHeader title={title} crumbs={COMP_CRUMBS} />
      {isApiError(competition.error) && competition.error.kind === "not_found"
        ? <NotFoundState message="Competition not found." />
        : <ErrorState error={competition.error} onRetry={() => competition.refetch()} />}</>);
  }
  return <>{children(competition.data)}</>;
}

export function StaffCompetitionDetailPage() {
  return <WithCompetition title="Competition">{(c) => <CompetitionOverview competition={c} />}</WithCompetition>;
}

function CompetitionOverview({ competition: c }: { competition: StaffCompetition }) {
  const me = useMe();
  const { endpoints } = useServices();
  const seesEntries = can(me, "competition.registrations.view_all");
  const manages = can(me, "competition.manage");
  const summary = useQuery({
    queryKey: competitionKeys.summary(String(c.id)),
    queryFn: ({ signal }) => endpoints.competitionSummary(c.id, signal),
    enabled: seesEntries,
  });
  const [editing, setEditing] = useState<CompetitionEvent | "new" | null>(null);
  const perEvent = new Map((summary.data?.events ?? []).map((e) => [e.id, e]));
  return (
    <CompetitionFrame competition={c}
                      actions={manages ? <Link className="btn btn-secondary" to={`${COMP_ROOT}/${c.id}/edit`}>Edit details</Link> : null}>
      <DefinitionList items={[
        ["Organiser", c.organiser || "—"],
        ["Dates", formatPeriod(c.start_date, c.end_date)],
        ["Venue", c.venue || "—"],
        ["Registration deadline", formatDate(c.registration_deadline)],
        ["Open for registration now", c.is_open ? "Yes" : "No"],
        ["Parents may register", c.allow_parent_registration ? "Yes" : "No (the academy registers)"],
        ["Parents may withdraw", c.allow_parent_withdrawal ? "Yes, until the deadline" : "No (the academy withdraws)"],
        ["Events per student", c.max_events_per_student ? `Up to ${c.max_events_per_student}` : "No limit"],
        ["Ages counted on", c.age_reference_date ? formatDate(c.age_reference_date) : `${formatDate(c.start_date)} (start date)`],
        ["Form published", c.registration_form.published_at ? formatDateTime(c.registration_form.published_at) : "Never"],
      ]} />

      {seesEntries ? (
        <Section title="Entries">
          {summary.isPending ? <LoadingState /> : summary.isError ? (
            <ErrorState error={summary.error} onRetry={() => summary.refetch()} />
          ) : (
            <section aria-label="Entries at a glance" className="grid grid-kpi">
              <KpiCard label="Awaiting payment" value={summary.data.registrations.PENDING}
                       hint="Confirmed automatically once the fee is paid" />
              <KpiCard label="Confirmed" value={summary.data.registrations.CONFIRMED} />
              <KpiCard label="Withdrawn / rejected"
                       value={summary.data.registrations.WITHDRAWN + summary.data.registrations.REJECTED} />
              <KpiCard label="Results recorded" value={summary.data.results.recorded}
                       hint={`${summary.data.results.confirmed_without_result} confirmed without a result`} />
            </section>
          )}
          <p><Link className="tap-link" to={`${COMP_ROOT}/${c.id}/participants`}>View participants</Link></p>
        </Section>
      ) : null}

      <Section title="Events" actions={manages ? <Button variant="secondary" onClick={() => setEditing("new")}>Add event</Button> : null}>
        <DataTable<CompetitionEvent>
          caption={`Events at ${c.name}`}
          rows={c.events}
          rowKey={(e) => e.id}
          emptyMessage="No events yet."
          className="staff-table"
          columns={[
            { key: "name", header: "Event", render: (e) => <>{e.name}<br /><span className="muted">{EVENT_TYPE[e.event_type] ?? e.event_type}</span></> },
            { key: "gender", header: "Gender", render: (e) => GENDER[e.gender] ?? e.gender },
            { key: "age", header: "Age", render: ageText },
            { key: "weight", header: "Weight class", priority: "secondary", render: (e) => e.weight_class || "—" },
            { key: "fee", header: "Fee", align: "end", render: (e) => (Number(e.fee) > 0 ? <Money value={e.fee} /> : "Free") },
            ...(seesEntries ? [{ key: "entries", header: "Entries", align: "end" as const, render: (e: CompetitionEvent) => {
              const row = perEvent.get(e.id);
              if (!row) return "—";
              return `${row.entries}${e.max_entries ? ` / ${e.max_entries}` : ""}`;
            } }] : []),
            ...(manages ? [{ key: "edit", header: "Actions", render: (e: CompetitionEvent) => (
              <Button variant="ghost" onClick={() => setEditing(e)} aria-label={`Edit ${e.name}`}>Edit</Button>) }] : []),
          ]}
        />
        <p className="muted">Age and gender eligibility is checked by the backend when a student is registered.</p>
      </Section>

      {c.description ? <Section title="Description"><p className="prewrap">{c.description}</p></Section> : null}
      {c.rules ? <Section title="Rules"><p className="prewrap">{c.rules}</p></Section> : null}

      {editing ? <EventDialog competitionId={c.id} event={editing === "new" ? null : editing}
                              onClose={() => setEditing(null)} /> : null}
    </CompetitionFrame>
  );
}

const EMPTY_EVENT = { event_type: "OTHER", name: "", gender: "OPEN", min_age: "", max_age: "", weight_class: "", fee: "0.00",
                      max_entries: "" };

function EventDialog({ competitionId, event, onClose }: { competitionId: number; event: CompetitionEvent | null; onClose: () => void }) {
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const [form, setForm] = useState(event ? {
    event_type: event.event_type, name: event.name, gender: event.gender as string,
    min_age: event.min_age?.toString() ?? "", max_age: event.max_age?.toString() ?? "", weight_class: event.weight_class,
    fee: event.fee, max_entries: event.max_entries?.toString() ?? "",
  } : EMPTY_EVENT);
  const save = useMutation({
    mutationFn: () => {
      const body: CompetitionEventInput = {
        competition: competitionId, event_type: form.event_type, name: form.name.trim(),
        gender: form.gender as CompetitionEventInput["gender"], min_age: intOrNull(form.min_age),
        max_age: intOrNull(form.max_age), weight_class: form.weight_class.trim(), fee: form.fee.trim() || "0",
        max_entries: intOrNull(form.max_entries),
      };
      return event ? endpoints.updateCompetitionEvent(event.id, body) : endpoints.createCompetitionEvent(body);
    },
    onSuccess: async () => {
      onClose();
      await queryClient.invalidateQueries({ queryKey: competitionKeys.all });
    },
  });
  const errors = fieldErrorsOf(save.error);
  const set = (name: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [name]: e.target.value });
  return (
    <ActionDialog open title={event ? `Edit ${event.name}` : "Add event"} submitLabel={event ? "Save event" : "Add event"}
                  onClose={onClose} onSubmit={() => save.mutate()} busy={save.isPending}
                  error={save.error ? errorText(save.error) : null}>
      <TextField label="Name" required value={form.name} onChange={set("name")} errors={errors.name} maxLength={120} />
      <SelectField label="Event type" value={form.event_type} onChange={set("event_type")} errors={errors.event_type}>
        {Object.entries(EVENT_TYPE).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </SelectField>
      <SelectField label="Gender" value={form.gender} onChange={set("gender")} errors={errors.gender}>
        {Object.entries(GENDER).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </SelectField>
      <div className="form-grid">
        <TextField label="Minimum age" inputMode="numeric" value={form.min_age} onChange={set("min_age")} errors={errors.min_age} />
        <TextField label="Maximum age" inputMode="numeric" value={form.max_age} onChange={set("max_age")} errors={errors.max_age} />
      </div>
      <TextField label="Weight class" value={form.weight_class} onChange={set("weight_class")} errors={errors.weight_class} />
      <div className="form-grid">
        <TextField label="Fee (RM)" inputMode="decimal" value={form.fee} onChange={set("fee")} errors={errors.fee}
                   hint="0 for a free event (confirmed at once)." />
        <TextField label="Maximum entries" inputMode="numeric" value={form.max_entries} onChange={set("max_entries")}
                   errors={errors.max_entries} hint="Empty for no limit." />
      </div>
      {event ? <Alert tone="info">A fee change applies to new registrations; existing entries keep the invoice they were issued.</Alert> : null}
    </ActionDialog>
  );
}
