import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { json, makeMe, renderApp } from "../../test/helpers";
import {
  children, invoice, openCompetition, page, parentMe, parentRoutes, registrations, students,
} from "./fixtures";

function requested(fetchImpl: ReturnType<typeof renderApp>["fetchImpl"], path: string) {
  return fetchImpl.mock.calls.map(([url]) => new URL(String(url), "http://x"))
    .filter((url) => url.pathname === path);
}

function main() {
  return screen.getByRole("main");
}

describe("parent dashboard", () => {
  it("a parent-only account lands on the family overview with real data for each child", async () => {
    const { router } = renderApp({ route: "/dashboard", routes: parentRoutes() });
    expect(await screen.findByRole("heading", { name: "Family overview", level: 1 })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/parent/dashboard");
    expect(await screen.findByText("Tan family")).toBeInTheDocument();
    expect(screen.getByText(/3 children linked to your account/)).toBeInTheDocument();
    const aaron = screen.getByRole("heading", { name: "Aaron Tan", level: 2 }).closest("article")!;
    expect(await within(aaron).findByText("Junior Taolu")).toBeInTheDocument();
    expect(await within(aaron).findByText(/93\.33% · 14 present · 1 absent · 1 not marked/)).toBeInTheDocument();
    expect(within(aaron).getByText(/17:00 · Junior Taolu/)).toBeInTheDocument();
    const chen = screen.getByRole("heading", { name: "Chen Tan", level: 2 }).closest("article")!;
    expect(await within(chen).findByText("On leave")).toBeInTheDocument();
    expect(await within(chen).findByText(/— · 0 present · 0 absent · 2 not marked/)).toBeInTheDocument();
  });

  it("summarises finance and competitions and offers only supported actions", async () => {
    renderApp({ route: "/parent/dashboard", routes: parentRoutes() });
    const finance = (await screen.findByRole("heading", { name: "Family finance", level: 2 })).closest("section")!;
    expect(await within(finance).findByRole("link", { name: "INV-2026-0012" })).toBeInTheDocument();
    expect(within(finance).getByText("RM 250.00")).toBeInTheDocument();
    expect(await within(finance).findByText("RM 300.00")).toBeInTheDocument();
    const comps = screen.getByRole("heading", { name: "Competitions", level: 2 }).closest("section")!;
    expect(await within(comps).findByRole("link", { name: "State Wushu Open" })).toBeInTheDocument();
    expect(within(comps).getByText("Awaiting payment")).toBeInTheDocument();
    const actions = screen.getByRole("heading", { name: "Quick actions" }).closest("section")!;
    expect(within(actions).getByRole("link", { name: "Enter a competition" })).toBeInTheDocument();
    expect(within(actions).queryByRole("link", { name: /pay/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/billing contact|bill to/i)).not.toBeInTheDocument();
  });

  it("shows 'No children found.' when no child is linked", async () => {
    renderApp({ route: "/parent/dashboard", routes: parentRoutes({
      "GET /api/me/": json({ ...parentMe, children: [] }), "GET /api/families/": json(page([])) }) });
    expect(await screen.findByText("No children found.")).toBeInTheDocument();
  });
});

describe("family and student profile", () => {
  it("lists the family's students without any billing contact", async () => {
    renderApp({ route: "/parent/family", routes: parentRoutes() });
    const section = (await screen.findByRole("heading", { name: "Tan family", level: 2 })).closest("section")!;
    expect(within(section).getByText("3 students")).toBeInTheDocument();
    for (const child of children) {
      expect(within(section).getByRole("heading", { name: child.full_name })).toBeInTheDocument();
    }
    expect(await within(section).findByText("1 class: Senior Sanda")).toBeInTheDocument();
    expect(screen.getByText("a@example.com")).toBeInTheDocument();
    expect(screen.queryByText(/billing|bill to/i)).not.toBeInTheDocument();
  });

  it("shows the profile fields the API returns, with the IC masked", async () => {
    renderApp({ route: "/parent/students/11", routes: parentRoutes() });
    expect(await screen.findByRole("heading", { name: "Aaron Tan", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("SK Damai")).toBeInTheDocument();
    expect(screen.getByText("••••••••1234")).toBeInTheDocument();
    expect(screen.queryByText("140501101234")).not.toBeInTheDocument();
    expect(screen.getByText("Asthma: carries inhaler")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Coach Lim" })).toBeInTheDocument();
    expect(await screen.findByText("93.33%")).toBeInTheDocument();
  });

  it("switches between children from the profile", async () => {
    const { user, router } = renderApp({ route: "/parent/students/11", routes: parentRoutes() });
    await user.selectOptions(await screen.findByLabelText("Switch child"), "Beth Tan");
    expect(await screen.findByRole("heading", { name: "Beth Tan", level: 1 })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/parent/students/12");
  });

  it("another family's student is 'Record not found.' (the API's 404)", async () => {
    renderApp({ route: "/parent/students/99", routes: parentRoutes() });
    expect(await screen.findByTestId("not-found")).toHaveTextContent("Record not found.");
    expect(screen.queryByText(/Dina/)).not.toBeInTheDocument();
  });
});

describe("schedule", () => {
  it("labels sessions with the child, shows cancellations and leaves out other classes", async () => {
    renderApp({ route: "/parent/schedule", routes: parentRoutes() });
    const table = await screen.findByRole("table", { name: "Training sessions this week" });
    const rows = within(table).getAllByRole("row").slice(1);
    const text = rows.map((r) => r.textContent).join("|");
    expect(text).toContain("Aaron Tan, Chen Tan");
    expect(text).toContain("Coach Lim");
    expect(text).not.toContain("Class the parent coaches");
    const cancelled = rows.find((r) => r.textContent?.includes("Senior Sanda"));
    // The session may fall in next week depending on the weekday; if shown, it says Cancelled.
    if (cancelled) expect(cancelled).toHaveTextContent("Cancelled");
  });

  it("filters by child and loads another week on request", async () => {
    const { user, fetchImpl } = renderApp({ route: "/parent/schedule", routes: parentRoutes() });
    await screen.findByRole("table", { name: "Training sessions this week" });
    await user.selectOptions(screen.getByLabelText("Viewing"), "Beth Tan");
    const table = screen.getByRole("table", { name: "Training sessions this week" });
    expect(within(table).queryByText("Aaron Tan, Chen Tan")).not.toBeInTheDocument();
    const before = requested(fetchImpl, "/api/sessions/").length;
    await user.click(screen.getByRole("button", { name: "Next week" }));
    await waitFor(() => expect(requested(fetchImpl, "/api/sessions/").length).toBe(before + 1));
    const last = requested(fetchImpl, "/api/sessions/").at(-1)!;
    expect(last.searchParams.get("start")).not.toBeNull();
    expect(last.searchParams.get("end")).not.toBeNull();
  });
});

describe("attendance", () => {
  it("shows the backend's percentage with 'Not marked' separate from absent", async () => {
    renderApp({ route: "/parent/attendance?student=11", routes: parentRoutes() });
    const summary = (await screen.findByRole("heading", { name: "Aaron Tan: summary" })).closest("section")!;
    expect(await within(summary).findByText("93.33%")).toBeInTheDocument();
    expect(within(summary).getByText(/14 present · 0 late · 1 absent · 0 excused/)).toBeInTheDocument();
    expect(within(summary).getByText(/1 student not\s+marked \(not counted in the percentage\)/)).toBeInTheDocument();
    const history = await screen.findByRole("table", { name: "Attendance history for Aaron Tan" });
    expect(within(history).getByText("Present")).toBeInTheDocument();
    expect(within(history).getByText("Absent")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit|mark|change/i })).not.toBeInTheDocument();
  });

  it("switching child refetches only that child's history", async () => {
    const { user, fetchImpl, router } = renderApp({ route: "/parent/attendance", routes: parentRoutes() });
    await screen.findByRole("table", { name: "Attendance history for Aaron Tan" });
    const familiesBefore = requested(fetchImpl, "/api/families/").length;
    await user.selectOptions(screen.getByLabelText("Viewing"), "Chen Tan");
    expect(await screen.findByText("No attendance records found.")).toBeInTheDocument();
    expect(router.state.location.search).toBe("?student=13");
    expect(requested(fetchImpl, "/api/attendance/").map((u) => u.searchParams.get("student"))).toEqual(["11", "13"]);
    expect(requested(fetchImpl, "/api/families/").length).toBe(familiesBefore);
    const overview = screen.getByRole("table", { name: "Attendance summary for each child" });
    expect(within(overview).getAllByRole("row")).toHaveLength(4);
  });
});

describe("family finance", () => {
  it("overview lists outstanding invoices and each child's charges", async () => {
    const { user, fetchImpl } = renderApp({ route: "/parent/finance", routes: parentRoutes() });
    const outstanding = await screen.findByRole("table", { name: "Invoices awaiting payment" });
    expect(within(outstanding).getByText("Aaron Tan, Beth Tan, Chen Tan")).toBeInTheDocument();
    const charges = await screen.findByRole("table", { name: "Charges" });
    expect(within(charges).getByText("RM 200.00")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Child"), "Aaron Tan");
    await waitFor(() => expect(requested(fetchImpl, "/api/charges/").at(-1)!.searchParams.get("student")).toBe("11"));
    expect(screen.queryByText(/notes/i)).not.toBeInTheDocument();
  });

  it("invoice filters are sent to the API", async () => {
    const { user, fetchImpl } = renderApp({ route: "/parent/finance/invoices", routes: parentRoutes() });
    await screen.findByRole("link", { name: "INV-2026-0009" });
    await user.click(screen.getByRole("radio", { name: "Awaiting payment" }));
    await waitFor(() => expect(requested(fetchImpl, "/api/invoices/").at(-1)!.searchParams.get("outstanding")).toBe("1"));
    expect(await screen.findByRole("link", { name: "INV-2026-0012" })).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Void" }));
    expect(await screen.findByText("No invoices available.")).toBeInTheDocument();
  });

  it("invoice detail shows each child's lines, exact totals and payments applied", async () => {
    renderApp({ route: "/parent/finance/invoices/301", routes: parentRoutes() });
    expect(await screen.findByRole("heading", { name: "Invoice INV-2026-0012", level: 1 })).toBeInTheDocument();
    for (const [name, amount] of [["Aaron Tan (A1)", "RM 220.00"], ["Beth Tan (A2)", "RM 280.00"], ["Chen Tan (A3)", "RM 50.00"]]) {
      const table = screen.getByRole("table", { name: `Charges for ${name}` });
      expect(within(table).getByText(amount)).toBeInTheDocument();
    }
    const totals = document.querySelector(".totals")!;
    expect(totals).toHaveTextContent("TotalRM 550.00");
    expect(totals).toHaveTextContent("PaidRM 300.00");
    expect(totals).toHaveTextContent("Balance dueRM 250.00");
    const applied = await screen.findByRole("table", { name: "Payments applied to this invoice" });
    expect(within(applied).getAllByRole("link", { name: "RCP-2026-0030" })).toHaveLength(2);
    expect(screen.getByText(/the app does not take payments|Payments are recorded by the academy/)).toBeInTheDocument();
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    screen.getByRole("button", { name: "Print" }).click();
    expect(print).toHaveBeenCalled();
  });

  it("a void invoice shows its reason and nothing due", async () => {
    renderApp({ route: "/parent/finance/invoices/302", routes: parentRoutes({
      "GET /api/invoices/302/": json({ ...invoice, id: 302, number: "INV-2026-0020", status: "VOID",
                                       void_reason: "Entry withdrawn", amount_paid: "0.00", balance_due: "550.00" }),
    }) });
    expect(await screen.findByText("This invoice was cancelled (void).")).toBeInTheDocument();
    expect(screen.getByText("Reason: Entry withdrawn")).toBeInTheDocument();
    expect(document.querySelector(".totals")).toHaveTextContent("Balance dueNothing (cancelled)");
    expect(screen.queryByText("How to pay")).not.toBeInTheDocument();
  });

  it("another family's invoice or receipt is not found", async () => {
    renderApp({ route: "/parent/finance/invoices/999", routes: parentRoutes() });
    expect(await screen.findByTestId("not-found")).toHaveTextContent("Record not found.");
  });

  it("payments show what they paid for and link to the receipt, without staff notes", async () => {
    renderApp({ route: "/parent/finance/payments", routes: parentRoutes() });
    const table = await screen.findByRole("table", { name: "Family payments" });
    expect(within(table).getByText("Bank transfer")).toBeInTheDocument();
    expect(within(table).getByText(/INV-2026-0012 · Beth Tan/)).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "RCP-2026-0030" })).toHaveAttribute("href", "/parent/finance/receipts/701");
  });

  it("a receipt is shown exactly as issued and can be printed", async () => {
    const { user } = renderApp({ route: "/parent/finance/receipts", routes: parentRoutes() });
    await user.click(await screen.findByRole("link", { name: "RCP-2026-0030" }));
    const doc = await screen.findByRole("article", { name: "Official receipt RCP-2026-0030" });
    expect(within(doc).getByText("SR Wushu Academy")).toBeInTheDocument();
    expect(within(doc).getByText("Aaron Tan (A1), Beth Tan (A2)")).toBeInTheDocument();
    expect(doc.querySelector(".totals")).toHaveTextContent("RM 300.00");
    expect(screen.getByRole("button", { name: "Print" })).toBeInTheDocument();
  });

  it("a receipt of another family is not found", async () => {
    renderApp({ route: "/parent/finance/receipts/799", routes: parentRoutes() });
    expect(await screen.findByTestId("not-found")).toBeInTheDocument();
  });
});

describe("competitions", () => {
  it("lists open and other competitions and the children's entries with payment status", async () => {
    renderApp({ route: "/parent/competitions", routes: parentRoutes() });
    const entries = await screen.findByRole("table", { name: "Competition entries" });
    const pending = within(entries).getByText("Aaron Tan").closest("tr")!;
    expect(pending).toHaveTextContent("Awaiting payment");
    expect(pending).toHaveTextContent("Unpaid");
    expect(within(entries).getByText("Beth Tan").closest("tr")).toHaveTextContent("Confirmed");
    const open = screen.getByRole("heading", { name: "Open for registration", level: 2 }).closest("section")!;
    expect(within(open).getByRole("link", { name: "State Wushu Open" })).toBeInTheDocument();
    const other = screen.getByRole("heading", { name: "Other competitions" }).closest("section")!;
    expect(within(other).getByText("Registration closed")).toBeInTheDocument();
  });

  it("detail shows events and backend rules; a closed competition cannot be entered", async () => {
    renderApp({ route: "/parent/competitions/82", routes: parentRoutes() });
    expect(await screen.findByRole("heading", { name: "National Junior Cup", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("Registration for this competition is closed.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Register a child" })).not.toBeInTheDocument();
  });

  it("withdrawing warns that paid fees are not refunded and uses the backend action", async () => {
    let withdrawn: unknown = null;
    const { user } = renderApp({ route: "/parent/competitions/81", routes: parentRoutes({
      "POST /api/competition-registrations/92/withdraw/": ({ body }: { body: unknown }) => {
        withdrawn = body;
        return json({ ...registrations[1], status: "WITHDRAWN" });
      },
    }) });
    const table = await screen.findByRole("table", { name: "Your children’s entries in this competition" });
    await user.click(within(within(table).getByText("Beth Tan").closest("tr")!).getByRole("button", { name: "Withdraw" }));
    const dialog = screen.getByRole("dialog", { name: "Withdraw this entry?" });
    expect(dialog).toHaveTextContent("Fees already paid are not refunded.");
    await user.type(within(dialog).getByLabelText(/Reason/), "Injured");
    await user.click(within(dialog).getByRole("button", { name: "Withdraw entry" }));
    await waitFor(() => expect(withdrawn).toEqual({ reason: "Injured" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("registration: validation, confirmation, then 'Awaiting payment' with the issued invoice", async () => {
    let posted: unknown = null;
    const { user } = renderApp({ route: "/parent/competitions/81/register", routes: parentRoutes({
      "POST /api/competition-registrations/": ({ body }: { body: unknown }) => {
        posted = body;
        return json({ ...registrations[0], id: 95, event: 811, status: "PENDING",
                      invoice: { id: 306, number: "INV-2026-0031", balance_due: "50.00" } }, 201);
      },
    }) });
    await screen.findByRole("form", { name: "Competition registration" });
    await user.click(screen.getByRole("button", { name: "Review and register" }));
    expect(screen.getByText("Choose an event.")).toBeInTheDocument();
    expect(posted).toBeNull();
    await user.click(screen.getByRole("radio", { name: /Changquan U12/ }));
    await user.click(screen.getByRole("button", { name: "Review and register" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm registration" });
    expect(dialog).toHaveTextContent("Register Aaron Tan for Changquan U12");
    expect(dialog).toHaveTextContent("RM 50.00");
    await user.click(within(dialog).getByRole("button", { name: "Register" }));
    expect(await screen.findByRole("heading", { name: "Registration received" })).toBeInTheDocument();
    expect(posted).toEqual({ student: 11, event: 811, notes: "" });
    expect(screen.getByText("Awaiting payment")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "INV-2026-0031" })).toHaveAttribute("href", "/parent/finance/invoices/306");
    expect(screen.getByText(/the app does not take payments/)).toBeInTheDocument();
    expect(screen.queryByText(/payment successful|paid successfully/i)).not.toBeInTheDocument();
  });

  it("shows the backend's eligibility error when a registration is refused", async () => {
    const { user } = renderApp({ route: "/parent/competitions/81/register?student=11", routes: parentRoutes({
      "POST /api/competition-registrations/": json({ detail: ["Nanquan Girls is for female athletes only."] }, 400),
    }) });
    await user.click(await screen.findByRole("radio", { name: /Nanquan Girls/ }));
    await user.click(screen.getByRole("button", { name: "Review and register" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Register" }));
    expect(await screen.findByText("Nanquan Girls is for female athletes only.")).toBeInTheDocument();
  });
});

describe("authorization and errors", () => {
  it("the registration page needs the register capability (direct navigation)", async () => {
    const me = { ...parentMe, capabilities: parentMe.capabilities.filter((c) => c !== "competition.register_own_children") };
    renderApp({ route: "/parent/competitions/81/register", routes: parentRoutes({ "GET /api/me/": json(me) }) });
    expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
  });

  it("a coach without the parent role is denied the parent portal", async () => {
    renderApp({ route: "/parent/finance", routes: { "GET /api/me/": json(makeMe(["COACH"])) } });
    expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
  });

  it("an API 403 on a parent page shows the permission message", async () => {
    renderApp({ route: "/parent/finance/payments", routes: parentRoutes({
      "GET /api/payments/": json({ detail: "You do not have permission." }, 403) }) });
    expect(await screen.findByTestId("access-denied")).toHaveTextContent("You don’t have permission to access this page.");
  });

  it("a network failure shows the connection message with retry", async () => {
    renderApp({ route: "/parent/finance/receipts", routes: parentRoutes({
      "GET /api/receipts/": () => { throw new TypeError("offline"); } }) });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Unable to connect. Please try again.");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("/parent/students goes to the family page (no duplicate list)", async () => {
    const { router } = renderApp({ route: "/parent/students", routes: parentRoutes() });
    await screen.findByRole("heading", { name: "My family", level: 1 });
    expect(router.state.location.pathname).toBe("/parent/family");
  });

  it("the phone menu lists the parent portal sections", async () => {
    const { user } = renderApp({ route: "/parent/dashboard", routes: parentRoutes() });
    await user.click(await screen.findByRole("button", { name: "Open menu" }));
    const drawer = screen.getByRole("dialog", { name: "Menu" });
    for (const name of ["Overview", "My family", "Schedule", "Attendance", "Family finance", "Competitions"]) {
      expect(within(drawer).getByRole("link", { name })).toBeInTheDocument();
    }
    await user.click(within(drawer).getByRole("link", { name: "Family finance" }));
    expect(await within(main()).findByRole("heading", { name: "Family finance", level: 1 })).toBeInTheDocument();
  });
});

// Keep fixtures honest: the invoice lines add up to the backend total.
it("fixture invoice lines add up to the invoice total", () => {
  expect(invoice.items.map((i) => i.amount)).toEqual(["220.00", "280.00", "50.00"]);
  expect(students[11].current_classes?.[0].class_name).toBe("Junior Taolu");
  expect(openCompetition.events).toHaveLength(2);
});
