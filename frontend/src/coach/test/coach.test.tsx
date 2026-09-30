import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { json, makeMe, renderApp } from "../../test/helpers";
import { coachRoutes, sheet } from "./fixtures";

function requested(fetchImpl: ReturnType<typeof renderApp>["fetchImpl"], path: string) {
  return fetchImpl.mock.calls.map(([url, init]) => ({ url: new URL(String(url), "http://x"), init: init as RequestInit }))
    .filter(({ url }) => url.pathname === path);
}

describe("coach dashboard", () => {
  it("a coach-only account lands on the coach dashboard with today, next session and attendance to finish", async () => {
    const { router } = renderApp({ route: "/dashboard", routes: coachRoutes() });
    expect(await screen.findByRole("heading", { name: "Coach dashboard", level: 1 })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/coach/dashboard");
    const kpis = await screen.findByRole("region", { name: "Today at a glance" });
    expect(within(kpis).getByText("Sessions today").closest(".kpi")).toHaveTextContent("1");
    expect(within(kpis).getByText("1 cancelled")).toBeInTheDocument();
    expect(within(kpis).getByText("Next session").closest(".kpi")).toHaveTextContent("10:00");
    expect(within(kpis).getByText("Attendance to finish").closest(".kpi")).toHaveTextContent("1");
    expect(screen.getByText(/In progress: Junior Taolu/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Take attendance" })).toHaveAttribute("href", "/coach/sessions/701/attendance");
    expect(screen.getByText("You are covering 1 session as a substitute.")).toBeInTheDocument();
    const today = screen.getByRole("heading", { name: "Today" }).closest("section")!;
    const cancelled = within(today).getByText("Morning Sanda").closest("li")!;
    expect(cancelled).toHaveTextContent("Cancelled");
    expect(cancelled).toHaveClass("is-cancelled");
  });

  it("the coach menu has no finance, family, payroll or admin pages", async () => {
    renderApp({ route: "/coach/dashboard", routes: coachRoutes() });
    await screen.findByRole("heading", { name: "Coach dashboard", level: 1 });
    const sidebar = screen.getByRole("complementary", { name: "Sidebar" });
    const links = within(sidebar).getAllByRole("link").map((a) => a.textContent);
    expect(links).toEqual(["Dashboard", "Coach dashboard", "My sessions", "Attendance", "Competitions"]);
    for (const hidden of [/finance/i, /famil/i, /payroll|payslip/i, /invoice|payment|receipt/i]) {
      expect(links.some((l) => hidden.test(l ?? ""))).toBe(false);
    }
  });
});

describe("session list", () => {
  it("filters today, upcoming, past and cancelled through the API and marks substitute sessions", async () => {
    const { user, fetchImpl } = renderApp({ route: "/coach/sessions", routes: coachRoutes() });
    expect(await screen.findByText("Junior Taolu")).toBeInTheDocument();
    expect(requested(fetchImpl, "/api/sessions/coaching/").at(-1)!.url.searchParams.get("date")).not.toBeNull();
    await user.click(screen.getByRole("radio", { name: "Upcoming" }));
    const sub = (await screen.findByText("Senior Sanda")).closest("li")!;
    expect(sub).toHaveTextContent("Substitute");
    const last = requested(fetchImpl, "/api/sessions/coaching/").at(-1)!.url.searchParams;
    expect((last.get("start"), last.get("order"))).toBe("asc");
    await user.click(screen.getByRole("radio", { name: "Past" }));
    const locked = (await screen.findByText("Junior Taolu")).closest("li")!;
    expect(locked).toHaveTextContent("Locked");
    expect(locked).toHaveTextContent("3/3 marked");
    await user.click(screen.getByRole("radio", { name: "Cancelled" }));
    expect(await screen.findByText("Morning Sanda")).toBeInTheDocument();
    expect(requested(fetchImpl, "/api/sessions/coaching/").at(-1)!.url.searchParams.get("status")).toBe("CANCELLED");
    // No coach id is ever sent: the backend decides the scope.
    expect(requested(fetchImpl, "/api/sessions/coaching/").every(({ url }) => !url.searchParams.has("coach"))).toBe(true);
  });
});

describe("session detail", () => {
  it("shows the session, coaches, attendance summary and roster with safety information", async () => {
    const { user } = renderApp({ route: "/coach/sessions/701", routes: coachRoutes() });
    expect(await screen.findByRole("heading", { name: "Junior Taolu", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Take attendance" })).toHaveAttribute("href", "/coach/sessions/701/attendance");
    expect(await screen.findByText(/2 students not\s+marked/)).toBeInTheDocument();
    expect(screen.getByText("3 expected · 1 marked · 2 not marked")).toBeInTheDocument();
    const roster = screen.getByRole("heading", { name: "Roster (3)" }).closest("section")!;
    const aaron = within(roster).getByText("Aaron Tan").closest("li")!;
    expect(within(aaron).getByText("Health note")).toBeInTheDocument();
    await user.click(within(aaron).getByText("Safety information"));
    expect(within(aaron).getByText(/Asthma: carries inhaler/)).toBeInTheDocument();
    expect(within(aaron).getByRole("link", { name: "0191" })).toHaveAttribute("href", "tel:0191");
    expect(within(roster).queryByText(/\bIC\b|passport|invoice|\bfee\b/i)).not.toBeInTheDocument();
  });

  it("a cancelled session says so and offers no attendance", async () => {
    renderApp({ route: "/coach/sessions/702", routes: coachRoutes() });
    expect(await screen.findByText("This session is CANCELLED.")).toBeInTheDocument();
    expect(within(screen.getByRole("main")).queryByRole("link", { name: /attendance/i })).not.toBeInTheDocument();
  });

  it("another coach's session is 'Record not found.' (the API's 404)", async () => {
    renderApp({ route: "/coach/sessions/999", routes: coachRoutes() });
    expect(await screen.findByTestId("not-found")).toHaveTextContent("Record not found.");
  });
});

describe("taking attendance", () => {
  it("shows every expected student with counts, and 'mark all' only fills the unmarked", async () => {
    const { user } = renderApp({ route: "/coach/sessions/701/attendance", routes: coachRoutes() });
    const bar = await screen.findByRole("group", { name: "Attendance counts" });
    expect(bar).toHaveTextContent("3 expected");
    expect(bar).toHaveTextContent("1 marked");
    expect(bar).toHaveTextContent("2 not marked");
    const beatrice = screen.getByRole("group", { name: /Beatrice Alexandra Wong Mei Ling Tan/ });
    expect(within(beatrice).getByRole("radio", { name: "Not marked" })).toBeChecked();
    expect(within(beatrice).getAllByRole("radio").map((r) => r.closest("label")!.textContent))
      .toEqual(["Present", "Late", "Absent", "Excused", "Not marked"]);
    await user.click(screen.getByRole("button", { name: "Mark all unmarked as present" }));
    expect(bar).toHaveTextContent("3 marked");
    expect(bar).toHaveTextContent("0 not marked");
    expect(within(beatrice).getByRole("radio", { name: "Present" })).toBeChecked();
    // The saved percentage is the backend's; the page never recomputes it.
    expect(bar).toHaveTextContent("Saved attendance: 100.00%");
  });

  it("saves only changed students without a reason for first marks, then confirms and refreshes", async () => {
    let posted: unknown = null;
    let saved = sheet();
    const { user } = renderApp({ route: "/coach/sessions/701/attendance", routes: coachRoutes({
      "GET /api/sessions/701/attendance/": () => json(saved),
      "POST /api/sessions/701/attendance/": ({ body }: { body: unknown }) => {
        posted = body;
        saved = { ...saved, summary: { ...saved.summary, marked: 3, unmarked: 0 }, state: "COMPLETE",
                  sheet: saved.sheet.map((r) => (r.student === 12 ? { ...r, status: "LATE" } : r.student === 13 ? { ...r, status: "ABSENT", remarks: "Sick" } : r)) };
        return json([]);
      },
    }) });
    const beatrice = await screen.findByRole("group", { name: /Beatrice/ });
    await user.click(within(beatrice).getByRole("radio", { name: "Late" }));
    const chen = screen.getByRole("group", { name: /Chen Tan/ });
    await user.click(within(chen).getByRole("radio", { name: "Absent" }));
    await user.click(within(chen).getByText("Remark", { selector: "summary" }));
    await user.type(within(chen).getByLabelText("Remark for Chen Tan"), "Sick");
    expect(screen.queryByLabelText(/Reason for changing saved attendance/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save attendance" }));
    const status = await screen.findByText("Attendance saved.");
    expect(posted).toEqual({ reason: "", records: [
      { student: 12, status: "LATE", remarks: "" }, { student: 13, status: "ABSENT", remarks: "Sick" }] });
    expect(status.closest("[role=status]")).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector(".attendance-status")).toHaveFocus());
    await waitFor(() => expect(screen.getByRole("group", { name: "Attendance counts" })).toHaveTextContent("0 not marked"));
  });

  it("changing a saved mark asks for a reason and sends it", async () => {
    let posted: { reason?: string } | null = null;
    const { user } = renderApp({ route: "/coach/sessions/701/attendance", routes: coachRoutes({
      "POST /api/sessions/701/attendance/": ({ body }: { body: { reason?: string } }) => { posted = body; return json([]); },
    }) });
    const aaron = await screen.findByRole("group", { name: /Aaron Tan/ });
    await user.click(within(aaron).getByRole("radio", { name: "Absent" }));
    await user.click(screen.getByRole("button", { name: "Save attendance" }));
    const reason = screen.getByLabelText(/Reason for changing saved attendance/);
    await waitFor(() => expect(reason).toHaveFocus());
    expect(screen.getByRole("alert")).toHaveTextContent(/needs one/);
    expect(posted).toBeNull();
    await user.type(reason, "Left before warm-up");
    await user.click(screen.getByRole("button", { name: "Save attendance" }));
    await waitFor(() => expect(posted).toEqual({ reason: "Left before warm-up",
                                                 records: [{ student: 11, status: "ABSENT", remarks: "" }] }));
  });

  it("a late correction refused by the backend (403) says attendance is locked", async () => {
    const { user } = renderApp({ route: "/coach/sessions/701/attendance", routes: coachRoutes({
      "POST /api/sessions/701/attendance/": json({ detail: "The 48-hour attendance edit window ... closed" }, 403),
    }) });
    const chen = await screen.findByRole("group", { name: /Chen Tan/ });
    await user.click(within(chen).getByRole("radio", { name: "Present" }));
    await user.click(screen.getByRole("button", { name: "Save attendance" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/can no longer be changed by coaches/);
    await waitFor(() => expect(alert.parentElement).toHaveFocus());
  });

  it("shows the backend's validation message (e.g. before the start)", async () => {
    const { user } = renderApp({ route: "/coach/sessions/701/attendance", routes: coachRoutes({
      "POST /api/sessions/701/attendance/": json({ detail: ["Attendance can be recorded from the session start."] }, 400),
    }) });
    const chen = await screen.findByRole("group", { name: /Chen Tan/ });
    await user.click(within(chen).getByRole("radio", { name: "Present" }));
    await user.click(screen.getByRole("button", { name: "Save attendance" }));
    expect(await screen.findByText("Attendance can be recorded from the session start.")).toBeInTheDocument();
  });

  it("a locked session is read-only and says who can correct it", async () => {
    renderApp({ route: "/coach/sessions/704/attendance", routes: coachRoutes() });
    expect(await screen.findByText("Attendance is locked.")).toBeInTheDocument();
    expect(screen.getByText(/Only an administrator can correct it now, with a reason/)).toBeInTheDocument();
    for (const group of screen.getAllByRole("group").filter((g) => g.tagName === "FIELDSET")) expect(group).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save attendance" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Mark all/ })).not.toBeInTheDocument();
  });

  it("a cancelled session takes no attendance", async () => {
    renderApp({ route: "/coach/sessions/702/attendance", routes: coachRoutes() });
    expect(await screen.findByText("This session is CANCELLED.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save attendance" })).not.toBeInTheDocument();
  });
});

describe("attendance index and competitions", () => {
  it("lists sessions open for attendance", async () => {
    renderApp({ route: "/coach/attendance", routes: coachRoutes() });
    const open = (await screen.findByRole("heading", { name: "Open for attendance" })).closest("section")!;
    expect(within(open).getByText("Junior Taolu")).toBeInTheDocument();
    expect(within(open).queryByText("Morning Sanda")).not.toBeInTheDocument();
  });

  it("shows athletes' entries and results without any payment or family details", async () => {
    renderApp({ route: "/coach/competitions", routes: coachRoutes() });
    const table = await screen.findByRole("table", { name: "Entries in State Wushu Open" });
    expect(within(table).getByText("Aaron Tan").closest("tr")).toHaveTextContent("Not yet confirmed");
    expect(within(table).getByText("Chen Tan").closest("tr")).toHaveTextContent("Placing 2 · Silver · Score 8.70");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Student", "Event", "Entry", "Result"]);
    expect(screen.queryByText(/invoice|fee|payment|RM /i)).not.toBeInTheDocument();
  });
});

describe("route guards", () => {
  it("a coach cannot open finance, family or staff pages by URL", async () => {
    for (const route of ["/parent/finance", "/parent/family", "/staff/finance", "/staff/payroll"]) {
      const { unmount } = renderApp({ route, routes: coachRoutes() });
      expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
      unmount();
    }
  });

  it("a parent cannot open the coach portal", async () => {
    renderApp({ route: "/coach/sessions/701/attendance", routes: { "GET /api/me/": json(makeMe(["PARENT"])) } });
    expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
  });
});
