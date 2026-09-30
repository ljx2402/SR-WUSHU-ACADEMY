import { useEffect, type ReactNode } from "react";
import { Link } from "react-router";

export interface Crumb {
  label: string;
  to?: string;
}

/** Page title (also sets the document title) with optional breadcrumbs and actions. */
export function PageHeader({ title, crumbs = [], actions, description }: {
  title: string;
  crumbs?: Crumb[];
  actions?: ReactNode;
  description?: ReactNode;
}) {
  useEffect(() => {
    document.title = `${title} · SR Wushu Academy`;
  }, [title]);
  return (
    <div className="page-header">
      {crumbs.length ? (
        <nav aria-label="Breadcrumb">
          <ol className="breadcrumbs">
            {crumbs.map((crumb) => (
              <li key={crumb.label}>{crumb.to ? <Link to={crumb.to}>{crumb.label}</Link> : crumb.label}</li>
            ))}
            <li aria-current="page">{title}</li>
          </ol>
        </nav>
      ) : null}
      <div className="page-header-row">
        <h1>{title}</h1>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </div>
      {description ? <div className="page-description">{description}</div> : null}
    </div>
  );
}
