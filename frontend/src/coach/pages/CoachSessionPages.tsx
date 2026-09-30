import { useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router";

import { allPages } from "../../api/endpoints";
import { isApiError } from "../../api/errors";
import type { CoachingSession } from "../../api/types";
import { useServices } from "../../app/services";
import { AttendanceStateBadge, AttendanceSummary, LockedNotice, SessionPhaseBadge } from "../../domain/attendance";
import { academyToday, addDays, formatDate, formatDateTime, formatTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { LoadMore, Section, DefinitionList } from "../../parent/components";
import { usePagedList } from "../../parent/queries";
import { Alert, Badge } from "../../ui/primitives";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { SessionCards, coachKeys } from "../components";

const CRUMBS = [{ label: "Coach dashboard", to: "/coach/dashboard" }];

const VIEWS = [
  { id: "today", label: "Today" },
  { id: "upcoming", label: "Upcoming" },
  { id: "past", label: "Past" },
  { id: "cancelled", label: "Cancelled" },
] as const;
type View = (typeof VIEWS)[number]["id"];

function queryFor(view: View) {
  const today = academyToday();
  switch (view) {
    case "today": return { date: today, order: "asc" as const };
    case "upcoming": return { start: addDays(today, 1), order: "asc" as const };
    case "past": return { end: addDays(today, -1) };
    case "cancelled": return { status: "CANCELLED" };
  }
}

/** The coach's sessions. The backend decides which sessions exist for this coach. */
export function CoachSessionsPage() {
  const { endpoints } = useServices();
  const [params, setParams] = useSearchParams();
  const view = (VIEWS.find((v) => v.id === params.get("view"))?.id ?? "today") as View;
  const query = queryFor(view);
  const sessions = usePagedList<CoachingSession>(coachKeys.sessions({ view, ...query }),
    (page, signal) => endpoints.coachingSessions({ ...query, page }, signal));
  return (
    <>
      <PageHeader title="My sessions" crumbs={CRUMBS}
                  description="Your classes' sessions and any session you cover as a substitute." />
      <fieldset className="segmented">
        <legend>Show</legend>
        {VIEWS.map((v) => (
          <label key={v.id}>
            <input type="radio" name="session-view" value={v.id} checked={view === v.id}
                   onChange={() => setParams({ view: v.id }, { replace: true })} />
            <span>{v.label}</span>
          </label>
        ))}
      </fieldset>
      {sessions.isPending ? <LoadingState /> : sessions.isError ? (
        <ErrorState error={sessions.error} onRetry={() => sessions.refetch()} />
      ) : (
        <>
          <SessionCards sessions={sessions.rows}
                        empty={view === "today" ? "No sessions today." : view === "cancelled" ? "No cancelled sessions." : "No sessions."} />
          <LoadMore shown={sessions.rows.length} total={sessions.count} hasMore={!!sessions.hasNextPage}
                    loading={sessions.isFetchingNextPage} onMore={() => sessions.fetchNextPage()} />
        </>
      )}
    </>
  );
}

/** Sessions whose attendance is open (today and the previous two days, inside the 48-hour window). */
export function CoachAttendanceIndexPage() {
  const { endpoints } = useServices();
  const today = academyToday();
  const range = { start: addDays(today, -2), end: today, order: "asc" as const };
  const sessions = useQuery({
    queryKey: coachKeys.sessions({ attendance: true, ...range }),
    queryFn: ({ signal }) => allPages((page) => endpoints.coachingSessions({ ...range, page }, signal), 5),
  });
  const rows = sessions.data?.rows ?? [];
  const open = rows.filter((s) => s.attendance.state === "OPEN" || s.attendance.state === "COMPLETE");
  const later = rows.filter((s) => s.attendance.state === "NOT_STARTED");
  return (
    <>
      <PageHeader title="Attendance" crumbs={CRUMBS}
                  description="Coaches can record and correct attendance from the session start until 48 hours after it ends." />
      {sessions.isPending ? <LoadingState /> : sessions.isError ? (
        <ErrorState error={sessions.error} onRetry={() => sessions.refetch()} />
      ) : (
        <>
          <Section title="Open for attendance">
            <SessionCards sessions={open} empty="No sessions are open for attendance right now." />
          </Section>
          {later.length ? (
            <Section title="Later today"><SessionCards sessions={later} /></Section>
          ) : null}
          <p><Link to="/coach/sessions?view=past">Older sessions</Link></p>
        </>
      )}
    </>
  );
}

export function CoachSessionDetailPage() {
  const { sessionId = "" } = useParams();
  const { endpoints } = useServices();
  const session = useQuery({
    queryKey: coachKeys.session(sessionId),
    queryFn: ({ signal }) => endpoints.session(sessionId, signal),
  });
  const sheet = useQuery({
    queryKey: coachKeys.sheet(sessionId),
    queryFn: ({ signal }) => endpoints.attendanceSheet(sessionId, signal),
    enabled: session.isSuccess,
  });
  const roster = useQuery({
    queryKey: coachKeys.roster(sessionId),
    queryFn: ({ signal }) => endpoints.roster(sessionId, signal),
    enabled: session.isSuccess,
  });

  if (session.isPending) return <LoadingState />;
  if (session.isError) {
    if (isApiError(session.error) && session.error.kind === "not_found") {
      return (<><PageHeader title="Session not found" crumbs={CRUMBS} /><NotFoundState /></>);
    }
    return <ErrorState error={session.error} onRetry={() => session.refetch()} />;
  }
  const s = session.data;
  const cancelled = s.status === "CANCELLED";
  const regular = s.coaches.filter((c) => c.role === "REGULAR");
  const substitutes = s.coaches.filter((c) => c.role === "SUBSTITUTE");
  const state = sheet.data?.state;
  const actionLabel = state === "LOCKED" ? "View attendance" : state === "NOT_STARTED" ? "Attendance (opens at the start)"
    : state === "COMPLETE" ? "Review attendance" : "Take attendance";

  return (
    <>
      <PageHeader title={s.class_name} crumbs={[...CRUMBS, { label: "My sessions", to: "/coach/sessions" }]}
                  actions={!cancelled ? <Link className="btn btn-primary" to={`/coach/sessions/${s.id}/attendance`}>{actionLabel}</Link> : null} />
      {cancelled ? (
        <Alert tone="danger" title="This session is CANCELLED.">
          <p>No attendance is taken for a cancelled session. Any attendance recorded before it was cancelled is kept.</p>
        </Alert>
      ) : null}
      <p className="badge-row"><SessionPhaseBadge phase={s.phase} />{state ? <AttendanceStateBadge state={state} /> : null}</p>
      <DefinitionList items={[
        ["Date", formatDate(s.date)],
        ["Time", `${formatTime(s.start_time)}–${formatTime(s.end_time)}`],
        ["Venue", s.venue],
        ["Coach", regular.map((c) => `${c.coach_name}${c.status === "REPLACED" ? " (replaced)" : c.status === "ABSENT" ? " (absent)" : ""}`).join(", ")],
        ["Substitute", substitutes.length ? substitutes.map((c) => `${c.coach_name} (${c.status.toLowerCase()})`).join(", ") : "None"],
      ]} />
      {s.notes ? <Section title="Session notes"><p className="prewrap">{s.notes}</p></Section> : null}

      <Section title="Attendance">
        {sheet.isPending ? <LoadingState /> : sheet.isError ? <ErrorState error={sheet.error} onRetry={() => sheet.refetch()} /> : (
          <>
            <AttendanceSummary summary={sheet.data.summary} />
            <p className="muted">{sheet.data.summary.expected} expected · {sheet.data.summary.marked} marked
              {" "}· {sheet.data.summary.unmarked} not marked</p>
            <LockedNotice state={sheet.data.state} />
            {!cancelled && sheet.data.state !== "LOCKED" ? (
              <p className="muted">Coaches can change attendance until {formatDateTime(sheet.data.coach_edit_deadline)}.</p>
            ) : null}
          </>
        )}
      </Section>

      <Section title={`Roster${roster.data ? ` (${roster.data.length})` : ""}`}>
        <p className="muted">Students expected at this session. Safety information is for coaching use only.</p>
        {roster.isPending ? <LoadingState /> : roster.isError ? <ErrorState error={roster.error} onRetry={() => roster.refetch()} /> : (
          roster.data.length ? (
            <ul className="roster-list">
              {roster.data.map((student) => (
                <li key={student.id}>
                  <div className="roster-name">
                    <strong>{student.full_name}</strong>
                    {student.chinese_name ? <span className="muted"> {student.chinese_name}</span> : null}
                    <span className="muted"> · {student.student_no}{student.age !== null ? ` · age ${student.age}` : ""}</span>
                    {student.medical_notes ? <Badge tone="warning">Health note</Badge> : null}
                  </div>
                  {student.medical_notes || student.emergency_contacts.length ? (
                    <details>
                      <summary>Safety information</summary>
                      {student.medical_notes ? <p className="prewrap"><strong>Health:</strong> {student.medical_notes}</p> : null}
                      {student.emergency_contacts.length ? (
                        <ul className="plain-list">
                          {student.emergency_contacts.map((c) => (
                            <li key={`${c.name}-${c.phone}`}>Emergency contact: {c.name} ({c.relationship.toLowerCase()}) · <a href={`tel:${c.phone}`}>{c.phone}</a></li>
                          ))}
                        </ul>
                      ) : null}
                    </details>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : <p className="muted">No students are expected at this session.</p>
        )}
      </Section>
    </>
  );
}

