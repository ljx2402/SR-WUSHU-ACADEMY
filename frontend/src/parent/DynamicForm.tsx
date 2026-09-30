import { useId } from "react";

import type { FormAnswer, FormFieldDefinition } from "../api/types";
import { TextAreaField, TextField } from "../ui/Field";

/**
 * Renders a competition's registration questions from the form definition the
 * backend publishes. Nothing about any particular competition is written
 * here: the questions, their types, options and limits all come from the API.
 * Validation here is only for convenience; the backend validates every answer.
 */

export type AnswerValue = string | string[] | boolean | null;
export type Answers = Record<string, AnswerValue>;
export type FieldErrors = Record<string, string[] | undefined>;

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE = /^\+?[0-9][0-9 ()-]{5,19}$/;
const NUMBER = /^-?\d+(\.\d+)?$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TEXT_LIMITS: Record<string, number> = { TEXT: 200, LONG_TEXT: 2000, EMAIL: 254, PHONE: 20 };

export const errorKey = (key: string) => `responses.${key}`;

function isEmpty(value: AnswerValue | undefined) {
  return value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0);
}

/** Convenience checks mirroring the backend's rules (the backend decides). */
export function validateAnswers(fields: FormFieldDefinition[], answers: Answers): FieldErrors {
  const errors: FieldErrors = {};
  for (const field of fields) {
    const value = answers[field.key];
    const key = errorKey(field.key);
    if (isEmpty(value)) {
      if (field.required) errors[key] = ["This field is required."];
      continue;
    }
    const text = typeof value === "string" ? value.trim() : "";
    const limit = field.max_length ?? TEXT_LIMITS[field.type];
    if (typeof value === "string" && limit && text.length > limit) errors[key] = [`At most ${limit} characters.`];
    else if (field.type === "EMAIL" && !EMAIL.test(text)) errors[key] = ["Enter a valid email address."];
    else if (field.type === "PHONE" && !PHONE.test(text)) errors[key] = ["Enter a valid phone number."];
    else if (field.type === "DATE" && !DATE.test(text)) errors[key] = ["Enter a date."];
    else if (field.type === "NUMBER") {
      if (!NUMBER.test(text)) errors[key] = ["Enter a number."];
      else if (field.min_value !== null && Number(text) < Number(field.min_value)) errors[key] = [`Must be at least ${field.min_value}.`];
      else if (field.max_value !== null && Number(text) > Number(field.max_value)) errors[key] = [`Must be at most ${field.max_value}.`];
    } else if (field.type === "SINGLE_SELECT" && !field.options.includes(text)) errors[key] = ["Choose one of the listed options."];
  }
  return errors;
}

/** The request body's `responses`: only answered questions, trimmed. */
export function toResponses(fields: FormFieldDefinition[], answers: Answers): Record<string, AnswerValue> {
  const out: Record<string, AnswerValue> = {};
  for (const field of fields) {
    const value = answers[field.key];
    if (isEmpty(value)) continue;
    out[field.key] = typeof value === "string" ? value.trim() : value;
  }
  return out;
}

/** "Yes" / "No" / "A, B" / text: how an answer reads before it is submitted. */
export function describeAnswer(field: FormFieldDefinition, value: AnswerValue | undefined): string {
  if (isEmpty(value)) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return field.options.filter((o) => value.includes(o)).join(", ");
  return String(value).trim();
}

export function DynamicForm({ fields, answers, errors, onChange }: {
  fields: FormFieldDefinition[];
  answers: Answers;
  errors: FieldErrors;
  onChange: (key: string, value: AnswerValue) => void;
}) {
  return (
    <div className="dynamic-form">
      {fields.map((field) => (
        <DynamicField key={field.key} field={field} value={answers[field.key] ?? null}
                      errors={errors[errorKey(field.key)]} onChange={(v) => onChange(field.key, v)} />
      ))}
    </div>
  );
}

function DynamicField({ field, value, errors, onChange }: {
  field: FormFieldDefinition;
  value: AnswerValue;
  errors?: string[];
  onChange: (value: AnswerValue) => void;
}) {
  const id = `response-${field.key}`;
  const groupId = useId();
  const common = {
    id,
    label: field.label,
    required: field.required,
    hint: field.help_text || undefined,
    errors,
    placeholder: field.placeholder || undefined,
  };
  const text = typeof value === "string" ? value : "";
  switch (field.type) {
    case "LONG_TEXT":
      return <TextAreaField {...common} rows={3} maxLength={field.max_length ?? 2000} value={text}
                            onChange={(e) => onChange(e.target.value)} />;
    case "NUMBER":
      return <TextField {...common} inputMode="decimal" value={text} onChange={(e) => onChange(e.target.value)} />;
    case "DATE":
      return <TextField {...common} type="date" value={text} onChange={(e) => onChange(e.target.value)} />;
    case "EMAIL":
      return <TextField {...common} type="email" autoComplete="email" maxLength={254} value={text}
                        onChange={(e) => onChange(e.target.value)} />;
    case "PHONE":
      return <TextField {...common} type="tel" autoComplete="tel" maxLength={20} value={text}
                        onChange={(e) => onChange(e.target.value)} />;
    case "SINGLE_SELECT":
      return (
        <div className={`field${errors ? " field-invalid" : ""}`}>
          <label htmlFor={id}>{field.label}{field.required ? <span className="required" aria-hidden="true"> *</span> : null}</label>
          {field.help_text ? <p id={`${id}-hint`} className="field-hint">{field.help_text}</p> : null}
          <select id={id} value={text} required={field.required} aria-invalid={errors ? true : undefined}
                  aria-describedby={[field.help_text ? `${id}-hint` : "", errors ? `${id}-error` : ""].filter(Boolean).join(" ") || undefined}
                  onChange={(e) => onChange(e.target.value || null)}>
            <option value="">{field.placeholder || "Choose…"}</option>
            {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
          </select>
          {errors ? <p id={`${id}-error`} className="field-error">{errors.join(" ")}</p> : null}
        </div>
      );
    case "MULTI_SELECT":
    case "YES_NO": {
      const multi = field.type === "MULTI_SELECT";
      const chosen = Array.isArray(value) ? value : [];
      return (
        <fieldset id={id} tabIndex={-1} className={`field choices${errors ? " field-invalid" : ""}`}
                  aria-describedby={[field.help_text ? `${groupId}-hint` : "", errors ? `${groupId}-error` : ""].filter(Boolean).join(" ") || undefined}>
          <legend>{field.label}{field.required ? <span className="required" aria-hidden="true"> *</span> : null}</legend>
          {field.help_text ? <p id={`${groupId}-hint`} className="field-hint">{field.help_text}</p> : null}
          {multi ? field.options.map((option) => (
            <label key={option} className="choice">
              <input type="checkbox" checked={chosen.includes(option)}
                     onChange={(e) => onChange(e.target.checked ? [...chosen, option] : chosen.filter((o) => o !== option))} />
              <span>{option}</span>
            </label>
          )) : [true, false].map((option) => (
            <label key={String(option)} className="choice">
              <input type="radio" name={id} checked={value === option} onChange={() => onChange(option)} />
              <span>{option ? "Yes" : "No"}</span>
            </label>
          ))}
          {errors ? <p id={`${groupId}-error`} className="field-error">{errors.join(" ")}</p> : null}
        </fieldset>
      );
    }
    default:
      return <TextField {...common} maxLength={field.max_length ?? 200} value={text}
                        onChange={(e) => onChange(e.target.value)} />;
  }
}

/** Submitted answers as stored with the registration (plain text; React escapes it). */
export function AnswerList({ answers }: { answers: FormAnswer[] }) {
  if (!answers.length) return null;
  return (
    <dl className="answer-list">
      {answers.map((answer) => (
        <div key={answer.key}>
          <dt>{answer.label}</dt>
          <dd className="prewrap">{answer.display || "—"}</dd>
        </div>
      ))}
    </dl>
  );
}
