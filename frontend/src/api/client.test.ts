import { describe, expect, it, vi } from "vitest";

import { json } from "../test/helpers";
import { ApiClient } from "./client";
import { ApiError, parseErrorBody, USER_MESSAGES } from "./errors";

function client(fetchImpl: (...args: unknown[]) => Promise<Response>, extra: Partial<ConstructorParameters<typeof ApiClient>[0]> = {}) {
  const onUnauthorized = vi.fn();
  const api = new ApiClient({
    baseUrl: "https://academy.example/",
    timeoutMs: 50,
    getToken: () => "secret-token",
    onUnauthorized,
    fetchImpl: fetchImpl as unknown as typeof fetch,
    ...extra,
  });
  return { api, onUnauthorized };
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected the request to fail");
}

describe("ApiClient", () => {
  it("sends the token, JSON headers and query, and never cookies", async () => {
    const fetchImpl = vi.fn(async () => json({ ok: true }));
    const { api } = client(fetchImpl);
    await api.get("/api/sessions/", { date: "2026-10-01", empty: "", skip: undefined });
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://academy.example/api/sessions/?date=2026-10-01");
    expect(init.headers).toMatchObject({ Authorization: "Token secret-token", Accept: "application/json" });
    expect(init.credentials).toBe("omit");
  });

  it("does not send the token on anonymous requests", async () => {
    const fetchImpl = vi.fn(async () => json({ token: "t" }));
    const { api } = client(fetchImpl);
    await api.request("/api/auth/token/", { method: "POST", body: { username: "a" }, anonymous: true });
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(init.body).toBe('{"username":"a"}');
  });

  it("returns undefined for 204 No Content", async () => {
    const { api } = client(async () => new Response(null, { status: 204 }));
    await expect(api.post("/api/auth/logout/")).resolves.toBeUndefined();
  });

  it("401 calls the unauthorized handler and hides the backend text", async () => {
    const { api, onUnauthorized } = client(async () => json({ detail: "Token has expired." }, 401));
    const error = await failure(api.get("/api/me/"));
    expect(error.kind).toBe("unauthorized");
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(error.userMessage).toBe(USER_MESSAGES.unauthorized);
  });

  it("401 on an anonymous request does not trigger the sign-out handler", async () => {
    const { api, onUnauthorized } = client(async () => json({ detail: "x" }, 401));
    await failure(api.request("/api/auth/token/", { method: "POST", body: {}, anonymous: true }));
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("403 becomes the standard permission message", async () => {
    const { api, onUnauthorized } = client(async () => json({ detail: "You lack finance.view_all" }, 403));
    const error = await failure(api.get("/api/invoices/"));
    expect(error.kind).toBe("forbidden");
    expect(error.userMessage).toBe("You don’t have permission to access this page.");
    expect(error.messages).toEqual([]);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("404 becomes 'Record not found.'", async () => {
    const { api } = client(async () => json({ detail: "No Student matches the given query." }, 404));
    const error = await failure(api.get("/api/students/99/"));
    expect(error.kind).toBe("not_found");
    expect(error.userMessage).toBe("Record not found.");
  });

  it("400 keeps field and general validation messages", async () => {
    const { api } = client(async () =>
      json({ amount: ["Ensure this value is greater than 0."], non_field_errors: ["Invoice is void."] }, 400));
    const error = await failure(api.post("/api/payments/", {}));
    expect(error.kind).toBe("validation");
    expect(error.fieldErrors).toEqual({ amount: ["Ensure this value is greater than 0."] });
    expect(error.userMessage).toBe("Invoice is void.");
  });

  it("500 and HTML error pages never reach the user", async () => {
    const { api } = client(async () => new Response("<html>Traceback (most recent call last)…</html>", { status: 500 }));
    const error = await failure(api.get("/api/me/"));
    expect(error.kind).toBe("server");
    expect(error.userMessage).not.toMatch(/Traceback/);
    expect(error.message).not.toMatch(/Traceback/);
  });

  it("network failures give the standard connection message", async () => {
    const { api } = client(async () => { throw new TypeError("Failed to fetch"); });
    const error = await failure(api.get("/api/me/"));
    expect(error.kind).toBe("network");
    expect(error.userMessage).toBe("Unable to connect. Please try again.");
  });

  it("aborts slow requests with a timeout error", async () => {
    const { api } = client((_url, init) => new Promise((_resolve, reject) => {
      (init as RequestInit).signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    const error = await failure(api.get("/api/me/"));
    expect(error.kind).toBe("timeout");
  });

  it("429 shows the backend's throttle message", async () => {
    const { api } = client(async () => json({ detail: "Request was throttled. Expected available in 42 seconds." }, 429));
    const error = await failure(api.get("/api/me/"));
    expect(error.kind).toBe("rate_limited");
    expect(error.userMessage).toMatch(/throttled/);
  });
});

describe("parseErrorBody", () => {
  it("handles detail lists (Django ValidationError) and nested values", () => {
    expect(parseErrorBody({ detail: ["A.", "B."] })).toEqual({ fieldErrors: {}, messages: ["A.", "B."] });
    expect(parseErrorBody({ records: [{ status: ["Bad."] }] })).toEqual({ fieldErrors: { records: ["Bad."] }, messages: [] });
    expect(parseErrorBody("<html>")).toEqual({ fieldErrors: {}, messages: [] });
    expect(parseErrorBody(null)).toEqual({ fieldErrors: {}, messages: [] });
  });
});
