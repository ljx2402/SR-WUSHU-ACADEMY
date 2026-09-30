import { NavLink } from "react-router";

import type { Me } from "../api/types";
import { allowed, PORTAL_LABELS, portalsOf } from "../auth/access";
import { NAV_ITEMS, PORTAL_ORDER } from "../nav/navigation";

/** Sections for each portal the user belongs to; pages the user may open inside each. */
export function visibleSections(me: Me) {
  const portals = portalsOf(me);
  return PORTAL_ORDER.filter((portal) => portals.includes(portal))
    .map((portal) => ({
      portal,
      label: PORTAL_LABELS[portal],
      items: NAV_ITEMS.filter((item) => item.portal === portal && allowed(me, portal, item.capabilities)),
    }))
    .filter((section) => section.items.length > 0);
}

export function NavMenu({ me, onNavigate }: { me: Me; onNavigate?: () => void }) {
  return (
    <nav aria-label="Main">
      <ul className="nav-list">
        <li>
          <NavLink to="/dashboard" className="nav-link" onClick={onNavigate}>Dashboard</NavLink>
        </li>
      </ul>
      {visibleSections(me).map((section) => (
        <div key={section.portal} className="nav-section" data-portal={section.portal}>
          <h2 className="nav-section-title" id={`nav-${section.portal}`}>{section.label}</h2>
          <ul className="nav-list" aria-labelledby={`nav-${section.portal}`}>
            {section.items.map((item) => (
              <li key={item.id}>
                <NavLink to={item.path} className="nav-link" onClick={onNavigate}>{item.label}</NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}
