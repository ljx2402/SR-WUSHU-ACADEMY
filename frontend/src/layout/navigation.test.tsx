import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Role } from "../api/types";
import { json, makeMe, renderApp } from "../test/helpers";

function sidebar() {
  return screen.getByRole("complementary", { name: "Sidebar" });
}

function sectionTitles() {
  return within(sidebar()).queryAllByRole("heading").map((h) => h.textContent);
}

async function renderAs(roles: Role[], route = "/dashboard") {
  const utils = renderApp({ route, routes: { "GET /api/me/": json(makeMe(roles)) } });
  await screen.findByRole("heading", { level: 1 });
  return utils;
}

describe("role-based navigation", () => {
  it("parent sees only the family section", async () => {
    await renderAs(["PARENT"]);
    expect(sectionTitles()).toEqual(["My family"]);
    expect(within(sidebar()).getByRole("link", { name: "Fees & receipts" })).toBeInTheDocument();
    expect(within(sidebar()).queryByRole("link", { name: "Finance" })).not.toBeInTheDocument();
  });

  it("student sees only the student section", async () => {
    await renderAs(["STUDENT"]);
    expect(sectionTitles()).toEqual(["My training"]);
  });

  it("admin sees staff pages matching their capabilities only", async () => {
    await renderAs(["ADMIN"]);
    expect(sectionTitles()).toEqual(["Academy staff"]);
    expect(within(sidebar()).getByRole("link", { name: "Attendance" })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("link", { name: "Finance" })).toBeInTheDocument();
    expect(within(sidebar()).queryByRole("link", { name: "Payroll" })).not.toBeInTheDocument();
  });

  it("finance admin sees finance and payroll but not attendance", async () => {
    await renderAs(["FINANCE_ADMIN"]);
    expect(within(sidebar()).getByRole("link", { name: "Finance" })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("link", { name: "Payroll" })).toBeInTheDocument();
    expect(within(sidebar()).queryByRole("link", { name: "Attendance" })).not.toBeInTheDocument();
  });

  it("a coach who is also a parent sees both sections under one sign-in", async () => {
    await renderAs(["COACH", "PARENT"]);
    expect(sectionTitles()).toEqual(["Coaching", "My family"]);
    expect(within(sidebar()).getByRole("link", { name: "Take attendance" })).toBeInTheDocument();
    expect(within(sidebar()).getByRole("link", { name: "My children" })).toBeInTheDocument();
  });

  it("a super admin gets the staff section only, not parent or coach sections", async () => {
    await renderAs(["SUPER_ADMIN"]);
    expect(sectionTitles()).toEqual(["Academy staff"]);
  });

  it("lists every role in the user menu", async () => {
    const { user } = await renderAs(["COACH", "PARENT"]);
    await user.click(screen.getByRole("button", { name: /Test User/ }));
    const roles = screen.getByRole("list", { name: "Your roles" });
    expect(within(roles).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Coach", "Parent"]);
  });
});

describe("route protection", () => {
  it("direct navigation to another portal's page shows access denied", async () => {
    await renderAs(["PARENT"], "/staff/finance");
    expect(screen.getByTestId("access-denied")).toHaveTextContent("You don’t have permission to access this page.");
  });

  it("a staff role without the capability is denied the page", async () => {
    await renderAs(["ADMIN"], "/staff/payroll");
    expect(screen.getByTestId("access-denied")).toBeInTheDocument();
  });

  it("an allowed page that is not built yet says so (no invented data)", async () => {
    await renderAs(["FINANCE_ADMIN"], "/staff/finance");
    expect(screen.getByRole("heading", { level: 1, name: "Finance" })).toBeInTheDocument();
    expect(screen.getByTestId("not-implemented")).toHaveTextContent("Phase 6F");
    expect(document.title).toBe("Finance · SR Wushu Academy");
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toBeInTheDocument();
  });

  it("unknown URLs show a not-found page", async () => {
    await renderAs(["PARENT"], "/no/such/page");
    expect(screen.getByTestId("not-found")).toHaveTextContent("Page not found.");
  });
});

describe("mobile navigation", () => {
  it("opens as a dialog, moves focus inside, and closes with Escape", async () => {
    const { user } = await renderAs(["COACH", "PARENT"]);
    const toggle = screen.getByRole("button", { name: "Open menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    const drawer = screen.getByRole("dialog", { name: "Menu" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(within(drawer).getByRole("link", { name: "Dashboard" })).toHaveFocus();
    expect(within(drawer).getByRole("link", { name: "My children" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Menu" })).not.toBeInTheDocument();
    expect(toggle).toHaveFocus();
  });

  it("has a visible close button", async () => {
    const { user } = await renderAs(["PARENT"]);
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    await user.click(within(screen.getByRole("dialog", { name: "Menu" })).getByRole("button", { name: /Close menu/ }));
    expect(screen.queryByRole("dialog", { name: "Menu" })).not.toBeInTheDocument();
  });

  it("closes after choosing a page", async () => {
    const { user, router } = await renderAs(["PARENT"]);
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    await user.click(within(screen.getByRole("dialog", { name: "Menu" })).getByRole("link", { name: "Timetable" }));
    expect(router.state.location.pathname).toBe("/parent/timetable");
    expect(screen.queryByRole("dialog", { name: "Menu" })).not.toBeInTheDocument();
  });
});
