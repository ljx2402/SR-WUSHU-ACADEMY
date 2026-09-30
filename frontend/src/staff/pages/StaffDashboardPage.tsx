import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { allPages } from "../../api/endpoints";
import type { StaffDashboard } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { academyToday, addDays, formatDate, formatShortDate, formatTime } from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { Alert, Card, KpiCard } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState } from "../../ui/states";
import { AdminLink, StaffSessionTable, staffKeys } from "../components";

type AlertRow = StaffDashboard["alerts"][number];

/** Every alert is a query over current data (no stored notifications, nothing invented). */
function AlertList({ alert }: { alert: AlertRow }) {
  if (!alert.count) return null;
  return (
    <li className="ops-alert">
      <p className="ops-alert-title"><strong>{alert.count}</strong> {alert.label}</p>
      <ul className="plain-list">
        {(alert.sessions ?? []).slice(0, 5).map((s) => (
          <li key={s.id}>
            <Link className="tap-link" to={alert.code.startsWith("attendance") ? `/staff/sessions/${s.id}/attendance` : `/staff/sessions/${s.id}`}>
              {formatShortDate(s.date)} {formatTime(s.start_time)} · {s.class_name}
              {s.unmarked ? ` · ${s.unmarked} not marked` : ""}
            </Link>
          </li>
        ))}
        {(alert.classes ?? []).slice(0, 5).map((c) => (
          <li key={c.id}><Link className="tap-link" to={`/staff/classes/${c.id}`}>{c.name}</Link></li>
        ))}
      </ul>
    </li>
  );
}

export function StaffDashboardPage() {
  const me = useMe();
  const { endpoints } = useServices();
  const today = academyToday();
  const dashboard = useQuery({ queryKey: staffKeys.dashboard, queryFn: ({ signal }) => endpoints.staffDashboard(signal) });
  const todays = useQuery({
    queryKey: staffKeys.sessions({ date: today, order: "asc" }),
    queryFn: ({ signal }) => allPages((page) => endpoints.staffSessions({ date: today, order: "asc", page }, signal), 5),
  });
  const recent = useQuery({
    queryKey: staffKeys.sessions({ start: addDays(today, -2), end: today }),
    queryFn: ({ signal }) => allPages((page) => endpoints.staffSessions({ start: addDays(today, -2), end: today, page }, signal), 5),
  });

  const counts = dashboard.data?.counts;
  const alerts = dashboard.data?.alerts ?? [];
  const rows = todays.data?.rows ?? [];
  const inProgress = rows.filter((s) => s.phase === "IN_PROGRESS");
  const next = rows.find((s) => s.phase === "UPCOMING");
  const completed = (recent.data?.rows ?? []).filter((s) => s.attendance.state === "COMPLETE");
  const activeAlerts = alerts.filter((a) => a.count > 0);

  return (
    <>
      <PageHeader title="Operations" description={<>Welcome, {me.name || me.username}. Today is {formatDate(today)}.</>} />
      <section aria-label="Today at a glance" className="grid grid-kpi">
        <KpiCard label="Sessions today" loading={dashboard.isPending} value={counts?.sessions_today ?? "—"}
                 hint={counts?.cancelled_today ? `${counts.cancelled_today} cancelled` : undefined} />
        <KpiCard label="In progress" loading={dashboard.isPending} value={counts?.in_progress ?? "—"}
                 hint={counts?.substitute_covered_today ? `${counts.substitute_covered_today} covered by a substitute` : undefined} />
        <KpiCard label="Next session" loading={todays.isPending} value={next ? formatTime(next.start_time) : "—"}
                 hint={next ? next.class_name : "No more sessions today"} />
        {counts?.active_students !== undefined ? <KpiCard label="Active students" value={counts.active_students} /> : null}
        {counts?.active_classes !== undefined ? <KpiCard label="Active classes" value={counts.active_classes} /> : null}
      </section>
      {dashboard.isError ? <ErrorState error={dashboard.error} onRetry={() => dashboard.refetch()} /> : null}

      <div className="grid grid-2">
        <Card title="Needs attention" className="span-all">
          {dashboard.isPending ? <LoadingState /> : activeAlerts.length ? (
            <ul className="plain-list ops-alerts">{activeAlerts.map((a) => <AlertList key={a.code} alert={a} />)}</ul>
          ) : dashboard.isSuccess ? <EmptyState message="Nothing needs attention right now." /> : null}
        </Card>
        <Card title="Today's sessions" className="span-all" actions={<Link className="tap-link" to="/staff/sessions">All sessions</Link>}>
          {todays.isPending ? <LoadingState /> : todays.isError ? (
            <ErrorState error={todays.error} onRetry={() => todays.refetch()} />
          ) : (
            <>
              {inProgress.length ? (
                <Alert tone="info" title={`In progress: ${inProgress.map((s) => s.class_name).join(", ")}`} />
              ) : null}
              <StaffSessionTable sessions={rows} caption="Today's sessions" empty="No sessions today." />
            </>
          )}
        </Card>
        <Card title="Attendance completed (last 3 days)" actions={<Link className="tap-link" to="/staff/attendance">Attendance</Link>}>
          {recent.isPending ? <LoadingState /> : recent.isError ? (
            <ErrorState error={recent.error} onRetry={() => recent.refetch()} />
          ) : completed.length ? (
            <ul className="plain-list">
              {completed.slice(0, 6).map((s) => (
                <li key={s.id}><Link className="tap-link" to={`/staff/sessions/${s.id}/attendance`}>
                  {formatShortDate(s.date)} · {s.class_name} · {s.attendance.marked}/{s.attendance.expected}
                </Link></li>
              ))}
            </ul>
          ) : <EmptyState message="No completed attendance in the last 3 days." />}
        </Card>
        <Card title="More">
          <ul className="plain-list">
            {can(me, "competition.registrations.view_all") ? (
              <li><AdminLink path="competitions/">Competitions (Django Admin)</AdminLink></li>) : null}
            {can(me, "finance.view_all") ? <li><Link className="tap-link" to="/finance/dashboard">Finance dashboard</Link></li> : null}
            <li><AdminLink>Academy administration (Django Admin)</AdminLink></li>
          </ul>
          <p className="muted">Django Admin has its own sign-in and stays available for deeper administration.</p>
        </Card>
      </div>
    </>
  );
}
