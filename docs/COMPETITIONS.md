# Competitions (Phase 1 flow, Phase 4 integrity, registration forms)

```
Selection → Registration → competition invoice issued → payment required → full payment → CONFIRMED → result
```

## Registration status

| Status | Meaning |
|---|---|
| `PENDING` ("Awaiting payment") | Registered; the competition invoice (due today) is not fully paid |
| `CONFIRMED` | Fee fully paid (or a free event). Set automatically by the payment; staff "confirm" also refuses an unpaid registration |
| `WITHDRAWN` / `REJECTED` | Final. Unpaid: its invoice is voided and the charge cancelled. Paid: **no automatic refund** |

* A partial payment does not confirm.
* A voided payment returns the registration to `PENDING`.
* A withdrawn or rejected registration cannot be confirmed.
* An exceptional refund (ADMIN, FINANCE_ADMIN, SUPER_ADMIN) needs a reason and is audited. It is a
  separate `Refund`; the original payment and receipt are untouched.
* Registrations cannot be deleted.
* In the admin, a registration's student, event, status and charge are read-only.

## Results (Phase 4)

A result can be recorded **only for a `CONFIRMED` (paid) registration**. This holds everywhere:

| Path | Enforcement |
|---|---|
| Service | `competitions.services.record_result` (`competition.results.manage`: ADMIN, SUPER_ADMIN), under a lock on the registration |
| Model | `CompetitionResult.save()` refuses an unconfirmed registration and moving a result to another registration |
| Bulk | `update()`, `bulk_create()`, `bulk_update()`, `delete()` on results are refused |
| API | `POST /api/competition-results/` and `PATCH` go through the service; a second result for the same registration and moving a result are refused; no delete |
| Admin | The result form validates the registration. A registration's page shows the result form only when it is confirmed. There is no delete and no bulk action |
| PostgreSQL | Trigger `sr_competitionresult_guard` refuses inserting or updating a result of an unconfirmed registration, moving it, or deleting it, even from raw SQL |

* A registration that has a result cannot be withdrawn.
* A result entry and a withdrawal racing each other: exactly one wins (tested concurrently).
* Existing results recorded before Phase 4 are kept unchanged.
* The `results` report shows each result's registration status. The `competitions` report shows
  status, fee and fee status.

## Registration forms (per competition)

Every competition has its own registration form. Parents register their own children by
answering that competition's form; staff never have to key in every entry.

```
Competition
├── basic information, rules (text shown to parents), withdrawal rule
├── events: each with its own fee, gender, age range, weight class, capacity
└── registration form
    ├── system fields: student, event (and competition, fee, status, payment, invoice, dates)
    └── custom fields: configured by staff for this competition only
```

### System fields vs custom fields

System fields belong to the application and are not form fields: `student`, `event`,
`competition`, `status`, `fee`, `fee_status`/payment, `invoice`, `charge`, `registered_at`,
`registered_by`, `notes`, `result`, `form_version`, `form_responses`. Their keys are reserved:
the form builder refuses them as custom field keys.

Custom fields (`RegistrationFormField`) have: label, key (`^[a-z][a-z0-9_]{0,39}$`, fixed once
created because answers are stored under it), type, required, help text, placeholder, display
order, options (choice fields), maximum length (text), minimum/maximum (number), active flag.

| Type | Answer | Server checks |
| --- | --- | --- |
| `TEXT` | text | ≤ max length (default 200); control characters removed |
| `LONG_TEXT` | text | ≤ max length (default 2000) |
| `NUMBER` | decimal | a finite number within min/max |
| `DATE` | `YYYY-MM-DD` | a valid date |
| `SINGLE_SELECT` | one option | must be one of the options |
| `MULTI_SELECT` | list of options | subset of the options (stored in form order, no duplicates) |
| `YES_NO` | true / false | a boolean |
| `EMAIL` | text | a valid email address |
| `PHONE` | text | digits, spaces, `+ ( ) -`, 6–20 characters |

No arbitrary code, HTML or file fields. At most 30 custom fields and 50 options per field.
**Sensitive data guard:** keys, labels, help texts and options that ask for passwords,
passcodes, PINs, OTPs, tokens, API keys, secrets, card numbers, CVV or bank logins are refused.
Health or emergency-contact questions are allowed, and their answers follow the registration's
access rules (below).

### Draft / published lifecycle and versions

* Staff edit a **working copy** (the field rows). Nothing they change reaches parents until
  they **publish**.
* **Publish** freezes the active fields into `Competition.published_form` and increments
  `form_version` (only if something changed). Parents only ever see and submit the published
  version.
* **Unpublish** sets the form to `DRAFT`: parents cannot register until it is published again.
* A new competition starts with the empty form (system fields only) published as version 1,
  which is the behaviour competitions had before forms existed. Staff add custom fields and
  publish before (or while) the competition is open.
* Parents can only submit while the competition is open for registration (status OPEN and
  before the deadline), parent registration is allowed, and the form is published.

### Historical answers

A registration stores `form_version` and `form_responses`: for every question of the version
it answered, `{key, label, type, value, display}`. Later changes (a field removed, renamed,
retyped, options changed, a new version published) never touch stored answers, which the
model refuses to rewrite. Past registrations are always read from their own snapshot.

### Validation (the backend is the authority)

`POST /api/competition-registrations/` `{student, event, competition?, notes?, responses}`:

1. The student must be the parent's own child (otherwise 403, before anything else).
2. The event must exist; if `competition` is sent it must be the event's competition (400).
3. The competition must be open, allow parent registration, and have a published form.
4. `responses` must be an object; unknown keys are refused; required questions must be
   answered (parents); every value is checked by type, options, length and range. Errors are
   returned per question as `responses.<key>`.
5. Eligibility (gender, age on the competition's age date), duplicates, event capacity and the
   per-child event limit are checked as before.
6. The entry is created PENDING ("Awaiting payment") with a charge for **that event's fee**
   and an issued competition invoice (a free event is confirmed at once).

Staff registering on a family's behalf (Django admin or API with
`competition.registrations.manage`) may leave questions they cannot answer empty; given
answers are still validated.

### Payment and withdrawal

Payment is unchanged: the parent pays manually and uploads payment proof
(`docs/PAYMENT_PROOFS.md`); staff record the payment; full payment confirms the entry, a
partial payment or none leaves it PENDING. Each competition decides whether parents may
withdraw (`allow_parent_withdrawal`, while registration is open); otherwise withdrawal is an
academy action. Withdrawing never refunds automatically: an unpaid invoice is voided, a paid fee
stays paid, and exceptional refunds remain a finance action.

### Who sees and changes what

| | Parent (own children) | Coach (own athletes) | Staff with `competition.registrations.view_all` | `competition.manage` (ADMIN, SUPER_ADMIN) |
| --- | --- | --- | --- | --- |
| Published form | read | read | read | read |
| Working copy, preview, publish, reorder, unpublish | – | – | – | yes |
| Submit a registration with answers | own children only | – | – | on anyone's behalf (`registrations.manage`) |
| Read answers (`form_responses`) | own children | **no** (entry only) | yes | yes |

Another family's registration is 404 for a parent; registrations cannot be edited through the
API (no update route), and answers cannot be rewritten at all.

### API and admin

* `GET /api/competitions/:id/` includes `registration_form` = `{status, version,
  published_at, fields}` (published version only), `rules` and `allow_parent_withdrawal`.
* `/api/competition-form-fields/?competition=` (list, create, update, delete) — working copy.
* `GET /api/competitions/:id/form/` — working copy, preview, published version,
  `has_unpublished_changes`.
* `POST /api/competitions/:id/publish-form/`, `/unpublish-form/`, `/reorder-form/ {keys}`.
* Django admin → Competitions → a competition: "Registration form" section (add, edit,
  reorder, deactivate or delete custom fields), a read-only preview of the working copy and of
  the published form, and the actions **Publish registration form** /
  **Unpublish registration form**. A registration's admin page shows its submitted answers
  (escaped). The full staff competition screens come in a later phase.

Every change (fields, publish, unpublish, reorder, registrations) is audited.

Tests: `apps/api/tests/test_registration_forms.py` (16 tests).
