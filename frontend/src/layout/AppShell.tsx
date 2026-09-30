import { useEffect, useRef, useState } from "react";
import { Outlet, useLocation } from "react-router";

import { ROLE_LABELS } from "../auth/access";
import { useAuth, useMe } from "../auth/AuthProvider";
import { Button } from "../ui/Button";
import { NavMenu } from "./NavMenu";

/**
 * The signed-in frame: sidebar on wide screens, a menu drawer on phones,
 * a top bar with the user menu, and the page in <main>.
 */
export function AppShell() {
  const me = useMe();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => setMenuOpen(false), [location.pathname]);

  return (
    <div className="shell">
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="topbar">
        <button
          type="button"
          className="menu-button"
          aria-controls="mobile-nav"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <span aria-hidden="true">☰</span>
          <span className="visually-hidden">{menuOpen ? "Close menu" : "Open menu"}</span>
        </button>
        <p className="brand"><span className="brand-mark" aria-hidden="true">SR</span> <span className="brand-text">SR Wushu Academy</span></p>
        <div className="topbar-end">
          <NotificationsArea />
          <UserMenu />
        </div>
      </header>

      <aside className="sidebar" aria-label="Sidebar">
        <NavMenu me={me} />
      </aside>

      {menuOpen ? <MobileNav onClose={() => setMenuOpen(false)} /> : null}

      <main id="main" className="main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}

function MobileNav({ onClose }: { onClose: () => void }) {
  const me = useMe();
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panel.current?.querySelector<HTMLElement>("a")?.focus();
    return () => opener?.focus();
  }, []);
  return (
    <div className="drawer-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        id="mobile-nav"
        ref={panel}
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Menu"
        onKeyDown={(e) => e.key === "Escape" && onClose()}
      >
        <button type="button" className="drawer-close" onClick={onClose}>
          <span aria-hidden="true">✕</span> Close menu
        </button>
        <NavMenu me={me} onNavigate={onClose} />
      </div>
    </div>
  );
}

function UserMenu() {
  const { signOut } = useAuth();
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const displayName = me.name || me.username;
  return (
    <div className="user-menu" ref={wrapper} onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
      <button type="button" className="user-button" aria-expanded={open} aria-controls="user-menu-panel"
              onClick={() => setOpen((value) => !value)}>
        <span className="avatar" aria-hidden="true">{displayName.slice(0, 1).toUpperCase()}</span>
        <span className="user-name">{displayName}</span>
      </button>
      {open ? (
        <div id="user-menu-panel" className="user-panel">
          <p className="user-panel-name">{displayName}</p>
          <p className="muted">Signed in as {me.username}</p>
          <ul className="role-list" aria-label="Your roles">
            {me.roles.map((role) => <li key={role}>{ROLE_LABELS[role] ?? role}</li>)}
          </ul>
          <Button variant="secondary" busy={busy} onClick={async () => {
            setBusy(true);
            await signOut();
          }}>
            Sign out
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** Placeholder: notifications are delivered in Phase 6J. Nothing is invented here. */
function NotificationsArea() {
  return (
    <div className="notifications" title="Notifications arrive in a later phase">
      <span aria-hidden="true">🔔</span>
      <span className="visually-hidden">Notifications (not available yet)</span>
    </div>
  );
}
