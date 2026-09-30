import { useId, type ReactNode } from "react";
import { NavLink } from "react-router";

import type { StudentStatus, TrainingSession } from "../api/types";
import { StatusBadge } from "../domain/finance";
import { Button } from "../ui/Button";
import type { Tone } from "../ui/primitives";
import { useSelectedStudent } from "./ParentContext";

export const STUDENT_STATUS: Record<StudentStatus, { label: string; tone: Tone }> = {
  ACTIVE: { label: "Active", tone: "success" },
  ON_LEAVE: { label: "On leave", tone: "info" },
  SUSPENDED: { label: "Suspended", tone: "warning" },
  WITHDRAWN: { label: "Withdrawn", tone: "neutral" },
  GRADUATED: { label: "Graduated", tone: "neutral" },
};

export const StudentStatusBadge = ({ status }: { status: string }) => <StatusBadge map={STUDENT_STATUS} value={status} />;

export const COMPETITION_STATUS: Record<string, { label: string; tone: Tone }> = {
  OPEN: { label: "Open for registration", tone: "success" },
  CLOSED: { label: "Registration closed", tone: "neutral" },
  COMPLETED: { label: "Completed", tone: "neutral" },
  CANCELLED: { label: "Cancelled", tone: "danger" },
};

export const CompetitionStatusBadge = ({ status }: { status: string }) =>
  <StatusBadge map={COMPETITION_STATUS} value={status} />;

/**
 * "Viewing: [child ▾]". A native <select>: keyboard, screen-reader and phone
 * friendly. Switching updates the URL (?student=), so every child-specific
 * query on the page refetches (or comes from cache) while family-wide data
 * stays as it is.
 */
export function StudentSelector({ allowAll = false, label = "Viewing" }: { allowAll?: boolean; label?: string }) {
  const { children, selected, select } = useSelectedStudent({ allowAll });
  const id = useId();
  if (children.length < 2) return null;
  return (
    <div className="student-selector">
      <label htmlFor={id}>{label}</label>
      <select id={id} value={selected === null ? "" : String(selected)}
              onChange={(e) => select(e.target.value === "all" ? "all" : Number(e.target.value))}>
        {allowAll ? <option value="all">All children</option> : null}
        {children.map((child) => <option key={child.id} value={child.id}>{child.full_name}</option>)}
      </select>
    </div>
  );
}

/** Sub-navigation for Family finance. */
export function FinanceNav() {
  const links = [
    { to: "/parent/finance", label: "Overview", end: true },
    { to: "/parent/finance/invoices", label: "Invoices" },
    { to: "/parent/finance/payments", label: "Payments" },
    { to: "/parent/finance/receipts", label: "Receipts" },
  ];
  return (
    <nav aria-label="Family finance" className="subnav no-print">
      <ul>
        {links.map((link) => (
          <li key={link.to}><NavLink to={link.to} end={link.end}>{link.label}</NavLink></li>
        ))}
      </ul>
    </nav>
  );
}

/** "Load more" for paginated lists, with the running count announced. */
export function LoadMore({ shown, total, hasMore, loading, onMore }: {
  shown: number; total: number; hasMore: boolean; loading: boolean; onMore: () => void;
}) {
  if (!shown) return null;
  return (
    <div className="load-more no-print">
      <p className="muted" aria-live="polite">Showing {shown} of {total}.</p>
      {hasMore ? <Button variant="secondary" busy={loading} onClick={onMore}>Show more</Button> : null}
    </div>
  );
}

/** Coaches currently assigned to the session (names only, as the API returns them to parents). */
export function sessionCoaches(session: TrainingSession): string {
  const names = session.coaches
    .filter((slot) => slot.status === "ASSIGNED")
    .map((slot) => (slot.role === "SUBSTITUTE" ? `${slot.coach_name} (substitute)` : slot.coach_name));
  return names.length ? names.join(", ") : "—";
}

export function Section({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <section className="page-section" aria-labelledby={id}>
      <div className="section-header">
        <h2 id={id}>{title}</h2>
        {actions ? <div className="section-actions">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function DefinitionList({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="definition-list">
      {items.map(([term, value]) => (
        <div key={term}>
          <dt>{term}</dt>
          <dd>{value === "" || value === null || value === undefined ? "—" : value}</dd>
        </div>
      ))}
    </dl>
  );
}
