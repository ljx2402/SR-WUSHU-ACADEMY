import { useState } from "react";

import type { OwnStudent } from "../../api/types";
import { SessionPhaseBadge } from "../../domain/attendance";
import { academyToday, addDays, formatDate, formatTime, weekStart } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { Button } from "../../ui/Button";
import { DataTable } from "../../ui/DataTable";
import { Alert } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState } from "../../ui/states";
import { StudentSelector, sessionCoaches } from "../components";
import { useSelectedStudent } from "../ParentContext";
import { useSessionsRange, useStudents } from "../queries";
import { scheduleRows, type ScheduleRow } from "../schedule";

/**
 * Week-by-week training schedule for the parent's children, in academy time
 * (Asia/Kuala_Lumpur). Session status (upcoming, in progress, completed,
 * cancelled) is the backend's, never worked out here.
 */
export function SchedulePage() {
  const { children, selected } = useSelectedStudent({ allowAll: true });
  const [week, setWeek] = useState(() => weekStart(academyToday()));
  const end = addDays(week, 6);
  const students = useStudents(children.map((c) => c.id));
  const sessions = useSessionsRange(week, end);
  const loaded = students.map((q) => q.data).filter((s): s is OwnStudent => !!s);
  const studentsPending = students.some((q) => q.isPending);
  const studentError = students.find((q) => q.isError)?.error;
  const rows = scheduleRows(sessions.data?.rows ?? [], loaded, selected);
  const thisWeek = weekStart(academyToday());

  return (
    <>
      <PageHeader title="Schedule" crumbs={[{ label: "Overview", to: "/parent/dashboard" }]}
                  description="Times are shown in academy time (Malaysia)." />
      {!children.length ? <EmptyState message="No children found." /> : (
        <>
          <div className="toolbar">
            <StudentSelector allowAll />
            <div className="week-nav" role="group" aria-label="Choose week">
              <Button variant="secondary" onClick={() => setWeek(addDays(week, -7))}>Previous week</Button>
              <Button variant="secondary" onClick={() => setWeek(thisWeek)} disabled={week === thisWeek}>This week</Button>
              <Button variant="secondary" onClick={() => setWeek(addDays(week, 7))}>Next week</Button>
            </div>
          </div>
          <h2 aria-live="polite">{formatDate(week)} – {formatDate(end)}</h2>
          {sessions.isPending || studentsPending ? <LoadingState /> : sessions.isError ? (
            <ErrorState error={sessions.error} onRetry={() => sessions.refetch()} />
          ) : studentError ? <ErrorState error={studentError} /> : (
            <>
              {!sessions.data.complete ? (
                <Alert tone="warning">Only the first {sessions.data.rows.length} of {sessions.data.count} sessions this
                  week could be shown.</Alert>
              ) : null}
              <DataTable<ScheduleRow>
                caption="Training sessions this week"
                rows={rows}
                rowKey={(row) => row.session.id}
                emptyMessage="No sessions this week."
                className="schedule-table"
                columns={[
                  { key: "day", header: "Day", render: (r) => formatDate(r.session.date) },
                  { key: "time", header: "Time", render: (r) => `${formatTime(r.session.start_time)}–${formatTime(r.session.end_time)}` },
                  { key: "class", header: "Class", render: (r) => r.session.class_name },
                  { key: "student", header: "Child", render: (r) => r.students.map((s) => s.full_name).join(", ") },
                  { key: "venue", header: "Venue", render: (r) => r.session.venue || "—", priority: "secondary" },
                  { key: "coach", header: "Coach", render: (r) => sessionCoaches(r.session) },
                  { key: "status", header: "Status", render: (r) => <SessionPhaseBadge phase={r.session.phase} /> },
                ]}
              />
            </>
          )}
        </>
      )}
    </>
  );
}
