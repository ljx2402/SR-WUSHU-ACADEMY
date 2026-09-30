import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { allPages } from "../../api/endpoints";
import { useServices } from "../../app/services";
import { useMe } from "../../auth/AuthProvider";
import { academyToday, addDays, formatDate, formatTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { Alert, Card, KpiCard } from "../../ui/primitives";
import { ErrorState, LoadingState } from "../../ui/states";
import { SessionCards, coachKeys } from "../components";

/** Today, what needs attendance, and the week ahead: all from the coach's own session scope. */
export function CoachDashboardPage() {
  const me = useMe();
  const { endpoints } = useServices();
  const today = academyToday();
  const range = { start: addDays(today, -2), end: addDays(today, 7), order: "asc" as const };
  const sessions = useQuery({
    queryKey: coachKeys.sessions(range),
    queryFn: ({ signal }) => allPages((page) => endpoints.coachingSessions({ ...range, page }, signal), 5),
  });

  if (sessions.isPending) return (<><PageHeader title="Coach dashboard" /><LoadingState /></>);
  if (sessions.isError) {
    return (<><PageHeader title="Coach dashboard" /><ErrorState error={sessions.error} onRetry={() => sessions.refetch()} /></>);
  }
  const rows = sessions.data.rows;
  const todays = rows.filter((s) => s.date === today);
  const live = todays.filter((s) => s.status !== "CANCELLED");
  const current = live.find((s) => s.phase === "IN_PROGRESS");
  const next = rows.find((s) => s.phase === "UPCOMING" && s.status !== "CANCELLED");
  const needsAttendance = rows.filter((s) => s.attendance.state === "OPEN" && s.attendance.unmarked > 0);
  const upcoming = rows.filter((s) => s.date > today);
  const substitute = rows.filter((s) => s.my_role?.role === "SUBSTITUTE" && s.my_role.status === "ASSIGNED");

  return (
    <>
      <PageHeader title="Coach dashboard" description={<>Welcome, {me.name || me.username}. Today is {formatDate(today)}.</>} />
      <section aria-label="Today at a glance" className="grid grid-kpi">
        <KpiCard label="Sessions today" value={live.length}
                 hint={todays.length > live.length ? `${todays.length - live.length} cancelled` : undefined} />
        <KpiCard label="Next session" value={next ? `${formatTime(next.start_time)}` : "—"}
                 hint={next ? `${next.class_name}${next.date !== today ? ` · ${formatDate(next.date)}` : ""}` : "None in the next 7 days"} />
        <KpiCard label="Attendance to finish" value={needsAttendance.length}
                 hint={needsAttendance.length ? "Sessions with students not marked" : "All caught up"} />
      </section>

      {current ? (
        <Alert tone="info" title={`In progress: ${current.class_name} (${formatTime(current.start_time)}–${formatTime(current.end_time)})`}>
          <p><Link className="btn btn-primary" to={`/coach/sessions/${current.id}/attendance`}>Take attendance</Link></p>
        </Alert>
      ) : null}
      {substitute.length ? (
        <Alert tone="info" title={`You are covering ${substitute.length} session${substitute.length === 1 ? "" : "s"} as a substitute.`}>
          <p>You can see those sessions and their rosters only while your cover is authorized.</p>
        </Alert>
      ) : null}

      <div className="grid grid-2">
        <Card title="Today" className="span-all">
          <SessionCards sessions={todays} empty="No sessions today." />
        </Card>
        {needsAttendance.length ? (
          <Card title="Attendance to finish" actions={<Link to="/coach/attendance">All</Link>}>
            <SessionCards sessions={needsAttendance} />
          </Card>
        ) : null}
        <Card title="Upcoming (next 7 days)" actions={<Link to="/coach/sessions?view=upcoming">All sessions</Link>}>
          <SessionCards sessions={upcoming.slice(0, 8)} empty="No upcoming sessions." />
        </Card>
      </div>
    </>
  );
}
