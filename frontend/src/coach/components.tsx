import { Link } from "react-router";

import type { CoachingSession } from "../api/types";
import { AttendanceStateBadge, SessionPhaseBadge } from "../domain/attendance";
import { formatShortDate, formatTime } from "../domain/format";
import { Badge } from "../ui/primitives";
import { EmptyState } from "../ui/states";

/*
 * Coach Portal building blocks. Every session shown here comes from
 * GET /api/sessions/coaching/, which the backend limits to the signed-in
 * coach's classes and open substitute sessions.
 */

export const coachKeys = {
  all: ["coach"] as const,
  sessions: (query: object) => ["coach", "sessions", query] as const,
  session: (id: string) => ["coach", "session", id] as const,
  roster: (id: string) => ["coach", "roster", id] as const,
  sheet: (id: string) => ["coach", "sheet", id] as const,
  registrations: ["coach", "registrations"] as const,
  competitions: ["coach", "competitions"] as const,
};

/** "Substitute" / "Replaced" / nothing for the coach's usual own class. */
export function RoleBadge({ session }: { session: CoachingSession }) {
  const role = session.my_role;
  if (!role) return null;
  if (role.role === "SUBSTITUTE") {
    if (role.status === "ASSIGNED") return <Badge tone="info">Substitute</Badge>;
    return <Badge tone="neutral">Substitute ({(role.status ?? "").toLowerCase()})</Badge>;
  }
  if (role.status === "REPLACED") return <Badge tone="warning">Covered by a substitute</Badge>;
  if (role.status === "ABSENT") return <Badge tone="warning">Marked absent</Badge>;
  return null;
}

export function AttendanceCounts({ session }: { session: CoachingSession }) {
  const a = session.attendance;
  if (a.state === "CANCELLED" || a.state === "NOT_STARTED") return <AttendanceStateBadge state={a.state} />;
  return (
    <span className="attendance-counts">
      <AttendanceStateBadge state={a.state} />{" "}
      <span>{a.marked}/{a.expected} marked</span>
      {a.unmarked ? <span className="muted"> · {a.unmarked} not marked</span> : null}
    </span>
  );
}

/** A tappable card per session (phones first; also used on desktop). */
export function SessionCards({ sessions, empty = "No sessions." }: { sessions: CoachingSession[]; empty?: string }) {
  if (!sessions.length) return <EmptyState message={empty} />;
  return (
    <ul className="session-cards">
      {sessions.map((s) => (
        <li key={s.id} className={`session-card${s.status === "CANCELLED" ? " is-cancelled" : ""}`}>
          <Link to={`/coach/sessions/${s.id}`} className="session-card-link">
            <span className="session-card-when">
              <strong>{formatShortDate(s.date)}</strong> {formatTime(s.start_time)}–{formatTime(s.end_time)}
            </span>
            <span className="session-card-class">{s.class_name}</span>
            {s.venue ? <span className="muted session-card-venue">{s.venue}</span> : null}
          </Link>
          <div className="session-card-badges">
            <SessionPhaseBadge phase={s.phase} />
            <RoleBadge session={s} />
            <AttendanceCounts session={s} />
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Registration status as a coach sees it (payment details are not shown to coaches). */
export const COACH_ENTRY_STATUS: Record<string, { label: string; tone: "warning" | "success" | "neutral" | "danger" }> = {
  PENDING: { label: "Not yet confirmed", tone: "warning" },
  CONFIRMED: { label: "Confirmed", tone: "success" },
  WITHDRAWN: { label: "Withdrawn", tone: "neutral" },
  REJECTED: { label: "Rejected", tone: "danger" },
};
