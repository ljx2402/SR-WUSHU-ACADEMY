import type {
  StudentAttendanceResponse, StudentCompetitionEntry, StudentSelfProfile, StudentSession,
} from "../../api/types";
import { academyToday, addDays } from "../../domain/format";
import { json, makeMe } from "../../test/helpers";

export const today = academyToday();
export const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });

export const studentMe = makeMe(["STUDENT"], {
  name: "Aaron Tan", student: { id: 11, student_no: "A1", full_name: "Aaron Tan" },
});

function session(id: number, date: string, start: string, extra: Partial<StudentSession> = {}): StudentSession {
  return { id, class_name: "Junior Taolu", date, start_time: start, end_time: "20:00:00", venue: "Hall A",
           status: "SCHEDULED", phase: "UPCOMING", coaches: ["Coach Lim"], my_attendance: null, ...extra };
}

export const todayDone = session(801, today, "08:00:00", { phase: "COMPLETED", my_attendance: "PRESENT" });
export const todayCancelled = session(802, today, "12:00:00", { status: "CANCELLED", phase: "CANCELLED",
                                                                 class_name: "Morning Sanda" });
export const tomorrow = session(803, addDays(today, 1), "18:00:00");
/** The backend's "upcoming" view also lists a session in progress; it is not the "next" one. */
export const inProgressToday = session(806, today, "17:00:00", { phase: "IN_PROGRESS", my_attendance: "UNMARKED",
                                                                  class_name: "Evening Sanda" });
export const pastUnmarked = session(804, addDays(today, -2), "18:00:00", { phase: "COMPLETED", my_attendance: "UNMARKED" });
export const pastAbsent = session(805, addDays(today, -9), "18:00:00", { phase: "COMPLETED", my_attendance: "ABSENT" });

export const profile: StudentSelfProfile = {
  id: 11, student_no: "A1", full_name: "Aaron Tan", chinese_name: "陈亚伦", gender: "M", age: 12, status: "ACTIVE",
  join_date: "2025-01-06",
  current_classes: [{ class_name: "Junior Taolu", category: "SCHOOL", team_name: null, start_date: "2025-01-06" }],
};

/** The backend's summary: 1 present, 1 absent, 1 not marked → 50.00% (UNMARKED left out). */
export const attendance: StudentAttendanceResponse = {
  summary: { percentage: "50.00", present: 1, late: 0, absent: 1, excused: 0, expected: 3, marked: 2, unmarked: 1 },
  sessions: [
    { session: 801, date: today, start_time: "08:00:00", end_time: "20:00:00", class_name: "Junior Taolu", status: "PRESENT" },
    { session: 804, date: addDays(today, -2), start_time: "18:00:00", end_time: "20:00:00", class_name: "Junior Taolu",
      status: "UNMARKED" },
    { session: 805, date: addDays(today, -9), start_time: "18:00:00", end_time: "20:00:00", class_name: "Junior Taolu",
      status: "ABSENT" },
  ],
};

export const entries: StudentCompetitionEntry[] = [
  { id: 91, status: "PENDING", registered_at: "2026-09-01T09:00:00+08:00",
    competition: { id: 81, name: "State Wushu Open", organiser: "State association", venue: "Stadium Juara",
                   start_date: addDays(today, 30), end_date: addDays(today, 31), status: "OPEN",
                   rules: "Bring your own weapon <b>now</b>." },
    event: { name: "Changquan U12", event_type: "CHANGQUAN", gender: "M", min_age: 8, max_age: 12, weight_class: "" },
    result: null },
  { id: 92, status: "CONFIRMED", registered_at: "2026-05-01T09:00:00+08:00",
    competition: { id: 71, name: "Junior Cup", organiser: "", venue: "Hall B", start_date: addDays(today, -60),
                   end_date: addDays(today, -60), status: "COMPLETED", rules: "" },
    event: { name: "Nanquan", event_type: "NANQUAN", gender: "", min_age: null, max_age: null, weight_class: "" },
    result: { placing: 2, medal: "SILVER", score: "8.950" } },
];

type Req = { url: URL };

export function studentRoutes(overrides: Record<string, unknown> = {}) {
  const all = [todayDone, todayCancelled, tomorrow, pastUnmarked, pastAbsent];
  return {
    "GET /api/me/": json(studentMe),
    "GET /api/students/me/": json(profile),
    "GET /api/students/me/sessions/": ({ url }: Req) => {
      const view = url.searchParams.get("view");
      const rows = view === "today" ? [todayDone, todayCancelled]
        : view === "upcoming" ? [inProgressToday, tomorrow]
        : view === "past" ? [todayDone, pastUnmarked, pastAbsent]
        : view === "cancelled" ? [todayCancelled] : all;
      return json(page(rows));
    },
    "GET /api/students/me/sessions/801/": json(todayDone),
    "GET /api/students/me/sessions/802/": json(todayCancelled),
    "GET /api/students/me/sessions/803/": json(tomorrow),
    "GET /api/students/me/sessions/804/": json(pastUnmarked),
    "GET /api/students/me/attendance/": json(attendance),
    "GET /api/students/me/competitions/": json(entries),
    ...overrides,
  } as unknown as Record<string, never>;
}
