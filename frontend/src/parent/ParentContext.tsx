import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { Outlet, useSearchParams } from "react-router";

import type { PersonRef } from "../api/types";
import { useMe } from "../auth/AuthProvider";

/**
 * Parent Portal context: the signed-in parent's children (from /api/me/) and
 * which child is being viewed. The choice is kept in the URL (?student=) so it
 * survives reloads and can be shared between the parent's own tabs, and is
 * remembered while moving between portal pages.
 *
 * The list only decides what the selector offers; every request is still
 * scoped by the backend, and an id typed into the URL that is not one of the
 * parent's children is ignored here and refused (404) by the API.
 */
export type Selection = number | "all";

interface ParentContextValue {
  children: PersonRef[];
  remembered: Selection | null;
  remember(selection: Selection): void;
}

const ParentContext = createContext<ParentContextValue | null>(null);

export function ParentLayout() {
  const me = useMe();
  const [remembered, setRemembered] = useState<Selection | null>(null);
  const children = useMemo(
    () => [...(me.children ?? [])].sort((a, b) => a.full_name.localeCompare(b.full_name)),
    [me.children],
  );
  const remember = useCallback((selection: Selection) => setRemembered(selection), []);
  const value = useMemo(() => ({ children, remembered, remember }), [children, remembered, remember]);
  return (
    <ParentContext.Provider value={value}>
      <Outlet />
    </ParentContext.Provider>
  );
}

export function useParent(): ParentContextValue {
  const value = useContext(ParentContext);
  if (!value) throw new Error("useParent must be used inside ParentLayout");
  return value;
}

/** The child being viewed (or "all" where a page supports it), and a way to switch. */
export function useSelectedStudent({ allowAll = false } = {}) {
  const { children, remembered, remember } = useParent();
  const [params, setParams] = useSearchParams();
  const ids = children.map((child) => child.id);
  const raw = params.get("student");

  let selected: Selection | null;
  if (raw === "all" && allowAll) selected = "all";
  else if (raw && ids.includes(Number(raw))) selected = Number(raw);
  else if (remembered === "all" && allowAll) selected = "all";
  else if (typeof remembered === "number" && ids.includes(remembered)) selected = remembered;
  else selected = allowAll && ids.length > 1 ? "all" : (ids[0] ?? null);

  const select = useCallback(
    (next: Selection) => {
      remember(next);
      setParams((current) => {
        const updated = new URLSearchParams(current);
        updated.set("student", String(next));
        return updated;
      }, { replace: true });
    },
    [remember, setParams],
  );

  const child = typeof selected === "number" ? children.find((c) => c.id === selected) ?? null : null;
  return { children, selected, child, select };
}

