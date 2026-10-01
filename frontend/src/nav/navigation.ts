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
  // Academy staff (SUPER_ADMIN, ADMIN, FINANCE_ADMIN). Core operations (Phase 6E): each page
  // needs its own existing capability, so FINANCE_ADMIN (students.view_directory only) sees the
  // student directory and nothing of classes, sessions or attendance.
  { id: "staff-dashboard", portal: "staff", label: "Operations", path: "/staff/dashboard",
    capabilities: ["sessions.view_all"], phase: "6E",
    description: "Today's sessions, attendance to finish and operational alerts." },
  { id: "staff-students", portal: "staff", label: "Students", path: "/staff/students",
    capabilities: ["students.view_all", "students.view_directory"], phase: "6E",
    description: "Student records, families, guardians and class membership." },
  { id: "staff-classes", portal: "staff", label: "Classes", path: "/staff/classes",
    capabilities: ["classes.view_all"], phase: "6E", description: "Classes, coaches, timetable and rosters." },
  { id: "staff-timetable", portal: "staff", label: "Timetable", path: "/staff/timetable",
    capabilities: ["classes.view_all"], phase: "6E", description: "The weekly academy timetable." },
  { id: "staff-sessions", portal: "staff", label: "Sessions", path: "/staff/sessions",
    capabilities: ["sessions.view_all"], phase: "6E",
    description: "Sessions, cancellations, rescheduling, coaches and substitutes." },
  { id: "staff-attendance", portal: "staff", label: "Attendance", path: "/staff/attendance",
    capabilities: ["attendance.view_all"], phase: "6E",
    description: "Attendance monitoring, unmarked students and administrator corrections." },
  // Competition administration (Phase 6G): ADMIN and SUPER_ADMIN. FINANCE_ADMIN holds only
  // competition.view, so it sees competition fees through finance, not here.
  { id: "staff-competitions", portal: "staff", label: "Competitions", path: "/staff/competitions",
    capabilities: ["competition.registrations.view_all", "competition.manage"], phase: "6G",
    description: "Competitions, events, registration forms, participants and results." },
  { id: "staff-payroll", portal: "staff", label: "Payroll", path: "/staff/payroll",
    capabilities: ["payroll.view_all"], phase: "6I",
    description: "Coach rates, payroll periods and payslips." },
  { id: "staff-reports", portal: "staff", label: "Reports", path: "/staff/reports",
    capabilities: ["reports.students", "reports.attendance", "reports.competitions", "reports.finance", "reports.payroll"],
    phase: "6J", description: "Reports and exports." },

  // Finance staff (Phase 6F): capability-driven. ADMIN, FINANCE_ADMIN and SUPER_ADMIN see the
  // pages their finance capabilities allow; the backend refuses everything else (403).
  { id: "finance-dashboard", portal: "finance", label: "Finance dashboard", path: "/finance/dashboard",
    capabilities: ["finance.view_all"], phase: "6F", description: "Outstanding invoices, payments, receipts and proofs." },
  { id: "finance-invoices", portal: "finance", label: "Invoices", path: "/finance/invoices",
    capabilities: ["finance.view_all"], phase: "6F", description: "Family invoices." },
  { id: "finance-payments", portal: "finance", label: "Payments", path: "/finance/payments",
    capabilities: ["finance.view_all"], phase: "6F", description: "Payments received and exceptional refunds." },
  { id: "finance-proofs", portal: "finance", label: "Payment proofs", path: "/finance/payment-proofs",
    capabilities: ["finance.proofs.review"], phase: "6F", description: "Review parents' payment proofs." },
  { id: "finance-receipts", portal: "finance", label: "Receipts", path: "/finance/receipts",
    capabilities: ["finance.view_all"], phase: "6F", description: "Official receipts." },
  { id: "finance-payment-info", portal: "finance", label: "Payment information", path: "/finance/payment-info",
    capabilities: ["finance.payment_info.manage", "finance.view_all"], phase: "6F",
    description: "The academy's bank details, instructions and QR code." },

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

  // Student (STUDENT): the Student Portal (Phase 6D). Read only, own record only:
  // no finance, family, registration, attendance-taking or coach pages here.
  { id: "student-dashboard", portal: "student", label: "My dashboard", path: "/student/dashboard",
    capabilities: ["sessions.view_self"], phase: "6D", description: "Today, your next session, attendance and competitions." },
  { id: "student-schedule", portal: "student", label: "My schedule", path: "/student/schedule",
    capabilities: ["sessions.view_self"], phase: "6D", description: "Your training sessions." },
  { id: "student-attendance", portal: "student", label: "My attendance", path: "/student/attendance",
    capabilities: ["attendance.view_self"], phase: "6D", description: "Your attendance." },
  { id: "student-competitions", portal: "student", label: "My competitions", path: "/student/competitions",
    capabilities: ["competition.registrations.view_self"], phase: "6D",
    description: "Your competition entries and results." },
  { id: "student-profile", portal: "student", label: "My profile", path: "/student/profile",
    capabilities: ["students.view_self"], phase: "6D", description: "Your student record." },
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
  { path: "/staff/students/:studentId", parent: "staff-students" },
  { path: "/staff/competitions/new", parent: "staff-competitions", capabilities: ["competition.manage"] },
  { path: "/staff/competitions/:competitionId", parent: "staff-competitions" },
  { path: "/staff/competitions/:competitionId/edit", parent: "staff-competitions", capabilities: ["competition.manage"] },
  { path: "/staff/competitions/:competitionId/registration-form", parent: "staff-competitions",
    capabilities: ["competition.manage"] },
  { path: "/staff/competitions/:competitionId/participants", parent: "staff-competitions",
    capabilities: ["competition.registrations.view_all"] },
  { path: "/staff/competitions/:competitionId/participants/:registrationId", parent: "staff-competitions",
    capabilities: ["competition.registrations.view_all"] },
  { path: "/staff/competitions/:competitionId/results", parent: "staff-competitions",
    capabilities: ["competition.registrations.view_all"] },
  { path: "/finance/invoices/:invoiceId", parent: "finance-invoices" },
  { path: "/finance/payments/new", parent: "finance-payments", capabilities: ["finance.payments.record"] },
  { path: "/finance/payments/:paymentId", parent: "finance-payments" },
  { path: "/finance/payment-proofs/:proofId", parent: "finance-proofs" },
  { path: "/finance/receipts/:receiptId", parent: "finance-receipts" },
  { path: "/staff/classes/:classId", parent: "staff-classes" },
  { path: "/staff/sessions/:sessionId", parent: "staff-sessions" },
  { path: "/staff/sessions/:sessionId/attendance", parent: "staff-attendance" },
  { path: "/student/sessions/:sessionId", parent: "student-schedule" },
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

export const PORTAL_ORDER: readonly Portal[] = ["staff", "finance", "coach", "parent", "student"];
