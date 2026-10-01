# Coach payroll (Phase 4)

**All coach fees are per session (optionally per class).** There is no monthly salary and no
hourly pay. Regular and substitute rates may differ.

## Rates (`CoachRate`)

| Type | Used for |
|---|---|
| `PER_SESSION` (class-specific or general) | Regular sessions, and substitute sessions when no substitute rate is set |
| `SUBSTITUTE_PER_SESSION` (class-specific or general) | Substitute sessions |
| `HOURLY`, `MONTHLY`, `SUBSTITUTE_HOURLY` | **Legacy, never used.** Existing rows are kept as history; new ones cannot be created |

* Two rates of the same type for the same coach and class cannot overlap in time, so the rate for
  every session is unambiguous.
* A rate used by a finalized payroll cannot be rewritten; end it (`effective_to`) and add a new one.

**Resolution** (`payroll.services.resolve_rate`), recorded on every line as `rule` plus the rate
row (`rate_source`):

| Assignment | Order |
|---|---|
| Regular | `CLASS_REGULAR` → `GENERAL_REGULAR` |
| Substitute | `CLASS_SUBSTITUTE` → `GENERAL_SUBSTITUTE` → `SUBSTITUTE_USES_CLASS_REGULAR` → `SUBSTITUTE_USES_GENERAL_REGULAR` |
| Nothing found | `MISSING_RATE`: the line is flagged (amount 0, `issue = MISSING_RATE`), the run stays `DRAFT` and **cannot be finalized** until a rate is added and the payroll recalculated |

## What is paid

The payroll period is a calendar month (`PayrollRun.year/month`, `period_start`–`period_end`).
A coach assignment (`SessionCoach`) in the period is paid when:
* it is `ASSIGNED`: a regular coach who was not replaced, or an authorized substitute whose
  authorization was not revoked or cancelled. **The authorization is the only source of truth**;
  attendance records never create pay;
* the session is not cancelled and has **ended**;
* it was not already paid in another run (database constraint `assignment_paid_once`).

A session with two regular coaches (head and assistant) pays both. A replaced coach is not paid;
their substitute is. So one session never pays two coaches for the same role.

Everything else in the period is listed in `PayrollRun.excluded` with its reason:
* replaced by an authorized substitute;
* substitute authorization revoked or cancelled;
* session cancelled;
* session not completed yet;
* already paid.

Each line keeps:
* the coach;
* the session;
* the assignment (`slot`): regular or substitute, and for a substitute the original coach;
* the rate row and rule used;
* the amount.

Allowances, bonuses and deductions (`PayrollAdjustment`) are added per coach and month.

## Lifecycle

```
DRAFT ──calculate──▶ READY ──finalize──▶ FINALIZED (final)
  ▲                    │
  └── calculated with unresolved issues stays DRAFT; recalculating is always allowed before finalizing
```

| Step | Who | Rules |
|---|---|---|
| Calculate / recalculate | FINANCE_ADMIN, SUPER_ADMIN (`payroll.prepare`) | Replaces the draft's payslips; never touches a finalized run. Audited with line, exclusion and issue counts |
| Finalize | **SUPER_ADMIN only** (`payroll.finalize`) | The run must be `READY` (no issues). The period must have ended. Nothing may have changed since the calculation: sessions, substitutes, rates and adjustments are re-checked, and any difference means "recalculate". Audited |

* Calculation and finalization lock the run row, so simultaneous calculations run one after the
  other, and two finalizations finalize once. Both are tested with concurrent PostgreSQL
  transactions.
* A finalized run, its payslips and lines can never change, except the run's notes. This is
  enforced by model and queryset guards and, on PostgreSQL, by triggers. Its adjustments, sessions
  and paid substitutions are frozen too.
* There is no payroll reversal and no payment-status tracking (not part of the existing design).

## Where

* **Admin** (Payroll section):
  * Coach rates: per-session types only.
  * Adjustments.
  * Payroll runs: "Calculate", and "Finalize" for super admin only. The "Not paid (and why)" list
    is shown.
  * Payslips: read-only, with lines, rule and issue.
* **API:**
  * `GET /api/payroll-runs/`;
  * `POST /api/payroll-runs/calculate/` with `{year, month}`;
  * `POST /api/payroll-runs/{id}/finalize/`;
  * `GET /api/payslips/` (coaches: own finalized payslips only), with `?run=` and `?coach=`
    filters (Phase 6H). The filters only narrow the caller's scope (a coach's `?coach=` for
    another coach returns nothing), and a non-numeric id is 400.
* **Web app (Phase 6H):**
  * `/app/finance/payroll` (`payroll.view_all`: FINANCE_ADMIN, SUPER_ADMIN). It lists the
    periods and has "Calculate a month" (`payroll.prepare`).
  * The period page shows status, issues, payslips per coach and the "Not paid in this period"
    list with the backend's reasons. It has Recalculate (`payroll.prepare`) and Finalize. Finalize
    (`payroll.finalize`, SUPER_ADMIN only) appears only for a READY period and asks for
    confirmation that the action is permanent.
  * The coach payslip page lists every line with kind, original coach, the rate rule used, issue
    and amount.
  * Coaches get "My payslips" (`/app/coach/payslips`, `payroll.view_own`): their own finalized
    payslips only.
  * Every amount, count, rule and refusal is the backend's; the browser calculates nothing.
  * Rates, adjustments, run notes, deleting a draft, payout recording, regular-coach no-shows,
    payslip PDFs and reversal stay out of the web app. Rates and adjustments are edited in
    Django Admin.
* **Reports:** `payroll` (per payslip, including bank details for payroll staff) and
  `payroll_lines` (every paid session: coach, role, original coach, date, class, rate, rule, amount,
  issue). Both need `reports.payroll`.

Bank details are visible only with `coaches.bank_details` (FINANCE_ADMIN, SUPER_ADMIN), never to
ADMIN, coaches, parents or students, and never in payslips.
