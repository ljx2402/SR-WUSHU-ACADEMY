import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { json, makeMe, renderApp } from "../../test/helpers";
import { competitionRoutes, financeMe, form } from "./fixtures";

type Fetch = ReturnType<typeof renderApp>["fetchImpl"];
type Call = { url: URL; method: string; body: Record<string, unknown> | undefined };
function calls(fetchImpl: Fetch): Call[] {
  return fetchImpl.mock.calls.map(([url, init]) => {
    const i = (init ?? {}) as RequestInit;
    return { url: new URL(String(url), "http://x"), method: i.method ?? "GET",
             body: typeof i.body === "string" ? JSON.parse(i.body) : undefined };
  });
}
const writes = (f: Fetch) => calls(f).filter((c) => c.method !== "GET");
const gets = (f: Fetch, path: string) => calls(f).filter((c) => c.method === "GET" && c.url.pathname === path);

describe("competition dashboard", () => {
  it("lists competitions with backend statuses, form state and entry counts", async () => {
    renderApp({ route: "/staff/competitions", routes: competitionRoutes() });
    expect(await screen.findByRole("heading", { name: "Competitions", level: 1 })).toBeInTheDocument();
    const row = (await screen.findByRole("link", { name: "State Wushu Open" })).closest("tr")!;
    expect(row).toHaveTextContent("Open for registration");
    expect(row).toHaveTextContent("Published v2");
    expect(within(row).getByText("3")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Winter Invitational" }).closest("tr")).toHaveTextContent("Draft (staff only)");
    expect(screen.getByRole("link", { name: "New competition" })).toHaveAttribute("href", "/staff/competitions/new");
  });

  it("filters on the server and keeps the filter in the URL", async () => {
    const { user, fetchImpl, router } = renderApp({ route: "/staff/competitions", routes: competitionRoutes() });
    await screen.findByRole("link", { name: "State Wushu Open" });
    await user.selectOptions(screen.getByLabelText("Status"), "DRAFT");
    await waitFor(() => expect(screen.queryByRole("link", { name: "State Wushu Open" })).not.toBeInTheDocument());
    expect(gets(fetchImpl, "/api/competitions/").at(-1)!.url.searchParams.get("status")).toBe("DRAFT");
    expect(router.state.location.search).toBe("?status=DRAFT");
  });

  it("is in the staff menu for ADMIN and refused to roles without competition administration", async () => {
    renderApp({ route: "/staff/competitions", routes: competitionRoutes() });
    await screen.findByRole("heading", { name: "Competitions", level: 1 });
    const sidebar = screen.getByRole("complementary", { name: "Sidebar" });
    expect(within(sidebar).getByRole("link", { name: "Competitions" })).toHaveAttribute("href", "/staff/competitions");
  });

  it.each([
    ["FINANCE_ADMIN", financeMe],
    ["COACH", makeMe(["COACH"])],
    ["PARENT", makeMe(["PARENT"])],
    ["STUDENT", makeMe(["STUDENT"])],
  ])("%s gets access denied on every staff competition page", async (_role, me) => {
    for (const route of ["/staff/competitions", "/staff/competitions/81", "/staff/competitions/81/participants/91",
                         "/staff/competitions/81/registration-form", "/staff/competitions/81/results"]) {
      const { fetchImpl, unmount } = renderApp({ route, routes: competitionRoutes({ "GET /api/me/": json(me) }) });
      expect(await screen.findByRole("heading", { name: "Access denied" })).toBeInTheDocument();
      expect(calls(fetchImpl).filter((c) => c.url.pathname.startsWith("/api/compet"))).toEqual([]);
      unmount();
    }
  });
});

describe("competition detail", () => {
  it("shows details, entry counts and events without any family data", async () => {
    renderApp({ route: "/staff/competitions/81", routes: competitionRoutes() });
    expect(await screen.findByRole("heading", { name: "State Wushu Open", level: 1 })).toBeInTheDocument();
    const kpis = await screen.findByRole("region", { name: "Entries at a glance" });
    expect(within(kpis).getByText("Awaiting payment").closest(".kpi")).toHaveTextContent("1");
    expect(within(kpis).getByText("Confirmed").closest(".kpi")).toHaveTextContent("2");
    expect(within(kpis).getByText("1 confirmed without a result")).toBeInTheDocument();
    const events = screen.getByRole("table", { name: "Events at State Wushu Open" });
    expect(within(events).getByText("Changquan U12").closest("tr")).toHaveTextContent("2 / 20");
    expect(within(events).getByText("Nanquan Open").closest("tr")).toHaveTextContent("Free");
    const nav = screen.getByRole("navigation", { name: "Competition" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent))
      .toEqual(["Overview", "Participants", "Registration form", "Results"]);
    // No form answers, student names or proofs on the overview.
    expect(screen.getByRole("main").textContent).not.toMatch(/Aaron|T-shirt|slip\.png|Jersey/);
  });

  it("adds an event through the existing events endpoint", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/competitions/81", routes: competitionRoutes() });
    await user.click(await screen.findByRole("button", { name: "Add event" }));
    const dialog = screen.getByRole("dialog", { name: "Add event" });
    await user.type(within(dialog).getByLabelText(/^Name/), "Taijiquan Open");
    await user.selectOptions(within(dialog).getByLabelText("Event type"), "TAIJIQUAN");
    await user.clear(within(dialog).getByLabelText(/Fee/));
    await user.type(within(dialog).getByLabelText(/Fee/), "30");
    await user.click(within(dialog).getByRole("button", { name: "Add event" }));
    await waitFor(() => expect(writes(fetchImpl)).toHaveLength(1));
    expect(writes(fetchImpl)[0]).toMatchObject({ method: "POST", body: {
      competition: 81, name: "Taijiquan Open", event_type: "TAIJIQUAN", fee: "30", gender: "OPEN", max_entries: null } });
  });

  it("shows a retryable error when the summary fails and not-found for an unknown competition", async () => {
    renderApp({ route: "/staff/competitions/81",
                routes: competitionRoutes({ "GET /api/competitions/81/summary/": json({ detail: "x" }, 500) }) });
    expect(await screen.findByRole("button", { name: /Try again/i })).toBeInTheDocument();
  });

  it("an unknown competition is not found", async () => {
    renderApp({ route: "/staff/competitions/999", routes: competitionRoutes() });
    expect(await screen.findByText("Competition not found.")).toBeInTheDocument();
  });
});

describe("create and edit", () => {
  it("shows the backend's date rule and creates through the existing endpoint", async () => {
    const { user, fetchImpl, router } = renderApp({ route: "/staff/competitions/new", routes: competitionRoutes() });
    await user.type(await screen.findByLabelText(/^Name/), "Club Cup");
    await user.type(screen.getByLabelText(/Start date/), "2026-12-05");
    await user.type(screen.getByLabelText(/End date/), "2026-12-04");
    await user.type(screen.getByLabelText(/Registration deadline/), "2026-11-20");
    await user.click(screen.getByRole("button", { name: "Create competition" }));
    expect(await screen.findAllByText("End date cannot be before start date.")).not.toHaveLength(0);
    await user.clear(screen.getByLabelText(/End date/));
    await user.type(screen.getByLabelText(/End date/), "2026-12-06");
    await user.click(screen.getByRole("button", { name: "Create competition" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/staff/competitions/83"));
    expect(writes(fetchImpl).at(-1)).toMatchObject({ method: "POST", body: { name: "Club Cup", status: "DRAFT",
      start_date: "2026-12-05", end_date: "2026-12-06", max_events_per_student: null, age_reference_date: null } });
  });

  it("edits with PATCH and the backend's statuses", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/competitions/81/edit", routes: competitionRoutes() });
    const status = await screen.findByLabelText("Status");
    expect(within(status).getAllByRole("option").map((o) => o.getAttribute("value")))
      .toEqual(["DRAFT", "OPEN", "CLOSED", "COMPLETED", "CANCELLED"]);
    await user.selectOptions(status, "CLOSED");
    await user.click(screen.getByLabelText(/Parents may withdraw/));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(writes(fetchImpl)).toHaveLength(1));
    expect(writes(fetchImpl)[0]).toMatchObject({ method: "PATCH", body: { status: "CLOSED", allow_parent_withdrawal: false } });
  });
});

describe("registration form builder", () => {
  it("shows the working copy, unpublished changes, a preview and the published version", async () => {
    renderApp({ route: "/staff/competitions/81/registration-form", routes: competitionRoutes() });
    expect(await screen.findByText("The working copy has unpublished changes.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Working copy (2 of 30 fields)" })).toBeInTheDocument();
    expect(screen.getByText("shirt_size")).toBeInTheDocument();
    const preview = screen.getByLabelText("Form preview");
    expect(within(preview).getByLabelText(/Jersey size/)).toBeInTheDocument();
    expect(within(preview).getByRole("option", { name: "XL" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Published version 2" })).toBeInTheDocument();
  });

  it("adds a field with an existing type and publishes a new version", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/competitions/81/registration-form", routes: competitionRoutes() });
    await user.click(await screen.findByRole("button", { name: "Add field" }));
    const dialog = screen.getByRole("dialog", { name: "Add field" });
    expect(within(dialog).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(
      ["TEXT", "LONG_TEXT", "NUMBER", "DATE", "SINGLE_SELECT", "MULTI_SELECT", "YES_NO", "EMAIL", "PHONE"]);
    await user.type(within(dialog).getByLabelText(/Question/), "Uniform colour");
    await user.type(within(dialog).getByLabelText(/^Key/), "uniform_colour");
    await user.selectOptions(within(dialog).getByLabelText("Type"), "MULTI_SELECT");
    await user.type(within(dialog).getByLabelText(/Options/), "Red{enter}Black");
    await user.click(within(dialog).getByRole("button", { name: "Add field" }));
    await waitFor(() => expect(writes(fetchImpl)).toHaveLength(1));
    expect(writes(fetchImpl)[0]).toMatchObject({ method: "POST", url: expect.objectContaining({ pathname: "/api/competition-form-fields/" }),
      body: { competition: 81, key: "uniform_colour", field_type: "MULTI_SELECT", options: ["Red", "Black"], is_active: true } });

    await user.click(screen.getByRole("button", { name: "Publish form" }));
    const confirm = screen.getByRole("dialog", { name: "Publish the registration form?" });
    expect(confirm).toHaveTextContent("becomes version 3");
    await user.click(within(confirm).getByRole("button", { name: "Publish" }));
    expect(await screen.findByText("Registration form published as version 3.")).toBeInTheDocument();
    expect(writes(fetchImpl).at(-1)!.url.pathname).toBe("/api/competitions/81/publish-form/");
  });

  it("never changes a key, reorders with every key and deactivates instead of editing history", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/competitions/81/registration-form", routes: competitionRoutes() });
    await user.click(await screen.findByRole("button", { name: "Edit Jersey size" }));
    const dialog = screen.getByRole("dialog", { name: "Edit “Jersey size”" });
    expect(within(dialog).getByLabelText(/^Key/)).toBeDisabled();
    await user.clear(within(dialog).getByLabelText(/Question/));
    await user.type(within(dialog).getByLabelText(/Question/), "Shirt size");
    await user.click(within(dialog).getByRole("button", { name: "Save field" }));
    await waitFor(() => expect(writes(fetchImpl)).toHaveLength(1));
    expect(writes(fetchImpl)[0].body).not.toHaveProperty("key");
    expect(writes(fetchImpl)[0]).toMatchObject({ method: "PATCH", body: { label: "Shirt size" } });

    await user.click(screen.getByRole("button", { name: "Move Dietary needs up" }));
    await waitFor(() => expect(writes(fetchImpl)).toHaveLength(2));
    expect(writes(fetchImpl)[1].body).toEqual({ keys: ["diet", "shirt_size"] });
    await user.click(screen.getByRole("button", { name: "Deactivate Dietary needs" }));
    await waitFor(() => expect(writes(fetchImpl)).toHaveLength(3));
    expect(writes(fetchImpl)[2]).toMatchObject({ method: "PATCH", body: { is_active: false } });
  });

  it("shows the backend's refusal of a field (e.g. a secret)", async () => {
    const { user } = renderApp({ route: "/staff/competitions/81/registration-form", routes: competitionRoutes({
      "POST /api/competition-form-fields/": json({ label: ["Registration forms must not collect passwords, PINs, card details, tokens or keys."] }, 400),
    }) });
    await user.click(await screen.findByRole("button", { name: "Add field" }));
    const dialog = screen.getByRole("dialog", { name: "Add field" });
    await user.type(within(dialog).getByLabelText(/Question/), "Bank PIN");
    await user.type(within(dialog).getByLabelText(/^Key/), "pin");
    await user.click(within(dialog).getByRole("button", { name: "Add field" }));
    expect(await within(dialog).findAllByText(/must not collect passwords/)).not.toHaveLength(0);
  });

  it("removes a field from the working copy only after confirmation", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/competitions/81/registration-form", routes: competitionRoutes({
      "DELETE /api/competition-form-fields/6/": new Response(null, { status: 204 }),
    }) });
    await user.click(await screen.findByRole("button", { name: "Remove Dietary needs" }));
    const dialog = screen.getByRole("dialog", { name: "Remove this field?" });
    expect(dialog).toHaveTextContent("answers already submitted keep it");
    await user.click(within(dialog).getByRole("button", { name: "Remove field" }));
    expect(await screen.findByText("“Dietary needs” removed from the working copy.")).toBeInTheDocument();
    expect(writes(fetchImpl).at(-1)).toMatchObject({ method: "DELETE" });
    expect(form.fields).toHaveLength(2);
  });

  it("unpublishes with a confirmation", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/competitions/81/registration-form", routes: competitionRoutes() });
    await user.click(await screen.findByRole("button", { name: "Unpublish" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Unpublish" }));
    expect(await screen.findByText(/parents cannot register until it is published again/)).toBeInTheDocument();
    expect(writes(fetchImpl).at(-1)!.url.pathname).toBe("/api/competitions/81/unpublish-form/");
  });
});

describe("participants", () => {
  it("lists entries and sends every filter to the backend", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/competitions/81/participants", routes: competitionRoutes() });
    const table = await screen.findByRole("table", { name: "Participants in State Wushu Open" });
    expect(within(table).getByRole("link", { name: "Aaron Tan" })).toHaveAttribute("href", "/staff/competitions/81/participants/91");
    expect(within(table).getByText("Aaron Tan").closest("tr")).toHaveTextContent("Awaiting payment");
    expect(within(table).getByText("Chen Lee").closest("tr")).toHaveTextContent("Placing 1 · Gold");
    await user.selectOptions(screen.getByLabelText("Entry status"), "CONFIRMED");
    await user.selectOptions(screen.getByLabelText("Event"), "811");
    await user.selectOptions(screen.getByLabelText("Fee"), "PAID");
    await user.type(screen.getByLabelText("Student"), "beth");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(gets(fetchImpl, "/api/competition-registrations/").at(-1)!.url.searchParams.get("search")).toBe("beth"));
    const q = gets(fetchImpl, "/api/competition-registrations/").at(-1)!.url.searchParams;
    expect(Object.fromEntries(q)).toMatchObject({ competition: "81", status: "CONFIRMED", event: "811", payment: "PAID", search: "beth", page: "1" });
    expect(await screen.findByText("Beth Tan")).toBeInTheDocument();
    expect(screen.queryByText("Aaron Tan")).not.toBeInTheDocument();
  });

  it("shows a historical registration with its own form version and answers", async () => {
    renderApp({ route: "/staff/competitions/81/participants/91", routes: competitionRoutes() });
    expect(await screen.findByRole("heading", { name: "Aaron Tan", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Version 1")).toBeInTheDocument();
    expect(screen.getByText(/As submitted with form version 1/)).toBeInTheDocument();
    const answers = screen.getByRole("heading", { name: "Registration form answers" }).closest("section")!;
    expect(within(answers).getByText("T-shirt size")).toBeInTheDocument();   // v1 label, not today's "Jersey size"
    expect(within(answers).queryByText("Jersey size")).not.toBeInTheDocument();
    expect(within(answers).getByText("M")).toBeInTheDocument();
    // Finance state is displayed, not changed: invoice link and proofs (with the reminder).
    expect(screen.getByRole("link", { name: "INV-2026-0003" })).toHaveAttribute("href", "/finance/invoices/303");
    expect(await screen.findByText("A payment proof is not a payment.")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Payment proofs for this invoice" })).toHaveTextContent("Pending review");
    expect(screen.queryByRole("button", { name: /mark.*paid|record payment/i })).not.toBeInTheDocument();
  });

  it("rejects only with a reason and shows the backend's refusal to confirm an unpaid entry", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/competitions/81/participants/91", routes: competitionRoutes() });
    await user.click(await screen.findByRole("button", { name: "Confirm entry" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Confirm entry" }));
    expect(await screen.findByText(/fee has not been paid/)).toBeInTheDocument();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));

    await user.click(screen.getByRole("button", { name: "Reject" }));
    const dialog = screen.getByRole("dialog", { name: "Reject this entry?" });
    expect(dialog).toHaveTextContent("The unpaid competition invoice for this entry will be voided.");
    const submit = within(dialog).getByRole("button", { name: "Reject entry" });
    expect(submit).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Reason/), "Not eligible");
    await user.click(submit);
    expect(await screen.findByText("Entry rejected.")).toBeInTheDocument();
    expect(writes(fetchImpl).at(-1)).toMatchObject({ method: "POST", body: { reason: "Not eligible" } });
    expect(writes(fetchImpl).at(-1)!.url.pathname).toBe("/api/competition-registrations/91/reject/");
  });

  it("withdrawing a paid entry says fees are not refunded", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/competitions/81/participants/92", routes: competitionRoutes() });
    await user.click(await screen.findByRole("button", { name: "Withdraw" }));
    const dialog = screen.getByRole("dialog", { name: "Withdraw this entry?" });
    expect(dialog).toHaveTextContent("Fees already paid are not refunded automatically.");
    await user.type(within(dialog).getByLabelText(/Reason/), "Injury");
    await user.click(within(dialog).getByRole("button", { name: "Withdraw entry" }));
    expect(await screen.findByText("Entry withdrawn.")).toBeInTheDocument();
    expect(writes(fetchImpl).at(-1)!.url.pathname).toBe("/api/competition-registrations/92/withdraw/");
  });

  it("an entry with a result cannot be withdrawn and staff remarks are shown to staff", async () => {
    renderApp({ route: "/staff/competitions/81/participants/93", routes: competitionRoutes() });
    expect(await screen.findByText("Clean routine")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Withdraw" })).not.toBeInTheDocument();
    expect(screen.getByText(/cannot be withdrawn or rejected/)).toBeInTheDocument();
  });

  it("an entry of another competition or an unknown one is not found", async () => {
    renderApp({ route: "/staff/competitions/81/participants/404", routes: competitionRoutes() });
    expect(await screen.findByText("This entry was not found in this competition.")).toBeInTheDocument();
  });
});

describe("results", () => {
  it("asks the backend for confirmed entries only and records a result", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/competitions/81/results", routes: competitionRoutes() });
    const table = await screen.findByRole("table", { name: "Results for State Wushu Open" });
    expect(gets(fetchImpl, "/api/competition-registrations/").at(-1)!.url.searchParams.get("status")).toBe("CONFIRMED");
    expect(within(table).queryByText("Aaron Tan")).not.toBeInTheDocument();
    await user.click(within(table).getByRole("button", { name: "Record result for Beth Tan, Changquan U12" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Placing"), "2");
    await user.selectOptions(within(dialog).getByLabelText("Medal"), "SILVER");
    await user.type(within(dialog).getByLabelText("Score"), "8.75");
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));
    expect(await screen.findByText("Result saved for Beth Tan.")).toBeInTheDocument();
    expect(writes(fetchImpl).at(-1)).toMatchObject({ method: "POST", body: { registration: 92, placing: 2, medal: "SILVER", score: "8.75", remarks: "" } });
  });

  it("edits an existing result and shows a backend refusal", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/competitions/81/results", routes: competitionRoutes({
      "PATCH /api/competition-results/401/": json({ detail: "Results can only be recorded for a confirmed (paid) registration; this registration is withdrawn." }, 400),
    }) });
    await user.click(await screen.findByRole("button", { name: "Edit result for Chen Lee, Nanquan Open" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByLabelText("Placing")).toHaveValue("1");
    await user.click(within(dialog).getByRole("button", { name: "Save result" }));
    expect(await within(dialog).findByText(/Results can only be recorded/)).toBeInTheDocument();
    expect(writes(fetchImpl).at(-1)!.method).toBe("PATCH");
  });

});
