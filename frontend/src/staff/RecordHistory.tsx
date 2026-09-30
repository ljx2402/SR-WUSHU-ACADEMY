import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import type { AttendanceRecord } from "../api/types";
import { useServices } from "../app/services";
import { formatDateTime } from "../domain/format";
import { ErrorState, LoadingState } from "../ui/states";
import { staffKeys } from "./components";

/** One attendance record's audit trail (existing, masked audit log), loaded when opened. */
export function RecordHistory({ record }: { record: AttendanceRecord }) {
  const { endpoints } = useServices();
  const [open, setOpen] = useState(false);
  const history = useQuery({
    queryKey: staffKeys.recordHistory(record.id),
    queryFn: ({ signal }) => endpoints.attendanceHistory(record.id, signal),
    enabled: open,
  });
  return (
    <details className="record-history" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>{record.student_name}</summary>
      {!open ? null : history.isPending ? <LoadingState /> : history.isError ? (
        <ErrorState error={history.error} onRetry={() => history.refetch()} />
      ) : (
        <ol className="plain-list">
          {history.data.map((entry) => {
            // The audit log stores {field: {from, to}} (already masked by the backend).
            const change = (entry.changes as Record<string, { from?: unknown; to?: unknown } | undefined>).status;
            const status = change ? `${String(change.from ?? "—")} → ${String(change.to ?? "—")}` : null;
            return (
              <li key={entry.id}>
                <strong>{formatDateTime(entry.timestamp)}</strong> · {entry.actor_name ?? "System"} · {entry.action.toLowerCase()}
                {status ? ` · ${status}` : ""}{entry.reason ? <><br /><span className="muted">Reason: {entry.reason}</span></> : null}
              </li>
            );
          })}
        </ol>
      )}
    </details>
  );
}
