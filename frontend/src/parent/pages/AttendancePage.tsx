import { Link } from "react-router";

import type { AttendanceRecord } from "../../api/types";
import { useServices } from "../../app/services";
import { AttendanceStatusBadge, AttendanceSummary } from "../../domain/attendance";
import { formatDate, formatPercentage } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DataTable } from "../../ui/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../../ui/states";
import { LoadMore, Section, StudentSelector } from "../components";
import { useSelectedStudent } from "../ParentContext";
import { parentKeys, useAttendanceSummaries, usePagedList } from "../queries";

/**
 * Attendance, read-only (parents cannot change attendance; there is no
 * endpoint for it). Percentages and counts are the backend's: UNMARKED
 * ("Not marked") is never counted as absent and is left out of the
 * percentage.
 */
export function AttendancePage() {
  const { endpoints } = useServices();
  const { children, selected, child } = useSelectedStudent();
  const ids = children.map((c) => c.id);
  const summaries = useAttendanceSummaries(ids);
  const studentId = typeof selected === "number" ? selected : undefined;
  const history = usePagedList<AttendanceRecord>(
    parentKeys.attendance(studentId ?? 0),
    (page, signal) => endpoints.attendance({ student: studentId, page }, signal),
    studentId !== undefined,
  );
  const selectedIndex = ids.indexOf(studentId ?? -1);
  const summary = selectedIndex >= 0 ? summaries[selectedIndex] : undefined;

  if (!children.length) {
    return (<><PageHeader title="Attendance" /><EmptyState message="No children found." /></>);
  }
  return (
    <>
      <PageHeader title="Attendance" crumbs={[{ label: "Overview", to: "/parent/dashboard" }]}
                  description="Recorded by the coaches. “Not marked” sessions are not counted as absent and are left out of the percentage." />
      {children.length > 1 ? (
        <Section title="All children">
          <DataTable
            caption="Attendance summary for each child"
            rows={children.map((c, i) => ({ child: c, query: summaries[i] }))}
            rowKey={(r) => r.child.id}
            columns={[
              { key: "child", header: "Child", render: (r) => r.child.full_name },
              { key: "pct", header: "Attendance", align: "end",
                render: (r) => r.query.data ? formatPercentage(r.query.data.percentage) : r.query.isError ? "Unavailable" : "…" },
              { key: "present", header: "Present", align: "end", render: (r) => r.query.data?.present ?? "…" },
              { key: "absent", header: "Absent", align: "end", render: (r) => r.query.data?.absent ?? "…" },
              { key: "unmarked", header: "Not marked", align: "end", render: (r) => r.query.data?.unmarked ?? "…" },
            ]}
          />
        </Section>
      ) : null}

      <div className="toolbar"><StudentSelector /></div>
      {child ? (
        <>
          <Section title={`${child.full_name}: summary`}>
            {!summary || summary.isPending ? <LoadingState /> : summary.isError ? (
              <ErrorState error={summary.error} onRetry={() => summary.refetch()} />
            ) : (
              <>
                <AttendanceSummary summary={summary.data} />
                <p className="muted">{summary.data.expected} sessions held so far, {summary.data.marked} marked.</p>
              </>
            )}
          </Section>
          <Section title={`${child.full_name}: history`}>
            {history.isPending ? <LoadingState /> : history.isError ? (
              <ErrorState error={history.error} onRetry={() => history.refetch()} />
            ) : (
              <>
                <DataTable<AttendanceRecord>
                  caption={`Attendance history for ${child.full_name}`}
                  rows={history.rows}
                  rowKey={(r) => r.id}
                  emptyMessage="No attendance records found."
                  columns={[
                    { key: "date", header: "Date", render: (r) => formatDate(r.session_date) },
                    { key: "class", header: "Class", render: (r) => r.class_name },
                    { key: "status", header: "Status", render: (r) => <AttendanceStatusBadge status={r.status} /> },
                  ]}
                />
                <LoadMore shown={history.rows.length} total={history.count} hasMore={!!history.hasNextPage}
                          loading={history.isFetchingNextPage} onMore={() => history.fetchNextPage()} />
                <p className="muted">Sessions that have not been marked yet are counted under “Not marked” above;
                  they have no entry in this history until a coach records them.</p>
              </>
            )}
          </Section>
          <p><Link to={`/parent/students/${child.id}`}>{child.full_name}’s profile</Link></p>
        </>
      ) : null}
    </>
  );
}
