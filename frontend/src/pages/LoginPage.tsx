import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router";

import { isApiError } from "../api/errors";
import { useAuth } from "../auth/AuthProvider";
import { Button } from "../ui/Button";
import { TextField } from "../ui/Field";
import { Alert } from "../ui/primitives";
import { LoadingState } from "../ui/states";

type FieldErrors = { username?: string[]; password?: string[] };

/**
 * Sign-in with the existing Django accounts (POST /api/auth/token/).
 * The backend answers every failed sign-in with the same generic message
 * (no hint whether the username exists or is locked), and so does this page.
 */
export function LoginPage() {
  const { status, signIn, notice, error: authError, retry } = useAuth();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    document.title = "Sign in · SR Wushu Academy";
  }, []);

  if (status === "signedIn") {
    const from = (location.state as { from?: { pathname?: string; search?: string } } | null)?.from;
    const target = from?.pathname && from.pathname !== "/login" ? `${from.pathname}${from.search ?? ""}` : "/dashboard";
    return <Navigate to={target} replace />;
  }
  if (status === "checking" && !submitting) return <LoadingState fullPage />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const errors: FieldErrors = {};
    if (!username.trim()) errors.username = ["Enter your username."];
    if (!password) errors.password = ["Enter your password."];
    setFieldErrors(errors);
    setFormError(null);
    if (errors.username || errors.password) {
      document.getElementById(errors.username ? "login-username" : "login-password")?.focus();
      return;
    }
    setSubmitting(true);
    try {
      await signIn(username.trim(), password);
    } catch (error) {
      setPassword("");
      if (isApiError(error) && error.kind === "validation") {
        // Field errors from the API (e.g. blank fields) go to their inputs.
        setFieldErrors({ username: error.fieldErrors.username, password: error.fieldErrors.password });
        setFormError(error.messages.length ? error.userMessage : null);
      } else {
        setFormError(isApiError(error) ? error.userMessage : "Unable to connect. Please try again.");
      }
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <div className="card login-card">
        <div className="card-body">
          <p className="login-brand"><span className="brand-mark" aria-hidden="true">SR</span> SR Wushu Academy</p>
          <h1>Sign in</h1>
          {notice ? <Alert tone="info" role="status">{notice}</Alert> : null}
          {status === "error" && authError ? (
            <Alert tone="danger" title={authError.userMessage}>
              <Button variant="secondary" onClick={retry}>Try again</Button>
            </Alert>
          ) : null}
          {formError ? <Alert tone="danger">{formError}</Alert> : null}
          <form onSubmit={onSubmit} noValidate aria-label="Sign in">
            <TextField
              id="login-username"
              label="Username"
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
              value={username}
              errors={fieldErrors.username}
              onChange={(e) => setUsername(e.target.value)}
            />
            <TextField
              id="login-password"
              label="Password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              errors={fieldErrors.password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <Button type="submit" className="btn-block" busy={submitting}>Sign in</Button>
          </form>
          <p className="muted login-help">
            Forgotten your password? Ask the academy office to reset it.
          </p>
        </div>
      </div>
    </main>
  );
}
