/**
 * Shapes of the Django REST API responses used by the app.
 * Money and percentages arrive as decimal strings ("1234.50"): never parse
 * them to floats for arithmetic.
 */

export type Role = "SUPER_ADMIN" | "ADMIN" | "FINANCE_ADMIN" | "COACH" | "PARENT" | "STUDENT";

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface PersonRef {
  id: number;
  student_no?: string;
  full_name: string;
}

/** GET /api/me/ */
export interface Me {
  id: number;
  username: string;
  name: string;
  role: Role | null;
  roles: Role[];
  capabilities: string[];
  parent?: { id: number; full_name: string; phone?: string; email?: string };
  children?: PersonRef[];
  coach?: { id: number; full_name: string };
  classes?: { id: number; name: string }[];
  substitute_sessions?: { session: number; access_ends_at: string | null }[];
  student?: PersonRef;
}

export interface TokenResponse {
  token: string;
}

export type SessionPhase = "UPCOMING" | "IN_PROGRESS" | "COMPLETED" | "CANCELLED";

export interface SessionCoachSlot {
  id: number;
  coach: number;
  coach_name: string;
  role: "REGULAR" | "SUBSTITUTE";
  status: string;
  replaces: number | null;
}

/** GET /api/sessions/ */
export interface TrainingSession {
  id: number;
  training_class: number;
  class_name: string;
  date: string; // YYYY-MM-DD (academy time zone)
  start_time: string; // HH:MM:SS
  end_time: string;
  venue: string;
  status: "SCHEDULED" | "CANCELLED";
  phase: SessionPhase;
  notes: string;
  coaches: SessionCoachSlot[];
}

export interface PayrollRunSummary {
  id: number;
  year: number;
  month: number;
  period_start: string;
  period_end: string;
  status: "DRAFT" | "READY" | "FINALIZED";
  issue_count: number;
}
