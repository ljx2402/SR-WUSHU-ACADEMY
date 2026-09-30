import { PORTAL_LABELS } from "../auth/access";
import { PageHeader } from "../layout/PageHeader";
import type { NavItem } from "../nav/navigation";

/**
 * A page whose screens are built in a later phase. The route, navigation and
 * access check are real; no data is shown or invented here.
 */
export function ModulePlaceholderPage({ item }: { item: NavItem }) {
  return (
    <>
      <PageHeader
        title={item.label}
        crumbs={[{ label: "Dashboard", to: "/dashboard" }, { label: PORTAL_LABELS[item.portal] }]}
        description={item.description}
      />
      <div className="placeholder-note" data-testid="not-implemented">
        <p className="state-title">This page is not available yet.</p>
        <p>It is planned for Phase {item.phase}. Nothing is shown here until it is connected to the academy system.</p>
      </div>
    </>
  );
}
