import { ApiError, errorKindForStatus, parseErrorBody } from "./errors";

/**
 * The one place that talks HTTP to the Django API.
 *
 * - Adds `Authorization: Token …` when signed in (the token is never logged).
 * - Parses JSON; turns every failure into an ApiError (see errors.ts).
 * - 401 anywhere means the sign-in expired or was revoked (role change,
 *   password change, deactivation, logout elsewhere): the registered handler
 *   clears the local sign-in and sends the user to the login page.
 * - Aborts requests that take longer than the timeout.
 */

export type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** Sign-in requests must not send (or react to) the stored token. */
  anonymous?: boolean;
  /** "blob" for file downloads (the response body is returned as a Blob). */
  responseType?: "json" | "blob";
  /** Sent as `Idempotency-Key`: a retried payment submission never records twice (backend rule). */
  idempotencyKey?: string;
}

export interface ApiClientConfig {
  baseUrl?: string;
  timeoutMs?: number;
  getToken: () => string | null;
  onUnauthorized: () => void;
  fetchImpl?: typeof fetch;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly getToken: () => string | null;
  private readonly onUnauthorized: () => void;
  private readonly fetchImpl: typeof fetch;

  constructor(config: ApiClientConfig) {
    this.baseUrl = (config.baseUrl ?? "").replace(/\/$/, "");
    this.timeoutMs = config.timeoutMs ?? 15000;
    this.getToken = config.getToken;
    this.onUnauthorized = config.onUnauthorized;
    this.fetchImpl = config.fetchImpl ?? ((...args) => fetch(...args));
  }

  url(path: string, query?: Record<string, QueryValue>): string {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
    }
    const qs = params.toString();
    return `${this.baseUrl}${path}${qs ? `?${qs}` : ""}`;
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    const token = options.anonymous ? null : this.getToken();
    if (token) headers.Authorization = `Token ${token}`;
    if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
    const isForm = typeof FormData !== "undefined" && options.body instanceof FormData;
    // FormData sets its own multipart Content-Type (with the boundary).
    if (options.body !== undefined && !isForm) headers["Content-Type"] = "application/json";

    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, options.timeoutMs ?? this.timeoutMs);
    const onCallerAbort = () => controller.abort();
    options.signal?.addEventListener("abort", onCallerAbort);

    let response: Response;
    try {
      response = await this.fetchImpl(this.url(path, options.query), {
        method: options.method ?? "GET",
        headers,
        body: options.body === undefined ? undefined : isForm ? (options.body as FormData) : JSON.stringify(options.body),
        signal: controller.signal,
        credentials: "omit", // token auth only: no cookies, so no CSRF exposure
      });
    } catch (error) {
      if (options.signal?.aborted) throw error; // the caller cancelled (e.g. unmounted)
      throw new ApiError(timedOut ? "timeout" : "network", null);
    } finally {
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", onCallerAbort);
    }

    if (response.ok) {
      if (response.status === 204) return undefined as T;
      if (options.responseType === "blob") return (await response.blob()) as T;
      const text = await response.text();
      if (!text) return undefined as T;
      try {
        return JSON.parse(text) as T;
      } catch {
        throw new ApiError("server", response.status);
      }
    }

    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null; // HTML error pages etc. are never shown
    }
    const kind = errorKindForStatus(response.status);
    if (kind === "unauthorized" && !options.anonymous) this.onUnauthorized();
    const { fieldErrors, messages } = parseErrorBody(body);
    // Only validation and rate-limit messages are meant for people; others use fixed text.
    const keep = kind === "validation" || kind === "rate_limited";
    throw new ApiError(kind, response.status, keep ? fieldErrors : {}, keep ? messages : []);
  }

  get<T>(path: string, query?: Record<string, QueryValue>, signal?: AbortSignal) {
    return this.request<T>(path, { query, signal });
  }

  post<T>(path: string, body?: unknown, options: Omit<RequestOptions, "method" | "body"> = {}) {
    return this.request<T>(path, { ...options, method: "POST", body: body ?? {} });
  }
}
