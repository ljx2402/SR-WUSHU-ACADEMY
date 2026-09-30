import type { SessionPhase } from "../api/types";
import { TextAreaField } from "../ui/Field";
import { Badge, type Tone } from "../ui/primitives";
import { formatPercentage } from "./format";

/**
 * Attendance building blocks (Phase 6G builds the screens on these).
 * Backend rules they reflect (the backend enforces them; the UI only explains):
 * - UNMARKED is not absent, and is left out of the attendance percentage;
 * - attendance can be recorded from the session start;
 * - coaches may change it until 48 hours after the session ends ("locked" after);
 * - changes to a recorded mark, and every correction after the lock, need a reason.
 */

export type AttendanceStatus = "UNMARKED" | "PRESENT" | "ABSENT" | "LATE" | "EXCUSED";

const ATTENDANCE: Record<AttendanceStatus, { label: string; tone: Tone }> = {
  PRESENT: { label: "Present", tone: "success" },
  LATE: { label: "Late", tone: "info" },
  ABSENT: { label: "Absent", tone: "danger" },
  EXCUSED: { label: "Excused", tone: "neutral" },
  UNMARKED: { label: "Not marked", tone: "warning" },
};

export function AttendanceStatusBadge({ status }: { status: AttendanceStatus | string }) {
  const entry = ATTENDANCE[status as AttendanceStatus] ?? { label: status, tone: "neutral" as Tone };
  return <Badge tone={entry.tone}>{entry.label}</Badge>;
}

export interface AttendanceSummaryData {
  percentage: string | null;
  present: number;
  late: number;
  absent: number;
  excused: number;
  expected?: number;
  marked?: number;
  unmarked?: number;
}

/**
 * "87.50% attended · 7 present, 1 absent · 2 not marked". Unmarked students
 * are always shown separately and are not part of the percentage.
 */
export function AttendanceSummary({ summary }: { summary: AttendanceSummaryData }) {
  const unmarked = summary.unmarked ?? 0;
  return (
    <div className="attendance-summary">
      <p className="attendance-pct">
        <strong>{formatPercentage(summary.percentage)}</strong> attended
        {summary.percentage === null ? <span className="muted"> (nothing marked yet)</span> : null}
      </p>
      <p className="muted">
        {summary.present} present · {summary.late} late · {summary.absent} absent · {summary.excused} excused
      </p>
      {unmarked > 0 ? (
        <p className="attendance-unmarked">
          <AttendanceStatusBadge status="UNMARKED" /> {unmarked} {unmarked === 1 ? "student" : "students"} not
          marked (not counted in the percentage)
        </p>
      ) : null}
    </div>
  );
}

export type SheetState = "NOT_STARTED" | "OPEN" | "COMPLETE" | "LOCKED" | "CANCELLED";

const SHEET_STATE: Record<SheetState, { label: string; tone: Tone; help: string }> = {
  NOT_STARTED: { label: "Not started", tone: "neutral", help: "Attendance can be recorded from the session start." },
  OPEN: { label: "Open", tone: "info", help: "Some students are not marked yet." },
  COMPLETE: { label: "Complete", tone: "success", help: "Every expected student is marked." },
  LOCKED: { label: "Locked", tone: "warning",
            help: "The 48-hour coach window has closed. Only an administrator can correct it, with a reason." },
  CANCELLED: { label: "Session cancelled", tone: "danger", help: "No attendance for a cancelled session." },
};

export function AttendanceStateBadge({ state }: { state: SheetState | string }) {
  const entry = SHEET_STATE[state as SheetState] ?? { label: state, tone: "neutral" as Tone, help: "" };
  return <Badge tone={entry.tone} title={entry.help}>{entry.label}</Badge>;
}

export function LockedNotice({ state }: { state: SheetState | string }) {
  const entry = SHEET_STATE[state as SheetState];
  return entry ? <p className="muted">{entry.help}</p> : null;
}

const PHASE: Record<SessionPhase, { label: string; tone: Tone }> = {
  UPCOMING: { label: "Upcoming", tone: "neutral" },
  IN_PROGRESS: { label: "In progress", tone: "info" },
  COMPLETED: { label: "Completed", tone: "success" },
  CANCELLED: { label: "Cancelled", tone: "danger" },
};

export function SessionPhaseBadge({ phase }: { phase: SessionPhase | string }) {
  const entry = PHASE[phase as SessionPhase] ?? { label: phase, tone: "neutral" as Tone };
  return <Badge tone={entry.tone}>{entry.label}</Badge>;
}

/** Reason for changing a recorded mark or correcting after the lock (the backend requires it). */
export function CorrectionReasonField({ value, onChange, errors, afterLock = false }: {
  value: string;
  onChange: (value: string) => void;
  errors?: string[];
  afterLock?: boolean;
}) {
  return (
    <TextAreaField
      label="Reason for the change"
      required
      rows={2}
      value={value}
      errors={errors}
      hint={afterLock ? "Administrator correction after the 48-hour window. Stored in the audit log."
                      : "Needed when changing a mark that was already recorded. Stored in the audit log."}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
