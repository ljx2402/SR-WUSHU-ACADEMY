/**
 * Every API failure becomes one of these errors. Components show
 * `userMessage` (never raw response bodies or stack traces). Validation errors
 * carry field-level messages returned by the API.
 */

export type ApiErrorKind =
  | "unauthorized" // 401: missing, expired or revoked sign-in
  | "forbidden" // 403: signed in, but not permitted
  | "not_found" // 404: missing, or hidden from this user (never distinguished)
  | "validation" // 400: the request was refused, with messages
  | "rate_limited" // 429
  | "server" // 5xx
  | "network" // no response
  | "timeout"; // no response in time

export const USER_MESSAGES: Record<ApiErrorKind, string> = {
  unauthorized: "Your session has expired. Please sign in again.",
  forbidden: "You don’t have permission to access this page.",
  not_found: "Record not found.",
  validation: "Please check the highlighted fields.",
  rate_limited: "Too many requests. Please wait a minute and try again.",
  server: "Something went wrong on our side. Please try again later.",
  network: "Unable to connect. Please try again.",
  timeout: "The server took too long to respond. Please try again.",
};

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  /** Field name -> messages, from a DRF validation response. */
  readonly fieldErrors: Record<string, string[]>;
  /** Messages not tied to one field (DRF "detail" / "non_field_errors"). */
  readonly messages: string[];

  constructor(
    kind: ApiErrorKind,
    status: number | null,
    fieldErrors: Record<string, string[]> = {},
    messages: string[] = [],
  ) {
    super(USER_MESSAGES[kind]);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.fieldErrors = fieldErrors;
    this.messages = messages;
  }

  /** The best message to show a person. */
  get userMessage(): string {
    if ((this.kind === "validation" || this.kind === "rate_limited") && this.messages.length) {
      return this.messages.join(" ");
    }
    return USER_MESSAGES[this.kind];
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

function asMessages(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(asMessages);
  if (value && typeof value === "object") return Object.values(value).flatMap(asMessages);
  return [];
}

/**
 * Normalise a DRF error body:
 *   {"detail": "..."} | {"detail": ["...", "..."]} | {"non_field_errors": [...]} | {"field": ["..."]}
 * Anything unexpected is ignored rather than shown.
 */
export function parseErrorBody(body: unknown): { fieldErrors: Record<string, string[]>; messages: string[] } {
  const fieldErrors: Record<string, string[]> = {};
  const messages: string[] = [];
  if (typeof body === "string") return { fieldErrors, messages };
  if (Array.isArray(body)) return { fieldErrors, messages: asMessages(body) };
  if (!body || typeof body !== "object") return { fieldErrors, messages };
  for (const [key, value] of Object.entries(body as Record<string, unknown>)) {
    const texts = asMessages(value);
    if (!texts.length) continue;
    if (key === "detail" || key === "non_field_errors") messages.push(...texts);
    else fieldErrors[key] = texts;
  }
  return { fieldErrors, messages };
}

export function errorKindForStatus(status: number): ApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 429) return "rate_limited";
  if (status >= 500) return "server";
  return "validation";
}
