import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";

import { isApiError } from "../../api/errors";
import type { AttendanceMark, AttendanceSheet } from "../../api/types";
import { useServices } from "../../app/services";
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

export function CoachAttendancePage() {
  const { sessionId = "" } = useParams();
  const { endpoints } = useServices();
  const queryClient = useQueryClient();
  const session = useQuery({
    queryKey: coachKeys.session(sessionId),
    queryFn: ({ signal }) => endpoints.session(sessionId, signal),
  });
  const sheet = useQuery({
    queryKey: coachKeys.sheet(sessionId),
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
      await queryClient.invalidateQueries({ queryKey: coachKeys.all });
      window.setTimeout(() => statusRef.current?.focus(), 0);
    },
    onError: async (e) => {
      setSavedAt(null);
      if (isApiError(e) && e.kind === "forbidden") {
        setError("Attendance for this session can no longer be changed by coaches (the 48-hour window has closed, " +
                 "or you are not assigned to it). Ask an administrator if it needs a correction.");
        await queryClient.invalidateQueries({ queryKey: coachKeys.sheet(sessionId) });
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
  const editable = data.state === "OPEN" || data.state === "COMPLETE";
  const changed = current.filter((r) => {
    const o = original.get(r.student)!;
    return o.status !== r.status || o.remarks !== r.remarks;
  });
  // Changing a mark that was already recorded needs a reason (backend rule).
  const needsReason = changed.some((r) => original.get(r.student)!.status !== "UNMARKED");
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
      setError("Give a reason: changing attendance that was already recorded needs one.");
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
                  crumbs={[{ label: "Coach dashboard", to: "/coach/dashboard" }, { label: s.class_name, to: `/coach/sessions/${s.id}` }]}
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
          <p>The 48-hour coach window closed at {formatDateTime(data.coach_edit_deadline)}. Only an administrator can
            correct it now, with a reason.</p>
        </Alert>
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
                <TextAreaField id="attendance-reason" label="Reason for changing saved attendance" required rows={2}
                               value={reason} hint="Stored in the audit log with the change."
                               onChange={(e) => setReason(e.target.value)} />
              ) : null}
              <p className="muted" aria-live="polite">{changed.length ? `${changed.length} change${changed.length === 1 ? "" : "s"} to save.` : "No unsaved changes."}</p>
              <Button type="submit" busy={save.isPending}>Save attendance</Button>
            </div>
          ) : null}
        </form>
      )}
      <p><Link to={`/coach/sessions/${s.id}`}>Back to the session</Link></p>
    </>
  );
}
