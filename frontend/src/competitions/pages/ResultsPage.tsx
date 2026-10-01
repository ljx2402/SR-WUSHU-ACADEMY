import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import type { CompetitionRegistration, CompetitionResultInput, StaffCompetition } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { ChargeStatusBadge } from "../../domain/finance";
import { useUrlFilters } from "../../finance/filters";
import { LoadMore } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { ActionDialog, errorText } from "../../staff/components";
import { Button } from "../../ui/Button";
import { DataTable } from "../../ui/DataTable";
import { SelectField, TextField } from "../../ui/Field";
import { Alert } from "../../ui/primitives";
import { ErrorState, LoadingState } from "../../ui/states";
import { COMP_ROOT, MEDAL, competitionKeys, fieldErrorsOf, intOrNull } from "../components";
import { resultText } from "./ParticipantPages";
import { CompetitionFrame, WithCompetition } from "./StaffCompetitionDetailPage";

/*
 * Results through the existing endpoint (/api/competition-results/, written
 * by competitions.services.record_result): only confirmed, paid entries can
 * have a result, so the list asks the backend for confirmed entries only. The
 * backend refuses anything else; results are never deleted.
 */

export function ResultsPage() {
  return <WithCompetition title="Results">{(c) => <Results competition={c} />}</WithCompetition>;
}

function Results({ competition: c }: { competition: StaffCompetition }) {
  const me = useMe();
  const { endpoints } = useServices();
  const { params, set } = useUrlFilters();
  const query = { competition: c.id, status: "CONFIRMED", event: params.get("event") ? Number(params.get("event")) : undefined,
                  result: params.get("result") || undefined };
  const list = usePagedList<CompetitionRegistration>(competitionKeys.registrations(query),
    (page, signal) => endpoints.staffRegistrations({ ...query, page }, signal));
  const [editing, setEditing] = useState<CompetitionRegistration | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const records = can(me, "competition.results.manage");
  return (
    <CompetitionFrame competition={c} title="Results">
      <p className="muted">Results can be recorded only for confirmed entries whose fee is paid. Entries awaiting payment,
        withdrawn or rejected are not listed.</p>
      <div className="filter-bar">
        <SelectField label="Event" value={params.get("event") ?? ""} onChange={(e) => set("event", e.target.value)}>
          <option value="">All events</option>
          {c.events.map((ev) => <option key={ev.id} value={ev.id}>{ev.name}</option>)}
        </SelectField>
        <SelectField label="Result" value={params.get("result") ?? ""} onChange={(e) => set("result", e.target.value)}>
          <option value="">All</option>
          <option value="yes">Recorded</option>
          <option value="no">Not recorded yet</option>
        </SelectField>
      </div>
      {notice ? <Alert tone="success" role="status" title={notice} /> : null}
      {list.isPending ? <LoadingState /> : list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : (
        <>
          <DataTable<CompetitionRegistration>
            caption={`Results for ${c.name}`}
            rows={list.rows}
            rowKey={(r) => r.id}
            emptyMessage="No confirmed entries match."
            className="staff-table"
            columns={[
              { key: "student", header: "Student", render: (r) => (
                <Link className="tap-link" to={`${COMP_ROOT}/${c.id}/participants/${r.id}`}>{r.student_name}</Link>) },
              { key: "event", header: "Event", render: (r) => r.event_name },
              { key: "payment", header: "Payment", priority: "secondary",
                render: (r) => (r.fee_status ? <ChargeStatusBadge status={r.fee_status} /> : "Free") },
              { key: "result", header: "Result", render: (r) => resultText(r.result) },
              ...(records ? [{ key: "action", header: "Actions", render: (r: CompetitionRegistration) => (
                <Button variant="ghost" onClick={() => setEditing(r)} aria-label={`${r.result ? "Edit" : "Record"} result for ${r.student_name}, ${r.event_name}`}>
                  {r.result ? "Edit result" : "Record result"}</Button>) }] : []),
            ]}
          />
          <LoadMore shown={list.rows.length} total={list.count} hasMore={!!list.hasNextPage}
                    loading={list.isFetchingNextPage} onMore={() => list.fetchNextPage()} />
        </>
      )}
      {editing ? <ResultDialog registration={editing} onClose={() => setEditing(null)}
                               onSaved={() => setNotice(`Result saved for ${editing.student_name}.`)} /> : null}
    </CompetitionFrame>
  );
}

function ResultDialog({ registration: r, onClose, onSaved }: {
  registration: CompetitionRegistration; onClose: () => void; onSaved: () => void;
}) {
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    placing: r.result?.placing?.toString() ?? "", medal: r.result?.medal || "NONE", score: r.result?.score ?? "",
    remarks: r.result?.remarks ?? "",
  });
  const save = useMutation({
    mutationFn: () => {
      const body: CompetitionResultInput = { placing: intOrNull(form.placing), medal: form.medal,
                                             score: form.score.trim() || null, remarks: form.remarks.trim() };
      return r.result ? endpoints.updateResult(r.result.id, body) : endpoints.createResult({ ...body, registration: r.id });
    },
    onSuccess: async () => {
      onClose();
      onSaved();
      await queryClient.invalidateQueries({ queryKey: competitionKeys.all });
    },
  });
  const errors = fieldErrorsOf(save.error);
  const set = (name: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [name]: e.target.value });
  return (
    <ActionDialog open title={`${r.result ? "Edit" : "Record"} result: ${r.student_name}`} submitLabel="Save result"
                  onClose={onClose} onSubmit={() => save.mutate()} busy={save.isPending} error={save.error ? errorText(save.error) : null}>
      <p>{r.event_name}</p>
      <div className="form-grid">
        <TextField label="Placing" inputMode="numeric" value={form.placing} onChange={set("placing")} errors={errors.placing} />
        <SelectField label="Medal" value={form.medal} onChange={set("medal")} errors={errors.medal}>
          {Object.entries(MEDAL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </SelectField>
        <TextField label="Score" inputMode="decimal" value={form.score} onChange={set("score")} errors={errors.score} />
      </div>
      <TextField label="Staff remarks" value={form.remarks} onChange={set("remarks")} errors={errors.remarks} maxLength={255}
                 hint="Internal: not shown in the Student Portal." />
    </ActionDialog>
  );
}
