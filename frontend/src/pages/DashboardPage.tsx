import { useQuery } from "@tanstack/react-query";
import { Link, Navigate } from "react-router";

import type { Me, TrainingSession } from "../api/types";
import { useServices } from "../app/services";
import { can, inPortal, portalsOf } from "../auth/access";
import { useMe } from "../auth/AuthProvider";
import { PayrollStatusBadge } from "../domain/finance";
import { SessionPhaseBadge } from "../domain/attendance";
import { academyToday, addDays, formatDate, formatDateTime, formatTime } from "../domain/format";
import { PageHeader } from "../layout/PageHeader";
import { visibleSections } from "../layout/NavMenu";
import { DataTable, type Column } from "../ui/DataTable";
import { Alert, Card, KpiCard } from "../ui/primitives";
import { EmptyState, ErrorState, LoadingState } from "../ui/states";

/**
 * Dashboard foundation. Every number and row comes from the API or from
 * /api/me/; widgets the user cannot use are not rendered (the API would
 * refuse them anyway). Widgets without a backend yet say so.
 */
export function DashboardPage() {
  const me = useMe();
  const portals = portalsOf(me);
  // A parent-only account goes straight to the Parent Portal overview.
  if (portals.length === 1 && portals[0] === "parent") return <Navigate to="/parent/dashboard" replace />;
  const staff = inPortal(me, "staff");
  const seesSessions = can(me, ["sessions.view_all", "sessions.view_assigned", "sessions.view_own_children",
                                "sessions.view_self"]);
  return (
    <>
      <PageHeader title="Dashboard" description={<>Welcome, {me.name || me.username}.</>} />
      {staff ? <StaffKpis me={me} /> : null}
      <div className="grid grid-2">
        {seesSessions ? <UpcomingSessions /> : null}
        {inPortal(me, "coach") ? <CoachCard me={me} /> : null}
        {inPortal(me, "parent") ? <FamilyCard me={me} /> : null}
        {inPortal(me, "student") ? <StudentCard me={me} /> : null}
        <QuickLinks me={me} />
        <Card title="Recent activity">
          <p className="muted" data-testid="not-implemented">Not available yet (planned for Phase 6J).</p>
        </Card>
      </div>
    </>
  );
}

function useCount(key: string, path: string, query: Record<string, string>, enabled: boolean) {
  const { endpoints } = useServices();
  return useQuery({
    queryKey: ["count", key, path, query],
    queryFn: ({ signal }) => endpoints.count(path, query, signal),
    enabled,
  });
}

function countValue(query: { data?: number; isError: boolean }) {
  return query.isError ? "—" : (query.data ?? "—");
}

function StaffKpis({ me }: { me: Me }) {
  const { endpoints } = useServices();
  const today = academyToday();
  const students = useCount("students", "/api/students/", { status: "ACTIVE" }, can(me, "students.view_all"));
  const invoices = useCount("invoices", "/api/invoices/", { outstanding: "1" }, can(me, "finance.view_all"));
  const sessions = useCount("sessions", "/api/sessions/", { date: today }, can(me, "sessions.view_all"));
  const payroll = useQuery({
    queryKey: ["payroll-runs"],
    queryFn: ({ signal }) => endpoints.payrollRuns(signal),
    enabled: can(me, "payroll.view_all"),
  });
  const latestRun = payroll.data?.results[0];
  return (
    <section aria-label="Key figures" className="grid grid-kpi">
      {can(me, "students.view_all") ? (
        <KpiCard label="Active students" value={countValue(students)} loading={students.isPending}
                 hint={students.isError ? "Could not load" : undefined} />
      ) : null}
      {can(me, "sessions.view_all") ? (
        <KpiCard label="Sessions today" value={countValue(sessions)} loading={sessions.isPending}
                 hint={sessions.isError ? "Could not load" : formatDate(today)} />
      ) : null}
      {can(me, "finance.view_all") ? (
        <KpiCard label="Invoices awaiting payment" value={countValue(invoices)} loading={invoices.isPending}
                 hint={invoices.isError ? "Could not load" : "Issued or partially paid"} />
      ) : null}
      {can(me, "payroll.view_all") ? (
        <KpiCard
          label="Latest payroll period"
          loading={payroll.isPending}
          value={latestRun ? (
            <>{String(latestRun.month).padStart(2, "0")}/{latestRun.year} <PayrollStatusBadge status={latestRun.status} /></>
          ) : "—"}
          hint={payroll.isError ? "Could not load"
            : !latestRun ? "No payroll periods yet"
            : latestRun.issue_count ? `${latestRun.issue_count} issue(s) to review` : undefined}
        />
      ) : null}
    </section>
  );
}

const SESSION_COLUMNS: Column<TrainingSession>[] = [
  { key: "time", header: "Time", render: (s) => `${formatTime(s.start_time)}–${formatTime(s.end_time)}` },
  { key: "class", header: "Class", render: (s) => s.class_name },
  { key: "venue", header: "Venue", render: (s) => s.venue || "—", priority: "secondary" },
  { key: "phase", header: "Status", render: (s) => <SessionPhaseBadge phase={s.phase} /> },
];

/** Today's and tomorrow's sessions the user may see (the API scopes them). */
function UpcomingSessions() {
  const today = academyToday();
  const tomorrow = addDays(today, 1);
  return (
    <Card title="Upcoming sessions">
      <SessionDay date={today} label="Today" />
      <SessionDay date={tomorrow} label="Tomorrow" />
    </Card>
  );
}

function SessionDay({ date, label }: { date: string; label: string }) {
  const { endpoints } = useServices();
  const query = useQuery({
    queryKey: ["sessions", { date }],
    queryFn: ({ signal }) => endpoints.sessions({ date }, signal),
  });
  const rows = [...(query.data?.results ?? [])].sort((a, b) => a.start_time.localeCompare(b.start_time));
  return (
    <div className="session-day">
      <h3>{label} <span className="muted">· {formatDate(date)}</span></h3>
      {query.isPending ? <LoadingState /> : query.isError ? <ErrorState error={query.error} onRetry={() => query.refetch()} /> : (
        <>
          <DataTable caption={`Sessions ${label.toLowerCase()}`} columns={SESSION_COLUMNS} rows={rows}
                     rowKey={(s) => s.id} emptyMessage="No sessions." />
          {query.data && query.data.count > rows.length ? (
            <p className="muted">Showing {rows.length} of {query.data.count} sessions.</p>
          ) : null}
        </>
      )}
    </div>
  );
}

function CoachCard({ me }: { me: Me }) {
  const classes = me.classes ?? [];
  const substitute = me.substitute_sessions ?? [];
  return (
    <Card title="Coaching">
      {substitute.length ? (
        <Alert tone="info" title={`You are covering ${substitute.length} session${substitute.length === 1 ? "" : "s"} as a substitute.`}>
          <ul className="item-list">
            {substitute.map((sub) => (
              <li key={sub.session}>
                <span>Session #{sub.session}</span>
                <span className="muted">Access until {formatDateTime(sub.access_ends_at)}</span>
              </li>
            ))}
          </ul>
        </Alert>
      ) : null}
      <h3>Your classes</h3>
      {classes.length ? (
        <ul className="item-list">{classes.map((c) => <li key={c.id}>{c.name}</li>)}</ul>
      ) : <EmptyState message="You are not assigned to a class." />}
    </Card>
  );
}

/** One family, several children. No billing-contact concept exists. */
function FamilyCard({ me }: { me: Me }) {
  const children = me.children ?? [];
  return (
    <Card title="My family" actions={<Link to="/parent/dashboard">Parent overview</Link>}>
      {children.length ? (
        <ul className="item-list">
          {children.map((child) => (
            <li key={child.id}>
              <span>{child.full_name}</span>
              {child.student_no ? <span className="muted">{child.student_no}</span> : null}
            </li>
          ))}
        </ul>
      ) : <EmptyState message="No children are linked to your account yet." />}
    </Card>
  );
}

function StudentCard({ me }: { me: Me }) {
  return (
    <Card title="My training">
      {me.student ? (
        <p>{me.student.full_name}{me.student.student_no ? <span className="muted"> · {me.student.student_no}</span> : null}</p>
      ) : <EmptyState message="Your student record is not linked yet." />}
    </Card>
  );
}

function QuickLinks({ me }: { me: Me }) {
  const items = visibleSections(me).flatMap((section) => section.items);
  if (!items.length) return null;
  return (
    <Card title="Quick links">
      <ul className="quick-links">
        {items.slice(0, 8).map((item) => (
          <li key={item.id}><Link className="btn btn-secondary" to={item.path}>{item.label}</Link></li>
        ))}
      </ul>
    </Card>
  );
}
