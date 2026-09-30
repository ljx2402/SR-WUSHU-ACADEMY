import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  busy?: boolean;
  icon?: ReactNode;
}

/** Buttons default to type="button" so they never submit a form by accident. */
export function Button({ variant = "primary", busy = false, icon, children, className = "", disabled, type = "button",
                         ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={`btn btn-${variant} ${className}`.trim()}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...rest}
    >
      {icon ? <span className="btn-icon" aria-hidden="true">{icon}</span> : null}
      <span>{busy ? "Please wait…" : children}</span>
    </button>
  );
}
