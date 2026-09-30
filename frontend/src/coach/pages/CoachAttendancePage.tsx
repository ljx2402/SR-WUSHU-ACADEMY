import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { AttendanceMark, AttendanceSheet } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { RecordHistory } from "../../staff/RecordHistory";
import { staffKeys } from "../../staff/components";
import { AttendanceStateBadge } from "../../domain/attendance";
import { formatDate, formatDateTime, formatPercentage, formatTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { Button } from "../../ui/Button";
import { TextAreaField } from "../../ui/Field";
import { Alert } from "../../ui/primitives";
import { ErrorState, LoadingState, NotFoundState } from "../../ui/states";
import { coachKeys } from "../components";

/*
 * Taking attendance. The rules are the backend's (apps/attendance/services.py):
 * expected roster only; from the session start; coaches until 48 hours after
 * the end, then locked; changing a recorded mark needs a reason; all or
 * nothing. This page only helps: it shows the state the backend reports, and
 * the backend refuses anything that is not allowed.
 */

const MARKS: { value: AttendanceMark; label: string }[] = [
  { value: "PRESENT", label: "Present" },
  { value: "LATE", label: "Late" },
  { value: "ABSENT", label: "Absent" },
  { value: "EXCUSED", label: "Excused" },
  { value: "UNMARKED", label: "Not marked" },
];

type Row = { student: number; name: string; status: AttendanceMark; remarks: string };

function rowsFrom(sheet: AttendanceSheet): Row[] {
  return sheet.sheet.map((r) => ({ student: r.student, name: r.student_name, status: r.status, remarks: r.remarks }));
}

/**
 * The attendance sheet. `portal="coach"` (Coach Portal) or `"staff"` (Staff
 * Portal, Phase 6E): staff with `attendance.take_any` record like a coach, and
 * those with `attendance.correct` can also correct a locked session, always
 * with a reason. The backend enforces all of it; the page only follows it.
 */
export function CoachAttendancePage({ portal = "coach" }: { portal?: "coach" | "staff" }) {
  const { sessionId = "" } = useParams();
  const me = useMe();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const staff = portal === "staff";
  const keys = staff ? { session: staffKeys.session(sessionId), sheet: staffKeys.sheet(sessionId), all: staffKeys.all }
    : { session: coachKeys.session(sessionId), sheet: coachKeys.sheet(sessionId), all: coachKeys.all };
  const sessionUrl = staff ? `/staff/sessions/${sessionId}` : `/coach/sessions/${sessionId}`;
  const session = useQuery({
    queryKey: keys.session,
    queryFn: ({ signal }) => endpoints.session(sessionId, signal),
  });
  const sheet = useQuery({
    queryKey: keys.sheet,
    queryFn: ({ signal }) => endpoints.attendanceSheet(sessionId, signal),
  });
  const [rows, setRows] = useState<Row[] | null>(null);
  const [reason, setReason] = useState("");
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const statusRef = useRef<HTMLDivElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);

  // Start from the saved sheet (and again after each save).
  useEffect(() => {
    if (sheet.data) setRows(rowsFrom(sheet.data));
  }, [sheet.data]);

  const save = useMutation({
    mutationFn: (body: Parameters<typeof endpoints.submitAttendance>[1]) => endpoints.submitAttendance(sessionId, body),
    onSuccess: async () => {
      setReason("");
      setError(null);
      setSavedAt(new Date().toISOString());
      await queryClient.invalidateQueries({ queryKey: keys.all });
      window.setTimeout(() => statusRef.current?.focus(), 0);
    },
    onError: async (e) => {
      setSavedAt(null);
      if (isApiError(e) && e.kind === "forbidden") {
        setError(staff ? "You are not allowed to change this session's attendance."
          : "Attendance for this session can no longer be changed by coaches (the 48-hour window has closed, " +
            "or you are not assigned to it). Ask an administrator if it needs a correction.");
        await queryClient.invalidateQueries({ queryKey: keys.sheet });
      } else {
        setError(isApiError(e) ? e.userMessage : "Unable to connect. Please try again.");
      }
      window.setTimeout(() => errorRef.current?.focus(), 0);
    },
  });

  if (session.isPending || sheet.isPending || (sheet.isSuccess && !rows)) return <LoadingState />;
  const failed = session.error ?? sheet.error;
  if (failed) {
    if (isApiError(failed) && failed.kind === "not_found") {
      return (<><PageHeader title="Session not found" /><NotFoundState /></>);
    }
    return <ErrorState error={failed} onRetry={() => { session.refetch(); sheet.refetch(); }} />;
  }
  const s = session.data!;
  const data = sheet.data!;
  const current = rows!;
  const original = new Map(data.sheet.map((r) => [r.student, r]));
  // Coaches: while the window is open. Staff: the same with attendance.take_any, and an
  // administrator correction of a locked session with attendance.correct.
  const correcting = staff && data.state === "LOCKED" && can(me, "attendance.correct");
  const editable = correcting || ((data.state === "OPEN" || data.state === "COMPLETE")
    && (!staff || can(me, "attendance.take_any")));
  const changed = current.filter((r) => {
    const o = original.get(r.student)!;
    return o.status !== r.status || o.remarks !== r.remarks;
  });
  // Changing a mark that was already recorded needs a reason (backend rule).
  // A correction after the window always needs one.
  const needsReason = correcting || changed.some((r) => original.get(r.student)!.status !== "UNMARKED");
  const marked = current.filter((r) => r.status !== "UNMARKED").length;
  const unmarked = current.length - marked;

  function update(student: number, patch: Partial<Row>) {
    setSavedAt(null);
    setRows((all) => all!.map((r) => (r.student === student ? { ...r, ...patch } : r)));
  }

  function markAllPresent() {
    setSavedAt(null);
    setRows((all) => all!.map((r) => (r.status === "UNMARKED" ? { ...r, status: "PRESENT" } : r)));
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!changed.length) {
      setError("There are no changes to save.");
      window.setTimeout(() => errorRef.current?.focus(), 0);
      return;
    }
    if (needsReason && !reason.trim()) {
      setError(correcting ? "Give a reason: a correction after the 48-hour window needs one."
        : "Give a reason: changing attendance that was already recorded needs one.");
      window.setTimeout(() => document.getElementById("attendance-reason")?.focus(), 0);
      return;
    }
    save.mutate({
      records: changed.map((r) => ({ student: r.student, status: r.status, remarks: r.remarks.trim() })),
      reason: reason.trim(),
    });
  }

  const title = `Attendance: ${s.class_name}`;
  return (
    <>
      <PageHeader title={title}
                  crumbs={staff ? [{ label: "Operations", to: "/staff/dashboard" }, { label: "Attendance", to: "/staff/attendance" },
                                   { label: s.class_name, to: sessionUrl }]
                    : [{ label: "Coach dashboard", to: "/coach/dashboard" }, { label: s.class_name, to: sessionUrl }]}
                  description={`${formatDate(s.date)}, ${formatTime(s.start_time)}–${formatTime(s.end_time)}${s.venue ? ` · ${s.venue}` : ""}`} />
      <p className="badge-row"><AttendanceStateBadge state={data.state} /></p>

      {data.state === "CANCELLED" ? (
        <Alert tone="danger" title="This session is CANCELLED.">
          <p>Attendance cannot be taken for a cancelled session.</p>
        </Alert>
      ) : data.state === "NOT_STARTED" ? (
        <Alert tone="info" title="Attendance opens when the session starts.">
          <p>You can record attendance from {formatDate(s.date)}, {formatTime(s.start_time)}.</p>
        </Alert>
      ) : data.state === "LOCKED" ? (
        <Alert tone="warning" title="Attendance is locked.">
          <p>The 48-hour coach window closed at {formatDateTime(data.coach_edit_deadline)}. {correcting
            ? "You can correct it as an administrator: a reason is required and every change is audited."
            : "Only an administrator can correct it now, with a reason."}</p>
        </Alert>
      ) : staff && !editable ? (
        <p className="muted">View only.</p>
      ) : (
        <p className="muted">You can record and correct attendance until {formatDateTime(data.coach_edit_deadline)}.
          Changing a mark that was already saved needs a reason.</p>
      )}

      <div className="attendance-bar" role="group" aria-label="Attendance counts">
        <span><strong>{current.length}</strong> expected</span>
        <span><strong>{marked}</strong> marked</span>
        <span className={unmarked ? "attendance-bar-unmarked" : undefined}><strong>{unmarked}</strong> not marked</span>
        <span className="muted">Saved attendance: {formatPercentage(data.summary.percentage)}</span>
      </div>

      {/* Focus targets after save / error; the Alerts inside announce themselves. */}
      <div ref={statusRef} tabIndex={-1} className="attendance-status">
        {savedAt ? <Alert tone="success" role="status" title="Attendance saved." /> : null}
      </div>
      <div ref={errorRef} tabIndex={-1}>
        {error ? <Alert tone="danger" role="alert" title={error} /> : null}
      </div>

      {!current.length ? <p className="muted">No students are expected at this session.</p> : (
        <form onSubmit={onSubmit} noValidate aria-label="Attendance" className="attendance-form">
          {editable ? (
            <div className="attendance-actions">
              <Button variant="secondary" onClick={markAllPresent} disabled={!unmarked}>Mark all unmarked as present</Button>
            </div>
          ) : null}
          <ol className="attendance-list">
            {current.map((row) => {
              const o = original.get(row.student)!;
              const edited = o.status !== row.status || o.remarks !== row.remarks;
              return (
                <li key={row.student} className={`attendance-row${row.status === "UNMARKED" ? " is-unmarked" : ""}${edited ? " is-edited" : ""}`}>
                  <fieldset disabled={!editable}>
                    <legend className="attendance-name">
                      {row.name}
                      {row.status === "UNMARKED" ? <span className="badge badge-warning">Not marked</span> : null}
                      {edited ? <span className="badge badge-info">Changed</span> : null}
                    </legend>
                    <div className="mark-options">
                      {MARKS.map((m) => (
                        <label key={m.value} className={`mark mark-${m.value.toLowerCase()}`}>
                          <input type="radio" name={`mark-${row.student}`} value={m.value} checked={row.status === m.value}
                                 onChange={() => update(row.student, { status: m.value })} />
                          <span>{m.label}</span>
                        </label>
                      ))}
                    </div>
                    <details className="remark" open={!!row.remarks || undefined}>
                      <summary>Remark{row.remarks ? `: ${row.remarks}` : ""}</summary>
                      <label className="visually-hidden" htmlFor={`remark-${row.student}`}>Remark for {row.name}</label>
                      <input id={`remark-${row.student}`} type="text" maxLength={255} value={row.remarks}
                             placeholder="Optional, e.g. arrived 10 minutes late"
                             onChange={(e) => update(row.student, { remarks: e.target.value })} />
                    </details>
                  </fieldset>
                </li>
              );
            })}
          </ol>
          {editable ? (
            <div className="attendance-submit">
              {needsReason ? (
                <TextAreaField id="attendance-reason" required rows={2}
                               label={correcting ? "Reason for this correction" : "Reason for changing saved attendance"}
                               value={reason} hint="Stored in the audit log with the change."
                               onChange={(e) => setReason(e.target.value)} />
              ) : null}
              <p className="muted" aria-live="polite">{changed.length ? `${changed.length} change${changed.length === 1 ? "" : "s"} to save.` : "No unsaved changes."}</p>
              <Button type="submit" busy={save.isPending}>Save attendance</Button>
            </div>
          ) : null}
        </form>
      )}
      {staff && data.records?.length && can(me, "attendance.view_all") ? (
        <section className="page-section" aria-label="Change history">
          <h2>Change history</h2>
          <ul className="plain-list">
            {data.records.map((r) => <li key={r.id}><RecordHistory record={r} /></li>)}
          </ul>
        </section>
      ) : null}
      <p><Link to={sessionUrl}>Back to the session</Link></p>
    </>
  );
}
