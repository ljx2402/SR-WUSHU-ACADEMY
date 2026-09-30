import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router";

import { isApiError } from "../../api/errors";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { AttendanceSummary } from "../../domain/attendance";
import { formatDate, maskIdentifier } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DataTable } from "../../ui/DataTable";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { DefinitionList, Section, StudentStatusBadge } from "../components";
import { useParent } from "../ParentContext";
import { parentKeys, useStudent } from "../queries";

const GENDER: Record<string, string> = { M: "Male", F: "Female" };

/**
 * A child's profile, showing only what /api/students/:id/ returns for a
 * parent's own child. Medical notes are shown because the backend returns them
 * to the child's own parents (and to no other parent). The IC number is shown
 * masked.
 */
export function StudentProfilePage() {
  const { studentId = "" } = useParams();
  const me = useMe();
  const { children } = useParent();
  const navigate = useNavigate();
  const { endpoints } = useServices();
  const student = useStudent(studentId);
  const summary = useQuery({
    queryKey: parentKeys.summary(Number(studentId)),
    queryFn: ({ signal }) => endpoints.attendanceSummary(studentId, {}, signal),
    enabled: student.isSuccess && can(me, "attendance.view_own_children"),
  });

  if (student.isPending) return <LoadingState />;
  if (student.isError) {
    if (isApiError(student.error) && student.error.kind === "not_found") {
      return (<><PageHeader title="Student not found" /><NotFoundState /></>);
    }
    return <ErrorState error={student.error} onRetry={() => student.refetch()} />;
  }
  const s = student.data;
  return (
    <>
      <PageHeader
        title={s.full_name}
        crumbs={[{ label: "Overview", to: "/parent/dashboard" }, { label: "My family", to: "/parent/family" }]}
        actions={children.length > 1 ? (
          <label className="student-selector">
            <span>Switch child</span>
            <select value={s.id} onChange={(e) => navigate(`/parent/students/${e.target.value}`)}>
              {children.map((c) => <option key={c.id} value={c.id}>{c.full_name}</option>)}
            </select>
          </label>
        ) : null}
      />
      <p><StudentStatusBadge status={s.status} /> <span className="muted">Student no. {s.student_no}</span></p>

      <div className="grid grid-2">
        <Section title="Details">
          <DefinitionList items={[
            ["Chinese name", s.chinese_name],
            ["Gender", GENDER[s.gender] ?? s.gender],
            ["Date of birth", s.date_of_birth ? formatDate(s.date_of_birth) : ""],
            ["Age", s.age ?? ""],
            ["IC / passport", s.ic_number ? maskIdentifier(s.ic_number) : ""],
            ["Nationality", s.nationality],
            ["School", s.school],
            ["Joined", s.join_date ? formatDate(s.join_date) : ""],
            ["Family", s.family_name],
          ]} />
        </Section>
        <Section title="Contact">
          <DefinitionList items={[["Phone", s.phone], ["Email", s.email], ["Address", s.address]]} />
          <h3>Emergency contacts</h3>
          {s.guardians?.length ? (
            <ul className="item-list">
              {s.guardians.map((g) => (
                <li key={`${g.name}-${g.phone}`}><span>{g.name} <span className="muted">({g.relationship.toLowerCase()})</span></span>
                  <span>{g.phone}</span></li>
              ))}
            </ul>
          ) : <p className="muted">None recorded.</p>}
        </Section>
      </div>

      <Section title="Classes">
        <DataTable
          caption={`${s.full_name}’s classes`}
          rows={s.current_classes ?? []}
          rowKey={(c) => c.id}
          emptyMessage="Not in a class at the moment."
          columns={[
            { key: "class", header: "Class", render: (c) => c.class_name },
            { key: "team", header: "Team", render: (c) => c.team_name ?? "—", priority: "secondary" },
            { key: "coach", header: "Coach", render: (c) => c.coach_name ?? "—" },
            { key: "since", header: "Since", render: (c) => formatDate(c.start_date) },
          ]}
        />
        <p><Link to={`/parent/schedule?student=${s.id}`}>See {s.full_name}’s schedule</Link></p>
      </Section>

      <Section title="Health and safety">
        <p className="muted">What the academy has recorded for coaches to know (allergies, injuries, conditions).
          To update it, please contact the academy office.</p>
        <p className="prewrap">{s.medical_notes?.trim() ? s.medical_notes : "Nothing recorded."}</p>
      </Section>

      {can(me, "attendance.view_own_children") ? (
        <Section title="Attendance" actions={<Link to={`/parent/attendance?student=${s.id}`}>Attendance history</Link>}>
          {summary.isPending ? <LoadingState /> : summary.isError ? <ErrorState error={summary.error} /> : (
            <AttendanceSummary summary={summary.data} />
          )}
        </Section>
      ) : null}
    </>
  );
}
