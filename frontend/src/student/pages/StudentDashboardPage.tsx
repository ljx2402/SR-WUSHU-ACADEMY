import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { useServices } from "../../app/services";
import { useMe } from "../../auth/AuthProvider";
import { academyToday, formatDate, formatPercentage, formatPeriod, formatTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { Card, KpiCard } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState } from "../../ui/states";
import {
  EntryStatusBadge, MyAttendanceSummary, ResultText, StudentSessionCards, studentKeys,
} from "../components";

/** Today, the next session, attendance and competitions: all from the student's own record. */
export function StudentDashboardPage() {
  const me = useMe();
  const { endpoints } = useServices();
  const today = academyToday();
  const todays = useQuery({
    queryKey: studentKeys.sessions("today"),
    queryFn: ({ signal }) => endpoints.mySessions({ view: "today" }, signal),
  });
  const upcoming = useQuery({
    queryKey: studentKeys.sessions("upcoming"),
    queryFn: ({ signal }) => endpoints.mySessions({ view: "upcoming" }, signal),
  });
  const attendance = useQuery({ queryKey: studentKeys.attendance, queryFn: ({ signal }) => endpoints.myAttendance(signal) });
  const competitions = useQuery({
    queryKey: studentKeys.competitions,
    queryFn: ({ signal }) => endpoints.myCompetitions(signal),
  });

  // "Upcoming" also lists a session in progress; the next session is the first not yet started.
  const next = upcoming.data?.results.find((s) => s.phase === "UPCOMING");
  const entries = competitions.data ?? [];
  const coming = entries.filter((e) => e.competition.end_date >= today && (e.status === "PENDING" || e.status === "CONFIRMED"));
  const results = entries.filter((e) => e.result).slice(0, 5);

  return (
    <>
      <PageHeader title="My dashboard"
                  description={<>Welcome, {me.student?.full_name ?? me.name ?? me.username}. Today is {formatDate(today)}.</>} />
      <section aria-label="At a glance" className="grid grid-kpi">
        <KpiCard label="Sessions today" loading={todays.isPending}
                 value={todays.isError ? "—" : (todays.data?.results.filter((s) => s.status !== "CANCELLED").length ?? "—")} />
        <KpiCard label="Next session" loading={upcoming.isPending}
                 value={next ? formatTime(next.start_time) : "—"}
                 hint={upcoming.isError ? "Could not load" : next ? `${next.class_name} · ${formatDate(next.date)}` : "No upcoming sessions"} />
        <KpiCard label="Attendance" loading={attendance.isPending}
                 value={attendance.isError ? "—" : formatPercentage(attendance.data?.summary.percentage)}
                 hint={attendance.data?.summary.unmarked ? `${attendance.data.summary.unmarked} not marked yet (not counted)` : undefined} />
      </section>

      <div className="grid grid-2">
        <Card title="Today" className="span-all" actions={<Link className="tap-link" to="/student/schedule">My schedule</Link>}>
          {todays.isPending ? <LoadingState /> : todays.isError ? (
            <ErrorState error={todays.error} onRetry={() => todays.refetch()} />
          ) : <StudentSessionCards sessions={todays.data.results} empty="No sessions today." />}
        </Card>
        <Card title="Next session">
          {upcoming.isPending ? <LoadingState /> : upcoming.isError ? (
            <ErrorState error={upcoming.error} onRetry={() => upcoming.refetch()} />
          ) : <StudentSessionCards sessions={next ? [next] : []} empty="No upcoming sessions." />}
        </Card>
        <Card title="Attendance" actions={<Link className="tap-link" to="/student/attendance">Details</Link>}>
          {attendance.isPending ? <LoadingState /> : attendance.isError ? (
            <ErrorState error={attendance.error} onRetry={() => attendance.refetch()} />
          ) : <MyAttendanceSummary summary={attendance.data.summary} />}
        </Card>
        <Card title="Competitions" className="span-all" actions={<Link className="tap-link" to="/student/competitions">My competitions</Link>}>
          {competitions.isPending ? <LoadingState /> : competitions.isError ? (
            <ErrorState error={competitions.error} onRetry={() => competitions.refetch()} />
          ) : (
            <div className="grid grid-2">
              <div>
                <h3>Coming up</h3>
                {coming.length ? (
                  <ul className="plain-list entry-list">
                    {coming.map((e) => (
                      <li key={e.id}>
                        <strong>{e.competition.name}</strong> · {e.event.name}
                        <span className="muted"> · {formatPeriod(e.competition.start_date, e.competition.end_date)}</span>{" "}
                        <EntryStatusBadge status={e.status} />
                      </li>
                    ))}
                  </ul>
                ) : <EmptyState message="No upcoming competitions." />}
              </div>
              <div>
                <h3>Recent results</h3>
                {results.length ? (
                  <ul className="plain-list entry-list">
                    {results.map((e) => (
                      <li key={e.id}>
                        <strong>{e.competition.name}</strong> · {e.event.name}<br />
                        <ResultText result={e.result} />
                      </li>
                    ))}
                  </ul>
                ) : <EmptyState message="No results yet." />}
              </div>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
