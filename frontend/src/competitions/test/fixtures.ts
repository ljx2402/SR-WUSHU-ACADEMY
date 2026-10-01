import type {
  CompetitionRegistration, CompetitionSummary, StaffCompetition, StaffPaymentProof, StaffRegistrationForm,
} from "../../api/types";
import { json, makeMe } from "../../test/helpers";

export const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });

export const adminMe = makeMe(["ADMIN"], { name: "Front Desk" });
export const financeMe = makeMe(["FINANCE_ADMIN"], { name: "Finance Officer" });

const shirt = { key: "shirt_size", label: "T-shirt size", type: "SINGLE_SELECT" as const, required: true, help_text: "",
                placeholder: "", options: ["S", "M", "L"], max_length: null, min_value: null, max_value: null };
const jersey = { ...shirt, label: "Jersey size" };
const diet = { key: "diet", label: "Dietary needs", type: "LONG_TEXT" as const, required: false, help_text: "Allergies",
               placeholder: "", options: [], max_length: 2000, min_value: null, max_value: null };

export const competition: StaffCompetition = {
  id: 81, name: "State Wushu Open", organiser: "State Association", venue: "Stadium Hall", start_date: "2026-11-14",
  end_date: "2026-11-15", registration_deadline: "2026-10-30", status: "OPEN", allow_parent_registration: true,
  allow_parent_withdrawal: true, max_events_per_student: 2, age_reference_date: null, description: "Annual state open.",
  rules: "Bring your own weapon.", is_open: true,
  events: [
    { id: 811, competition: 81, event_type: "CHANGQUAN", name: "Changquan U12", gender: "OPEN", min_age: null, max_age: 12,
      weight_class: "", fee: "50.00", max_entries: 20 },
    { id: 812, competition: 81, event_type: "NANQUAN", name: "Nanquan Open", gender: "M", min_age: 10, max_age: null,
      weight_class: "", fee: "0.00", max_entries: null },
  ],
  registration_form: { status: "PUBLISHED", version: 2, published_at: "2026-10-01T09:00:00+08:00", fields: [jersey, diet] },
  entry_count: 3, pending_count: 1,
};
export const draftCompetition: StaffCompetition = {
  ...competition, id: 82, name: "Winter Invitational", status: "DRAFT", is_open: false, events: [],
  registration_form: { status: "DRAFT", version: 0, published_at: null, fields: [] }, entry_count: 0, pending_count: 0,
};

export const summary: CompetitionSummary = {
  registrations: { PENDING: 1, CONFIRMED: 2, WITHDRAWN: 1, REJECTED: 0 },
  fees: { paid: 1, awaiting_payment: 1, free: 1 },
  results: { recorded: 1, confirmed_without_result: 1 },
  events: [{ id: 811, name: "Changquan U12", entries: 2, max_entries: 20, confirmed: 1 },
           { id: 812, name: "Nanquan Open", entries: 1, max_entries: null, confirmed: 1 }],
};

export const form: StaffRegistrationForm = {
  status: "PUBLISHED", version: 2, published_at: "2026-10-01T09:00:00+08:00", published_fields: [jersey, diet],
  fields: [
    { id: 5, competition: 81, key: "shirt_size", label: "Jersey size", field_type: "SINGLE_SELECT", required: true,
      help_text: "", placeholder: "", options: ["S", "M", "L", "XL"], max_length: null, min_value: null, max_value: null,
      order: 1, is_active: true },
    { id: 6, competition: 81, key: "diet", label: "Dietary needs", field_type: "LONG_TEXT", required: false,
      help_text: "Allergies", placeholder: "", options: [], max_length: null, min_value: null, max_value: null, order: 2,
      is_active: true },
  ],
  preview: [{ ...jersey, options: ["S", "M", "L", "XL"] }, diet],
  has_unpublished_changes: true,
};

/** Registered with form version 1 ("T-shirt size"); the form is now version 2 ("Jersey size"). */
export const pending: CompetitionRegistration = {
  id: 91, competition: 81, competition_name: "State Wushu Open", event: 811, event_name: "Changquan U12", student: 11,
  student_name: "Aaron Tan", status: "PENDING", registered_at: "2026-10-01T09:00:00+08:00", notes: "",
  fee: "50.00", fee_status: "UNPAID", invoice: { id: 303, number: "INV-2026-0003", balance_due: "50.00" }, result: null,
  form_version: 1, form_responses: [{ key: "shirt_size", label: "T-shirt size", type: "SINGLE_SELECT", value: "M", display: "M" }],
};
export const confirmed: CompetitionRegistration = {
  ...pending, id: 92, student: 12, student_name: "Beth Tan", status: "CONFIRMED", fee_status: "PAID",
  invoice: { id: 304, number: "INV-2026-0004", balance_due: "0.00" }, form_version: 2,
  form_responses: [{ key: "shirt_size", label: "Jersey size", type: "SINGLE_SELECT", value: "S", display: "S" }],
};
export const withResult: CompetitionRegistration = {
  ...confirmed, id: 93, student: 13, student_name: "Chen Lee", event: 812, event_name: "Nanquan Open", fee: null,
  fee_status: null, invoice: null, result: { id: 401, placing: 1, medal: "GOLD", score: "9.100", remarks: "Clean routine" },
};
export const registrations = [pending, confirmed, withResult];

export const proof: StaffPaymentProof = {
  id: 71, family: 6, family_name: "Tan family", invoice: 303, invoice_number: "INV-2026-0003", invoice_status: "ISSUED",
  invoice_balance_due: "50.00", uploaded_by_name: "Parent Tan", uploaded_at: "2026-10-01T10:00:00+08:00",
  original_name: "slip.png", content_type: "image/png", size: 2048, amount_claimed: "50.00", payment_date: "2026-10-01",
  reference: "MBB-1", note: "", status: "PENDING_REVIEW", reviewed_by_name: null, reviewed_at: null, review_note: "",
  payment: null, payment_number: null,
};

type Req = { url: URL; body: Record<string, unknown> };

export function competitionRoutes(overrides: Record<string, unknown> = {}) {
  return {
    "GET /api/me/": json(adminMe),
    "GET /api/competitions/": ({ url }: Req) => {
      const status = url.searchParams.get("status");
      return json(page([competition, draftCompetition].filter((c) => !status || c.status === status)));
    },
    "GET /api/competitions/81/": json(competition),
    "GET /api/competitions/81/summary/": json(summary),
    "GET /api/competitions/81/form/": json(form),
    "POST /api/competitions/81/publish-form/": json({ ...form, version: 3, has_unpublished_changes: false }),
    "POST /api/competitions/81/unpublish-form/": json({ ...form, status: "DRAFT" }),
    "POST /api/competitions/81/reorder-form/": ({ body }: Req) =>
      json({ ...form, fields: (body.keys as string[]).map((k) => form.fields.find((f) => f.key === k)) }),
    "POST /api/competition-form-fields/": ({ body }: Req) => json({ ...form.fields[1], ...body, id: 7, order: 3 }, 201),
    "PATCH /api/competition-form-fields/5/": ({ body }: Req) => json({ ...form.fields[0], ...body }),
    "PATCH /api/competition-form-fields/6/": ({ body }: Req) => json({ ...form.fields[1], ...body }),
    "GET /api/competition-registrations/": ({ url }: Req) => {
      const q = url.searchParams;
      let rows = registrations;
      if (q.get("status")) rows = rows.filter((r) => r.status === q.get("status"));
      if (q.get("event")) rows = rows.filter((r) => String(r.event) === q.get("event"));
      if (q.get("search")) rows = rows.filter((r) => r.student_name.toLowerCase().includes(q.get("search")!.toLowerCase()));
      return json(page(rows));
    },
    "GET /api/competition-registrations/91/": json(pending),
    "GET /api/competition-registrations/92/": json(confirmed),
    "GET /api/competition-registrations/93/": json(withResult),
    "POST /api/competition-registrations/91/reject/": ({ body }: Req) => json({ ...pending, status: "REJECTED", _reason: body.reason }),
    "POST /api/competition-registrations/92/withdraw/": json({ ...confirmed, status: "WITHDRAWN" }),
    "POST /api/competition-registrations/91/confirm/": json(
      { detail: "The competition fee has not been paid; the registration cannot be confirmed." }, 400),
    "GET /api/payment-proofs/": ({ url }: Req) => json(page(url.searchParams.get("invoice") === "303" ? [proof] : [])),
    "POST /api/competition-results/": ({ body }: Req) => json({ id: 402, student_name: "Beth Tan", event_name: "Changquan U12", ...body }, 201),
    "PATCH /api/competition-results/401/": ({ body }: Req) => json({ ...withResult.result, ...body }),
    "POST /api/competitions/": ({ body }: Req) => (body.end_date && body.start_date && String(body.end_date) < String(body.start_date)
      ? json({ end_date: ["End date cannot be before start date."] }, 400)
      : json({ ...draftCompetition, ...body, id: 83 }, 201)),
    "PATCH /api/competitions/81/": ({ body }: Req) => json({ ...competition, ...body }),
    "POST /api/competition-events/": ({ body }: Req) => json({ ...body, id: 813 }, 201),
    ...overrides,
  } as unknown as Record<string, never>;
}
