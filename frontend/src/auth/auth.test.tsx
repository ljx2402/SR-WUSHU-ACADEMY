import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { json, makeMe, renderApp } from "../test/helpers";

const parent = makeMe(["PARENT"], { name: "Aisha Rahman", children: [{ id: 5, student_no: "S0005", full_name: "Lina Rahman" }] });

describe("login", () => {
  it("shows field-level errors for empty fields without calling the API", async () => {
    const { user, fetchImpl } = renderApp({ route: "/login", token: null });
    await user.click(await screen.findByRole("button", { name: "Sign in" }));
    const username = screen.getByLabelText(/Username/);
    expect(username).toHaveAttribute("aria-invalid", "true");
    expect(username).toHaveAccessibleDescription("Enter your username.");
    expect(screen.getByLabelText(/Password/)).toHaveAccessibleDescription("Enter your password.");
    expect(username).toHaveFocus();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("signs in, stores the token and opens the dashboard", async () => {
    const { user, tokens, router, fetchImpl } = renderApp({
      route: "/login",
      token: null,
      routes: {
        "POST /api/auth/token/": ({ body }) =>
          (body as { username: string; password: string }).password === "right"
            ? json({ token: "fresh-token" }) : json({ non_field_errors: ["x"] }, 400),
        "GET /api/me/": ({ headers }) =>
          headers.Authorization === "Token fresh-token" ? json(parent) : json({ detail: "Invalid token." }, 401),
      },
    });
    await user.type(await screen.findByLabelText(/Username/), "aisha");
    await user.type(screen.getByLabelText(/Password/), "right");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    // A parent-only account lands on the Parent Portal overview.
    expect(await screen.findByRole("heading", { name: "Family overview" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/parent/dashboard");
    expect(tokens.value).toBe("fresh-token");
    expect(screen.getByText(/Welcome, Aisha Rahman/)).toBeInTheDocument();
    // The sign-in request itself never carries an old token.
    const signIn = fetchImpl.mock.calls.find(([url]) => String(url).includes("/api/auth/token/"))!;
    expect((signIn[1] as RequestInit).headers).not.toHaveProperty("Authorization");
  });

  it("shows the backend's generic failure message and clears the password", async () => {
    const { user, tokens } = renderApp({
      route: "/login",
      token: null,
      routes: {
        "POST /api/auth/token/": json({ non_field_errors: ["Unable to sign in with the provided credentials."] }, 400),
      },
    });
    await user.type(await screen.findByLabelText(/Username/), "aisha");
    await user.type(screen.getByLabelText(/Password/), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Unable to sign in with the provided credentials.")).toBeInTheDocument();
    expect(screen.getByLabelText(/Password/)).toHaveValue("");
    expect(tokens.value).toBeNull();
  });

  it("explains throttling (429)", async () => {
    const { user } = renderApp({
      route: "/login",
      token: null,
      routes: { "POST /api/auth/token/": json({ detail: "Request was throttled. Expected available in 30 seconds." }, 429) },
    });
    await user.type(await screen.findByLabelText(/Username/), "aisha");
    await user.type(screen.getByLabelText(/Password/), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/Request was throttled/)).toBeInTheDocument();
  });

  it("shows a connection error when the server cannot be reached", async () => {
    const { user } = renderApp({
      route: "/login",
      token: null,
      routes: { "POST /api/auth/token/": () => { throw new TypeError("Failed to fetch"); } },
    });
    await user.type(await screen.findByLabelText(/Username/), "aisha");
    await user.type(screen.getByLabelText(/Password/), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Unable to connect. Please try again.")).toBeInTheDocument();
  });

  it("a signed-in user visiting /login goes to the dashboard", async () => {
    const { router } = renderApp({ route: "/login", routes: { "GET /api/me/": json(parent) } });
    await screen.findByRole("heading", { name: "Family overview" });
    expect(router.state.location.pathname).toBe("/parent/dashboard");
  });
});

describe("protected routes", () => {
  it("send signed-out users to /login and back to the page after signing in", async () => {
    const { user, router } = renderApp({
      route: "/parent/finance",
      token: null,
      routes: { "POST /api/auth/token/": json({ token: "t2" }), "GET /api/me/": json(parent) },
    });
    await screen.findByRole("heading", { name: "Sign in" });
    expect(router.state.location.pathname).toBe("/login");
    await user.type(screen.getByLabelText(/Username/), "aisha");
    await user.type(screen.getByLabelText(/Password/), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Family finance", level: 1 })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/parent/finance");
  });
});

describe("logout", () => {
  it("revokes the token on the server, clears it locally and returns to login", async () => {
    let loggedOut = false;
    const { user, tokens, router } = renderApp({
      routes: {
        "GET /api/me/": json(parent),
        "POST /api/auth/logout/": () => { loggedOut = true; return new Response(null, { status: 204 }); },
      },
    });
    await user.click(await screen.findByRole("button", { name: /Aisha Rahman/ }));
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByText("You have signed out.")).toBeInTheDocument();
    expect(loggedOut).toBe(true);
    expect(tokens.value).toBeNull();
    expect(router.state.location.pathname).toBe("/login");
  });

  it("still signs out locally when the server cannot be reached", async () => {
    const { user, tokens } = renderApp({
      routes: {
        "GET /api/me/": json(parent),
        "POST /api/auth/logout/": () => { throw new TypeError("offline"); },
      },
    });
    await user.click(await screen.findByRole("button", { name: /Aisha Rahman/ }));
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(tokens.value).toBeNull();
  });
});

describe("expired or revoked session", () => {
  it("a 401 from /api/me/ clears the token and shows the expiry notice", async () => {
    const { tokens, router } = renderApp({ routes: { "GET /api/me/": json({ detail: "Token has expired." }, 401) } });
    expect(await screen.findByText("Your session has expired or was ended. Please sign in again.")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(tokens.value).toBeNull();
    expect(screen.queryByText(/Token has expired/)).not.toBeInTheDocument();
  });

  it("a 401 from any later request signs the user out", async () => {
    const { tokens } = renderApp({
      routes: {
        "GET /api/me/": json(parent),
        "GET /api/sessions/": json({ detail: "Invalid token." }, 401),
      },
    });
    expect(await screen.findByText("Your session has expired or was ended. Please sign in again.")).toBeInTheDocument();
    expect(tokens.value).toBeNull();
  });

  it("a connection failure while checking the session offers a retry, not a sign-out", async () => {
    let calls = 0;
    const { user, tokens } = renderApp({
      routes: {
        "GET /api/me/": () => {
          calls += 1;
          if (calls === 1) throw new TypeError("offline");
          return json(parent);
        },
      },
    });
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Unable to connect. Please try again.")).toBeInTheDocument();
    expect(tokens.value).toBe("tok-123");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Family overview" })).toBeInTheDocument());
  });
});
