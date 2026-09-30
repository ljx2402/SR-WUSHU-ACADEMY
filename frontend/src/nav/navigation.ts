import type { Portal } from "../auth/access";

/**
 * The app's sections and pages. Navigation, routes and route guards are all
 * built from this list, so a page can never be linked without being guarded.
 *
 * `capabilities`: any one is enough (the backend capability names).
 * `phase`: the delivery phase that builds the page. Until then the route
 * exists and is protected, but shows "not available yet" (no invented data).
 */
export interface NavItem {
  id: string;
  portal: Portal;
  label: string;
  path: string; // relative to /app
  capabilities: readonly string[];
  phase: string;
  description: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  // Academy staff (SUPER_ADMIN, ADMIN, FINANCE_ADMIN)
  { id: "staff-students", portal: "staff", label: "Students & families", path: "/staff/students",
    capabilities: ["students.view_all", "students.view_directory"], phase: "6E",
    description: "Student records, families, guardians and class membership." },
  { id: "staff-sessions", portal: "staff", label: "Classes & sessions", path: "/staff/sessions",
    capabilities: ["sessions.view_all"], phase: "6E",
    description: "Timetables, sessions, cancellations, rescheduling and substitute coaches." },
  { id: "staff-attendance", portal: "staff", label: "Attendance", path: "/staff/attendance",
    capabilities: ["attendance.view_all"], phase: "6G",
    description: "Attendance sheets, unmarked students and administrator corrections." },
  { id: "staff-competitions", portal: "staff", label: "Competitions", path: "/staff/competitions",
    capabilities: ["competition.registrations.view_all", "competition.manage"], phase: "6H",
    description: "Competitions, events, registrations and results." },
  { id: "staff-finance", portal: "staff", label: "Finance", path: "/staff/finance",
    capabilities: ["finance.view_all"], phase: "6F",
    description: "Charges, family invoices, payments, receipts and refunds." },
  { id: "staff-payroll", portal: "staff", label: "Payroll", path: "/staff/payroll",
    capabilities: ["payroll.view_all"], phase: "6I",
    description: "Coach rates, payroll periods and payslips." },
  { id: "staff-reports", portal: "staff", label: "Reports", path: "/staff/reports",
    capabilities: ["reports.students", "reports.attendance", "reports.competitions", "reports.finance", "reports.payroll"],
    phase: "6J", description: "Reports and exports." },

  // Coach (COACH, including authorized substitute sessions): the Coach Portal (Phase 6C).
  // No finance, family, payroll or form-configuration pages here.
  { id: "coach-dashboard", portal: "coach", label: "Coach dashboard", path: "/coach/dashboard",
    capabilities: ["sessions.view_assigned"], phase: "6C",
    description: "Today's sessions, attendance to finish and the week ahead." },
  { id: "coach-sessions", portal: "coach", label: "My sessions", path: "/coach/sessions",
    capabilities: ["sessions.view_assigned"], phase: "6C",
    description: "Your classes' sessions and the sessions you cover as a substitute." },
  { id: "coach-attendance", portal: "coach", label: "Attendance", path: "/coach/attendance",
    capabilities: ["attendance.take_assigned"], phase: "6C",
    description: "Record attendance from the session start until 48 hours after it ends." },
  { id: "coach-competitions", portal: "coach", label: "Competitions", path: "/coach/competitions",
    capabilities: ["competition.registrations.view_assigned"], phase: "6C",
    description: "Competition entries of the students you coach." },

  // Parent (PARENT): the Parent Portal (Phase 6B)
  { id: "parent-overview", portal: "parent", label: "Overview", path: "/parent/dashboard",
    capabilities: ["students.view_own_children"], phase: "6B",
    description: "Your family at a glance." },
  { id: "parent-family", portal: "parent", label: "My family", path: "/parent/family",
    capabilities: ["students.view_own_children"], phase: "6B",
    description: "Your family's students and their profiles." },
  { id: "parent-schedule", portal: "parent", label: "Schedule", path: "/parent/schedule",
    capabilities: ["sessions.view_own_children"], phase: "6B",
    description: "Training sessions for your children." },
  { id: "parent-attendance", portal: "parent", label: "Attendance", path: "/parent/attendance",
    capabilities: ["attendance.view_own_children"], phase: "6B",
    description: "Your children's attendance." },
  { id: "parent-finance", portal: "parent", label: "Family finance", path: "/parent/finance",
    capabilities: ["finance.view_own_children"], phase: "6B",
    description: "Your family's charges, invoices, payments and receipts." },
  { id: "parent-competitions", portal: "parent", label: "Competitions", path: "/parent/competitions",
    capabilities: ["competition.registrations.view_own_children", "competition.register_own_children"], phase: "6B",
    description: "Competitions and your children's entries." },

  // Student (STUDENT)
  { id: "student-profile", portal: "student", label: "My profile", path: "/student/profile",
    capabilities: ["students.view_self"], phase: "6D", description: "Your student record." },
  { id: "student-timetable", portal: "student", label: "Timetable", path: "/student/timetable",
    capabilities: ["sessions.view_self"], phase: "6D", description: "Your upcoming sessions." },
  { id: "student-attendance", portal: "student", label: "Attendance", path: "/student/attendance",
    capabilities: ["attendance.view_self"], phase: "6D", description: "Your attendance." },
  { id: "student-competitions", portal: "student", label: "Competitions", path: "/student/competitions",
    capabilities: ["competition.registrations.view_self"], phase: "6D",
    description: "Your competition entries and results." },
];

/**
 * Pages inside a section (details, sub-pages). Each is guarded exactly like the
 * section item it belongs to (`parent`), plus its own capabilities if given.
 */
export interface SubRoute {
  path: string;
  parent: string; // NavItem id
  capabilities?: readonly string[];
}

export const SUB_ROUTES: readonly SubRoute[] = [
  { path: "/coach/sessions/:sessionId", parent: "coach-sessions" },
  { path: "/coach/sessions/:sessionId/attendance", parent: "coach-attendance" },
  { path: "/parent/students/:studentId", parent: "parent-family" },
  { path: "/parent/finance/invoices", parent: "parent-finance" },
  { path: "/parent/finance/invoices/:invoiceId", parent: "parent-finance" },
  { path: "/parent/finance/payments", parent: "parent-finance" },
  { path: "/parent/finance/proofs", parent: "parent-finance" },
  { path: "/parent/finance/receipts", parent: "parent-finance" },
  { path: "/parent/finance/receipts/:receiptId", parent: "parent-finance" },
  { path: "/parent/competitions/:competitionId", parent: "parent-competitions" },
  { path: "/parent/competitions/:competitionId/register", parent: "parent-competitions",
    capabilities: ["competition.register_own_children"] },
];

export const PORTAL_ORDER: readonly Portal[] = ["staff", "coach", "parent", "student"];
