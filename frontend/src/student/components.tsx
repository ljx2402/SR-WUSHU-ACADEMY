import { Link } from "react-router";

import type { StudentAttendanceResponse, StudentCompetitionEntry, StudentSession } from "../api/types";
import { AttendanceStatusBadge, SessionPhaseBadge } from "../domain/attendance";
import { formatPercentage, formatShortDate, formatTime } from "../domain/format";
import { Badge, type Tone } from "../ui/primitives";
import { EmptyState } from "../ui/states";

/*
 * Student Portal building blocks. Everything shown comes from
 * /api/students/me/..., which the backend limits to the signed-in student's
 * own linked record. The portal is read only.
 */

export const studentKeys = {
  all: ["student"] as const,
  profile: ["student", "profile"] as const,
  sessions: (view: string) => ["student", "sessions", view] as const,
  session: (id: string) => ["student", "session", id] as const,
  attendance: ["student", "attendance"] as const,
  competitions: ["student", "competitions"] as const,
};

/** The student's own mark, once the session has started. */
export function MyAttendance({ session }: { session: StudentSession }) {
  if (session.my_attendance === null) return null;
  return <span className="my-attendance">You: <AttendanceStatusBadge status={session.my_attendance} /></span>;
}

export function StudentSessionCards({ sessions, empty = "No sessions." }: { sessions: StudentSession[]; empty?: string }) {
  if (!sessions.length) return <EmptyState message={empty} />;
  return (
    <ul className="session-cards">
      {sessions.map((s) => (
        <li key={s.id} className={`session-card${s.status === "CANCELLED" ? " is-cancelled" : ""}`}>
          <Link to={`/student/sessions/${s.id}`} className="session-card-link">
            <span className="session-card-when">
              <strong>{formatShortDate(s.date)}</strong> {formatTime(s.start_time)}–{formatTime(s.end_time)}
            </span>
            <span className="session-card-class">{s.class_name}</span>
            {s.venue ? <span className="muted session-card-venue">{s.venue}</span> : null}
          </Link>
          <div className="session-card-badges">
            <SessionPhaseBadge phase={s.phase} />
            <MyAttendance session={s} />
          </div>
        </li>
      ))}
    </ul>
  );
}

/**
 * The backend's own numbers (attendance service). Sessions not marked yet are
 * shown separately and are not part of the percentage; the page never
 * recalculates it.
 */
export function MyAttendanceSummary({ summary }: { summary: StudentAttendanceResponse["summary"] }) {
  return (
    <div className="attendance-summary">
      <p className="attendance-pct">
        <strong>{formatPercentage(summary.percentage)}</strong> attended
        {summary.percentage === null ? <span className="muted"> (nothing marked yet)</span> : null}
      </p>
      <p className="muted">
        {summary.present} present · {summary.late} late · {summary.absent} absent · {summary.excused} excused
      </p>
      <p className="muted">{summary.marked} of {summary.expected} sessions marked</p>
      {summary.unmarked > 0 ? (
        <p className="attendance-unmarked">
          <AttendanceStatusBadge status="UNMARKED" /> {summary.unmarked} {summary.unmarked === 1 ? "session" : "sessions"}{" "}
          not marked yet (not counted in the percentage)
        </p>
      ) : null}
    </div>
  );
}

/** Entry status as a student sees it (payment is handled by the family). */
export const ENTRY_STATUS: Record<StudentCompetitionEntry["status"], { label: string; tone: Tone }> = {
  PENDING: { label: "Not yet confirmed", tone: "warning" },
  CONFIRMED: { label: "Confirmed", tone: "success" },
  WITHDRAWN: { label: "Withdrawn", tone: "neutral" },
  REJECTED: { label: "Not accepted", tone: "danger" },
};

export function EntryStatusBadge({ status }: { status: StudentCompetitionEntry["status"] }) {
  const entry = ENTRY_STATUS[status] ?? { label: status, tone: "neutral" as Tone };
  return <Badge tone={entry.tone}>{entry.label}</Badge>;
}

const MEDAL: Record<string, { label: string; tone: Tone }> = {
  GOLD: { label: "Gold medal", tone: "warning" },
  SILVER: { label: "Silver medal", tone: "neutral" },
  BRONZE: { label: "Bronze medal", tone: "brand" },
};

/** "Placing 2 · Silver medal · Score 8.950", or "Result not recorded yet". */
export function ResultText({ result }: { result: StudentCompetitionEntry["result"] }) {
  if (!result) return <span className="muted">Result not recorded yet</span>;
  const medal = MEDAL[result.medal];
  return (
    <span className="result-text">
      {result.placing ? <strong>Placing {result.placing}</strong> : null}
      {medal ? <Badge tone={medal.tone}>{medal.label}</Badge> : <span className="muted">No medal</span>}
      {result.score ? <span>Score {result.score}</span> : null}
    </span>
  );
}
