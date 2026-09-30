import type { Me, Role } from "../api/types";

/**
 * Role and capability checks for the UI.
 *
 * These only decide what to *show*. The Django API checks every request
 * again (capabilities + record scoping) and is the only real protection.
 *
 * A user may hold several roles (e.g. COACH + PARENT). Each role opens a
 * portal section; each page inside it needs a capability. Sections come from
 * roles (so a SUPER_ADMIN, who holds every capability, does not also get the
 * parent or coach sections), pages from capabilities.
 */

export type Portal = "staff" | "finance" | "coach" | "parent" | "student";

export const PORTAL_ROLES: Record<Portal, Role[]> = {
  staff: ["SUPER_ADMIN", "ADMIN", "FINANCE_ADMIN"],
  // Finance staff pages (Phase 6F); each page still needs its own finance capability.
  finance: ["SUPER_ADMIN", "ADMIN", "FINANCE_ADMIN"],
  coach: ["COACH"],
  parent: ["PARENT"],
  student: ["STUDENT"],
};

export const PORTAL_LABELS: Record<Portal, string> = {
  staff: "Academy staff",
  finance: "Finance",
  coach: "Coaching",
  parent: "My family",
  student: "My training",
};

export const ROLE_LABELS: Record<Role, string> = {
  SUPER_ADMIN: "Super admin",
  ADMIN: "Admin",
  FINANCE_ADMIN: "Finance admin",
  COACH: "Coach",
  PARENT: "Parent",
  STUDENT: "Student",
};

export function hasRole(me: Me | null | undefined, role: Role): boolean {
  return !!me?.roles.includes(role);
}

export function inPortal(me: Me | null | undefined, portal: Portal): boolean {
  return PORTAL_ROLES[portal].some((role) => hasRole(me, role));
}

export function portalsOf(me: Me | null | undefined): Portal[] {
  return (Object.keys(PORTAL_ROLES) as Portal[]).filter((portal) => inPortal(me, portal));
}

/** True if the user holds the capability, or any of them when given a list. */
export function can(me: Me | null | undefined, capability: string | readonly string[]): boolean {
  if (!me) return false;
  const wanted = typeof capability === "string" ? [capability] : capability;
  return wanted.length === 0 || wanted.some((cap) => me.capabilities.includes(cap));
}

export function allowed(me: Me | null | undefined, portal: Portal, capabilities: readonly string[]): boolean {
  return inPortal(me, portal) && can(me, capabilities);
}
