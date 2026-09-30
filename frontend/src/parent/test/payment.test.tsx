import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PaymentProof } from "../../api/types";
import { json, renderApp } from "../../test/helpers";
import { invoice, page, paidInvoice, parentMe, parentRoutes, paymentInfo, QR } from "./fixtures";

const INVOICE_URL = "/parent/finance/invoices/301";

function proof(overrides: Partial<PaymentProof> = {}): PaymentProof {
  return {
    id: 71, family: 7, family_name: "Tan family", invoice: 301, invoice_number: "INV-2026-0012",
    invoice_status: "PARTIALLY_PAID", invoice_balance_due: "250.00", uploaded_by_name: "Parent A",
    uploaded_at: "2026-09-30T09:15:00+08:00", original_name: "maybank-transfer.png", content_type: "image/png",
    size: 2048, amount_claimed: "250.00", payment_date: "2026-09-30", reference: "MBB998877", note: "",
    status: "PENDING_REVIEW", reviewed_at: null, review_note: "", payment: null, payment_number: null,
    ...overrides,
  };
}

function pngFile(name = "transfer.png", size = 1024) {
  return new File([new Uint8Array(size)], name, { type: "image/png" });
}

describe("paying an invoice: payment information", () => {
  it("shows the amount due, bank details, the invoice number as reference and the QR code", async () => {
    renderApp({ route: INVOICE_URL, routes: parentRoutes() });
    const section = (await screen.findByRole("heading", { name: "How to pay" })).closest("section")!;
    expect(within(section).getByText("RM 250.00")).toBeInTheDocument();
    expect(await within(section).findByText("Maybank")).toBeInTheDocument();
    expect(within(section).getByText("SR Wushu Academy")).toBeInTheDocument();
    expect(within(section).getByText("5140 1234 5678")).toBeInTheDocument();
    expect(within(section).getByText("INV-2026-0012")).toBeInTheDocument();
    const qr = within(section).getByRole("img", { name: "Academy payment QR code" });
    expect(qr).toHaveAttribute("src", QR);
    expect(within(section).getByText("Transfer the amount due, then upload your proof.")).toBeInTheDocument();
  });

  it("copies the account number and announces it", async () => {
    const { user } = renderApp({ route: INVOICE_URL, routes: parentRoutes() });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await user.click(await screen.findByRole("button", { name: "Copy account number" }));
    expect(writeText).toHaveBeenCalledWith("5140 1234 5678");
    expect(await screen.findByText("Account number copied.")).toBeInTheDocument();
  });

  it("says so when the academy has not published payment details", async () => {
    renderApp({ route: INVOICE_URL, routes: parentRoutes({
      "GET /api/payment-info/": json({ ...paymentInfo, configured: false, bank_name: "", account_number: "",
                                       qr_code: null }) }) });
    expect(await screen.findByText(/has not published its payment details yet/)).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /QR/ })).not.toBeInTheDocument();
  });

  it("a paid or void invoice offers no payment or upload", async () => {
    renderApp({ route: "/parent/finance/invoices/300", routes: parentRoutes({ "GET /api/invoices/300/": json(paidInvoice) }) });
    await screen.findByRole("heading", { name: "Invoice INV-2026-0009", level: 1 });
    expect(screen.queryByRole("heading", { name: "How to pay" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Upload payment proof" })).not.toBeInTheDocument();
  });
});

describe("uploading payment proof", () => {
  it("validates the file at the field before sending anything", async () => {
    const { user, fetchImpl } = renderApp({ route: INVOICE_URL, routes: parentRoutes() });
    const form = await screen.findByRole("form", { name: "Upload payment proof" });
    await user.click(within(form).getByRole("button", { name: "Upload payment proof" }));
    const input = within(form).getByLabelText(/Proof of payment/);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/Choose the file with your proof of payment\./);
    expect(input).toHaveFocus();
    // Passes the input's accept filter by MIME type, but the extension is not allowed.
    await user.upload(input, new File(["MZ"], "setup.exe", { type: "image/png" }));
    await user.click(within(form).getByRole("button", { name: "Upload payment proof" }));
    expect(within(form).getByText("Upload a PDF, JPG or PNG file.")).toBeInTheDocument();
    await user.upload(input, pngFile("huge.png", 6 * 1024 * 1024));
    await user.click(within(form).getByRole("button", { name: "Upload payment proof" }));
    expect(within(form).getByText("The file is too large (maximum 5 MB).")).toBeInTheDocument();
    await user.upload(input, pngFile());
    await user.type(within(form).getByLabelText("Amount paid (RM)"), "abc");
    await user.click(within(form).getByRole("button", { name: "Upload payment proof" }));
    expect(within(form).getByText("Enter an amount like 220.00.")).toBeInTheDocument();
    expect(fetchImpl.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(false);
  });

  it("uploads, then shows 'pending review' without marking anything paid", async () => {
    let sent: FormData | null = null;
    let proofs: PaymentProof[] = [];
    const { user } = renderApp({ route: INVOICE_URL, routes: parentRoutes({
      "POST /api/payment-proofs/": ({ body }: { body: unknown }) => {
        sent = body as FormData;
        proofs = [proof()];
        return json(proofs[0], 201);
      },
      "GET /api/payment-proofs/": () => json(page(proofs)),
    }) });
    const form = await screen.findByRole("form", { name: "Upload payment proof" });
    await user.upload(within(form).getByLabelText(/Proof of payment/), pngFile("maybank-transfer.png"));
    await user.type(within(form).getByLabelText("Amount paid (RM)"), "250.00");
    await user.type(within(form).getByLabelText("Bank / transfer reference"), "MBB998877");
    await user.click(within(form).getByRole("button", { name: "Upload payment proof" }));
    expect(await screen.findByText("Your payment proof has been submitted and is waiting for academy verification."))
      .toBeInTheDocument();
    expect(sent).not.toBeNull();
    const data = sent! as FormData;
    expect(data.get("invoice")).toBe("301");
    expect((data.get("file") as File).name).toBe("maybank-transfer.png");
    expect(data.get("amount_claimed")).toBe("250.00");
    expect(data.get("reference")).toBe("MBB998877");
    const history = await screen.findByRole("table", { name: "Payment proofs for this invoice" });
    expect(within(history).getByText("Pending review")).toBeInTheDocument();
    // Nothing claims the invoice is paid: its own status is unchanged.
    expect(screen.getAllByText("Partially paid").length).toBeGreaterThan(0);
    expect(screen.queryByText(/payment successful|invoice paid/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Paid", { selector: ".badge" })).not.toBeInTheDocument();
  });

  it("shows the backend's file error at the file field", async () => {
    const { user } = renderApp({ route: INVOICE_URL, routes: parentRoutes({
      "POST /api/payment-proofs/": json({ file: ["The file's contents do not match its type. Upload a real PDF or JPG or PNG file."] }, 400),
    }) });
    const form = await screen.findByRole("form", { name: "Upload payment proof" });
    await user.upload(within(form).getByLabelText(/Proof of payment/), pngFile("fake.png"));
    await user.click(within(form).getByRole("button", { name: "Upload payment proof" }));
    const input = within(form).getByLabelText(/Proof of payment/);
    await waitFor(() => expect(input).toHaveAccessibleDescription(/contents do not match its type/));
  });

  it("an invoice the backend refuses (another family's: 404) shows the standard message", async () => {
    const { user } = renderApp({ route: INVOICE_URL, routes: parentRoutes({
      "POST /api/payment-proofs/": json({ detail: "Not found." }, 404) }) });
    const form = await screen.findByRole("form", { name: "Upload payment proof" });
    await user.upload(within(form).getByLabelText(/Proof of payment/), pngFile());
    await user.click(within(form).getByRole("button", { name: "Upload payment proof" }));
    expect(await screen.findByText("Record not found.")).toBeInTheDocument();
  });

  it("a network failure keeps the form and says so", async () => {
    const { user } = renderApp({ route: INVOICE_URL, routes: parentRoutes({
      "POST /api/payment-proofs/": () => { throw new TypeError("offline"); } }) });
    const form = await screen.findByRole("form", { name: "Upload payment proof" });
    await user.upload(within(form).getByLabelText(/Proof of payment/), pngFile());
    await user.click(within(form).getByRole("button", { name: "Upload payment proof" }));
    expect(await screen.findByText("Unable to connect. Please try again.")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Upload payment proof" })).toBeInTheDocument();
  });

  it("accounts without the upload capability see how to pay but no upload form", async () => {
    const me = { ...parentMe, capabilities: parentMe.capabilities.filter((c) => c !== "finance.proofs.upload_own") };
    renderApp({ route: INVOICE_URL, routes: parentRoutes({ "GET /api/me/": json(me) }) });
    expect(await screen.findByRole("heading", { name: "How to pay" })).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Upload payment proof" })).not.toBeInTheDocument();
  });
});

describe("proof history", () => {
  it("shows rejected proofs with the reason and accepted ones without calling the invoice paid", async () => {
    renderApp({ route: INVOICE_URL, routes: parentRoutes({
      "GET /api/payment-proofs/": json(page([
        proof({ id: 72, status: "ACCEPTED", reviewed_at: "2026-09-30T12:00:00+08:00" }),
        proof({ id: 71, status: "REJECTED", review_note: "Payment amount does not match invoice.",
                reviewed_at: "2026-09-30T11:00:00+08:00" }),
      ])),
    }) });
    const history = await screen.findByRole("table", { name: "Payment proofs for this invoice" });
    expect(within(history).getByText("Rejected")).toBeInTheDocument();
    expect(within(history).getByText("Reason: Payment amount does not match invoice.")).toBeInTheDocument();
    expect(within(history).getByText("Checked by the academy")).toBeInTheDocument();
    expect(screen.getByText(/A proof is not a receipt/)).toBeInTheDocument();
    // The invoice is still what the backend says.
    expect(document.querySelector(".totals")).toHaveTextContent("Balance dueRM 250.00");
  });

  it("downloads the proof file through the API", async () => {
    let asked = false;
    const { user } = renderApp({ route: INVOICE_URL, routes: parentRoutes({
      "GET /api/payment-proofs/": json(page([proof()])),
      "GET /api/payment-proofs/71/file/": () => {
        asked = true;
        return new Response(new Uint8Array([137, 80, 78, 71]), { status: 200, headers: { "Content-Type": "image/png" } });
      },
    }) });
    const createObjectURL = vi.fn(() => "blob:proof");
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await user.click(await screen.findByRole("button", { name: "Download maybank-transfer.png" }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(asked).toBe(true);
    expect(createObjectURL).toHaveBeenCalled();
  });

  it("the payment proofs page lists the family's proofs", async () => {
    renderApp({ route: "/parent/finance/proofs", routes: parentRoutes({
      "GET /api/payment-proofs/": json(page([proof()])) }) });
    const table = await screen.findByRole("table", { name: "Payment proofs" });
    expect(within(table).getByRole("link", { name: "INV-2026-0012" })).toHaveAttribute("href", "/parent/finance/invoices/301");
    expect(within(table).getByText("Pending review")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Payment proofs" })).toHaveAttribute("aria-current", "page");
  });
});

it("fixture: the invoice used here is open", () => {
  expect(invoice.status).toBe("PARTIALLY_PAID");
});
