import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";

import { allPages } from "../api/endpoints";
import { isApiError } from "../api/errors";
import type { CoachingSession, SessionCoachSlot } from "../api/types";
import { useServices } from "../app/services";
import { AttendanceStateBadge, SessionPhaseBadge } from "../domain/attendance";
import { formatPercentage, formatShortDate, formatTime } from "../domain/format";
import { Button } from "../ui/Button";
import { DataTable } from "../ui/DataTable";
import { Dialog } from "../ui/Dialog";
import { Alert } from "../ui/primitives";

/*
 * Staff Portal building blocks (Phase 6E). The backend decides every
 * permission, lifecycle rule and number; these components only show what it
 * returns and send changes to its existing service endpoints.
 */

export const staffKeys = {
  all: ["staff"] as const,
  dashboard: ["staff", "dashboard"] as const,
  students: (query: object) => ["staff", "students", query] as const,
  student: (id: string) => ["staff", "student", id] as const,
  studentHistory: (id: string) => ["staff", "student-history", id] as const,
  classes: ["staff", "classes"] as const,
  trainingClass: (id: string) => ["staff", "class", id] as const,
  roster: (id: string) => ["staff", "roster", id] as const,
  programs: ["staff", "programs"] as const,
  coaches: ["staff", "coaches"] as const,
  sessions: (query: object) => ["staff", "sessions", query] as const,
  session: (id: string) => ["staff", "session", id] as const,
  sheet: (id: string) => ["staff", "sheet", id] as const,
  recordHistory: (id: number) => ["staff", "record-history", id] as const,
};

/** Every class (bounded pages), for filters and pickers. */
export function useAllClasses(enabled = true) {
  const { endpoints } = useServices();
  return useQuery({
    queryKey: staffKeys.classes,
    queryFn: ({ signal }) => allPages((page) => endpoints.classes({ page }, signal), 10),
    enabled,
  });
}

/** Every coach (bounded pages), for filters and pickers. Only names are used. */
export function useAllCoaches(enabled = true) {
  const { endpoints } = useServices();
  return useQuery({
    queryKey: staffKeys.coaches,
    queryFn: ({ signal }) => allPages((page) => endpoints.coaches({ page }, signal), 10),
    enabled,
  });
}

/** "Coach Lim, Coach Wong (substitute)" from the assigned slots. */
export function coachNames(slots: SessionCoachSlot[]) {
  const names = slots.filter((slot) => slot.status === "ASSIGNED")
    .map((slot) => (slot.role === "SUBSTITUTE" ? `${slot.coach_name} (substitute)` : slot.coach_name));
  return names.length ? names.join(", ") : "No coach";
}

/** The backend's message for a refused change (field errors joined), or a generic one. */
export function errorText(error: unknown): string {
  if (!isApiError(error)) return "Unable to connect. Please try again.";
  const fields = Object.entries(error.fieldErrors ?? {}).flatMap(([, messages]) => messages);
  return [...fields, ...(error.messages ?? [])].join(" ") || error.userMessage;
}

/** A table of sessions with the backend's attendance state and counts. */
export function StaffSessionTable({ sessions, caption, empty = "No sessions.", attendanceLink = false }: {
  sessions: CoachingSession[]; caption: string; empty?: string; attendanceLink?: boolean;
}) {
  return (
    <DataTable<CoachingSession>
      caption={caption}
      rows={sessions}
      rowKey={(s) => s.id}
      emptyMessage={empty}
      className="staff-table"
      columns={[
        { key: "when", header: "Date", render: (s) => (
          <Link className="tap-link" to={attendanceLink ? `/staff/sessions/${s.id}/attendance` : `/staff/sessions/${s.id}`}>
            {formatShortDate(s.date)} {formatTime(s.start_time)}–{formatTime(s.end_time)}
          </Link>) },
        { key: "class", header: "Class", render: (s) => s.class_name },
        { key: "coach", header: "Coach", render: (s) => coachNames(s.coaches) },
        { key: "venue", header: "Venue", priority: "secondary", render: (s) => s.venue || "—" },
        { key: "status", header: "Status", render: (s) => <SessionPhaseBadge phase={s.phase} /> },
        { key: "attendance", header: "Attendance", render: (s) => <AttendanceCell session={s} /> },
      ]}
    />
  );
}

export function AttendanceCell({ session }: { session: CoachingSession }) {
  const a = session.attendance;
  if (a.state === "CANCELLED" || a.state === "NOT_STARTED") return <AttendanceStateBadge state={a.state} />;
  return (
    <span className="attendance-counts">
      <AttendanceStateBadge state={a.state} />{" "}
      <span>{a.marked}/{a.expected} marked</span>
      {a.unmarked ? <span className="muted"> · {a.unmarked} not marked</span> : null}
      <span className="muted"> · {formatPercentage(a.percentage)}</span>
    </span>
  );
}

/**
 * A dialog form for a service action: shows the backend's refusal in the
 * dialog and keeps the dialog open; closes on success.
 */
export function ActionDialog({ open, title, submitLabel, onClose, onSubmit, busy, error, children, tone = "primary" }: {
  open: boolean; title: string; submitLabel: string; onClose: () => void; onSubmit: () => void; busy: boolean;
  error: string | null; children: ReactNode; tone?: "primary" | "danger";
}) {
  function submit(e: FormEvent) {
    e.preventDefault();
    onSubmit();
  }
  return (
    <Dialog open={open} title={title} onClose={onClose}
            footer={
              <>
                <Button variant="secondary" onClick={onClose} disabled={busy}>Cancel</Button>
                <Button variant={tone} type="submit" form="action-dialog-form" busy={busy}>{submitLabel}</Button>
              </>
            }>
      <form id="action-dialog-form" onSubmit={submit} noValidate>
        {children}
      </form>
      {error ? <Alert tone="danger" role="alert" title={error} /> : null}
    </Dialog>
  );
}

/** Dialog state helper: which dialog is open, plus its error. */
export function useDialog<T extends string>() {
  const [open, setOpen] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  return {
    open,
    error,
    setError,
    show: (name: T) => { setError(null); setOpen(name); },
    close: () => { setError(null); setOpen(null); },
  };
}

/** Deeper administration stays in Django Admin (its own sign-in). */
export function AdminLink({ path = "", children }: { path?: string; children: ReactNode }) {
  return <a className="tap-link" href={`/admin/${path}`} target="_blank" rel="noopener noreferrer">{children}</a>;
}
