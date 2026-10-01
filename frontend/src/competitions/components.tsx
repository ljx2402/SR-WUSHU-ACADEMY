import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router";

import type { CompetitionEvent, FormFieldType } from "../api/types";
import { isApiError } from "../api/errors";
import { useServices } from "../app/services";
import { can } from "../auth/access";
import { useMe } from "../auth/AuthProvider";

/*
 * Competition Staff Portal building blocks (Phase 6G). The backend decides
 * every permission, eligibility, payment and result rule; these components
 * show what it returns and send changes to its existing endpoints.
 */

export const competitionKeys = {
  all: ["competitions-staff"] as const,
  list: (query: object) => ["competitions-staff", "list", query] as const,
  detail: (id: string) => ["competitions-staff", "detail", id] as const,
  summary: (id: string) => ["competitions-staff", "summary", id] as const,
  form: (id: string) => ["competitions-staff", "form", id] as const,
  registrations: (query: object) => ["competitions-staff", "registrations", query] as const,
  registration: (id: string) => ["competitions-staff", "registration", id] as const,
  proofs: (invoice: number) => ["competitions-staff", "proofs", invoice] as const,
};

export const COMP_ROOT = "/staff/competitions";
export const COMP_CRUMBS = [{ label: "Competitions", to: COMP_ROOT }];

/** The backend's choices (apps/competitions/models.py), labelled as the admin labels them. */
export const EVENT_TYPE: Record<string, string> = {
  CHANGQUAN: "Changquan", NANQUAN: "Nanquan", TAIJIQUAN: "Taijiquan", JIANSHU: "Jianshu", DAOSHU: "Daoshu",
  GUNSHU: "Gunshu", QIANGSHU: "Qiangshu", NANDAO: "Nandao", NANGUN: "Nangun", TAIJIJIAN: "Taijijian",
  SANDA: "Sanda", DUILIAN: "Duilian", GROUP: "Group set", OTHER: "Other",
};
export const GENDER: Record<string, string> = { M: "Male", F: "Female", OPEN: "Open / mixed" };
export const MEDAL: Record<string, string> = { GOLD: "Gold", SILVER: "Silver", BRONZE: "Bronze", NONE: "No medal" };
export const FIELD_TYPE: Record<FormFieldType, string> = {
  TEXT: "Short text", LONG_TEXT: "Long text", NUMBER: "Number", DATE: "Date", SINGLE_SELECT: "Single choice",
  MULTI_SELECT: "Multiple choice", YES_NO: "Yes / No", EMAIL: "Email", PHONE: "Phone",
};
/** ?payment= on /api/competition-registrations/ (the charge's state, from finance). */
export const PAYMENT_FILTER: Record<string, string> = { PAID: "Paid", UNPAID: "Awaiting payment", FREE: "Free entry" };

export function ageText(event: CompetitionEvent) {
  if (event.min_age !== null && event.max_age !== null) return `Ages ${event.min_age}–${event.max_age}`;
  if (event.min_age !== null) return `Age ${event.min_age}+`;
  if (event.max_age !== null) return `Up to age ${event.max_age}`;
  return "All ages";
}

/** One competition, as the staff portal reads it. */
export function useStaffCompetition(id: string) {
  const { endpoints } = useServices();
  return useQuery({ queryKey: competitionKeys.detail(id), queryFn: ({ signal }) => endpoints.staffCompetition(id, signal) });
}

/** Sub-navigation inside one competition; each link only for the capability its page needs. */
export function CompetitionNav({ id }: { id: number | string }) {
  const me = useMe();
  const base = `${COMP_ROOT}/${id}`;
  const links = [
    { to: base, label: "Overview", end: true, show: true },
    { to: `${base}/participants`, label: "Participants", show: can(me, "competition.registrations.view_all") },
    { to: `${base}/registration-form`, label: "Registration form", show: can(me, "competition.manage") },
    { to: `${base}/results`, label: "Results", show: can(me, "competition.registrations.view_all") },
  ].filter((link) => link.show);
  return (
    <nav aria-label="Competition" className="subnav no-print">
      <ul>
        {links.map((link) => <li key={link.to}><NavLink to={link.to} end={link.end}>{link.label}</NavLink></li>)}
      </ul>
    </nav>
  );
}

/** Field errors of a refused change, by field name (empty when the error is not the backend's). */
export function fieldErrorsOf(error: unknown): Record<string, string[]> {
  return isApiError(error) ? error.fieldErrors ?? {} : {};
}

/** "" → null, otherwise a whole number (the form controls hold text). */
export function intOrNull(value: string): number | null {
  const text = value.trim();
  return text === "" ? null : Number(text);
}
