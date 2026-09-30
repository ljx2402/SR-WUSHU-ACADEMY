import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/errors";
import { DataTable } from "./DataTable";
import { ConfirmDialog, Dialog } from "./Dialog";
import { TextField } from "./Field";
import { EmptyState, ErrorState, LoadingState } from "./states";

function DialogHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Open</button>
      <Dialog open={open} title="Void payment" onClose={() => setOpen(false)}
              footer={<><button>First</button><button>Last</button></>}>
        <p>Body</p>
      </Dialog>
    </>
  );
}

describe("Dialog", () => {
  it("is labelled, traps focus, closes on Escape and restores focus", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    const opener = screen.getByRole("button", { name: "Open" });
    await user.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Void payment" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: "First" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Last" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "First" })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Last" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});

describe("ConfirmDialog", () => {
  it("requires a reason before confirming", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmDialog open title="Void receipt" message="This cannot be undone." confirmLabel="Void"
                          requireReason onConfirm={onConfirm} onCancel={() => {}} />);
    const confirm = screen.getByRole("button", { name: "Void" });
    expect(confirm).toBeDisabled();
    expect(screen.getByRole("dialog")).toHaveAccessibleDescription("This cannot be undone.");
    await user.type(screen.getByLabelText(/Reason/), "  Entered twice  ");
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith("Entered twice");
  });
});

describe("TextField", () => {
  it("associates hint and errors with the input", () => {
    render(<TextField label="Amount" hint="In ringgit" errors={["Must be positive."]} />);
    const input = screen.getByLabelText("Amount");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("In ringgit Must be positive.");
  });
});

describe("standard states", () => {
  it("use the standard wording", () => {
    render(<><LoadingState /><EmptyState /></>);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.getByText("No records found.")).toBeInTheDocument();
  });

  it("map API errors without leaking details", () => {
    const { rerender } = render(<MemoryRouter><ErrorState error={new ApiError("forbidden", 403)} /></MemoryRouter>);
    expect(screen.getByTestId("access-denied")).toBeInTheDocument();
    rerender(<MemoryRouter><ErrorState error={new ApiError("not_found", 404)} /></MemoryRouter>);
    expect(screen.getByTestId("not-found")).toHaveTextContent("Record not found.");
    rerender(<MemoryRouter><ErrorState error={new ApiError("network", null)} onRetry={() => {}} /></MemoryRouter>);
    expect(screen.getByRole("alert")).toHaveTextContent("Unable to connect. Please try again.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    rerender(<MemoryRouter><ErrorState error={new Error("TypeError: x is undefined at foo.js:1")} /></MemoryRouter>);
    expect(screen.getByRole("alert")).not.toHaveTextContent("foo.js");
  });
});

describe("DataTable", () => {
  it("labels every cell for the stacked mobile layout", () => {
    render(<DataTable caption="Payments" rows={[{ id: 1, amount: "RM 5.00" }]} rowKey={(r) => r.id}
                      columns={[{ key: "amount", header: "Amount", render: (r) => r.amount, align: "end" }]} />);
    expect(screen.getByRole("table", { name: "Payments" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "RM 5.00" })).toHaveAttribute("data-label", "Amount");
  });

  it("shows the empty state when there are no rows", () => {
    render(<DataTable caption="Payments" rows={[]} rowKey={() => 1} columns={[]} />);
    expect(screen.getByText("No records found.")).toBeInTheDocument();
  });
});
