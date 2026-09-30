import { useId, type InputHTMLAttributes, type TextareaHTMLAttributes } from "react";

interface FieldBase {
  label: string;
  hint?: string;
  errors?: string[];
}

function describedBy(ids: (string | false | undefined)[]) {
  const value = ids.filter(Boolean).join(" ");
  return value || undefined;
}

/** A labelled input whose hint and errors are announced with it (aria-describedby). */
export function TextField({ label, hint, errors = [], id, required, ...rest }: FieldBase &
  InputHTMLAttributes<HTMLInputElement>) {
  const generated = useId();
  const inputId = id ?? generated;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errorId = errors.length ? `${inputId}-error` : undefined;
  return (
    <div className={`field${errors.length ? " field-invalid" : ""}`}>
      <label htmlFor={inputId}>
        {label}
        {required ? <span className="required" aria-hidden="true"> *</span> : null}
      </label>
      {hint ? <p id={hintId} className="field-hint">{hint}</p> : null}
      <input
        id={inputId}
        required={required}
        aria-invalid={errors.length ? true : undefined}
        aria-describedby={describedBy([hintId, errorId])}
        {...rest}
      />
      {errors.length ? (
        <p id={errorId} className="field-error">{errors.join(" ")}</p>
      ) : null}
    </div>
  );
}

export function TextAreaField({ label, hint, errors = [], id, required, ...rest }: FieldBase &
  TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const generated = useId();
  const inputId = id ?? generated;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errorId = errors.length ? `${inputId}-error` : undefined;
  return (
    <div className={`field${errors.length ? " field-invalid" : ""}`}>
      <label htmlFor={inputId}>
        {label}
        {required ? <span className="required" aria-hidden="true"> *</span> : null}
      </label>
      {hint ? <p id={hintId} className="field-hint">{hint}</p> : null}
      <textarea
        id={inputId}
        required={required}
        aria-invalid={errors.length ? true : undefined}
        aria-describedby={describedBy([hintId, errorId])}
        {...rest}
      />
      {errors.length ? <p id={errorId} className="field-error">{errors.join(" ")}</p> : null}
    </div>
  );
}
