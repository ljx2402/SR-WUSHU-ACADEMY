import type { ReactNode } from "react";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger" | "brand";

export function Card({ title, actions, children, className = "", as: Tag = "section" }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article";
}) {
  return (
    <Tag className={`card ${className}`.trim()}>
      {title || actions ? (
        <header className="card-header">
          {title ? <h2 className="card-title">{title}</h2> : <span />}
          {actions ? <div className="card-actions">{actions}</div> : null}
        </header>
      ) : null}
      <div className="card-body">{children}</div>
    </Tag>
  );
}

/** Status labels always carry text (never colour alone). */
export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Alert({ tone = "info", title, children, role }: {
  tone?: Exclude<Tone, "brand" | "neutral">;
  title?: ReactNode;
  children?: ReactNode;
  role?: "alert" | "status";
}) {
  return (
    <div className={`alert alert-${tone}`} role={role ?? (tone === "danger" ? "alert" : "status")}>
      {title ? <p className="alert-title">{title}</p> : null}
      {children ? <div className="alert-body">{children}</div> : null}
    </div>
  );
}

export function KpiCard({ label, value, hint, loading }: {
  label: string;
  value: ReactNode;
  hint?: string;
  loading?: boolean;
}) {
  return (
    <div className="kpi" aria-busy={loading || undefined}>
      <p className="kpi-label">{label}</p>
      <p className="kpi-value">{loading ? <span className="skeleton" aria-label="Loading" /> : value}</p>
      {hint ? <p className="kpi-hint">{hint}</p> : null}
    </div>
  );
}
