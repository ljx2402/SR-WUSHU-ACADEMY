import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { StaffGuardian, StaffStudent, StaffStudentRow } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { formatDate, formatDateTime, maskIdentifier } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DefinitionList, LoadMore, Section, STUDENT_STATUS, StudentStatusBadge } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { Button } from "../../ui/Button";
import { DataTable, type Column } from "../../ui/DataTable";
import { SelectField, TextAreaField, TextField } from "../../ui/Field";
import { Alert } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { ActionDialog, AdminLink, errorText, staffKeys, useAllClasses, useDialog } from "../components";

const CRUMBS = [{ label: "Operations", to: "/staff/dashboard" }];

/** Student list: minimal rows from the backend (no IC, contact details or medical notes). */
export function StaffStudentsPage() {
  const me = useMe();
  const { endpoints } = useServices();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("search") ?? "");
  const query = {
    search: params.get("search") || undefined,
    status: params.get("status") || undefined,
    class: params.get("class") ? Number(params.get("class")) : undefined,
  };
  const classes = useAllClasses(can(me, "classes.view_all"));
  const students = usePagedList<StaffStudentRow>(staffKeys.students(query),
    (page, signal) => endpoints.staffStudents({ ...query, page }, signal));
  const directory = !can(me, "students.view_all");

  function set(name: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value); else next.delete(name);
    setParams(next, { replace: true });
  }
  function onSearch(e: FormEvent) {
    e.preventDefault();
    set("search", search.trim());
  }

  const columns: Column<StaffStudentRow>[] = [
    { key: "name", header: "Student", render: (r) => (
      <Link className="tap-link" to={`/staff/students/${r.id}`}>{r.full_name}{r.chinese_name ? ` ${r.chinese_name}` : ""}</Link>) },
    { key: "no", header: "Student no.", render: (r) => r.student_no },
    { key: "status", header: "Status", render: (r) => <StudentStatusBadge status={r.status} /> },
    directory
      ? { key: "family", header: "Family", render: (r) => r.family_name ?? "—" }
      : { key: "classes", header: "Classes", render: (r) => (r.current_classes ?? []).map((c) => c.name).join(", ") || "—" },
    ...(directory ? [] : [{ key: "age", header: "Age", priority: "secondary" as const, render: (r: StaffStudentRow) => r.age ?? "—" }]),
  ];

  return (
    <>
      <PageHeader title="Students" crumbs={CRUMBS}
                  description={directory ? "The student directory: names, status, family and guardian contacts."
                    : "Search students by name, Chinese name or student number."} />
      <form className="filter-bar" role="search" aria-label="Find students" onSubmit={onSearch}>
        <TextField label="Search" type="search" value={search} onChange={(e) => setSearch(e.target.value)}
                   placeholder="Name, Chinese name or student no." />
        <SelectField label="Status" value={params.get("status") ?? ""} onChange={(e) => set("status", e.target.value)}>
          <option value="">All statuses</option>
          {Object.entries(STUDENT_STATUS).map(([value, s]) => <option key={value} value={value}>{s.label}</option>)}
        </SelectField>
        {can(me, "classes.view_all") ? (
          <SelectField label="Class" value={params.get("class") ?? ""} onChange={(e) => set("class", e.target.value)}>
            <option value="">All classes</option>
            {(classes.data?.rows ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </SelectField>
        ) : null}
        <div className="filter-actions"><Button type="submit">Search</Button></div>
      </form>
      {students.isPending ? <LoadingState /> : students.isError ? (
        <ErrorState error={students.error} onRetry={() => students.refetch()} />
      ) : (
        <>
          <DataTable<StaffStudentRow>
            caption="Students"
            rows={students.rows}
            rowKey={(r) => r.id}
            emptyMessage="No students match."
            className="staff-table"
            columns={columns}
          />
          <LoadMore shown={students.rows.length} total={students.count} hasMore={!!students.hasNextPage}
                    loading={students.isFetchingNextPage} onMore={() => students.fetchNextPage()} />
        </>
      )}
    </>
  );
}

function isFullGuardian(g: NonNullable<StaffStudent["guardians"]>[number]): g is StaffGuardian {
  return "parent" in g;
}

type DialogName = "edit" | "status" | "enroll" | "end";

export function StaffStudentDetailPage() {
  const { studentId = "" } = useParams();
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const student = useQuery({ queryKey: staffKeys.student(studentId), queryFn: ({ signal }) => endpoints.staffStudent(studentId, signal) });
  const family = student.data?.family ?? null;
  const siblings = useQuery({
    queryKey: staffKeys.students({ family }),
    queryFn: ({ signal }) => endpoints.staffStudents({ family: family! }, signal),
    enabled: family !== null,
  });
  const history = useQuery({
    queryKey: staffKeys.studentHistory(studentId),
    queryFn: ({ signal }) => endpoints.studentHistory(studentId, signal),
    enabled: can(me, "students.history") && student.isSuccess,
  });
  const classes = useAllClasses(can(me, "students.manage"));
  const dialog = useDialog<DialogName>();
  const [showIc, setShowIc] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [ending, setEnding] = useState<number | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async (name: DialogName) => {
      const s = student.data!;
      if (name === "edit") {
        return endpoints.updateStudent(s.id, { full_name: form.full_name, chinese_name: form.chinese_name,
          gender: form.gender as "M" | "F", date_of_birth: form.date_of_birth || null, school: form.school });
      }
      if (name === "status") return endpoints.changeStudentStatus(s.id, form.status, form.reason ?? "");
      if (name === "enroll") return endpoints.enroll(s.id, Number(form.training_class), form.start_date || undefined);
      return endpoints.endEnrollment(ending!, form.reason ?? "", form.end_date || undefined);
    },
    onSuccess: async (_, name) => {
      dialog.close();
      setDone({ edit: "Student details saved.", status: "Status changed.", enroll: "Added to the class.",
                end: "Class membership ended." }[name]);
      await queryClient.invalidateQueries({ queryKey: staffKeys.all });
    },
    onError: (e) => dialog.setError(errorText(e)),
  });

  if (student.isPending) return (<><PageHeader title="Student" crumbs={[...CRUMBS, { label: "Students", to: "/staff/students" }]} /><LoadingState /></>);
  if (student.isError) {
    return (
      <>
        <PageHeader title="Student" crumbs={[...CRUMBS, { label: "Students", to: "/staff/students" }]} />
        {isApiError(student.error) && student.error.kind === "not_found"
          ? <NotFoundState message="Student not found." /> : <ErrorState error={student.error} onRetry={() => student.refetch()} />}
      </>
    );
  }
  const s = student.data;
  const full = "ic_number" in s;               // the backend returned the full record (students.view_all)
  const manage = can(me, "students.manage") && full;

  function open(name: DialogName, values: Record<string, string> = {}) {
    setDone(null);
    setForm(values);
    dialog.show(name);
  }
  const field = (name: string) => ({ value: form[name] ?? "", onChange: (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [name]: e.target.value })) });

  return (
    <>
      <PageHeader title={s.full_name} crumbs={[...CRUMBS, { label: "Students", to: "/staff/students" }]}
                  description={<><StudentStatusBadge status={s.status} /> <span className="muted">{s.student_no}</span></>}
                  actions={manage ? (
                    <>
                      <Button variant="secondary" onClick={() => open("edit", { full_name: s.full_name,
                        chinese_name: s.chinese_name, gender: s.gender ?? "M", date_of_birth: s.date_of_birth ?? "",
                        school: s.school ?? "" })}>Edit details</Button>
                      <Button variant="secondary" onClick={() => open("status", { status: s.status })}>Change status</Button>
                    </>
                  ) : null} />
      <div role="status" className="status-slot">{done ? <Alert tone="success" title={done} /> : null}</div>

      <Section title="Student">
        <DefinitionList items={[
          ["Student no.", s.student_no],
          ["Name", s.full_name],
          ["Chinese name", s.chinese_name],
          ...(full ? [
            ["Gender", s.gender === "F" ? "Female" : "Male"],
            ["Date of birth", s.date_of_birth ? formatDate(s.date_of_birth) : ""],
            ["Age", s.age ?? ""],
            ["Joined", s.join_date ? formatDate(s.join_date) : ""],
            ["School", s.school],
          ] as [string, string | number][] : []),
          ["Status", <StudentStatusBadge key="st" status={s.status} />],
        ]} />
      </Section>

      {full ? (
        <Section title="Classes" actions={manage ? <Button variant="secondary" onClick={() => open("enroll")}>Add to a class</Button> : null}>
          <DataTable
            caption="Current classes"
            rows={s.current_classes ?? []}
            rowKey={(e) => e.id}
            emptyMessage="Not in a class at the moment."
            columns={[
              { key: "class", header: "Class", render: (e) => <Link className="tap-link" to={`/staff/classes/${e.training_class}`}>{e.class_name}</Link> },
              { key: "since", header: "Since", render: (e) => formatDate(e.start_date) },
              { key: "coach", header: "Coach", priority: "secondary", render: (e) => e.coach_name ?? "—" },
              ...(manage ? [{ key: "end", header: "Action", render: (e: { id: number; class_name: string }) => (
                <Button variant="ghost" onClick={() => { setEnding(e.id); open("end", {}); }}>End membership</Button>) }] : []),
            ]}
          />
        </Section>
      ) : null}

      <Section title="Family and guardians">
        <DefinitionList items={[["Family", s.family_name ?? ""]]} />
        {siblings.data && siblings.data.results.length > 1 ? (
          <p>Students in this family:{" "}
            {siblings.data.results.map((r, i) => (
              <span key={r.id}>{i ? ", " : ""}{r.id === s.id ? r.full_name : <Link to={`/staff/students/${r.id}`}>{r.full_name}</Link>}</span>
            ))}
          </p>
        ) : null}
        {s.guardians?.length ? (
          <ul className="plain-list guardian-list">
            {s.guardians.map((g, i) => isFullGuardian(g) ? (
              <li key={g.id}>
                <strong>{g.parent.full_name}</strong> ({g.relationship.toLowerCase()})
                {g.is_primary_contact ? <span className="badge badge-info">Primary contact</span> : null}
                {g.is_emergency_contact ? <span className="badge badge-warning">Emergency contact</span> : null}
                <br /><span className="muted">{[g.parent.phone, g.parent.email].filter(Boolean).join(" · ") || "No contact details"}</span>
              </li>
            ) : (
              <li key={i}><strong>{g.name}</strong> ({g.relationship.toLowerCase()}) <span className="muted">· {g.phone || "—"}</span></li>
            ))}
          </ul>
        ) : <EmptyState message="No guardians recorded." />}
      </Section>

      {full ? (
        <Section title="Personal details">
          <DefinitionList items={[
            ["IC / passport", s.ic_number ? (
              <span key="ic">{showIc ? s.ic_number : maskIdentifier(s.ic_number)}{" "}
                <Button variant="ghost" onClick={() => setShowIc((v) => !v)} aria-pressed={showIc}>{showIc ? "Hide" : "Show"}</Button>
              </span>) : ""],
            ["Nationality", s.nationality ?? ""],
            ["Phone", s.phone ?? ""],
            ["Email", s.email ?? ""],
            ["Address", s.address ?? ""],
          ]} />
        </Section>
      ) : null}
      {full ? (
        <Section title="Health and safety">
          <p className="prewrap">{s.medical_notes?.trim() ? s.medical_notes : "Nothing recorded."}</p>
        </Section>
      ) : null}

      {history.data ? (
        <Section title="Recent changes">
          <DataTable
            caption="Recent changes to this student"
            rows={history.data.changes.slice(0, 8)}
            rowKey={(c) => c.id}
            emptyMessage="No changes recorded."
            columns={[
              { key: "when", header: "When", render: (c) => formatDateTime(c.timestamp) },
              { key: "who", header: "By", render: (c) => c.actor_name ?? "System" },
              { key: "what", header: "Change", render: (c) => `${c.action.toLowerCase()} · ${c.object_repr}` },
              { key: "why", header: "Reason", priority: "secondary", render: (c) => c.reason || "—" },
            ]}
          />
        </Section>
      ) : null}
      {full ? <p className="muted">Guardians, IC, contact details and medical notes are edited in <AdminLink path={`academy/student/${s.id}/change/`}>Django Admin</AdminLink>.</p> : null}

      <ActionDialog open={dialog.open === "edit"} title="Edit student details" submitLabel="Save" busy={mutation.isPending}
                    error={dialog.error} onClose={dialog.close} onSubmit={() => mutation.mutate("edit")}>
        <TextField label="Full name" required {...field("full_name")} />
        <TextField label="Chinese name" {...field("chinese_name")} />
        <SelectField label="Gender" {...field("gender")}><option value="M">Male</option><option value="F">Female</option></SelectField>
        <TextField label="Date of birth" type="date" {...field("date_of_birth")} />
        <TextField label="School" {...field("school")} />
      </ActionDialog>
      <ActionDialog open={dialog.open === "status"} title="Change status" submitLabel="Change status" busy={mutation.isPending}
                    error={dialog.error} onClose={dialog.close} onSubmit={() => mutation.mutate("status")}>
        <SelectField label="New status" {...field("status")}>
          {Object.entries(STUDENT_STATUS).map(([value, st]) => <option key={value} value={value}>{st.label}</option>)}
        </SelectField>
        <p className="muted">Withdrawn or graduated students leave their classes.</p>
        <TextAreaField label="Reason" rows={2} hint="Stored with the status history." {...field("reason")} />
      </ActionDialog>
      <ActionDialog open={dialog.open === "enroll"} title="Add to a class" submitLabel="Add" busy={mutation.isPending}
                    error={dialog.error} onClose={dialog.close} onSubmit={() => mutation.mutate("enroll")}>
        <SelectField label="Class" required {...field("training_class")}>
          <option value="">Choose a class</option>
          {(classes.data?.rows ?? []).filter((c) => c.is_active).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </SelectField>
        <TextField label="Start date" type="date" hint="Today if empty." {...field("start_date")} />
      </ActionDialog>
      <ActionDialog open={dialog.open === "end"} title="End class membership" submitLabel="End membership" tone="danger"
                    busy={mutation.isPending} error={dialog.error} onClose={dialog.close} onSubmit={() => mutation.mutate("end")}>
        <TextField label="End date" type="date" hint="Today if empty." {...field("end_date")} />
        <TextAreaField label="Reason" rows={2} {...field("reason")} />
      </ActionDialog>
    </>
  );
}
