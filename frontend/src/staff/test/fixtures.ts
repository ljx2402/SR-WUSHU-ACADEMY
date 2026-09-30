import type {
  CoachingSession, StaffDashboard, StaffStudent, StaffStudentRow, StudentHistory, TrainingClass,
} from "../../api/types";
import { academyToday, addDays } from "../../domain/format";
import { json, makeMe } from "../../test/helpers";

export const today = academyToday();
export const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });

export const adminMe = makeMe(["ADMIN"], { name: "Front Desk" });
export const financeMe = makeMe(["FINANCE_ADMIN"], { name: "Finance Officer" });

export const dashboard: StaffDashboard = {
  date: today,
  counts: { sessions_today: 2, in_progress: 1, cancelled_today: 1, substitute_covered_today: 1, active_students: 42,
            active_classes: 5 },
  alerts: [
    { code: "attendance_open", label: "Attendance not finished (coach window open)", count: 1,
      sessions: [{ id: 701, class_name: "Junior Taolu", date: today, start_time: "17:00:00", end_time: "19:00:00", unmarked: 2 }] },
    { code: "attendance_locked", label: "Attendance incomplete after the 48-hour window (administrator correction)", count: 1,
      sessions: [{ id: 704, class_name: "Junior Taolu", date: addDays(today, -5), start_time: "17:00:00", end_time: "19:00:00", unmarked: 1 }] },
    { code: "session_without_coach", label: "Sessions without a coach (next 14 days)", count: 1,
      sessions: [{ id: 705, class_name: "Senior Sanda", date: addDays(today, 3), start_time: "19:00:00", end_time: "21:00:00" }] },
    { code: "class_without_coach", label: "Active classes without a coach", count: 0, classes: [] },
  ],
};

function session(id: number, date: string, start: string, className: string, extra: Partial<CoachingSession> = {}): CoachingSession {
  return {
    id, training_class: 100, class_name: className, date, start_time: start, end_time: "19:00:00", venue: "Hall A",
    status: "SCHEDULED", phase: "UPCOMING", notes: "Hall key at the desk",
    coaches: [{ id: id * 10, coach: 1, coach_name: "Coach Lim", role: "REGULAR", status: "ASSIGNED", replaces: null }],
    my_role: null,
    attendance: { state: "NOT_STARTED", coach_edit_deadline: "2026-10-03T20:00:00+08:00", expected: 3, marked: 0,
                  unmarked: 3, percentage: null },
    ...extra,
  };
}

export const openSession = session(701, today, "17:00:00", "Junior Taolu", {
  phase: "IN_PROGRESS", attendance: { state: "OPEN", coach_edit_deadline: "2026-10-03T20:00:00+08:00", expected: 3,
                                      marked: 1, unmarked: 2, percentage: "100.00" } });
export const cancelledSession = session(702, today, "08:00:00", "Morning Sanda", { status: "CANCELLED", phase: "CANCELLED",
  attendance: { state: "CANCELLED", coach_edit_deadline: "2026-10-03T20:00:00+08:00", expected: 0, marked: 0, unmarked: 0, percentage: null } });
export const upcomingSession = session(703, addDays(today, 2), "17:00:00", "Junior Taolu");
export const lockedSession = session(704, addDays(today, -5), "17:00:00", "Junior Taolu", { phase: "COMPLETED",
  attendance: { state: "LOCKED", coach_edit_deadline: "2026-09-27T20:00:00+08:00", expected: 3, marked: 2, unmarked: 1, percentage: "50.00" } });
export const completeSession = session(706, addDays(today, -1), "17:00:00", "Senior Sanda", { phase: "COMPLETED",
  attendance: { state: "COMPLETE", coach_edit_deadline: "2026-10-02T20:00:00+08:00", expected: 2, marked: 2, unmarked: 0, percentage: "100.00" } });

export const rows: StaffStudentRow[] = [
  { id: 11, student_no: "A1", full_name: "Aaron Tan", chinese_name: "陈亚伦", gender: "M", age: 12, status: "ACTIVE",
    join_date: "2025-01-06", family: 5, family_name: "Tan family", current_classes: [{ id: 100, name: "Junior Taolu" }] },
  { id: 12, student_no: "A2", full_name: "Beth Tan", chinese_name: "", gender: "F", age: 9, status: "ON_LEAVE",
    join_date: "2025-02-01", family: 5, family_name: "Tan family", current_classes: [] },
];

export const fullStudent: StaffStudent = {
  id: 11, student_no: "A1", full_name: "Aaron Tan", chinese_name: "陈亚伦", gender: "M", date_of_birth: "2014-05-01", age: 12,
  ic_number: "140501-10-1234", nationality: "Malaysian", school: "SJK Test", phone: "", email: "", address: "1 Test Street",
  medical_notes: "Asthma", join_date: "2025-01-06", status: "ACTIVE", family: 5, family_name: "Tan family",
  guardians: [{ id: 1, relationship: "MOTHER", is_primary_contact: true, is_emergency_contact: true,
                parent: { id: 3, full_name: "Mei Tan", ic_number: "800101-10-9999", phone: "0123", alt_phone: "", email: "mei@example.test",
                          address: "", occupation: "", is_active: true } }],
  current_classes: [{ id: 51, training_class: 100, class_name: "Junior Taolu", category: "SCHOOL", team_name: null,
                      coach_name: "Coach Lim", start_date: "2025-01-06", end_date: null }],
};

export const directoryStudent = { id: 11, student_no: "A1", full_name: "Aaron Tan", chinese_name: "陈亚伦", status: "ACTIVE",
  family: 5, family_name: "Tan family", guardians: [{ name: "Mei Tan", relationship: "MOTHER", phone: "0123" }] };

export const history: StudentHistory = {
  status_history: [], enrollments: [],
  changes: [{ id: 9, timestamp: "2026-09-30T10:00:00+08:00", actor_name: "Front Desk", action: "UPDATE",
              object_repr: "Aaron Tan [A1]", changes: {}, reason: "Name spelling" }],
};

export const classes: TrainingClass[] = [
  { id: 100, code: "junior", name: "Junior Taolu", category: "SCHOOL", program: 1, program_name: "Wushu Taolu", team: null,
    team_name: null, venue: "Hall A", capacity: 20, description: "", is_active: true, active_students: 12,
    schedules: [{ id: 1, weekday: 0, weekday_name: "Monday", start_time: "17:00:00", end_time: "19:00:00", venue: "Hall A",
                  effective_from: "2025-01-01", effective_to: null }],
    current_coaches: [{ id: 1, full_name: "Coach Lim" }] },
  { id: 200, code: "sanda", name: "Senior Sanda", category: "ELITE", program: 1, program_name: "Wushu Taolu", team: null,
    team_name: null, venue: "Hall B", capacity: null, description: "", is_active: true, active_students: 0,
    schedules: [{ id: 2, weekday: 2, weekday_name: "Wednesday", start_time: "19:00:00", end_time: "21:00:00", venue: "",
                  effective_from: null, effective_to: null }],
    current_coaches: [] },
  { id: 300, code: "old", name: "Old Class", category: "ADDITIONAL", program: 1, program_name: "Wushu Taolu", team: null,
    team_name: null, venue: "", capacity: null, description: "", is_active: false, active_students: 0, schedules: [],
    current_coaches: [] },
];

export const coaches = [{ id: 1, full_name: "Coach Lim", phone: "", email: "", is_active: true },
                        { id: 2, full_name: "Coach Wong", phone: "", email: "", is_active: true },
                        { id: 3, full_name: "Former Coach", phone: "", email: "", is_active: false }];

export const sheet = (state = "OPEN") => ({
  session: 701, state, coach_edit_deadline: "2026-10-03T20:00:00+08:00",
  summary: { expected: 3, marked: 2, unmarked: 1, present: 1, late: 0, absent: 1, excused: 0, percentage: "50.00" },
  sheet: [{ student: 11, student_name: "Aaron Tan", status: "PRESENT", remarks: "" },
          { student: 12, student_name: "Beth Tan", status: "ABSENT", remarks: "Sick" },
          { student: 13, student_name: "Chen Tan", status: "UNMARKED", remarks: "" }],
  records: [{ id: 501, session: 701, session_date: today, class_name: "Junior Taolu", student: 11, student_name: "Aaron Tan",
              status: "PRESENT", remarks: "", recorded_by_name: "Coach Lim" }],
});

type Req = { url: URL };

export function staffRoutes(overrides: Record<string, unknown> = {}) {
  const all = [cancelledSession, openSession, upcomingSession, lockedSession, completeSession];
  return {
    "GET /api/me/": json(adminMe),
    "GET /api/staff/dashboard/": json(dashboard),
    "GET /api/sessions/coaching/": ({ url }: Req) => {
      const q = url.searchParams;
      let list = all;
      if (q.get("status")) list = list.filter((s) => s.status === q.get("status"));
      if (q.get("date")) list = list.filter((s) => s.date === q.get("date"));
      if (q.get("start")) list = list.filter((s) => s.date >= q.get("start")!);
      if (q.get("end")) list = list.filter((s) => s.date <= q.get("end")!);
      if (q.get("class")) list = list.filter((s) => String(s.training_class) === q.get("class"));
      return json(page(list));
    },
    "GET /api/students/": ({ url }: Req) => {
      const search = url.searchParams.get("search")?.toLowerCase();
      return json(page(search ? rows.filter((r) => r.full_name.toLowerCase().includes(search)) : rows));
    },
    "GET /api/students/11/": json(fullStudent),
    "GET /api/students/11/history/": json(history),
    "GET /api/classes/": json(page(classes)),
    "GET /api/classes/100/": json(classes[0]),
    "GET /api/classes/100/students/": json([{ id: 11, student_no: "A1", full_name: "Aaron Tan", chinese_name: "", gender: "M",
                                              age: 12, status: "ACTIVE" }]),
    "GET /api/programs/": json([{ id: 1, code: "taolu", name: "Wushu Taolu", is_active: true }]),
    "GET /api/coaches/": json(page(coaches)),
    "GET /api/sessions/701/": json(openSession),
    "GET /api/sessions/701/attendance/": json(sheet()),
    "GET /api/sessions/703/": json(upcomingSession),
    "GET /api/sessions/703/attendance/": json({ ...sheet("NOT_STARTED"), summary: { ...sheet().summary, marked: 0, unmarked: 3,
      present: 0, absent: 0, percentage: null }, sheet: sheet().sheet.map((r) => ({ ...r, status: "UNMARKED", remarks: "" })), records: [] }),
    "GET /api/sessions/704/": json(lockedSession),
    "GET /api/sessions/704/attendance/": json(sheet("LOCKED")),
    "GET /api/attendance/501/history/": json([{ id: 1, timestamp: "2026-09-26T19:00:00+08:00", actor_name: "Coach Lim",
      action: "CREATE", object_repr: "Aaron Tan", changes: { status: { from: null, to: "PRESENT" } }, reason: "" }]),
    ...overrides,
  } as unknown as Record<string, never>;
}
