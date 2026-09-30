import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";

import { allPages } from "../../api/endpoints";
import { isApiError } from "../../api/errors";
import type { ClassSchedule, TrainingClass } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { academyToday, formatDate, formatTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DefinitionList, Section, StudentStatusBadge } from "../../parent/components";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/Dialog";
import { DataTable } from "../../ui/DataTable";
import { SelectField, TextAreaField, TextField } from "../../ui/Field";
import { Alert, Badge } from "../../ui/primitives";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import {
  ActionDialog, AdminLink, StaffSessionTable, errorText, staffKeys, useAllClasses, useAllCoaches, useDialog,
} from "../components";

const CRUMBS = [{ label: "Operations", to: "/staff/dashboard" }];
const CATEGORIES = [["SCHOOL", "School"], ["ADDITIONAL", "Additional"], ["ELITE", "Elite"]] as const;

function scheduleText(schedules: ClassSchedule[]) {
  return schedules.map((s) => `${s.weekday_name.slice(0, 3)} ${formatTime(s.start_time)}–${formatTime(s.end_time)}`).join(", ") || "—";
}

const ActiveBadge = ({ active }: { active: boolean }) =>
  <Badge tone={active ? "success" : "neutral"}>{active ? "Active" : "Inactive"}</Badge>;

export function StaffClassesPage() {
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const show = params.get("show") ?? "active";
  const classes = useAllClasses();
  const programs = useQuery({ queryKey: staffKeys.programs, queryFn: ({ signal }) => endpoints.programs(signal),
                              enabled: can(me, "classes.manage") });
  const dialog = useDialog<"create">();
  const [form, setForm] = useState<Record<string, string>>({ category: "SCHOOL" });
  const create = useMutation({
    mutationFn: () => endpoints.createClass({ code: form.code ?? "", name: form.name ?? "", category: form.category,
      program: Number(form.program), venue: form.venue ?? "", capacity: form.capacity ? Number(form.capacity) : null }),
    onSuccess: async (created) => {
      dialog.close();
      await queryClient.invalidateQueries({ queryKey: staffKeys.all });
      navigate(`/staff/classes/${created.id}`);
    },
    onError: (e) => dialog.setError(errorText(e)),
  });
  const field = (name: string) => ({ value: form[name] ?? "", onChange: (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [name]: e.target.value })) });
  const rows = (classes.data?.rows ?? []).filter((c) => show === "all" || (show === "active") === c.is_active);

  return (
    <>
      <PageHeader title="Classes" crumbs={CRUMBS} description="Classes, their coaches, timetable and current students."
                  actions={can(me, "classes.manage") ? <Button onClick={() => dialog.show("create")}>New class</Button> : null} />
      <fieldset className="segmented">
        <legend>Show</legend>
        {[["active", "Active"], ["inactive", "Inactive"], ["all", "All"]].map(([value, label]) => (
          <label key={value}>
            <input type="radio" name="class-show" value={value} checked={show === value}
                   onChange={() => setParams({ show: value }, { replace: true })} />
            <span>{label}</span>
          </label>
        ))}
      </fieldset>
      {classes.isPending ? <LoadingState /> : classes.isError ? (
        <ErrorState error={classes.error} onRetry={() => classes.refetch()} />
      ) : (
        <DataTable<TrainingClass>
          caption="Classes"
          rows={rows}
          rowKey={(c) => c.id}
          emptyMessage="No classes."
          className="staff-table"
          columns={[
            { key: "name", header: "Class", render: (c) => <Link className="tap-link" to={`/staff/classes/${c.id}`}>{c.name}</Link> },
            { key: "coaches", header: "Coaches", render: (c) => c.current_coaches.map((x) => x.full_name).join(", ") || "No coach" },
            { key: "timetable", header: "Timetable", priority: "secondary", render: (c) => scheduleText(c.schedules) },
            { key: "students", header: "Students", align: "end", render: (c) => c.active_students ?? "—" },
            { key: "status", header: "Status", render: (c) => <ActiveBadge active={c.is_active} /> },
          ]}
        />
      )}
      <ActionDialog open={dialog.open === "create"} title="New class" submitLabel="Create class" busy={create.isPending}
                    error={dialog.error} onClose={dialog.close} onSubmit={() => create.mutate()}>
        <TextField label="Name" required {...field("name")} />
        <TextField label="Code" required hint="Short unique code, e.g. junior-taolu (letters, numbers, dashes)." {...field("code")} />
        <SelectField label="Category" {...field("category")}>
          {CATEGORIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </SelectField>
        <SelectField label="Program" required {...field("program")}>
          <option value="">Choose a program</option>
          {(programs.data ?? []).filter((p) => p.is_active).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </SelectField>
        <TextField label="Venue" {...field("venue")} />
        <TextField label="Capacity" inputMode="numeric" {...field("capacity")} />
      </ActionDialog>
    </>
  );
}

export function StaffClassDetailPage() {
  const { classId = "" } = useParams();
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const today = academyToday();
  const klass = useQuery({ queryKey: staffKeys.trainingClass(classId), queryFn: ({ signal }) => endpoints.trainingClass(classId, signal) });
  const roster = useQuery({ queryKey: staffKeys.roster(classId), queryFn: ({ signal }) => endpoints.classRoster(classId, signal),
                            enabled: can(me, "roster.view_all") && klass.isSuccess });
  const sessions = useQuery({
    queryKey: staffKeys.sessions({ class: classId, start: today }),
    queryFn: ({ signal }) => allPages((page) => endpoints.staffSessions({ class: Number(classId), start: today, order: "asc", page }, signal), 1),
    enabled: can(me, "sessions.view_all") && klass.isSuccess,
  });
  const dialog = useDialog<"edit" | "toggle">();
  const [form, setForm] = useState<Record<string, string>>({});
  const [done, setDone] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (body: Parameters<typeof endpoints.updateClass>[1]) => endpoints.updateClass(Number(classId), body),
    onSuccess: async (updated) => {
      dialog.close();
      setDone(`Saved: ${updated.name} is ${updated.is_active ? "active" : "inactive"}.`);
      await queryClient.invalidateQueries({ queryKey: staffKeys.all });
    },
    onError: (e) => dialog.setError(errorText(e)),
  });
  const field = (name: string) => ({ value: form[name] ?? "", onChange: (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [name]: e.target.value })) });

  const crumbs = [...CRUMBS, { label: "Classes", to: "/staff/classes" }];
  if (klass.isPending) return (<><PageHeader title="Class" crumbs={crumbs} /><LoadingState /></>);
  if (klass.isError) {
    return (<><PageHeader title="Class" crumbs={crumbs} />
      {isApiError(klass.error) && klass.error.kind === "not_found" ? <NotFoundState message="Class not found." />
        : <ErrorState error={klass.error} onRetry={() => klass.refetch()} />}</>);
  }
  const c = klass.data;
  const manage = can(me, "classes.manage");
  return (
    <>
      <PageHeader title={c.name} crumbs={crumbs} description={<ActiveBadge active={c.is_active} />}
                  actions={manage ? (
                    <>
                      <Button variant="secondary" onClick={() => { setDone(null); setForm({ name: c.name, venue: c.venue,
                        capacity: c.capacity?.toString() ?? "", description: c.description, category: c.category }); dialog.show("edit"); }}>
                        Edit class</Button>
                      <Button variant={c.is_active ? "danger" : "secondary"} onClick={() => { setDone(null); dialog.show("toggle"); }}>
                        {c.is_active ? "Deactivate" : "Activate"}</Button>
                    </>
                  ) : null} />
      <div role="status" className="status-slot">{done ? <Alert tone="success" title={done} /> : null}</div>
      <Section title="Class">
        <DefinitionList items={[
          ["Code", c.code], ["Category", CATEGORIES.find(([v]) => v === c.category)?.[1] ?? c.category],
          ["Program", c.program_name], ["Team", c.team_name ?? ""], ["Venue", c.venue],
          ["Capacity", c.capacity ?? ""], ["Current students", c.active_students ?? ""],
        ]} />
        {c.description ? <p className="prewrap">{c.description}</p> : null}
      </Section>
      <Section title="Coaches">
        <p>{c.current_coaches.map((x) => x.full_name).join(", ") || "No coach assigned."}</p>
        <p className="muted">Regular class coaches are assigned in <AdminLink path={`academy/trainingclass/${c.id}/change/`}>Django Admin</AdminLink>.
          A single session's coach is changed from the session page.</p>
      </Section>
      <Section title="Timetable">
        <DataTable<ClassSchedule>
          caption={`Timetable of ${c.name}`}
          rows={c.schedules}
          rowKey={(s) => s.id}
          emptyMessage="No timetable slots."
          columns={[
            { key: "day", header: "Day", render: (s) => s.weekday_name },
            { key: "time", header: "Time", render: (s) => `${formatTime(s.start_time)}–${formatTime(s.end_time)}` },
            { key: "venue", header: "Venue", render: (s) => s.venue || c.venue || "—" },
            { key: "from", header: "From", priority: "secondary", render: (s) => (s.effective_from ? formatDate(s.effective_from) : "—") },
          ]}
        />
      </Section>
      {roster.data ? (
        <Section title={`Roster (${roster.data.length})`}>
          <DataTable
            caption={`Current students of ${c.name}`}
            rows={roster.data}
            rowKey={(r) => r.id}
            emptyMessage="No current students."
            columns={[
              { key: "name", header: "Student", render: (r) => <Link className="tap-link" to={`/staff/students/${r.id}`}>{r.full_name}{r.chinese_name ? ` ${r.chinese_name}` : ""}</Link> },
              { key: "no", header: "Student no.", render: (r) => r.student_no },
              { key: "status", header: "Status", render: (r) => <StudentStatusBadge status={r.status} /> },
            ]}
          />
        </Section>
      ) : roster.isError ? <ErrorState error={roster.error} onRetry={() => roster.refetch()} /> : null}
      {sessions.data ? (
        <Section title="Upcoming sessions">
          <StaffSessionTable sessions={sessions.data.rows.slice(0, 10)} caption={`Upcoming sessions of ${c.name}`}
                             empty="No upcoming sessions." />
        </Section>
      ) : null}

      <ActionDialog open={dialog.open === "edit"} title="Edit class" submitLabel="Save" busy={save.isPending}
                    error={dialog.error} onClose={dialog.close}
                    onSubmit={() => save.mutate({ name: form.name, venue: form.venue, description: form.description,
                      category: form.category as TrainingClass["category"], capacity: form.capacity ? Number(form.capacity) : null })}>
        <TextField label="Name" required {...field("name")} />
        <SelectField label="Category" {...field("category")}>
          {CATEGORIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </SelectField>
        <TextField label="Venue" {...field("venue")} />
        <TextField label="Capacity" inputMode="numeric" {...field("capacity")} />
        <TextAreaField label="Description" rows={3} {...field("description")} />
      </ActionDialog>
      <ConfirmDialog open={dialog.open === "toggle"} title={c.is_active ? "Deactivate class" : "Activate class"}
                     message={c.is_active ? `${c.name} will no longer be offered for new students. Existing records are kept.`
                       : `${c.name} will be active again.`}
                     confirmLabel={c.is_active ? "Deactivate" : "Activate"} tone={c.is_active ? "danger" : "primary"}
                     busy={save.isPending} error={dialog.error} onCancel={dialog.close}
                     onConfirm={() => save.mutate({ is_active: !c.is_active })} />
    </>
  );
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

type Slot = ClassSchedule & { klass: TrainingClass };

/** The weekly timetable, from the classes' timetable slots (read only here). */
export function StaffTimetablePage() {
  const classes = useAllClasses();
  const coaches = useAllCoaches();
  const [params, setParams] = useSearchParams();
  const day = params.get("day") ?? "";
  const classId = params.get("class") ?? "";
  const coachId = params.get("coach") ?? "";
  const venue = params.get("venue") ?? "";
  const today = academyToday();
  const slots: Slot[] = (classes.data?.rows ?? []).flatMap((c) => c.schedules.map((s) => ({ ...s, klass: c })))
    .filter((s) => (!s.effective_to || s.effective_to >= today))
    .filter((s) => (day === "" || String(s.weekday) === day) && (classId === "" || String(s.klass.id) === classId)
      && (coachId === "" || s.klass.current_coaches.some((x) => String(x.id) === coachId))
      && (venue === "" || (s.venue || s.klass.venue) === venue))
    .sort((a, b) => a.weekday - b.weekday || a.start_time.localeCompare(b.start_time));
  const venues = [...new Set((classes.data?.rows ?? []).flatMap((c) => c.schedules.map((s) => s.venue || c.venue)).filter(Boolean))].sort();

  function set(name: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value); else next.delete(name);
    setParams(next, { replace: true });
  }

  return (
    <>
      <PageHeader title="Timetable" crumbs={CRUMBS}
                  description={<>The weekly timetable from each class's timetable slots. Slots are edited in <AdminLink path="academy/trainingclass/">Django Admin</AdminLink>; sessions are then generated from them.</>} />
      <div className="filter-bar" role="group" aria-label="Filter the timetable">
        <SelectField label="Day" value={day} onChange={(e) => set("day", e.target.value)}>
          <option value="">Every day</option>
          {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
        </SelectField>
        <SelectField label="Class" value={classId} onChange={(e) => set("class", e.target.value)}>
          <option value="">All classes</option>
          {(classes.data?.rows ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </SelectField>
        <SelectField label="Coach" value={coachId} onChange={(e) => set("coach", e.target.value)}>
          <option value="">All coaches</option>
          {(coaches.data?.rows ?? []).map((c) => <option key={c.id} value={c.id}>{c.full_name}</option>)}
        </SelectField>
        <SelectField label="Venue" value={venue} onChange={(e) => set("venue", e.target.value)}>
          <option value="">All venues</option>
          {venues.map((v) => <option key={v} value={v}>{v}</option>)}
        </SelectField>
      </div>
      {classes.isPending ? <LoadingState /> : classes.isError ? (
        <ErrorState error={classes.error} onRetry={() => classes.refetch()} />
      ) : (
        <DataTable<Slot>
          caption="Weekly timetable"
          rows={slots}
          rowKey={(s) => s.id}
          emptyMessage="No timetable slots match."
          className="staff-table"
          columns={[
            { key: "day", header: "Day", render: (s) => s.weekday_name },
            { key: "time", header: "Time", render: (s) => `${formatTime(s.start_time)}–${formatTime(s.end_time)}` },
            { key: "class", header: "Class", render: (s) => <Link className="tap-link" to={`/staff/classes/${s.klass.id}`}>{s.klass.name}</Link> },
            { key: "venue", header: "Venue", render: (s) => s.venue || s.klass.venue || "—" },
            { key: "coach", header: "Coach", render: (s) => s.klass.current_coaches.map((x) => x.full_name).join(", ") || "No coach" },
            { key: "status", header: "Status", render: (s) => <ActiveBadge active={s.klass.is_active} /> },
          ]}
        />
      )}
    </>
  );
}
