import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { allPages } from "../../api/endpoints";
import { isApiError } from "../../api/errors";
import type { CoachingSession, StaffSessionSlot, TrainingSession } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { AttendanceStatusBadge, AttendanceSummary, SessionPhaseBadge } from "../../domain/attendance";
import { academyToday, addDays, formatDate, formatDateTime, formatTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DefinitionList, LoadMore, Section } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/Dialog";
import { DataTable } from "../../ui/DataTable";
import { SelectField, TextAreaField, TextField } from "../../ui/Field";
import { Alert, Badge } from "../../ui/primitives";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import {
  ActionDialog, StaffSessionTable, errorText, staffKeys, useAllClasses, useAllCoaches, useDialog,
} from "../components";

const CRUMBS = [{ label: "Operations", to: "/staff/dashboard" }];
const VIEWS = [["today", "Today"], ["upcoming", "Upcoming"], ["past", "Past"], ["cancelled", "Cancelled"]] as const;
type View = (typeof VIEWS)[number][0];

function sessionQuery(view: View, date: string) {
  const today = academyToday();
  if (date) return { date, order: "asc" as const };
  switch (view) {
    case "today": return { date: today, order: "asc" as const };
    case "upcoming": return { start: addDays(today, 1), order: "asc" as const };
    case "past": return { end: addDays(today, -1) };
    case "cancelled": return { status: "CANCELLED" };
  }
}

function useFilters() {
  const [params, setParams] = useSearchParams();
  function setMany(values: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [name, value] of Object.entries(values)) {
      if (value) next.set(name, value); else next.delete(name);
    }
    setParams(next, { replace: true });
  }
  return { params, set: (name: string, value: string) => setMany({ [name]: value }), setMany };
}

function ClassCoachFilters({ params, set }: ReturnType<typeof useFilters>) {
  const classes = useAllClasses();
  const coaches = useAllCoaches();
  return (
    <>
      <SelectField label="Class" value={params.get("class") ?? ""} onChange={(e) => set("class", e.target.value)}>
        <option value="">All classes</option>
        {(classes.data?.rows ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </SelectField>
      <SelectField label="Coach" value={params.get("coach") ?? ""} onChange={(e) => set("coach", e.target.value)}>
        <option value="">All coaches</option>
        {(coaches.data?.rows ?? []).map((c) => <option key={c.id} value={c.id}>{c.full_name}</option>)}
      </SelectField>
    </>
  );
}

const num = (value: string | null) => (value ? Number(value) : undefined);

export function StaffSessionsPage() {
  const { endpoints } = useServices();
  const filters = useFilters();
  const { params, set } = filters;
  const view = (VIEWS.find(([v]) => v === params.get("view"))?.[0] ?? "today") as View;
  const date = params.get("date") ?? "";
  const query = { ...sessionQuery(view, date), class: num(params.get("class")), coach: num(params.get("coach")) };
  const sessions = usePagedList<CoachingSession>(staffKeys.sessions(query),
    (page, signal) => endpoints.staffSessions({ ...query, page }, signal));
  return (
    <>
      <PageHeader title="Sessions" crumbs={CRUMBS} description="Every session, with its coaches and attendance." />
      <fieldset className="segmented">
        <legend>Show</legend>
        {VIEWS.map(([value, label]) => (
          <label key={value}>
            <input type="radio" name="session-view" value={value} checked={!date && view === value}
                   onChange={() => filters.setMany({ view: value, date: "" })} />
            <span>{label}</span>
          </label>
        ))}
      </fieldset>
      <div className="filter-bar" role="group" aria-label="Filter sessions">
        <TextField label="Date" type="date" value={date} onChange={(e) => set("date", e.target.value)} />
        <ClassCoachFilters {...filters} />
      </div>
      {sessions.isPending ? <LoadingState /> : sessions.isError ? (
        <ErrorState error={sessions.error} onRetry={() => sessions.refetch()} />
      ) : (
        <>
          <StaffSessionTable sessions={sessions.rows} caption="Sessions" empty="No sessions match." />
          <LoadMore shown={sessions.rows.length} total={sessions.count} hasMore={!!sessions.hasNextPage}
                    loading={sessions.isFetchingNextPage} onMore={() => sessions.fetchNextPage()} />
        </>
      )}
    </>
  );
}

type SessionDialog = "reschedule" | "cancel" | "reinstate" | "reassign" | "substitute" | "revoke";

const SLOT_STATUS: Record<string, string> = {
  ASSIGNED: "Assigned", REPLACED: "Replaced by a substitute", ABSENT: "Did not attend", REVOKED: "Revoked",
  CANCELLED: "Cancelled (session cancelled)",
};

export function StaffSessionDetailPage() {
  const { sessionId = "" } = useParams();
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const session = useQuery({ queryKey: staffKeys.session(sessionId),
                             queryFn: ({ signal }) => endpoints.session(sessionId, signal) });
  const sheet = useQuery({ queryKey: staffKeys.sheet(sessionId), queryFn: ({ signal }) => endpoints.attendanceSheet(sessionId, signal),
                           enabled: can(me, "attendance.view_all") && session.isSuccess });
  const manage = can(me, "sessions.manage");
  const coaches = useAllCoaches(manage);
  const dialog = useDialog<SessionDialog>();
  const [form, setForm] = useState<Record<string, string>>({});
  const [done, setDone] = useState<string | null>(null);
  const id = Number(sessionId);

  const action = useMutation({
    mutationFn: async ({ name, reason }: { name: SessionDialog; reason: string }) => {
      if (name === "cancel") return endpoints.cancelSession(id, reason);
      if (name === "reinstate") return endpoints.reinstateSession(id, reason);
      if (name === "reschedule") {
        return endpoints.rescheduleSession(id, { date: form.date || undefined, start_time: form.start_time || undefined,
                                                 end_time: form.end_time || undefined, reason });
      }
      if (name === "reassign") {
        return endpoints.reassignCoach(id, { from_coach: Number(form.from_coach), to_coach: Number(form.to_coach), reason });
      }
      if (name === "substitute") {
        return endpoints.assignSubstitute(id, { substitute: Number(form.substitute),
                                                ...(form.replaces ? { replaces: Number(form.replaces) } : {}), reason });
      }
      return endpoints.revokeSubstitute(id, { substitute: Number(form.substitute), reason });
    },
    onSuccess: async (_, { name }) => {
      dialog.close();
      setDone({ cancel: "Session cancelled.", reinstate: "Session reinstated.", reschedule: "Session rescheduled.",
                reassign: "Coach reassigned.", substitute: "Substitute assigned.", revoke: "Substitute revoked." }[name]);
      await queryClient.invalidateQueries({ queryKey: staffKeys.all });
    },
    onError: (e) => dialog.setError(errorText(e)),
  });
  const field = (name: string) => ({ value: form[name] ?? "", onChange: (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [name]: e.target.value })) });

  const crumbs = [...CRUMBS, { label: "Sessions", to: "/staff/sessions" }];
  if (session.isPending) return (<><PageHeader title="Session" crumbs={crumbs} /><LoadingState /></>);
  if (session.isError) {
    return (<><PageHeader title="Session" crumbs={crumbs} />
      {isApiError(session.error) && session.error.kind === "not_found" ? <NotFoundState message="Session not found." />
        : <ErrorState error={session.error} onRetry={() => session.refetch()} />}</>);
  }
  const s = session.data as TrainingSession & { coaches: StaffSessionSlot[] };
  const cancelled = s.status === "CANCELLED";
  const regular = s.coaches.filter((c) => c.role === "REGULAR");
  const substitutes = s.coaches.filter((c) => c.role === "SUBSTITUTE");
  const activeSubstitute = substitutes.find((c) => c.status === "ASSIGNED");
  const activeCoaches = (coaches.data?.rows ?? []).filter((c) => c.is_active);

  function open(name: SessionDialog, values: Record<string, string> = {}) {
    setDone(null);
    setForm(values);
    dialog.show(name);
  }

  return (
    <>
      <PageHeader title={s.class_name} crumbs={crumbs} description={<SessionPhaseBadge phase={s.phase} />}
                  actions={manage ? (
                    <>
                      {!cancelled && s.phase === "UPCOMING" ? (
                        <Button variant="secondary" onClick={() => open("reschedule", { date: s.date, start_time: s.start_time.slice(0, 5),
                          end_time: s.end_time.slice(0, 5) })}>Reschedule</Button>) : null}
                      {cancelled ? <Button variant="secondary" onClick={() => open("reinstate")}>Reinstate</Button>
                        : <Button variant="danger" onClick={() => open("cancel")}>Cancel session</Button>}
                    </>
                  ) : null} />
      <div role="status" className="status-slot">{done ? <Alert tone="success" title={done} /> : null}</div>
      {cancelled ? <Alert tone="warning" title="This session is cancelled." /> : null}

      <Section title="Session">
        <DefinitionList items={[
          ["Date", formatDate(s.date)], ["Time", `${formatTime(s.start_time)}–${formatTime(s.end_time)}`],
          ["Venue", s.venue], ["Status", cancelled ? "Cancelled" : "Scheduled"],
        ]} />
        {s.notes ? <p className="prewrap"><strong>Notes:</strong> {s.notes}</p> : null}
      </Section>

      <Section title="Coaches" actions={manage && !cancelled ? (
        <>
          {s.phase === "UPCOMING" && regular.some((c) => c.status === "ASSIGNED") ? (
            <Button variant="secondary" onClick={() => open("reassign", { from_coach: String(regular.find((c) => c.status === "ASSIGNED")!.coach) })}>
              Reassign coach</Button>) : null}
          {activeSubstitute ? (
            <Button variant="secondary" onClick={() => open("revoke", { substitute: String(activeSubstitute.coach) })}>Revoke substitute</Button>
          ) : s.phase !== "COMPLETED" ? (
            <Button variant="secondary" onClick={() => open("substitute", { replaces: String(regular.find((c) => c.status === "ASSIGNED")?.coach ?? "") })}>
              Assign substitute</Button>
          ) : null}
        </>) : null}>
        <DataTable<StaffSessionSlot>
          caption="Coaches of this session"
          rows={s.coaches}
          rowKey={(c) => c.id}
          emptyMessage="No coach assigned."
          columns={[
            { key: "coach", header: "Coach", render: (c) => c.coach_name },
            { key: "role", header: "Role", render: (c) => (c.role === "SUBSTITUTE" ? <Badge tone="info">Substitute</Badge> : "Regular") },
            { key: "status", header: "Status", render: (c) => SLOT_STATUS[c.status] ?? c.status },
            { key: "window", header: "Access", priority: "secondary", render: (c) => (c.role === "SUBSTITUTE" && c.access_ends_at
              ? `${formatDateTime(c.access_starts_at)} – ${formatDateTime(c.access_ends_at)}` : "—") },
          ]}
        />
      </Section>

      {sheet.data ? (
        <Section title="Attendance" actions={<Link className="btn btn-secondary" to={`/staff/sessions/${s.id}/attendance`}>
          {can(me, ["attendance.take_any", "attendance.correct"]) ? "Open attendance sheet" : "View attendance"}</Link>}>
          <AttendanceSummary summary={sheet.data.summary} />
          <DataTable
            caption="Expected students"
            rows={sheet.data.sheet}
            rowKey={(r) => r.student}
            emptyMessage="No students are expected at this session."
            columns={[
              { key: "name", header: "Student", render: (r) => <Link className="tap-link" to={`/staff/students/${r.student}`}>{r.student_name}</Link> },
              { key: "status", header: "Attendance", render: (r) => <AttendanceStatusBadge status={r.status} /> },
              { key: "remarks", header: "Remark", priority: "secondary", render: (r) => r.remarks || "—" },
            ]}
          />
        </Section>
      ) : sheet.isError ? <ErrorState error={sheet.error} onRetry={() => sheet.refetch()} /> : null}

      <ActionDialog open={dialog.open === "reschedule"} title="Reschedule session" submitLabel="Reschedule"
                    busy={action.isPending} error={dialog.error} onClose={dialog.close}
                    onSubmit={() => action.mutate({ name: "reschedule", reason: (form.reason ?? "").trim() })}>
        <p className="muted">Now: {formatDate(s.date)}, {formatTime(s.start_time)}–{formatTime(s.end_time)}. Only an upcoming
          session without attendance, substitutes or payroll can be moved; the academy system checks this.</p>
        <TextField label="New date" type="date" {...field("date")} />
        <TextField label="Start time" type="time" {...field("start_time")} />
        <TextField label="End time" type="time" {...field("end_time")} />
        <TextAreaField label="Reason" required rows={2} hint="Required. Stored in the audit log." {...field("reason")} />
      </ActionDialog>
      <ConfirmDialog open={dialog.open === "cancel"} title="Cancel session" confirmLabel="Cancel session" requireReason
                     message={<p>Cancel {s.class_name} on {formatDate(s.date)}? An active substitute's access ends, and no
                       attendance can be taken. It can be reinstated later.</p>}
                     busy={action.isPending} error={dialog.error} onCancel={dialog.close}
                     onConfirm={(reason) => action.mutate({ name: "cancel", reason })} />
      <ConfirmDialog open={dialog.open === "reinstate"} title="Reinstate session" confirmLabel="Reinstate" tone="primary"
                     requireReason message={<p>Reinstate {s.class_name} on {formatDate(s.date)}? A cancelled substitute stays
                       cancelled; assign one again if needed.</p>}
                     busy={action.isPending} error={dialog.error} onCancel={dialog.close}
                     onConfirm={(reason) => action.mutate({ name: "reinstate", reason })} />
      <ActionDialog open={dialog.open === "reassign"} title="Reassign regular coach" submitLabel="Reassign"
                    busy={action.isPending} error={dialog.error} onClose={dialog.close}
                    onSubmit={() => action.mutate({ name: "reassign", reason: (form.reason ?? "").trim() })}>
        <SelectField label="Replace" {...field("from_coach")}>
          {regular.filter((c) => c.status === "ASSIGNED").map((c) => <option key={c.id} value={c.coach}>{c.coach_name}</option>)}
        </SelectField>
        <SelectField label="With coach" required {...field("to_coach")}>
          <option value="">Choose a coach</option>
          {activeCoaches.map((c) => <option key={c.id} value={c.id}>{c.full_name}</option>)}
        </SelectField>
        <TextAreaField label="Reason" required rows={2} hint="Required. For an absence on the day, assign a substitute instead." {...field("reason")} />
      </ActionDialog>
      <ActionDialog open={dialog.open === "substitute"} title="Assign substitute" submitLabel="Assign substitute"
                    busy={action.isPending} error={dialog.error} onClose={dialog.close}
                    onSubmit={() => action.mutate({ name: "substitute", reason: (form.reason ?? "").trim() })}>
        <SelectField label="Substitute coach" required {...field("substitute")}>
          <option value="">Choose a coach</option>
          {activeCoaches.map((c) => <option key={c.id} value={c.id}>{c.full_name}</option>)}
        </SelectField>
        <SelectField label="Covering for" {...field("replaces")}>
          <option value="">Nobody (extra coach)</option>
          {regular.filter((c) => c.status === "ASSIGNED").map((c) => <option key={c.id} value={c.coach}>{c.coach_name}</option>)}
        </SelectField>
        <TextAreaField label="Reason" required rows={2} hint="Required. One active substitute per session; access is limited to this session's window." {...field("reason")} />
      </ActionDialog>
      <ConfirmDialog open={dialog.open === "revoke"} title="Revoke substitute" confirmLabel="Revoke" requireReason
                     message={<p>Revoke {activeSubstitute?.coach_name}'s authorization for this session? Their access ends now.</p>}
                     busy={action.isPending} error={dialog.error} onCancel={dialog.close}
                     onConfirm={(reason) => action.mutate({ name: "revoke", reason })} />
    </>
  );
}

/** Attendance monitoring: the backend's state and counts per session (nothing recalculated). */
export function StaffAttendancePage() {
  const { endpoints } = useServices();
  const filters = useFilters();
  const { params, set } = filters;
  const today = academyToday();
  const start = params.get("start") ?? addDays(today, -6);
  const end = params.get("end") ?? today;
  const incomplete = params.get("incomplete") === "1";
  const showCancelled = params.get("cancelled") === "1";
  const query = { start, end, class: num(params.get("class")), coach: num(params.get("coach")) };
  const sessions = useQuery({
    queryKey: staffKeys.sessions({ monitoring: true, ...query }),
    queryFn: ({ signal }) => allPages((page) => endpoints.staffSessions({ ...query, page }, signal), 10),
  });
  const rows = (sessions.data?.rows ?? []).filter((s) =>
    (showCancelled || s.attendance.state !== "CANCELLED")
    && (!incomplete || ((s.attendance.state === "OPEN" || s.attendance.state === "LOCKED") && s.attendance.unmarked > 0)));
  return (
    <>
      <PageHeader title="Attendance" crumbs={CRUMBS}
                  description="Attendance per session. Numbers come from the attendance records; students not marked are never counted as absent." />
      <div className="filter-bar" role="group" aria-label="Filter attendance">
        <TextField label="From" type="date" value={start} onChange={(e) => set("start", e.target.value)} />
        <TextField label="To" type="date" value={end} onChange={(e) => set("end", e.target.value)} />
        <ClassCoachFilters {...filters} />
        <label className="check-field"><input type="checkbox" checked={incomplete}
          onChange={(e) => set("incomplete", e.target.checked ? "1" : "")} /> Incomplete only</label>
        <label className="check-field"><input type="checkbox" checked={showCancelled}
          onChange={(e) => set("cancelled", e.target.checked ? "1" : "")} /> Show cancelled</label>
      </div>
      {sessions.isPending ? <LoadingState /> : sessions.isError ? (
        <ErrorState error={sessions.error} onRetry={() => sessions.refetch()} />
      ) : (
        <>
          {!sessions.data.complete ? <Alert tone="warning" title={`Only the first ${sessions.data.rows.length} sessions are shown; narrow the dates.`} /> : null}
          <StaffSessionTable sessions={rows} caption="Attendance by session" attendanceLink
                             empty={incomplete ? "No incomplete attendance in this period." : "No sessions in this period."} />
        </>
      )}
    </>
  );
}
