import type { ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router";

import { PageHeader } from "../layout/PageHeader";
import { AccessDeniedState, ErrorState, LoadingState } from "../ui/states";
import { allowed, type Portal } from "./access";
import { useAuth } from "./AuthProvider";

/** Only signed-in users get past this; others go to /login and come back afterwards. */
export function RequireAuth() {
  const { status, error, retry } = useAuth();
  const location = useLocation();
  if (status === "checking") return <LoadingState fullPage />;
  if (status === "error" && error) return <ErrorState error={error} onRetry={retry} fullPage />;
  if (status !== "signedIn") return <Navigate to="/login" replace state={{ from: location }} />;
  return <Outlet />;
}

/**
 * Direct navigation to a page the user may not use shows "access denied"
 * (the link is also hidden, but hiding links is not the protection; the API
 * refuses the data anyway).
 */
export function RequireAccess({ portal, capabilities, children }: {
  portal: Portal;
  capabilities: readonly string[];
  children: ReactNode;
}) {
  const { me } = useAuth();
  if (!allowed(me, portal, capabilities)) {
    return (
      <>
        <PageHeader title="Access denied" />
        <AccessDeniedState />
      </>
    );
  }
  return <>{children}</>;
}
