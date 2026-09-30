import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Competition, FormFieldDefinition } from "../../api/types";
import { json, renderApp } from "../../test/helpers";
import { invoice, openCompetition, page, parentRoutes, registrations } from "./fixtures";

function field(key: string, label: string, type: FormFieldDefinition["type"], extra: Partial<FormFieldDefinition> = {}) {
  return { key, label, type, required: false, help_text: "", placeholder: "", options: [], max_length: null,
           min_value: null, max_value: null, ...extra } as FormFieldDefinition;
}

const ALL_TYPES: Competition = {
  ...openCompetition, id: 83, name: "Open Championship",
  registration_form: { status: "PUBLISHED", version: 4, published_at: null, fields: [
    field("emergency_contact", "Emergency contact", "TEXT", { required: true, help_text: "Name of an adult at the venue" }),
    field("achievement", "Previous achievement", "LONG_TEXT", { max_length: 500 }),
    field("weight", "Weight (kg)", "NUMBER", { min_value: "20", max_value: "120" }),
    field("arrival", "Arrival date", "DATE", { required: true }),
    field("food", "Food preference", "SINGLE_SELECT", { options: ["Normal", "Vegetarian", "Halal"] }),
    field("meals", "Meals", "MULTI_SELECT", { options: ["Breakfast", "Lunch", "Dinner"] }),
    field("transport", "Transport", "YES_NO", { required: true }),
    field("contact_email", "Contact email", "EMAIL"),
    field("contact_phone", "Contact phone", "PHONE"),
  ] },
};

function routes(extra: Record<string, unknown> = {}) {
  return parentRoutes({
    "GET /api/competitions/": json(page([openCompetition, ALL_TYPES])),
    "GET /api/competitions/83/": json(ALL_TYPES),
    ...extra,
  });
}

describe("dynamic registration form", () => {
  it("renders each competition's own questions with the right controls", async () => {
    renderApp({ route: "/parent/competitions/83/register?student=11", routes: routes() });
    const form = await screen.findByRole("form", { name: "Competition registration" });
    expect(within(form).getByRole("heading", { name: "3. Open Championship: registration questions" })).toBeInTheDocument();
    const contact = within(form).getByLabelText(/Emergency contact/);
    expect(contact.tagName).toBe("INPUT");
    expect(contact).toBeRequired();
    expect(contact).toHaveAccessibleDescription("Name of an adult at the venue");
    expect(within(form).getByLabelText(/Previous achievement/).tagName).toBe("TEXTAREA");
    expect(within(form).getByLabelText(/Weight/)).toHaveAttribute("inputmode", "decimal");
    expect(within(form).getByLabelText(/Arrival date/)).toHaveAttribute("type", "date");
    expect(within(form).getByLabelText(/Food preference/).tagName).toBe("SELECT");
    const meals = within(form).getByRole("group", { name: "Meals" });
    expect(within(meals).getAllByRole("checkbox")).toHaveLength(3);
    const transport = within(form).getByRole("group", { name: /Transport/ });
    expect(within(transport).getAllByRole("radio").map((r) => r.closest("label")!.textContent)).toEqual(["Yes", "No"]);
    expect(within(form).getByLabelText(/Contact email/)).toHaveAttribute("type", "email");
    expect(within(form).getByLabelText(/Contact phone/)).toHaveAttribute("type", "tel");
    // Nothing from another competition's form is hard-coded here.
    expect(within(form).queryByLabelText(/T-shirt size/)).not.toBeInTheDocument();
    expect(within(form).queryByText(/Accommodation/)).not.toBeInTheDocument();
  });

  it("validates required answers and types at each field before sending", async () => {
    const { user, fetchImpl } = renderApp({ route: "/parent/competitions/83/register?student=11", routes: routes() });
    const form = await screen.findByRole("form", { name: "Competition registration" });
    await user.click(within(form).getByRole("radio", { name: /Changquan U12/ }));
    await user.type(within(form).getByLabelText(/Weight/), "300");
    await user.type(within(form).getByLabelText(/Contact email/), "not-an-email");
    await user.click(within(form).getByRole("button", { name: "Review and register" }));
    const contact = within(form).getByLabelText(/Emergency contact/);
    expect(contact).toHaveAttribute("aria-invalid", "true");
    expect(contact).toHaveAccessibleDescription(/This field is required\./);
    expect(contact).toHaveFocus();
    expect(within(form).getByLabelText(/Weight/)).toHaveAccessibleDescription("Must be at most 120.");
    expect(within(form).getByLabelText(/Contact email/)).toHaveAccessibleDescription("Enter a valid email address.");
    expect(within(form).getByRole("group", { name: /Transport/ })).toHaveAccessibleDescription("This field is required.");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(fetchImpl.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(false);
  });

  it("shows the selected event's own fee", async () => {
    const { user } = renderApp({ route: "/parent/competitions/83/register?student=11", routes: routes() });
    await screen.findByRole("form", { name: "Competition registration" });
    expect(screen.getByText("Choose an event to see its fee.")).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /Changquan U12/ }));
    expect(document.querySelector(".fee-line")).toHaveTextContent("Fee for this entry: RM 50.00");
    await user.click(screen.getByRole("radio", { name: /Nanquan Girls/ }));
    expect(document.querySelector(".fee-line")).toHaveTextContent("Fee for this entry: RM 60.00");
  });

  it("submits the chosen child, event, competition and answers; shows answers, amount due and payment details", async () => {
    let posted: Record<string, unknown> | null = null;
    const { user } = renderApp({ route: "/parent/competitions/83/register", routes: routes({
      "POST /api/competition-registrations/": ({ body }: { body: Record<string, unknown> }) => {
        posted = body;
        return json({ ...registrations[0], id: 96, student: 12, student_name: "Beth Tan", event_name: "Nanquan Girls",
                      status: "PENDING", fee: "60.00", invoice: { id: 301, number: "INV-2026-0012", balance_due: "250.00" },
                      form_version: 4, form_responses: [
                        { key: "emergency_contact", label: "Emergency contact", type: "TEXT", value: "Aunt May <script>",
                          display: "Aunt May <script>" },
                        { key: "transport", label: "Transport", type: "YES_NO", value: true, display: "Yes" },
                      ] }, 201);
      },
    }) });
    const form = await screen.findByRole("form", { name: "Competition registration" });
    await user.selectOptions(within(form).getByLabelText(/1\. Child/), "Beth Tan");
    await user.click(within(form).getByRole("radio", { name: /Nanquan Girls/ }));
    await user.type(within(form).getByLabelText(/Emergency contact/), "Aunt May <script>");
    await user.type(within(form).getByLabelText(/Arrival date/), "2026-10-30");
    await user.selectOptions(within(form).getByLabelText(/Food preference/), "Halal");
    await user.click(within(within(form).getByRole("group", { name: "Meals" })).getByRole("checkbox", { name: "Dinner" }));
    await user.click(within(within(form).getByRole("group", { name: /Transport/ })).getByRole("radio", { name: "Yes" }));
    await user.click(within(form).getByRole("button", { name: "Review and register" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm registration" });
    expect(dialog).toHaveTextContent("Register Beth Tan for Nanquan Girls");
    expect(dialog).toHaveTextContent("Food preferenceHalal");
    expect(dialog).toHaveTextContent("RM 60.00");
    await user.click(within(dialog).getByRole("button", { name: "Register" }));
    expect(await screen.findByRole("heading", { name: "Registration received" })).toBeInTheDocument();
    expect(posted).toEqual({ student: 12, event: 812, competition: 83, notes: "", responses: {
      emergency_contact: "Aunt May <script>", arrival: "2026-10-30", food: "Halal", meals: ["Dinner"], transport: true } });
    expect(screen.getByText("Awaiting payment")).toBeInTheDocument();
    // Answers are shown as plain text (never as HTML).
    expect(screen.getByText("Aunt May <script>")).toBeInTheDocument();
    expect(document.querySelector("main script")).toBeNull();
    // Amount due and the academy's payment details, no fake payment.
    expect(await screen.findByText("Maybank")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Academy payment QR code" })).toBeInTheDocument();
    expect(screen.queryByText(/payment successful/i)).not.toBeInTheDocument();
  });

  it("shows the backend's answer errors at the matching fields", async () => {
    const { user } = renderApp({ route: "/parent/competitions/81/register?student=11", routes: parentRoutes({
      "POST /api/competition-registrations/": json({ "responses.shirt_size": ["Choose one of the listed options."] }, 400),
    }) });
    await user.click(await screen.findByRole("radio", { name: /Changquan U12/ }));
    await user.selectOptions(screen.getByLabelText(/T-shirt size/), "XL");
    await user.click(within(screen.getByRole("group", { name: /Accommodation/ })).getByRole("radio", { name: "No" }));
    await user.click(screen.getByRole("button", { name: "Review and register" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Register" }));
    const shirt = screen.getByLabelText(/T-shirt size/);
    await waitFor(() => expect(shirt).toHaveAccessibleDescription(/Choose one of the listed options\./));
    expect(shirt).toHaveFocus();
  });

  it("switches competition from the registration page", async () => {
    const { user, router } = renderApp({ route: "/parent/competitions/81/register", routes: routes() });
    await screen.findByRole("form", { name: "Competition registration" });
    await user.selectOptions(await screen.findByLabelText("Competition"), "Open Championship");
    expect(await screen.findByRole("heading", { name: "Register for Open Championship" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/parent/competitions/83/register");
  });

  it("an unpublished form cannot be used", async () => {
    const draft = { ...ALL_TYPES, registration_form: { status: "DRAFT" as const, version: 4, published_at: null, fields: [] } };
    renderApp({ route: "/parent/competitions/83/register", routes: routes({ "GET /api/competitions/83/": json(draft) }) });
    expect(await screen.findByText("The registration form for this competition is not available yet.")).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Competition registration" })).not.toBeInTheDocument();
  });
});

describe("registration answers and competition rules on the detail page", () => {
  it("shows the family's submitted answers and the competition's rules", async () => {
    const { user } = renderApp({ route: "/parent/competitions/81", routes: parentRoutes() });
    expect(await screen.findByText("Athletes must bring their own weapons. Uniform inspection at check-in.")).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Your children’s entries in this competition" });
    const row = within(table).getByText("Aaron Tan").closest("tr")!;
    await user.click(within(row).getByText("View answers"));
    expect(within(row).getByText("T-shirt size")).toBeInTheDocument();
    expect(within(row).getByText("L")).toBeInTheDocument();
  });

  it("hides withdrawal when the competition does not allow it (the backend decides)", async () => {
    renderApp({ route: "/parent/competitions/81", routes: parentRoutes({
      "GET /api/competitions/81/": json({ ...openCompetition, allow_parent_withdrawal: false }) }) });
    await screen.findByRole("table", { name: "Your children’s entries in this competition" });
    expect(screen.queryByRole("button", { name: "Withdraw" })).not.toBeInTheDocument();
    expect(screen.getByText("Withdrawal from this competition is handled by the academy.")).toBeInTheDocument();
  });
});

it("fixture sanity", () => {
  expect(invoice.id).toBe(301);
});
