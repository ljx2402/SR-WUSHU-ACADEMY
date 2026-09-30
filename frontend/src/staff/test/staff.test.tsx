import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { json, makeMe, renderApp } from "../../test/helpers";
import { directoryStudent, financeMe, fullStudent, staffRoutes, upcomingSession } from "./fixtures";

type Call = { url: URL; method: string; body: unknown };
function calls(fetchImpl: ReturnType<typeof renderApp>["fetchImpl"]): Call[] {
  return fetchImpl.mock.calls.map(([url, init]) => {
    const i = (init ?? {}) as RequestInit;
    return { url: new URL(String(url), "http://x"), method: i.method ?? "GET",
             body: typeof i.body === "string" ? JSON.parse(i.body) : undefined };
  });
}
const posts = (fetchImpl: ReturnType<typeof renderApp>["fetchImpl"]) => calls(fetchImpl).filter((c) => c.method !== "GET");

describe("staff dashboard", () => {
  it("shows today's operations, backend alerts and completed attendance", async () => {
    renderApp({ route: "/staff/dashboard", routes: staffRoutes() });
    expect(await screen.findByRole("heading", { name: "Operations", level: 1 })).toBeInTheDocument();
    const kpis = screen.getByRole("region", { name: "Today at a glance" });
    expect(await within(kpis).findByText("42")).toBeInTheDocument();
    expect(within(kpis).getByText("Sessions today").closest(".kpi")).toHaveTextContent("2");
    expect(within(kpis).getByText("1 cancelled")).toBeInTheDocument();
    expect(within(kpis).getByText("1 covered by a substitute")).toBeInTheDocument();
    const attention = screen.getByRole("heading", { name: "Needs attention" }).closest("section")!;
    expect(within(attention).getByText(/Attendance not finished/)).toBeInTheDocument();
    expect(within(attention).getByRole("link", { name: /Junior Taolu · 2 not marked/ }))
      .toHaveAttribute("href", "/staff/sessions/701/attendance");
    expect(within(attention).getByText(/administrator correction/)).toBeInTheDocument();
    expect(within(attention).getByRole("link", { name: /Senior Sanda/ })).toHaveAttribute("href", "/staff/sessions/705");
    // An alert with a zero count is not shown (only real state).
    expect(within(attention).queryByText(/Active classes without a coach/)).not.toBeInTheDocument();
    const today = screen.getByRole("heading", { name: "Today's sessions" }).closest("section")!;
    expect(await within(today).findByText("In progress: Junior Taolu")).toBeInTheDocument();
    expect(within(today).getByText("Morning Sanda").closest("tr")).toHaveTextContent("Cancelled");
    expect(screen.getByText(/Senior Sanda · 2\/2/)).toBeInTheDocument();
    // No finance, bank, payroll or medical data on the operations dashboard.
    expect(screen.getByRole("main").textContent).not.toMatch(/\bRM\b|invoice|bank|payslip|asthma|IC/);
  });

  it("/staff redirects to the dashboard and the staff menu follows the capabilities", async () => {
    const { router } = renderApp({ route: "/staff", routes: staffRoutes() });
    await screen.findByRole("heading", { name: "Operations", level: 1 });
    expect(router.state.location.pathname).toBe("/staff/dashboard");
    const sidebar = screen.getByRole("complementary", { name: "Sidebar" });
    const links = within(sidebar).getAllByRole("link").map((a) => a.textContent);
    for (const name of ["Operations", "Students", "Classes", "Timetable", "Sessions", "Attendance"]) {
      expect(links).toContain(name);
    }
    expect(links).not.toContain("Payroll");  // ADMIN has no payroll capability
  });

  it("shows an error with retry when the dashboard fails", async () => {
    renderApp({ route: "/staff/dashboard", routes: staffRoutes({ "GET /api/staff/dashboard/": json({ detail: "x" }, 500) }) });
    expect((await screen.findAllByRole("button", { name: /Try again/i })).length).toBeGreaterThan(0);
  });
});

describe("students", () => {
  it("lists minimal rows, searches and filters through the API", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/students", routes: staffRoutes() });
    const table = await screen.findByRole("table", { name: "Students" });
    expect(within(table).getByText("Aaron Tan 陈亚伦")).toBeInTheDocument();
    expect(within(table).getByText("Junior Taolu")).toBeInTheDocument();
    expect(table.textContent).not.toMatch(/140501|Asthma|Test Street/);
    await user.type(screen.getByLabelText("Search"), "beth");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await waitFor(() => expect(within(screen.getByRole("table", { name: "Students" })).queryByText(/Aaron/)).not.toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText("Status"), "ON_LEAVE");
    await user.selectOptions(await screen.findByLabelText("Class"), "Junior Taolu");
    const last = calls(fetchImpl).filter((c) => c.url.pathname === "/api/students/").at(-1)!.url.searchParams;
    expect([last.get("search"), last.get("status"), last.get("class")]).toEqual(["beth", "ON_LEAVE", "100"]);
  });

  it("detail shows the authorized record, masks the IC until shown, and lists guardians without their IC", async () => {
    const { user } = renderApp({ route: "/staff/students/11", routes: staffRoutes() });
    expect(await screen.findByRole("heading", { name: "Aaron Tan", level: 1 })).toBeInTheDocument();
    const personal = screen.getByRole("heading", { name: "Personal details" }).closest("section")!;
    expect(personal).not.toHaveTextContent("140501-10-1234");
    await user.click(within(personal).getByRole("button", { name: "Show" }));
    expect(personal).toHaveTextContent("140501-10-1234");
    expect(screen.getByRole("heading", { name: "Health and safety" }).closest("section")).toHaveTextContent("Asthma");
    const guardians = screen.getByRole("heading", { name: "Family and guardians" }).closest("section")!;
    expect(guardians).toHaveTextContent("Mei Tan");
    expect(guardians).toHaveTextContent("Emergency contact");
    expect(guardians).not.toHaveTextContent("800101");
    expect(await screen.findByText("Name spelling")).toBeInTheDocument();   // recent changes (audit)
    expect(screen.getByRole("main").textContent).not.toMatch(/invoice|\bRM\b|payment|bank/i);
  });

  it("edits only the allowed fields through the API and changes status with a reason", async () => {
    let patched: unknown = null;
    let status: unknown = null;
    const { user } = renderApp({ route: "/staff/students/11", routes: staffRoutes({
      "PATCH /api/students/11/": ({ body }: { body: unknown }) => { patched = body; return json({ ...fullStudent, full_name: "Aaron Tan Wei" }); },
      "POST /api/students/11/change-status/": ({ body }: { body: unknown }) => { status = body; return json({ ...fullStudent, status: "ON_LEAVE" }); },
    }) });
    await user.click(await screen.findByRole("button", { name: "Edit details" }));
    const dialog = screen.getByRole("dialog", { name: "Edit student details" });
    expect(within(dialog).queryByLabelText(/IC|Student no|Medical/i)).not.toBeInTheDocument();
    const name = within(dialog).getByLabelText(/Full name/);
    await user.clear(name);
    await user.type(name, "Aaron Tan Wei");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Student details saved.")).toBeInTheDocument();
    expect(patched).toEqual({ full_name: "Aaron Tan Wei", chinese_name: "陈亚伦", gender: "M", date_of_birth: "2014-05-01",
                              school: "SJK Test" });
    await user.click(screen.getByRole("button", { name: "Change status" }));
    const statusDialog = screen.getByRole("dialog", { name: "Change status" });
    await user.selectOptions(within(statusDialog).getByLabelText("New status"), "ON_LEAVE");
    await user.type(within(statusDialog).getByLabelText(/Reason/), "Exams");
    await user.click(within(statusDialog).getByRole("button", { name: "Change status" }));
    await waitFor(() => expect(status).toEqual({ status: "ON_LEAVE", reason: "Exams" }));
  });

  it("shows the backend's refusal inside the dialog", async () => {
    const { user } = renderApp({ route: "/staff/students/11", routes: staffRoutes({
      "POST /api/enrollments/": json({ detail: ["Aaron Tan is already in Junior Taolu."] }, 400) }) });
    await user.click(await screen.findByRole("button", { name: "Add to a class" }));
    const dialog = screen.getByRole("dialog", { name: "Add to a class" });
    await user.selectOptions(within(dialog).getByLabelText(/Class/), "Junior Taolu");
    await user.click(within(dialog).getByRole("button", { name: "Add" }));
    expect(await within(dialog).findByText("Aaron Tan is already in Junior Taolu.")).toBeInTheDocument();
  });

  it("finance admin sees the directory only, with no edit controls or class filters", async () => {
    renderApp({ route: "/staff/students/11", routes: staffRoutes({ "GET /api/me/": json(financeMe),
      "GET /api/students/11/": json(directoryStudent) }) });
    expect(await screen.findByRole("heading", { name: "Aaron Tan", level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Edit|Change status|Add to a class/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Personal details|Health and safety|Classes/ })).not.toBeInTheDocument();
    expect(screen.getByText("Mei Tan")).toBeInTheDocument();
  });
});

describe("classes and timetable", () => {
  it("lists active classes with coaches, timetable and counts; creates a class through the API", async () => {
    let created: unknown = null;
    const { user, router } = renderApp({ route: "/staff/classes", routes: staffRoutes({
      "POST /api/classes/": ({ body }: { body: unknown }) => { created = body; return json({ id: 100 }, 201); } }) });
    const table = await screen.findByRole("table", { name: "Classes" });
    expect(within(table).getByText("Junior Taolu").closest("tr")).toHaveTextContent("Coach Lim");
    expect(within(table).getByText("Senior Sanda").closest("tr")).toHaveTextContent("No coach");
    expect(within(table).queryByText("Old Class")).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "Inactive" }));
    expect(screen.getByText("Old Class")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New class" }));
    const dialog = screen.getByRole("dialog", { name: "New class" });
    await user.type(within(dialog).getByLabelText(/Name/), "Kids Taiji");
    await user.type(within(dialog).getByLabelText(/Code/), "kids-taiji");
    await user.selectOptions(await within(dialog).findByLabelText(/Program/), "Wushu Taolu");
    await user.click(within(dialog).getByRole("button", { name: "Create class" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/staff/classes/100"));
    expect(created).toMatchObject({ code: "kids-taiji", name: "Kids Taiji", category: "SCHOOL", program: 1 });
  });

  it("class detail: roster without sensitive data, timetable, and deactivation after confirmation", async () => {
    let patched: unknown = null;
    const { user } = renderApp({ route: "/staff/classes/100", routes: staffRoutes({
      "PATCH /api/classes/100/": ({ body }: { body: unknown }) => { patched = body; return json({ id: 100, name: "Junior Taolu", is_active: false }); } }) });
    expect(await screen.findByRole("heading", { name: "Junior Taolu", level: 1 })).toBeInTheDocument();
    const roster = (await screen.findByRole("heading", { name: "Roster (1)" })).closest("section")!;
    expect(roster).toHaveTextContent("Aaron Tan");
    expect(roster.textContent).not.toMatch(/Asthma|140501|Mei Tan/);
    expect(screen.getByRole("table", { name: "Timetable of Junior Taolu" })).toHaveTextContent("Monday");
    await user.click(screen.getByRole("button", { name: "Deactivate" }));
    await user.click(within(screen.getByRole("dialog", { name: "Deactivate class" })).getByRole("button", { name: "Deactivate" }));
    await waitFor(() => expect(patched).toEqual({ is_active: false }));
  });

  it("timetable filters by day, class, coach and venue", async () => {
    const { user } = renderApp({ route: "/staff/timetable", routes: staffRoutes() });
    const table = await screen.findByRole("table", { name: "Weekly timetable" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    await user.selectOptions(screen.getByLabelText("Day"), "Wednesday");
    expect(within(screen.getByRole("table", { name: "Weekly timetable" })).getByText("Senior Sanda")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Weekly timetable" })).queryByText("Junior Taolu")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Day"), "");
    await user.selectOptions(await screen.findByLabelText("Coach"), "Coach Lim");
    expect(within(screen.getByRole("table", { name: "Weekly timetable" })).getByText("Junior Taolu")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Weekly timetable" })).queryByText("Senior Sanda")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Venue"), "Hall B");
    expect(screen.getByText("No timetable slots match.")).toBeInTheDocument();
  });
});

describe("sessions", () => {
  it("filters sessions through the API (views, date, class)", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/sessions", routes: staffRoutes() });
    expect(await screen.findByRole("table", { name: "Sessions" })).toHaveTextContent("Junior Taolu");
    await user.click(screen.getByRole("radio", { name: "Cancelled" }));
    await waitFor(() => expect(calls(fetchImpl).filter((c) => c.url.pathname === "/api/sessions/coaching/").at(-1)!
      .url.searchParams.get("status")).toBe("CANCELLED"));
    await user.selectOptions(await screen.findByLabelText("Class"), "Junior Taolu");
    await waitFor(() => expect(calls(fetchImpl).filter((c) => c.url.pathname === "/api/sessions/coaching/").at(-1)!
      .url.searchParams.get("class")).toBe("100"));
  });

  it("session detail: coaches, attendance summary and roster; reschedule sends the service call with a reason", async () => {
    let body: unknown = null;
    const { user } = renderApp({ route: "/staff/sessions/703", routes: staffRoutes({
      "POST /api/sessions/703/reschedule/": (r: { body: unknown }) => { body = r.body; return json(upcomingSession); } }) });
    expect(await screen.findByRole("heading", { name: "Junior Taolu", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Coaches of this session" })).toHaveTextContent("Coach Lim");
    expect(await screen.findByRole("table", { name: "Expected students" })).toHaveTextContent("Chen Tan");
    await user.click(screen.getByRole("button", { name: "Reschedule" }));
    const dialog = screen.getByRole("dialog", { name: "Reschedule session" });
    await user.clear(within(dialog).getByLabelText("New date"));
    await user.type(within(dialog).getByLabelText("New date"), "2030-01-07");
    await user.type(within(dialog).getByLabelText(/Reason/), "Hall booked");
    await user.click(within(dialog).getByRole("button", { name: "Reschedule" }));
    expect(await screen.findByText("Session rescheduled.")).toBeInTheDocument();
    expect(body).toMatchObject({ date: "2030-01-07", reason: "Hall booked" });
  });

  it("cancel needs a reason in the dialog, and the backend's refusal is shown", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/sessions/703", routes: staffRoutes({
      "POST /api/sessions/703/cancel/": json({ detail: ["This session is already cancelled."] }, 400) }) });
    await user.click(await screen.findByRole("button", { name: "Cancel session" }));
    const dialog = screen.getByRole("dialog", { name: "Cancel session" });
    expect(within(dialog).getByRole("button", { name: "Cancel session" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Reason/), "Flood");
    await user.click(within(dialog).getByRole("button", { name: "Cancel session" }));
    expect(await within(dialog).findByText("This session is already cancelled.")).toBeInTheDocument();
    expect(posts(fetchImpl).map((c) => [c.url.pathname, c.body])).toEqual([["/api/sessions/703/cancel/", { reason: "Flood" }]]);
  });

  it("assigns a substitute and reassigns a coach with validated choices (active coaches only)", async () => {
    const { user, fetchImpl } = renderApp({ route: "/staff/sessions/703", routes: staffRoutes({
      "POST /api/sessions/703/assign-substitute/": json({ id: 9, status: "ASSIGNED" }, 201),
      "POST /api/sessions/703/reassign-coach/": json({ id: 10, status: "ASSIGNED" }) }) });
    await user.click(await screen.findByRole("button", { name: "Assign substitute" }));
    let dialog = screen.getByRole("dialog", { name: "Assign substitute" });
    const picker = await within(dialog).findByLabelText(/Substitute coach/);
    expect(within(picker).queryByText("Former Coach")).not.toBeInTheDocument();
    await user.selectOptions(picker, "Coach Wong");
    await user.type(within(dialog).getByLabelText(/Reason/), "Coach Lim at a course");
    await user.click(within(dialog).getByRole("button", { name: "Assign substitute" }));
    expect(await screen.findByText("Substitute assigned.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reassign coach" }));
    dialog = screen.getByRole("dialog", { name: "Reassign regular coach" });
    await user.selectOptions(within(dialog).getByLabelText(/With coach/), "Coach Wong");
    await user.type(within(dialog).getByLabelText(/Reason/), "Timetable change");
    await user.click(within(dialog).getByRole("button", { name: "Reassign" }));
    expect(await screen.findByText("Coach reassigned.")).toBeInTheDocument();
    expect(posts(fetchImpl).map((c) => c.body)).toEqual([
      { substitute: 2, replaces: 1, reason: "Coach Lim at a course" },
      { from_coach: 1, to_coach: 2, reason: "Timetable change" }]);
  });
});

describe("attendance", () => {
  it("monitoring shows the backend counts and filters incomplete sessions", async () => {
    const { user } = renderApp({ route: "/staff/attendance", routes: staffRoutes() });
    const table = await screen.findByRole("table", { name: "Attendance by session" });
    expect(within(table).queryByText("Morning Sanda")).not.toBeInTheDocument();   // cancelled hidden by default
    const locked = within(table).getAllByText("Junior Taolu").map((el) => el.closest("tr")!)
      .find((tr) => /Locked/.test(tr.textContent ?? ""))!;
    expect(locked).toHaveTextContent("2/3 marked");
    expect(locked).toHaveTextContent("1 not marked");
    expect(locked).toHaveTextContent("50.00%");
    await user.click(screen.getByLabelText("Incomplete only"));
    const filtered = screen.getByRole("table", { name: "Attendance by session" });
    expect(within(filtered).queryByText("Senior Sanda")).not.toBeInTheDocument();  // complete
    await user.click(screen.getByLabelText("Show cancelled"));
    await user.click(screen.getByLabelText("Incomplete only"));
    expect(within(screen.getByRole("table", { name: "Attendance by session" })).getByText("Morning Sanda")).toBeInTheDocument();
  });

  it("an administrator corrects a locked session with a required reason and sees the change history", async () => {
    let body: unknown = null;
    const { user } = renderApp({ route: "/staff/sessions/704/attendance", routes: staffRoutes({
      "POST /api/sessions/704/attendance/": (r: { body: unknown }) => { body = r.body; return json([]); } }) });
    expect(await screen.findByText(/You can correct it as an administrator/)).toBeInTheDocument();
    const row = screen.getByRole("group", { name: /Chen Tan/ });
    await user.click(within(row).getByRole("radio", { name: "Present" }));
    await user.click(screen.getByRole("button", { name: "Save attendance" }));
    expect(await screen.findByText(/a correction after the 48-hour window needs one/)).toBeInTheDocument();
    expect(body).toBeNull();
    await user.type(screen.getByLabelText(/Reason for this correction/), "Register found");
    await user.click(screen.getByRole("button", { name: "Save attendance" }));
    await waitFor(() => expect(body).toEqual({ records: [{ student: 13, status: "PRESENT", remarks: "" }], reason: "Register found" }));
    const history = screen.getByRole("region", { name: "Change history" });
    await user.click(within(history).getByText("Aaron Tan"));
    expect(await within(history).findByText(/— → PRESENT/)).toBeInTheDocument();
  });

  it("without attendance.correct a locked sheet stays read only", async () => {
    const me = makeMe(["ADMIN"]);
    renderApp({ route: "/staff/sessions/704/attendance", routes: staffRoutes({
      "GET /api/me/": json({ ...me, capabilities: me.capabilities.filter((c) => c !== "attendance.correct") }) }) });
    expect(await screen.findByText(/Only an administrator can correct it now/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save attendance" })).not.toBeInTheDocument();
  });
});

describe("route guards and roles", () => {
  it("coaches, parents and students are denied every staff page", async () => {
    for (const role of ["COACH", "PARENT", "STUDENT"] as const) {
      for (const route of ["/staff/dashboard", "/staff/students", "/staff/students/11", "/staff/classes", "/staff/sessions/701",
                           "/staff/attendance"]) {
        const { unmount } = renderApp({ route, routes: staffRoutes({ "GET /api/me/": json(makeMe([role])) }) });
        expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
        unmount();
      }
    }
  });

  it("finance admin gets the student directory but no operations, classes, sessions or attendance", async () => {
    const { unmount } = renderApp({ route: "/staff/students", routes: staffRoutes({ "GET /api/me/": json(financeMe) }) });
    expect(await screen.findByRole("heading", { name: "Students", level: 1 })).toBeInTheDocument();
    const links = within(screen.getByRole("complementary", { name: "Sidebar" })).getAllByRole("link").map((a) => a.textContent);
    expect(links).toContain("Students");
    for (const hidden of ["Operations", "Classes", "Timetable", "Sessions", "Attendance"]) expect(links).not.toContain(hidden);
    expect(screen.queryByLabelText("Class")).not.toBeInTheDocument();
    unmount();
    for (const route of ["/staff/dashboard", "/staff/classes", "/staff/timetable", "/staff/sessions", "/staff/attendance"]) {
      const view = renderApp({ route, routes: staffRoutes({ "GET /api/me/": json(financeMe) }) });
      expect(await screen.findByTestId("access-denied")).toBeInTheDocument();
      view.unmount();
    }
  });

  it("a super admin reaches the staff pages", async () => {
    renderApp({ route: "/staff/sessions", routes: staffRoutes({ "GET /api/me/": json(makeMe(["SUPER_ADMIN"])) }) });
    expect(await screen.findByRole("heading", { name: "Sessions", level: 1 })).toBeInTheDocument();
  });
});

describe("mobile and accessibility", () => {
  it("dense tables label every cell for the stacked phone layout", async () => {
    renderApp({ route: "/staff/attendance", routes: staffRoutes() });
    const table = await screen.findByRole("table", { name: "Attendance by session" });
    for (const cell of within(table).getAllByRole("cell")) expect(cell).toHaveAttribute("data-label");
  });

  it("the staff menu opens as a dialog on phones", async () => {
    const { user, router } = renderApp({ route: "/staff/dashboard", routes: staffRoutes() });
    await screen.findByRole("heading", { name: "Operations", level: 1 });
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    await user.click(within(screen.getByRole("dialog", { name: "Menu" })).getByRole("link", { name: "Timetable" }));
    expect(router.state.location.pathname).toBe("/staff/timetable");
  });

  it("an unknown student shows not found", async () => {
    renderApp({ route: "/staff/students/999", routes: staffRoutes() });
    expect(await screen.findByText("Student not found.")).toBeInTheDocument();
  });
});
