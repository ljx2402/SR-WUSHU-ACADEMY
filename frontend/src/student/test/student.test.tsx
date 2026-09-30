import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { json, makeMe, renderApp } from "../../test/helpers";
import { attendance, studentRoutes } from "./fixtures";

function requested(fetchImpl: ReturnType<typeof renderApp>["fetchImpl"]) {
  return fetchImpl.mock.calls.map(([url, init]) => ({ url: new URL(String(url), "http://x"),
                                                      method: ((init as RequestInit | undefined)?.method ?? "GET") }));
}

describe("student dashboard", () => {
  it("a student-only account lands on the student dashboard with today, next session, attendance and competitions", async () => {
    const { router } = renderApp({ route: "/dashboard", routes: studentRoutes() });
    expect(await screen.findByRole("heading", { name: "My dashboard", level: 1 })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/student/dashboard");
    const kpis = screen.getByRole("region", { name: "At a glance" });
    expect(await within(kpis).findByText("Next session")).toBeInTheDocument();
    expect(within(kpis).getByText("Sessions today").closest(".kpi")).toHaveTextContent("1"); // cancelled not counted
    expect(await within(kpis).findByText("18:00")).toBeInTheDocument();
    // The percentage is the backend's; unmarked sessions are said to be left out.
    expect(within(kpis).getByText("Attendance").closest(".kpi")).toHaveTextContent("50.00%");
    expect(within(kpis).getByText("1 not marked yet (not counted)")).toBeInTheDocument();
    const todayCard = screen.getByRole("heading", { name: "Today" }).closest("section")!;
    expect(within(todayCard).getByText("Morning Sanda").closest("li")).toHaveClass("is-cancelled");
    expect(within(todayCard).getByText("Junior Taolu").closest("li")).toHaveTextContent("You: Present");
    const competitions = screen.getByRole("heading", { name: "Competitions" }).closest("section")!;
    expect(await within(competitions).findByText("State Wushu Open")).toBeInTheDocument();
    expect(within(competitions).getByText("Not yet confirmed")).toBeInTheDocument();
    expect(within(competitions).getByText("Placing 2")).toBeInTheDocument();
    expect(within(competitions).getByText("Silver medal")).toBeInTheDocument();
  });

  it("/student redirects to the dashboard", async () => {
    const { router } = renderApp({ route: "/student", routes: studentRoutes() });
    await screen.findByRole("heading", { name: "My dashboard", level: 1 });
    expect(router.state.location.pathname).toBe("/student/dashboard");
  });

  it("the student menu has only the student pages: no finance, family, coach, admin or payroll", async () => {
    renderApp({ route: "/student/dashboard", routes: studentRoutes() });
    await screen.findByRole("heading", { name: "My dashboard", level: 1 });
    const sidebar = screen.getByRole("complementary", { name: "Sidebar" });
    const links = within(sidebar).getAllByRole("link").map((a) => a.textContent);
    expect(links).toEqual(["Dashboard", "My dashboard", "My schedule", "My attendance", "My competitions", "My profile"]);
    for (const hidden of [/finance/i, /famil/i, /payroll|payslip/i, /invoice|payment|receipt|proof/i, /coach/i, /admin/i]) {
      expect(links.some((l) => hidden.test(l ?? ""))).toBe(false);
    }
  });

  it("shows loading, then an error with retry, for each section", async () => {
    renderApp({ route: "/student/dashboard", routes: studentRoutes({
      "GET /api/students/me/attendance/": json({ detail: "boom" }, 500),
    }) });
    expect(screen.getAllByText(/Loading/).length).toBeGreaterThan(0);
    const card = (await screen.findByRole("heading", { name: "Attendance", level: 2 })).closest("section")!;
    expect(await within(card).findByRole("button", { name: /Try again/i })).toBeInTheDocument();
    expect(within(card).queryByText(/boom/)).not.toBeInTheDocument();
  });
});

describe("my schedule", () => {
  it("filters today, upcoming, past and cancelled through the API, never sending a student id", async () => {
    const { user, fetchImpl } = renderApp({ route: "/student/schedule", routes: studentRoutes() });
    expect(await screen.findByRole("heading", { name: "My schedule", level: 1 })).toBeInTheDocument();
    expect((await screen.findByText("Junior Taolu")).closest("li")).toHaveTextContent("Upcoming");
    await user.click(screen.getByRole("radio", { name: "Past" }));
    const unmarked = (await screen.findAllByText("Not marked"))[0].closest("li")!;
    expect(unmarked).toHaveTextContent("You: Not marked");
    expect(screen.getByText("Absent")).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Cancelled" }));
    expect(await screen.findByText("Morning Sanda")).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Today" }));
    expect(await screen.findByText("Morning Sanda")).toBeInTheDocument();
    const views = requested(fetchImpl).filter((r) => r.url.pathname === "/api/students/me/sessions/")
      .map((r) => r.url.searchParams.get("view"));
    expect(views).toEqual(expect.arrayContaining(["upcoming", "past", "cancelled", "today"]));
    for (const r of requested(fetchImpl)) {
      for (const key of ["student", "student_id", "family", "coach"]) expect(r.url.searchParams.has(key)).toBe(false);
    }
  });

  it("session detail shows when, where, coach and own attendance only", async () => {
    renderApp({ route: "/student/sessions/801", routes: studentRoutes() });
    expect(await screen.findByRole("heading", { name: "Junior Taolu", level: 1 })).toBeInTheDocument();
    const main = screen.getByRole("main");
    expect(main).toHaveTextContent("Hall A");
    expect(main).toHaveTextContent("Coach Lim");
    const mine = screen.getByRole("heading", { name: "My attendance" }).closest("section")!;
    expect(within(mine).getByText("Present")).toBeInTheDocument();
    expect(within(main).queryByText(/roster|other students|notes|substitute/i)).not.toBeInTheDocument();
    expect(within(main).queryByRole("button")).not.toBeInTheDocument();   // read only: nothing to change
  });

  it("a cancelled, a future and an unknown session", async () => {
    const { unmount } = renderApp({ route: "/student/sessions/802", routes: studentRoutes() });
    expect(await screen.findByText("This session is cancelled.")).toBeInTheDocument();
    expect(screen.getByText("No attendance (session cancelled).")).toBeInTheDocument();
    unmount();
    const future = renderApp({ route: "/student/sessions/803", routes: studentRoutes() });
    expect(await screen.findByText("Attendance is recorded from the session start.")).toBeInTheDocument();
    future.unmount();
    renderApp({ route: "/student/sessions/999", routes: studentRoutes() });
    expect(await screen.findByText("This session is not in your schedule.")).toBeInTheDocument();
  });
});

describe("my attendance", () => {
  it("shows the backend percentage, unmarked sessions separately, and one row per session", async () => {
    renderApp({ route: "/student/attendance", routes: studentRoutes() });
    expect(await screen.findByRole("heading", { name: "My attendance", level: 1 })).toBeInTheDocument();
    const summary = (await screen.findByRole("heading", { name: "Summary" })).closest("section")!;
    expect(summary).toHaveTextContent("50.00% attended");
    expect(summary).toHaveTextContent("2 of 3 sessions marked");
    expect(summary).toHaveTextContent("1 session not marked yet (not counted in the percentage)");
    const table = screen.getByRole("table", { name: "My attendance by session" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell").at(-1)!.textContent)).toEqual(["Present", "Not marked", "Absent"]);
    // Statuses are words, not colour alone; no remarks or recorder names.
    expect(within(table).queryByText(/recorded by|remark/i)).not.toBeInTheDocument();
  });

  it("does not recalculate: a null percentage shows as a dash", async () => {
    renderApp({ route: "/student/attendance", routes: studentRoutes({
      "GET /api/students/me/attendance/": json({ summary: { ...attendance.summary, percentage: null, present: 0, absent: 0,
                                                            marked: 0, unmarked: 3 }, sessions: [] }) }) });
    const summary = (await screen.findByRole("heading", { name: "Summary" })).closest("section")!;
    expect(summary).toHaveTextContent("— attended (nothing marked yet)");
    expect(screen.getByText("No sessions have taken place yet.")).toBeInTheDocument();
  });
});

describe("my competitions", () => {
  it("shows entries and results, rules as plain text, and nothing about fees, payment or answers", async () => {
    const { user, fetchImpl } = renderApp({ route: "/student/competitions", routes: studentRoutes() });
    expect(await screen.findByRole("heading", { name: "My competitions", level: 1 })).toBeInTheDocument();
    const open = (await screen.findByRole("heading", { name: "State Wushu Open" })).closest("article")!;
    expect(open).toHaveTextContent("Changquan U12");
    expect(open).toHaveTextContent("Not yet confirmed");
    expect(open).toHaveTextContent("Result not recorded yet");
    await user.click(within(open).getByText("Competition rules"));
    expect(within(open).getByText("Bring your own weapon <b>now</b>.")).toBeInTheDocument();
    expect(document.querySelector("main b")).toBeNull();
    const past = screen.getByRole("heading", { name: "Junior Cup" }).closest("article")!;
    expect(past).toHaveTextContent("Placing 2");
    expect(past).toHaveTextContent("Silver medal");
    expect(past).toHaveTextContent("Score 8.950");
    const main = screen.getByRole("main");
    expect(main.textContent).not.toMatch(/\bRM\b|\bfees?\b|invoice|payment|receipt|answers|T-shirt/i);
    // Read only: no register, withdraw or edit, and no write requests.
    expect(within(main).queryByRole("button", { name: /register|withdraw|edit/i })).not.toBeInTheDocument();
    expect(within(main).queryByRole("link", { name: /register/i })).not.toBeInTheDocument();
    expect(requested(fetchImpl).every((r) => r.method === "GET")).toBe(true);
  });

  it("says so when there are no entries", async () => {
    renderApp({ route: "/student/competitions", routes: studentRoutes({ "GET /api/students/me/competitions/": json([]) }) });
    expect(await screen.findByText("You are not entered in any competitions.")).toBeInTheDocument();
  });
});

describe("my profile", () => {
  it("shows the basic profile only, read only", async () => {
    renderApp({ route: "/student/profile", routes: studentRoutes() });
    expect(await screen.findByRole("heading", { name: "My profile", level: 1 })).toBeInTheDocument();
    await screen.findByText("陈亚伦");
    const main = screen.getByRole("main");
    for (const text of ["Aaron Tan", "陈亚伦", "A1", "12", "Active", "Junior Taolu"]) {
      expect(main).toHaveTextContent(text);
    }
    expect(main.textContent).not.toMatch(/\bIC\b|passport|address|guardian|emergency|medical|family|phone|email/i);
    expect(within(main).queryByRole("button")).not.toBeInTheDocument();
    expect(within(main).queryByRole("textbox")).not.toBeInTheDocument();
  });
});

describe("route guards", () => {
  it("a student cannot open parent, finance, coach or staff pages", async () => {
    for (const route of ["/parent/finance", "/parent/family", "/parent/competitions/81/register", "/coach/dashboard",
                         "/coach/sessions/701/attendance", "/staff/finance", "/staff/students"]) {
      const { unmount } = renderApp({ route, routes: studentRoutes() });
      expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
      unmount();
    }
  });

  it("other roles cannot open the student pages", async () => {
    for (const roles of [["PARENT"], ["COACH"], ["ADMIN"]] as const) {
      const { unmount } = renderApp({ route: "/student/attendance", routes: studentRoutes({
        "GET /api/me/": json(makeMe([...roles])) }) });
      expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
      unmount();
    }
  });

  it("a multi-role student + parent keeps both portals and the combined dashboard", async () => {
    renderApp({ route: "/dashboard", routes: studentRoutes({
      "GET /api/me/": json(makeMe(["PARENT", "STUDENT"], { student: { id: 11, student_no: "A1", full_name: "Aaron Tan" } })),
    }) });
    expect(await screen.findByRole("heading", { name: "Dashboard", level: 1 })).toBeInTheDocument();
    const sidebar = screen.getByRole("complementary", { name: "Sidebar" });
    expect(within(sidebar).getByRole("link", { name: "My schedule" })).toBeInTheDocument();
    expect(within(sidebar).getByRole("link", { name: "Family finance" })).toBeInTheDocument();
  });
});

describe("mobile", () => {
  it("the menu opens as a dialog with the student pages", async () => {
    const { user, router } = renderApp({ route: "/student/dashboard", routes: studentRoutes() });
    await screen.findByRole("heading", { name: "My dashboard", level: 1 });
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    const drawer = screen.getByRole("dialog", { name: "Menu" });
    await user.click(within(drawer).getByRole("link", { name: "My attendance" }));
    expect(router.state.location.pathname).toBe("/student/attendance");
    expect(screen.queryByRole("dialog", { name: "Menu" })).not.toBeInTheDocument();
  });

  it("the attendance table labels every cell for the stacked phone layout", async () => {
    renderApp({ route: "/student/attendance", routes: studentRoutes() });
    const table = await screen.findByRole("table", { name: "My attendance by session" });
    for (const cell of within(table).getAllByRole("cell")) expect(cell).toHaveAttribute("data-label");
  });
});
