import type { AttendanceSheet, CoachingSession, CompetitionRegistration, RosterStudent } from "../../api/types";
import { academyToday, addDays } from "../../domain/format";
import { json, makeMe } from "../../test/helpers";

export const today = academyToday();
export const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });

export const coachMe = makeMe(["COACH"], {
  name: "Coach Lim", coach: { id: 1, full_name: "Coach Lim" },
  classes: [{ id: 100, name: "Junior Taolu" }], substitute_sessions: [{ session: 703, access_ends_at: null }],
});

function session(id: number, date: string, start: string, className: string, extra: Partial<CoachingSession> = {}): CoachingSession {
  return {
    id, training_class: 100, class_name: className, date, start_time: start, end_time: "20:00:00", venue: "Hall A",
    status: "SCHEDULED", phase: "UPCOMING", notes: "",
    coaches: [{ id: id * 10, coach: 1, coach_name: "Coach Lim", role: "REGULAR", status: "ASSIGNED", replaces: null }],
    my_role: { role: "REGULAR", status: "ASSIGNED", access_ends_at: null },
    attendance: { state: "NOT_STARTED", coach_edit_deadline: "2026-10-03T20:00:00+08:00", expected: 3, marked: 0,
                  unmarked: 3, percentage: null },
    ...extra,
  };
}

export const inProgress = session(701, today, "18:00:00", "Junior Taolu", {
  phase: "IN_PROGRESS", attendance: { state: "OPEN", coach_edit_deadline: "2026-10-03T20:00:00+08:00", expected: 3,
                                      marked: 1, unmarked: 2, percentage: "100.00" } });
export const cancelledToday = session(702, today, "09:00:00", "Morning Sanda", {
  status: "CANCELLED", phase: "CANCELLED",
  attendance: { state: "CANCELLED", coach_edit_deadline: "2026-10-03T20:00:00+08:00", expected: 0, marked: 0, unmarked: 0, percentage: null } });
export const substituteTomorrow = session(703, addDays(today, 1), "10:00:00", "Senior Sanda", {
  training_class: 200,
  coaches: [{ id: 7031, coach: 2, coach_name: "Coach Wong", role: "REGULAR", status: "REPLACED", replaces: null },
            { id: 7032, coach: 1, coach_name: "Coach Lim", role: "SUBSTITUTE", status: "ASSIGNED", replaces: 2 }],
  my_role: { role: "SUBSTITUTE", status: "ASSIGNED", access_ends_at: "2026-10-02T12:00:00+08:00" } });
export const lockedPast = session(704, addDays(today, -5), "18:00:00", "Junior Taolu", {
  phase: "COMPLETED", attendance: { state: "LOCKED", coach_edit_deadline: "2026-09-27T20:00:00+08:00", expected: 3,
                                    marked: 3, unmarked: 0, percentage: "66.67" } });

export const roster: RosterStudent[] = [
  { id: 11, student_no: "A1", full_name: "Aaron Tan", chinese_name: "", gender: "M", age: 12, status: "ACTIVE" },
  { id: 12, student_no: "A2", full_name: "Beatrice Alexandra Wong Mei Ling Tan", chinese_name: "", gender: "F", age: 13,
    status: "ACTIVE" },
  { id: 13, student_no: "A3", full_name: "Chen Tan", chinese_name: "", gender: "M", age: 9, status: "ACTIVE" },
];

export function sheet(state: AttendanceSheet["state"] = "OPEN"): AttendanceSheet {
  return {
    session: 701, state, coach_edit_deadline: "2026-10-03T20:00:00+08:00",
    summary: { expected: 3, marked: 1, unmarked: 2, present: 1, late: 0, absent: 0, excused: 0, percentage: "100.00" },
    sheet: [
      { student: 11, student_name: "Aaron Tan", status: "PRESENT", remarks: "" },
      { student: 12, student_name: "Beatrice Alexandra Wong Mei Ling Tan", status: "UNMARKED", remarks: "" },
      { student: 13, student_name: "Chen Tan", status: "UNMARKED", remarks: "" },
    ],
  };
}

export const entries = [
  { id: 91, competition: 81, competition_name: "State Wushu Open", event: 811, event_name: "Changquan U12",
    student: 11, student_name: "Aaron Tan", status: "PENDING", registered_at: "2026-10-01T09:00:00+08:00",
    result: null, form_version: 2 },
  { id: 92, competition: 81, competition_name: "State Wushu Open", event: 812, event_name: "Nanquan",
    student: 13, student_name: "Chen Tan", status: "CONFIRMED", registered_at: "2026-09-20T09:00:00+08:00",
    result: { placing: 2, medal: "SILVER", score: "8.70", remarks: "" }, form_version: 1 },
] as unknown as CompetitionRegistration[];

type Req = { url: URL };

export function coachRoutes(overrides: Record<string, unknown> = {}) {
  const all = [cancelledToday, inProgress, substituteTomorrow, lockedPast];
  return {
    "GET /api/me/": json(coachMe),
    "GET /api/sessions/coaching/": ({ url }: Req) => {
      const q = url.searchParams;
      let rows = all;
      if (q.get("status")) rows = rows.filter((s) => s.status === q.get("status"));
      if (q.get("date")) rows = rows.filter((s) => s.date === q.get("date"));
      if (q.get("start")) rows = rows.filter((s) => s.date >= q.get("start")!);
      if (q.get("end")) rows = rows.filter((s) => s.date <= q.get("end")!);
      rows = [...rows].sort((a, b) => `${a.date}${a.start_time}`.localeCompare(`${b.date}${b.start_time}`));
      return json(page(q.get("order") === "asc" ? rows : rows.reverse()));
    },
    "GET /api/sessions/701/": json(inProgress),
    "GET /api/sessions/701/roster/": json(roster),
    "GET /api/sessions/701/attendance/": json(sheet()),
    "GET /api/sessions/702/": json(cancelledToday),
    "GET /api/sessions/702/roster/": json([]),
    "GET /api/sessions/702/attendance/": json({ ...sheet("CANCELLED"), sheet: [], summary: { ...sheet().summary, expected: 0, marked: 0, unmarked: 0 } }),
    "GET /api/sessions/704/": json(lockedPast),
    "GET /api/sessions/704/roster/": json(roster),
    "GET /api/sessions/704/attendance/": json(sheet("LOCKED")),
    "GET /api/competition-registrations/": json(page(entries)),
    "GET /api/competitions/": json(page([{ id: 81, name: "State Wushu Open", venue: "Stadium Juara",
      start_date: addDays(today, 30), end_date: addDays(today, 31), registration_deadline: addDays(today, 10),
      status: "OPEN", events: [] }])),
    ...overrides,
  } as unknown as Record<string, never>;
}
