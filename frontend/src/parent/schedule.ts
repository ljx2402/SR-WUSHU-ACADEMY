import type { OwnStudent, TrainingSession } from "../api/types";

/**
 * Which of the parent's children a session is for: those enrolled in the
 * session's class on the session date (from each child's enrollments as the
 * API returns them). The session list itself is already limited by the
 * backend; this only labels rows, and drops sessions that belong to none of
 * the children (e.g. classes a coach-and-parent teaches).
 */
export function childrenInSession(session: TrainingSession, students: OwnStudent[]): OwnStudent[] {
  return students.filter((student) =>
    (student.current_classes ?? []).some((enrollment) =>
      enrollment.training_class === session.training_class
      && enrollment.start_date <= session.date
      && (!enrollment.end_date || session.date <= enrollment.end_date)));
}

export interface ScheduleRow {
  session: TrainingSession;
  students: OwnStudent[];
}

export function scheduleRows(sessions: TrainingSession[], students: OwnStudent[], only?: number | "all" | null) {
  return sessions
    .map((session) => ({ session, students: childrenInSession(session, students) }))
    .filter((row) => row.students.length > 0)
    .filter((row) => only === "all" || only === null || only === undefined
      || row.students.some((student) => student.id === only))
    .sort((a, b) => `${a.session.date}${a.session.start_time}`.localeCompare(`${b.session.date}${b.session.start_time}`));
}
