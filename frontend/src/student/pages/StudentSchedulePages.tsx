import { useQuery } from "@tanstack/react-query";
import { useParams, useSearchParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { StudentSession } from "../../api/types";
import { useServices } from "../../app/services";
import { AttendanceStatusBadge, SessionPhaseBadge } from "../../domain/attendance";
import { formatDate, formatTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DefinitionList, LoadMore, Section } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { Alert } from "../../ui/primitives";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { StudentSessionCards, studentKeys } from "../components";

const CRUMBS = [{ label: "My dashboard", to: "/student/dashboard" }];

const VIEWS = [
  { id: "today", label: "Today" },
  { id: "upcoming", label: "Upcoming" },
  { id: "past", label: "Past" },
  { id: "cancelled", label: "Cancelled" },
] as const;
type View = (typeof VIEWS)[number]["id"];

const EMPTY: Record<View, string> = {
  today: "No sessions today.",
  upcoming: "No upcoming sessions.",
  past: "No past sessions.",
  cancelled: "No cancelled sessions.",
};

/** The student's own sessions. The backend decides which sessions exist for this student. */
export function StudentSchedulePage() {
  const { endpoints } = useServices();
  const [params, setParams] = useSearchParams();
  const view = (VIEWS.find((v) => v.id === params.get("view"))?.id ?? "upcoming") as View;
  const sessions = usePagedList<StudentSession>(studentKeys.sessions(view),
    (page, signal) => endpoints.mySessions({ view, page }, signal));
  return (
    <>
      <PageHeader title="My schedule" crumbs={CRUMBS} description="Your training sessions." />
      <fieldset className="segmented">
        <legend>Show</legend>
        {VIEWS.map((v) => (
          <label key={v.id}>
            <input type="radio" name="schedule-view" value={v.id} checked={view === v.id}
                   onChange={() => setParams({ view: v.id }, { replace: true })} />
            <span>{v.label}</span>
          </label>
        ))}
      </fieldset>
      {sessions.isPending ? <LoadingState /> : sessions.isError ? (
        <ErrorState error={sessions.error} onRetry={() => sessions.refetch()} />
      ) : (
        <>
          <StudentSessionCards sessions={sessions.rows} empty={EMPTY[view]} />
          <LoadMore shown={sessions.rows.length} total={sessions.count} hasMore={!!sessions.hasNextPage}
                    loading={sessions.isFetchingNextPage} onMore={() => sessions.fetchNextPage()} />
        </>
      )}
    </>
  );
}

function attendanceText(s: StudentSession) {
  if (s.status === "CANCELLED") return "No attendance (session cancelled).";
  if (s.my_attendance === null) return "Attendance is recorded from the session start.";
  return <AttendanceStatusBadge status={s.my_attendance} />;
}

/** One session: when, where, coaches and the student's own attendance. Nothing about other students. */
export function StudentSessionDetailPage() {
  const { sessionId = "" } = useParams();
  const { endpoints } = useServices();
  const session = useQuery({
    queryKey: studentKeys.session(sessionId),
    queryFn: ({ signal }) => endpoints.mySession(sessionId, signal),
  });
  const crumbs = [...CRUMBS, { label: "My schedule", to: "/student/schedule" }];
  if (session.isPending) return (<><PageHeader title="Session" crumbs={crumbs} /><LoadingState /></>);
  if (session.isError) {
    return (
      <>
        <PageHeader title="Session" crumbs={crumbs} />
        {isApiError(session.error) && session.error.kind === "not_found"
          ? <NotFoundState message="This session is not in your schedule." />
          : <ErrorState error={session.error} onRetry={() => session.refetch()} />}
      </>
    );
  }
  const s = session.data;
  return (
    <>
      <PageHeader title={s.class_name} crumbs={crumbs} description={<SessionPhaseBadge phase={s.phase} />} />
      {s.status === "CANCELLED" ? <Alert tone="warning" title="This session is cancelled." /> : null}
      <Section title="Session">
        <DefinitionList items={[
          ["Date", formatDate(s.date)],
          ["Time", `${formatTime(s.start_time)}–${formatTime(s.end_time)}`],
          ["Venue", s.venue],
          ["Coach", s.coaches.join(", ")],
          ["Status", s.status === "CANCELLED" ? "Cancelled" : "Scheduled"],
        ]} />
      </Section>
      <Section title="My attendance">
        <p>{attendanceText(s)}</p>
      </Section>
    </>
  );
}
