import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { json, makeMe, renderApp } from "../../test/helpers";
import {
  acceptedProof, adminMe, competitionInvoice, financeRoutes, invoice, payment, paymentInfo, pendingProof,
} from "./fixtures";

type Call = { url: URL; method: string; body: unknown; headers: Record<string, string> };
function calls(fetchImpl: ReturnType<typeof renderApp>["fetchImpl"]): Call[] {
  return fetchImpl.mock.calls.map(([url, init]) => {
    const i = (init ?? {}) as RequestInit;
    return { url: new URL(String(url), "http://x"), method: i.method ?? "GET", headers: (i.headers ?? {}) as Record<string, string>,
             body: typeof i.body === "string" ? JSON.parse(i.body) : i.body };
  });
}
const writes = (f: ReturnType<typeof renderApp>["fetchImpl"]) => calls(f).filter((c) => c.method !== "GET");

describe("finance dashboard", () => {
  it("shows backend totals, the proof queue and recent payments without bank details", async () => {
    renderApp({ route: "/finance", routes: financeRoutes() });
    expect(await screen.findByRole("heading", { name: "Finance dashboard", level: 1 })).toBeInTheDocument();
    const kpis = await screen.findByRole("region", { name: "Finance at a glance" });
    expect(within(kpis).getByText("Outstanding").closest(".kpi")).toHaveTextContent("RM 380.00");
    expect(within(kpis).getByText("Proofs to review").closest(".kpi")).toHaveTextContent("2");
    expect(screen.getByRole("link", { name: /2 payment proofs waiting for review/ }))
      .toHaveAttribute("href", "/finance/payment-proofs?status=PENDING_REVIEW");
    expect(screen.getByRole("link", { name: /1 accepted proof with the payment still to be recorded/ })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Recent payments" })).toHaveTextContent("PAY-2026-000001");
    expect(screen.getByRole("main").textContent).not.toMatch(/5140|account number/i);
    expect(screen.getByRole("link", { name: "Record payment" })).toHaveAttribute("href", "/finance/payments/new");
  });

  it("shows an error with retry", async () => {
    renderApp({ route: "/finance/dashboard", routes: financeRoutes({ "GET /api/finance/dashboard/": json({ detail: "x" }, 500) }) });
    expect(await screen.findByRole("button", { name: /Try again/i })).toBeInTheDocument();
  });
});

describe("invoices", () => {
  it("lists invoices with outstanding amounts; search and filters go to the API", async () => {
    const { user, fetchImpl } = renderApp({ route: "/finance/invoices", routes: financeRoutes() });
    const table = await screen.findByRole("table", { name: "Invoices" });
    const row = within(table).getByText("INV-2026-0001").closest("tr")!;
    expect(row).toHaveTextContent("Aaron Tan, Beth Tan");
    expect(row).toHaveTextContent("RM 250.00");
    expect(within(table).getByText("INV-2026-0003").closest("tr")).toHaveTextContent("Competition");
    await user.type(screen.getByLabelText("Search"), "Tan");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.selectOptions(screen.getByLabelText("Status"), "PARTIALLY_PAID");
    await user.click(screen.getByLabelText("Overdue only"));
    const last = calls(fetchImpl).filter((c) => c.url.pathname === "/api/invoices/").at(-1)!.url.searchParams;
    expect([last.get("search"), last.get("status"), last.get("overdue")]).toEqual(["Tan", "PARTIALLY_PAID", "1"]);
  });

  it("detail: lines per student, payments with receipts, and no generic edit (finance admin may void)", async () => {
    renderApp({ route: "/finance/invoices/301", routes: financeRoutes() });
    expect(await screen.findByRole("heading", { name: "Invoice INV-2026-0001", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Invoice lines" })).toHaveTextContent("Beth Tan (A2)");
    const pays = await screen.findByRole("table", { name: "Payments applied to this invoice" });
    expect(within(pays).getByRole("link", { name: "SRWA-2026-000001" })).toHaveAttribute("href", "/finance/receipts/601");
    expect(within(pays).getByText("RM 300.00")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Record payment" })).toHaveAttribute("href", "/finance/payments/new?invoice=301");
    expect(screen.getByRole("button", { name: "Void" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit/ })).not.toBeInTheDocument();
    expect(screen.getByRole("main").textContent).not.toMatch(/bill to|billing contact/i);
  });

  it("an admin (no invoice management) gets no issue/void, and sees the competition entry of a line", async () => {
    renderApp({ route: "/finance/invoices/303", routes: financeRoutes({ "GET /api/me/": json(adminMe) }) });
    expect(await screen.findByRole("heading", { name: "Invoice INV-2026-0003", level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Void|Issue/ })).not.toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Invoice lines" })).toHaveTextContent("State Open · Open set");
    expect(screen.getByRole("table", { name: "Invoice lines" })).toHaveTextContent("Awaiting payment");
  });

  it("voiding needs a reason and shows the backend's refusal", async () => {
    const { user } = renderApp({ route: "/finance/invoices/301", routes: financeRoutes({
      "POST /api/invoices/301/void/": json({ detail: ["An invoice with payments cannot be voided."] }, 400) }) });
    await user.click(await screen.findByRole("button", { name: "Void" }));
    const dialog = screen.getByRole("dialog", { name: "Void invoice" });
    expect(within(dialog).getByRole("button", { name: "Void invoice" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Reason/), "Duplicate");
    await user.click(within(dialog).getByRole("button", { name: "Void invoice" }));
    expect(await within(dialog).findByText("An invoice with payments cannot be voided.")).toBeInTheDocument();
  });
});

describe("payment proofs", () => {
  it("lists pending proofs by default with review states in words", async () => {
    const { fetchImpl } = renderApp({ route: "/finance/payment-proofs", routes: financeRoutes() });
    const table = await screen.findByRole("table", { name: "Payment proofs" });
    expect(within(table).getByText(/#71/).closest("tr")).toHaveTextContent("Pending review");
    expect(calls(fetchImpl).find((c) => c.url.pathname === "/api/payment-proofs/")!.url.searchParams.get("status")).toBe("PENDING_REVIEW");
    // Never "Paid" for a proof (only the "Paid on" column header).
    expect(table.textContent?.replace("Paid on", "")).not.toMatch(/Paid\b/);
  });

  it("detail shows the metadata, downloads through the API, and never shows a storage path", async () => {
    const create = vi.fn(() => "blob:mock");
    const revoke = vi.fn();
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke });
    const { user, fetchImpl } = renderApp({ route: "/finance/payment-proofs/71", routes: financeRoutes({
      "GET /api/payment-proofs/71/file/": () => new Response("png", { status: 200, headers: { "Content-Type": "image/png" } }) }) });
    expect(await screen.findByRole("heading", { name: "Payment proof #71", level: 1 })).toBeInTheDocument();
    const main = screen.getByRole("main");
    for (const text of ["INV-2026-0003", "Lee family", "RM 50.00", "MBB-777", "Paid for Dina", "transfer.png"]) {
      expect(main).toHaveTextContent(text);
    }
    expect(main).toHaveTextContent("A payment proof is not a payment.");
    expect(main.textContent).not.toMatch(/sha256|proofs\/|\/media\//);
    await user.click(screen.getByRole("button", { name: "Download proof" }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(calls(fetchImpl).some((c) => c.url.pathname === "/api/payment-proofs/71/file/")).toBe(true);
  });

  it("accepting sends only the review (no payment) and says so", async () => {
    let body: unknown = null;
    const { user, fetchImpl } = renderApp({ route: "/finance/payment-proofs/71", routes: financeRoutes({
      "POST /api/payment-proofs/71/accept/": (r: { body: unknown }) => { body = r.body; return json({ ...pendingProof, status: "ACCEPTED" }); } }) });
    await user.click(await screen.findByRole("button", { name: "Accept" }));
    const dialog = screen.getByRole("dialog", { name: "Accept payment proof" });
    expect(dialog).toHaveTextContent("It does not record a payment");
    await user.type(within(dialog).getByLabelText(/Note/), "Matches bank");
    await user.click(within(dialog).getByRole("button", { name: "Accept proof" }));
    expect(await screen.findByText("Proof accepted. No payment was recorded by this.")).toBeInTheDocument();
    expect(body).toEqual({ note: "Matches bank", payment: null });
    expect(writes(fetchImpl).map((c) => c.url.pathname)).toEqual(["/api/payment-proofs/71/accept/"]);
  });

  it("rejecting requires a reason (validated at the field) and sends it", async () => {
    let body: unknown = null;
    const { user } = renderApp({ route: "/finance/payment-proofs/71", routes: financeRoutes({
      "POST /api/payment-proofs/71/reject/": (r: { body: unknown }) => { body = r.body; return json({ ...pendingProof, status: "REJECTED" }); } }) });
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    const dialog = screen.getByRole("dialog", { name: "Reject payment proof" });
    await user.click(within(dialog).getByRole("button", { name: "Reject proof" }));
    const reason = within(dialog).getByLabelText(/Reason/);
    expect(reason).toHaveAttribute("aria-invalid", "true");
    expect(reason).toHaveAccessibleDescription(/family will see it/);
    expect(body).toBeNull();
    await user.type(reason, "Amount does not match");
    await user.click(within(dialog).getByRole("button", { name: "Reject proof" }));
    expect(await screen.findByText(/Proof rejected/)).toBeInTheDocument();
    expect(body).toEqual({ reason: "Amount does not match" });
  });

  it("an accepted proof without a payment says the payment still needs recording", async () => {
    renderApp({ route: "/finance/payment-proofs/72", routes: financeRoutes() });
    expect(await screen.findAllByText("Accepted — payment still requires recording")).not.toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /record the payment for INV-2026-0003/ }))
      .toHaveAttribute("href", "/finance/payments/new?invoice=303");
    expect(acceptedProof.payment).toBeNull();
  });

  it("an accepted proof whose invoice is already paid does not ask for a payment", async () => {
    renderApp({ route: "/finance/payment-proofs/72", routes: financeRoutes({
      "GET /api/payment-proofs/72/": json({ ...acceptedProof, invoice_status: "PAID", invoice_balance_due: "0.00" }) }) });
    expect(await screen.findByText("Invoice paid")).toBeInTheDocument();
    expect(screen.queryByText("Accepted — payment still requires recording")).not.toBeInTheDocument();
  });
});

describe("recording payments", () => {
  it("partial payment of a chosen invoice: backend methods, confirmation, idempotency key, then the payment page", async () => {
    let body: unknown = null;
    let key = "";
    const { user, router } = renderApp({ route: "/finance/payments/new?invoice=303", routes: financeRoutes({
      "POST /api/payments/": (r: { body: unknown; headers: Record<string, string> }) => {
        body = r.body; key = r.headers["Idempotency-Key"]; return json({ ...payment, id: 502 }, 201); },
      "GET /api/payments/502/": json({ ...payment, id: 502 }) }) });
    const input = await screen.findByLabelText(/INV-2026-0003 \(Dina Lee\) — 50.00 due/);
    await waitFor(() => expect(input).toHaveValue("50.00"));
    await user.clear(input);
    await user.type(input, "20.00");
    expect(screen.getByText(/Total received/).closest("p")).toHaveTextContent("RM 20.00");
    expect(within(screen.getByLabelText(/Payment method/)).getAllByRole("option").map((o) => o.textContent))
      .toEqual(["Choose…", "Cash", "Bank transfer", "Other"]);
    await user.click(screen.getByRole("button", { name: "Review payment" }));
    expect(screen.getByLabelText(/Payment method/)).toHaveAttribute("aria-invalid", "true");
    await user.selectOptions(screen.getByLabelText(/Payment method/), "CASH");
    await user.type(screen.getByLabelText(/Reference/), "Counter 1");
    await user.click(screen.getByRole("button", { name: "Review payment" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm payment" });
    expect(dialog).toHaveTextContent("RM 20.00");
    await user.click(within(dialog).getByRole("button", { name: "Record payment" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/finance/payments/502"));
    expect(body).toMatchObject({ amount: "20.00", method: "CASH", reference: "Counter 1",
                                 allocations: [{ invoice: 303, amount: "20.00" }] });
    expect(key).toMatch(/^pay-/);
  });

  it("shows the backend's refusal (e.g. more than the balance) without navigating", async () => {
    const { user } = renderApp({ route: "/finance/payments/new?invoice=303", routes: financeRoutes({
      "POST /api/payments/": json({ detail: ["RM 60.00 exceeds the balance due RM 50.00 on INV-2026-0003."] }, 400) }) });
    const input = await screen.findByLabelText(/INV-2026-0003/);
    await user.clear(input);
    await user.type(input, "60.00");
    await user.selectOptions(screen.getByLabelText(/Payment method/), "CASH");
    await user.click(screen.getByRole("button", { name: "Review payment" }));
    await user.click(within(screen.getByRole("dialog", { name: "Confirm payment" })).getByRole("button", { name: "Record payment" }));
    expect(await screen.findByText(/exceeds the balance due/)).toBeInTheDocument();
  });

  it("payment detail: allocation, receipt, and exceptional refund with a required reason", async () => {
    let body: unknown = null;
    const { user } = renderApp({ route: "/finance/payments/501", routes: financeRoutes({
      "POST /api/payments/501/refund/": (r: { body: unknown }) => { body = r.body; return json({ id: 1 }, 201); } }) });
    expect(await screen.findByRole("heading", { name: "Payment PAY-2026-000001", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Where this payment was applied" })).toHaveTextContent("Beth Tan");
    expect(screen.getByRole("link", { name: "SRWA-2026-000001" })).toHaveAttribute("href", "/finance/receipts/601");
    await user.click(screen.getByRole("button", { name: "Exceptional refund" }));
    const dialog = screen.getByRole("dialog", { name: "Exceptional refund" });
    expect(dialog).toHaveTextContent("normally not refundable");
    await user.selectOptions(within(dialog).getByLabelText(/Line/), "801");
    await user.type(within(dialog).getByLabelText(/Amount/), "10.00");
    await user.type(within(dialog).getByLabelText(/Reason/), "Exceptional: duplicate fee");
    await user.click(within(dialog).getByRole("button", { name: "Record refund" }));
    expect(await screen.findByText("Refund recorded.")).toBeInTheDocument();
    expect(body).toMatchObject({ allocation: 801, amount: "10.00", reason: "Exceptional: duplicate fee" });
  });

  it("an admin can record payments but has no void button", async () => {
    renderApp({ route: "/finance/payments/501", routes: financeRoutes({ "GET /api/me/": json(adminMe) }) });
    await screen.findByRole("heading", { name: "Payment PAY-2026-000001", level: 1 });
    expect(screen.queryByRole("button", { name: "Void payment" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Exceptional refund" })).toBeInTheDocument();
  });
});

describe("receipts and payment information", () => {
  it("receipt as issued, printable", async () => {
    renderApp({ route: "/finance/receipts/601", routes: financeRoutes() });
    const doc = await screen.findByRole("article", { name: "Official receipt SRWA-2026-000001" });
    expect(doc).toHaveTextContent("Aaron Tan, Beth Tan");
    expect(screen.getByRole("button", { name: "Print" })).toBeInTheDocument();
  });

  it("finance admin edits payment information; the QR accepts PNG/JPG only", async () => {
    let sent: FormData | null = null;
    const { user } = renderApp({ route: "/finance/payment-info", routes: financeRoutes({
      "PATCH /api/payment-info/": (r: { body: FormData }) => { sent = r.body; return json({ ...paymentInfo, bank_name: "New Bank" }); } }) });
    const bank = await screen.findByLabelText("Bank name");
    expect(bank).toHaveValue("Test Bank");
    expect(screen.getByLabelText(/New QR image/)).toHaveAttribute("accept", "image/png,image/jpeg");
    await user.clear(bank);
    await user.type(bank, "New Bank");
    await user.click(screen.getByRole("button", { name: "Save payment information" }));
    expect(await screen.findByText("Payment information saved.")).toBeInTheDocument();
    expect((sent as unknown as FormData).get("bank_name")).toBe("New Bank");
  });

  it("an admin without the manage capability sees it read only", async () => {
    renderApp({ route: "/finance/payment-info", routes: financeRoutes({ "GET /api/me/": json(adminMe) }) });
    expect(await screen.findByText(/View only/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
  });

  it("shows the backend's QR error at the field", async () => {
    const { user } = renderApp({ route: "/finance/payment-info", routes: financeRoutes({
      "PATCH /api/payment-info/": json({ qr_code: ["Upload a PNG or JPG image."] }, 400) }) });
    await screen.findByLabelText("Bank name");
    await user.upload(screen.getByLabelText(/New QR image/), new File(["x"], "qr.png", { type: "image/png" }));
    await user.click(screen.getByRole("button", { name: "Save payment information" }));
    expect(await screen.findByText("Upload a PNG or JPG image.")).toBeInTheDocument();
    expect(screen.getByLabelText(/New QR image/)).toHaveAttribute("aria-invalid", "true");
  });
});

describe("navigation and guards", () => {
  it("finance admin gets the finance menu", async () => {
    renderApp({ route: "/finance/dashboard", routes: financeRoutes() });
    await screen.findByRole("heading", { name: "Finance dashboard", level: 1 });
    const section = screen.getByRole("complementary", { name: "Sidebar" }).querySelector("[data-portal='finance']") as HTMLElement;
    expect(within(section).getAllByRole("link").map((a) => a.textContent)).toEqual(
      ["Finance dashboard", "Invoices", "Payments", "Payment proofs", "Receipts", "Payroll", "Payment information"]);
  });

  it("coach, parent and student are denied every finance page", async () => {
    for (const role of ["COACH", "PARENT", "STUDENT"] as const) {
      for (const route of ["/finance/dashboard", "/finance/invoices/301", "/finance/payments/new", "/finance/payment-proofs/71",
                           "/finance/receipts/601", "/finance/payment-info"]) {
        const view = renderApp({ route, routes: financeRoutes({ "GET /api/me/": json(makeMe([role])) }) });
        expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
        view.unmount();
      }
    }
  });

  it("a staff user without payment-recording capability cannot open the payment form", async () => {
    const me = makeMe(["FINANCE_ADMIN"]);
    renderApp({ route: "/finance/payments/new", routes: financeRoutes({
      "GET /api/me/": json({ ...me, capabilities: me.capabilities.filter((c) => c !== "finance.payments.record") }) }) });
    expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
  });

  it("dense tables label every cell for phones", async () => {
    renderApp({ route: "/finance/invoices", routes: financeRoutes() });
    const table = await screen.findByRole("table", { name: "Invoices" });
    for (const cell of within(table).getAllByRole("cell")) expect(cell).toHaveAttribute("data-label");
    expect(competitionInvoice.kind).toBe("COMPETITION");
    expect(invoice.items).toHaveLength(2);
  });
});
