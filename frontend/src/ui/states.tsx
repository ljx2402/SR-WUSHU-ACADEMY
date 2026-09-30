import type { ReactNode } from "react";
import { Link } from "react-router";

import { ApiError, USER_MESSAGES } from "../api/errors";
import { Button } from "./Button";

/**
 * Standard page states. Messages are fixed, friendly texts: backend details,
 * stack traces and raw responses are never shown.
 */

function StateBlock({ title, children, fullPage, role = "status", testId }: {
  title: string;
  children?: ReactNode;
  fullPage?: boolean;
  role?: "status" | "alert";
  testId?: string;
}) {
  return (
    <div className={`state${fullPage ? " state-full" : ""}`} role={role} data-testid={testId}>
      <p className="state-title">{title}</p>
      {children ? <div className="state-body">{children}</div> : null}
    </div>
  );
}

export function LoadingState({ label = "Loading…", fullPage }: { label?: string; fullPage?: boolean }) {
  return (
    <div className={`state${fullPage ? " state-full" : ""}`} role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p className="state-title">{label}</p>
    </div>
  );
}

export function EmptyState({ message = "No records found.", children }: { message?: string; children?: ReactNode }) {
  return <StateBlock title={message}>{children}</StateBlock>;
}

export function AccessDeniedState() {
  return (
    <StateBlock title={USER_MESSAGES.forbidden} role="alert" testId="access-denied">
      <p>If you think you should have access, ask an academy administrator.</p>
      <p><Link to="/dashboard">Back to the dashboard</Link></p>
    </StateBlock>
  );
}

export function NotFoundState({ message = USER_MESSAGES.not_found }: { message?: string }) {
  return (
    <StateBlock title={message} testId="not-found">
      <p><Link to="/dashboard">Back to the dashboard</Link></p>
    </StateBlock>
  );
}

/** Maps any API error to the right standard state. */
export function ErrorState({ error, onRetry, fullPage }: { error: unknown; onRetry?: () => void; fullPage?: boolean }) {
  const apiError = error instanceof ApiError ? error : new ApiError("network", null);
  if (apiError.kind === "forbidden") return <AccessDeniedState />;
  if (apiError.kind === "not_found") return <NotFoundState />;
  return (
    <StateBlock title={apiError.userMessage} role="alert" fullPage={fullPage}>
      {onRetry ? <Button variant="secondary" onClick={onRetry}>Try again</Button> : null}
    </StateBlock>
  );
}
