import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import type { StudentAttendanceResponse } from "../../api/types";
import { useServices } from "../../app/services";
import { AttendanceStatusBadge } from "../../domain/attendance";
import { formatShortDate, formatTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { Section } from "../../parent/components";
import { DataTable } from "../../ui/DataTable";
import { ErrorState, LoadingState } from "../../ui/states";
import { MyAttendanceSummary, studentKeys } from "../components";

type Row = StudentAttendanceResponse["sessions"][number];

/**
 * Own attendance: the backend's summary (UNMARKED left out of the percentage)
 * and one row per session that has taken place. Future and cancelled sessions
 * have no attendance, so they never show as absences.
 */
export function StudentAttendancePage() {
  const { endpoints } = useServices();
  const attendance = useQuery({ queryKey: studentKeys.attendance, queryFn: ({ signal }) => endpoints.myAttendance(signal) });
  return (
    <>
      <PageHeader title="My attendance" crumbs={[{ label: "My dashboard", to: "/student/dashboard" }]}
                  description="Your attendance at sessions that have taken place. Attendance is recorded by your coach." />
      {attendance.isPending ? <LoadingState /> : attendance.isError ? (
        <ErrorState error={attendance.error} onRetry={() => attendance.refetch()} />
      ) : (
        <>
          <Section title="Summary">
            <MyAttendanceSummary summary={attendance.data.summary} />
          </Section>
          <Section title="Sessions">
            <DataTable<Row>
              caption="My attendance by session"
              className="student-attendance"
              rows={attendance.data.sessions}
              rowKey={(r) => r.session}
              emptyMessage="No sessions have taken place yet."
              columns={[
                { key: "date", header: "Date", render: (r) => (
                  <Link className="tap-link" to={`/student/sessions/${r.session}`}>{formatShortDate(r.date)}</Link>) },
                { key: "time", header: "Time", priority: "secondary",
                  render: (r) => `${formatTime(r.start_time)}–${formatTime(r.end_time)}` },
                { key: "class", header: "Class", render: (r) => r.class_name },
                { key: "status", header: "Attendance", render: (r) => <AttendanceStatusBadge status={r.status} /> },
              ]}
            />
          </Section>
        </>
      )}
    </>
  );
}
