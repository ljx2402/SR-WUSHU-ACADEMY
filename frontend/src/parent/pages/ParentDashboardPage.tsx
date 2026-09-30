import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import type { OwnStudent } from "../../api/types";
import { useServices } from "../../app/services";
import { can } from "../../auth/access";
import { useMe } from "../../auth/AuthProvider";
import { InvoiceStatusBadge, Money, RegistrationStatusBadge } from "../../domain/finance";
import { SessionPhaseBadge } from "../../domain/attendance";
import {
  academyToday, addDays, formatDate, formatDateTime, formatPercentage, formatShortDate, formatTime,
} from "../../domain/format";
import { PageHeader } from "../../layout/PageHeader";
import { DataTable } from "../../ui/DataTable";
import { Card } from "../../ui/primitives";
import { EmptyState, ErrorState, LoadingState } from "../../ui/states";
import { StudentStatusBadge, sessionCoaches } from "../components";
import { useParent } from "../ParentContext";
import { parentKeys, useAttendanceSummaries, useFamilies, useSessionsRange, useStudents } from "../queries";
import { scheduleRows } from "../schedule";

/** Parent Portal overview: every figure comes from the API, nothing is estimated. */
export function ParentDashboardPage() {
  const me = useMe();
  const { children } = useParent();
  const ids = children.map((child) => child.id);
  const students = useStudents(ids);
  const summaries = useAttendanceSummaries(can(me, "attendance.view_own_children") ? ids : []);
  const today = academyToday();
  const sessions = useSessionsRange(today, addDays(today, 7), can(me, "sessions.view_own_children"));
  const loadedStudents = students.map((q) => q.data).filter((s): s is OwnStudent => !!s);
  const rows = scheduleRows(sessions.data?.rows ?? [], loadedStudents);

  return (
    <>
      <PageHeader title="Family overview" description={<>Welcome, {me.name || me.username}.</>} />
      <FamilySummary />
      {!children.length ? (
        <EmptyState message="No children found.">
          <p>No children are linked to your account yet. Please contact the academy office.</p>
        </EmptyState>
      ) : (
        <section aria-labelledby="children-heading" className="page-section">
          <h2 id="children-heading">Children</h2>
          <div className="grid grid-cards">
            {children.map((child, index) => {
              const detail = students[index];
              const summary = summaries[index]?.data;
              const next = rows.find((row) => row.students.some((s) => s.id === child.id)
                && row.session.phase === "UPCOMING");
              return (
                <Card key={child.id} as="article" title={child.full_name}
                      actions={detail.data ? <StudentStatusBadge status={detail.data.status} /> : null}>
                  {detail.isPending ? <LoadingState /> : detail.isError ? <ErrorState error={detail.error} /> : (
                    <dl className="definition-list compact">
                      <div><dt>Classes</dt><dd>{detail.data.current_classes?.map((c) => c.class_name).join(", ") || "None"}</dd></div>
                      <div><dt>Next session</dt><dd>{next
                        ? `${formatDate(next.session.date)}, ${formatTime(next.session.start_time)} · ${next.session.class_name}`
                        : "None in the next 7 days"}</dd></div>
                      {summary ? (
                        <div><dt>Attendance</dt><dd>
                          {formatPercentage(summary.percentage)} · {summary.present} present · {summary.absent} absent
                          · {summary.unmarked} not marked
                        </dd></div>
                      ) : null}
                    </dl>
                  )}
                  <p><Link to={`/parent/students/${child.id}`}>View {child.full_name}’s profile</Link></p>
                </Card>
              );
            })}
          </div>
        </section>
      )}

      <div className="grid grid-2">
        {can(me, "sessions.view_own_children") ? (
          <Card title="Upcoming sessions" className="span-all" actions={<Link to="/parent/schedule">Full schedule</Link>}>
            {sessions.isPending ? <LoadingState /> : sessions.isError ? <ErrorState error={sessions.error} /> : (
              <DataTable
                caption="Upcoming sessions in the next 7 days"
                rows={rows.filter((row) => row.session.phase !== "COMPLETED").slice(0, 8)}
                rowKey={(row) => row.session.id}
                emptyMessage="No sessions in the next 7 days."
                columns={[
                  { key: "when", header: "When", render: (r) => `${formatShortDate(r.session.date)}, ${formatTime(r.session.start_time)}` },
                  { key: "class", header: "Class", render: (r) => r.session.class_name },
                  { key: "student", header: "Child", render: (r) => r.students.map((s) => s.full_name).join(", ") },
                  { key: "coach", header: "Coach", render: (r) => sessionCoaches(r.session), priority: "secondary" },
                  { key: "status", header: "Status", render: (r) => <SessionPhaseBadge phase={r.session.phase} /> },
                ]}
              />
            )}
          </Card>
        ) : null}
        {can(me, "finance.view_own_children") ? <FinanceSummary /> : null}
        <CompetitionSummary />
        <QuickActions />
      </div>
    </>
  );
}

function FamilySummary() {
  const families = useFamilies();
  const { children } = useParent();
  if (families.isPending) return <LoadingState />;
  if (families.isError) return <ErrorState error={families.error} onRetry={() => families.refetch()} />;
  const list = families.data.results;
  return (
    <div className="family-banner" aria-label="Family summary" role="group">
      <p className="family-banner-name">{list.length ? list.map((f) => f.name).join(" · ") : "My family"}</p>
      <p className="muted">{children.length} {children.length === 1 ? "child" : "children"} linked to your account ·{" "}
        <Link to="/parent/family">Family details</Link></p>
    </div>
  );
}

function FinanceSummary() {
  const { endpoints } = useServices();
  const outstanding = useQuery({
    queryKey: parentKeys.invoices({ outstanding: 1 }),
    queryFn: ({ signal }) => endpoints.invoices({ outstanding: 1 }, signal),
  });
  const payments = useQuery({
    queryKey: [...parentKeys.payments, "recent"],
    queryFn: ({ signal }) => endpoints.payments({}, signal),
  });
  return (
    <Card title="Family finance" actions={<Link to="/parent/finance">Open</Link>}>
      <h3>Invoices awaiting payment</h3>
      {outstanding.isPending ? <LoadingState /> : outstanding.isError ? <ErrorState error={outstanding.error} /> : (
        outstanding.data.results.length ? (
          <ul className="item-list">
            {outstanding.data.results.slice(0, 5).map((invoice) => (
              <li key={invoice.id}>
                <Link to={`/parent/finance/invoices/${invoice.id}`}>{invoice.number}</Link>
                <span><InvoiceStatusBadge status={invoice.status} /> Balance due <Money value={invoice.balance_due} /></span>
              </li>
            ))}
          </ul>
        ) : <p className="muted">No invoices awaiting payment.</p>
      )}
      <h3>Recent payments</h3>
      {payments.isPending ? <LoadingState /> : payments.isError ? <ErrorState error={payments.error} /> : (
        payments.data.results.length ? (
          <ul className="item-list">
            {payments.data.results.slice(0, 3).map((payment) => (
              <li key={payment.id}>
                <span>{formatDateTime(payment.received_at)}</span>
                <Money value={payment.amount} />
              </li>
            ))}
          </ul>
        ) : <p className="muted">No payments recorded.</p>
      )}
    </Card>
  );
}

function CompetitionSummary() {
  const { endpoints } = useServices();
  const competitions = useQuery({
    queryKey: [...parentKeys.competitions, "first"],
    queryFn: ({ signal }) => endpoints.competitions({}, signal),
  });
  const registrations = useQuery({
    queryKey: parentKeys.registrations({ first: true }),
    queryFn: ({ signal }) => endpoints.registrations({}, signal),
  });
  const open = competitions.data?.results.filter((c) => c.is_open) ?? [];
  const active = registrations.data?.results.filter((r) => r.status === "PENDING" || r.status === "CONFIRMED") ?? [];
  return (
    <Card title="Competitions" actions={<Link to="/parent/competitions">Open</Link>}>
      <h3>Open for registration</h3>
      {competitions.isPending ? <LoadingState /> : competitions.isError ? <ErrorState error={competitions.error} /> : (
        open.length ? (
          <ul className="item-list">
            {open.slice(0, 3).map((c) => (
              <li key={c.id}>
                <Link to={`/parent/competitions/${c.id}`}>{c.name}</Link>
                <span className="muted">Register by {formatDate(c.registration_deadline)}</span>
              </li>
            ))}
          </ul>
        ) : <p className="muted">No upcoming competitions.</p>
      )}
      <h3>Your children’s entries</h3>
      {registrations.isPending ? <LoadingState /> : registrations.isError ? <ErrorState error={registrations.error} /> : (
        active.length ? (
          <ul className="item-list">
            {active.slice(0, 5).map((r) => (
              <li key={r.id}>
                <span>{r.student_name} · {r.competition_name} – {r.event_name}</span>
                <RegistrationStatusBadge status={r.status} />
              </li>
            ))}
          </ul>
        ) : <p className="muted">No current entries.</p>
      )}
    </Card>
  );
}

function QuickActions() {
  const me = useMe();
  const actions = [
    can(me, "sessions.view_own_children") && { to: "/parent/schedule", label: "View schedule" },
    can(me, "attendance.view_own_children") && { to: "/parent/attendance", label: "View attendance" },
    can(me, "finance.view_own_children") && { to: "/parent/finance/invoices", label: "View invoices" },
    can(me, "finance.view_own_children") && { to: "/parent/finance/receipts", label: "Receipts" },
    can(me, "competition.register_own_children") && { to: "/parent/competitions", label: "Enter a competition" },
  ].filter(Boolean) as { to: string; label: string }[];
  return (
    <Card title="Quick actions">
      <ul className="quick-links">
        {actions.map((a) => <li key={a.to}><Link className="btn btn-secondary" to={a.to}>{a.label}</Link></li>)}
      </ul>
    </Card>
  );
}
