# Web app (frontend)

The web app in `frontend/` is the user interface for staff, finance, coaches, parents and
students. It is a single-page app that talks only to the existing Django REST API. **The
backend is the source of truth for every permission and business rule**: the app decides what
to *show*, the API decides what is *allowed*.

Phase 6A delivered the foundation: sign-in, the application shell, navigation, the design
system, the API client, route protection and a dashboard built from real API data. Phase 6B
added the **Parent Portal**, Phase 6C the **Coach Portal**, Phase 6D the **Student
Portal** and Phase 6E the **Staff Core Operations Portal** (see below). The remaining staff
pages (competitions, finance, payroll, reports) are built in later phases (6F–6J); until then each of their pages exists, is protected, and says "This page is not
available yet" (no invented data).

## Technology (and why)

| Concern | Choice | Why |
| --- | --- | --- |
| Framework | React 19 | Widely known, stable, good accessibility tooling. |
| Language | TypeScript (strict) | API shapes are typed (`src/api/types.ts`); mistakes surface at build time. |
| Build / dev server | Vite | Fast, minimal configuration, dev proxy to Django. |
| Routing | React Router (data router) | Nested routes with guard components; `basename` `/app`. |
| Server state | TanStack Query | Caching, loading/error states and refetching without a hand-written store. |
| Client state | React state + context | Only the sign-in state is global (`AuthProvider`); nothing else needs a store. |
| API client | One `fetch` wrapper (`src/api/client.ts`) | No extra library; one place for auth, errors and timeouts. |
| Forms / validation | Plain controlled inputs + `TextField` | Required-field checks in the browser; the API's field errors shown at each field. A form library can be added in 6E/6F if forms grow. |
| Styling | Plain CSS with design tokens (`src/styles/`) | No framework to learn or upgrade; CSP-friendly (no runtime style injection). |
| Tests | Vitest + Testing Library + jsdom | Tests run against the real components and routes with a fake `fetch`. |

All dependency versions are pinned exactly in `package.json` and locked in `package-lock.json`
(`npm audit`: 0 known vulnerabilities at the time of writing).

## Layout

```
frontend/
  index.html               entry page (no inline scripts)
  vite.config.ts           base /app/, dev proxy /api -> Django, test settings
  .env.example             frontend settings (all public)
  src/
    main.tsx               creates the browser router and renders the app
    app/App.tsx            routes (built from nav/navigation.ts), providers, query client
    app/services.tsx       API client + token store, provided by context (swapped in tests)
    api/client.ts          the only code that calls fetch
    api/errors.ts          ApiError and the standard user messages
    api/endpoints.ts       typed endpoint functions
    api/types.ts           response shapes (Me, TrainingSession, …)
    auth/AuthProvider.tsx  sign-in state, /api/me/, sign-out, 401 handling
    auth/guards.tsx        RequireAuth, RequireAccess
    auth/access.ts         roles -> portals, capability checks (UX only)
    auth/tokenStore.ts     where the token is kept (sessionStorage)
    nav/navigation.ts      every section and page: path, portal, capabilities, phase
    layout/                AppShell (top bar, sidebar, phone drawer, user menu), NavMenu, PageHeader
    ui/                    Button, TextField, Card, Badge, Alert, KpiCard, DataTable, Dialog,
                           ConfirmDialog, Loading/Empty/Error/AccessDenied/NotFound states
    domain/                money, dates, finance and attendance badges and helpers
    pages/                 Login, Dashboard, ModulePlaceholder, NotFound
    styles/                tokens.css, base.css, layout.css, components.css
    test/                  test setup and helpers (fake fetch, users per role)
```

## Local development

Requirements: Node.js 20 or newer (CI uses 22), and the Django backend running locally
(see the README quick start).

```bash
# Terminal 1: the API
python manage.py runserver            # http://127.0.0.1:8000

# Terminal 2: the web app
cd frontend
cp .env.example .env.local            # optional; the defaults work
npm ci
npm run dev                           # http://localhost:5173/app/
```

The Vite dev server proxies `/api/…` to Django, so the browser only ever talks to one origin
(no CORS configuration, just like production). Sign in with any active Django user that has
a role (create one in the Django admin, `/admin/`).

## Commands

| Command | What it does |
| --- | --- |
| `npm run dev` | Development server with hot reload on port 5173. |
| `npm run typecheck` | TypeScript check, no output files. |
| `npm test` | Runs all frontend tests once (`npm run test:watch` to keep watching). |
| `npm run build` | Type check, then production build into `frontend/dist/`. |
| `npm run preview` | Serves the production build on port 4173 (also proxies `/api`). |

## Environment variables

`frontend/.env.example` lists them. **Every `VITE_*` value is compiled into the public
JavaScript**: never put a secret, key or password in them. The app has no client-side secrets.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VITE_API_BASE_URL` | empty (same origin) | Where the API lives. Keep it empty in production: serve `/app/` and `/api/` from the same host. A different origin would need CORS and a CSP `connect-src` change on the backend. |
| `VITE_API_TIMEOUT_MS` | `15000` | Requests taking longer fail with "The server took too long to respond." |
| `DEV_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Dev/preview only: where `/api` is proxied. Not included in the bundle. |

## API client

`src/api/client.ts` is the only code that calls `fetch`. It:

* builds URLs from the base URL and query parameters (empty values are dropped);
* sends `Accept: application/json`, JSON bodies, and `Authorization: Token <token>` when signed
  in; the sign-in request itself is sent without a token;
* never sends cookies (`credentials: "omit"`), so there is no CSRF exposure;
* aborts a request after the timeout;
* turns every failure into an `ApiError` with a `kind`:

| Response | Kind | What the user sees |
| --- | --- | --- |
| 401 | `unauthorized` | Signed out, back to the login page: "Your session has expired or was ended. Please sign in again." |
| 403 | `forbidden` | "You don’t have permission to access this page." |
| 404 | `not_found` | "Record not found." (the API also returns 404 for records the user may not see, so the app never reveals whether they exist) |
| 400 | `validation` | The API's messages, at the matching fields where possible |
| 429 | `rate_limited` | The API's throttle message ("Request was throttled…") |
| 5xx | `server` | "Something went wrong on our side. Please try again later." |
| no response | `network` | "Unable to connect. Please try again." |
| too slow | `timeout` | "The server took too long to respond. Please try again." |

Only validation and throttle messages from the API are shown; for every other error the
response body is discarded. HTML error pages and stack traces are never displayed or logged.
Tokens are never logged.

Retries: queries retry up to twice on network, timeout and server errors only; never on 400,
401, 403 or 404. Mutations are never retried automatically.

## Authentication flow

1. The login page posts `{username, password}` to `POST /api/auth/token/`.
   * Any failure returns the backend's single generic message ("Unable to sign in with the
     provided credentials."), whether the username is unknown, the password is wrong or the
     account is locked out. The page shows it as is and clears the password.
   * Too many attempts return 429 and the throttle message.
2. The token is stored (see below) and `GET /api/me/` loads the user's name, roles,
   capabilities and links (children, classes, substitute sessions, student record).
3. `/api/me/` is re-checked when the browser tab regains focus, so role or capability changes
   made by an administrator are picked up.
4. Any 401 from any request (token expired after 14 days, revoked by a role/password/active
   change, signed out elsewhere) clears the token and cached data and returns to the login
   page with a notice. After signing in again the user returns to the page they asked for.
5. Sign out calls `POST /api/auth/logout/` (the backend revokes the token), then clears the
   token and all cached data, even if the server cannot be reached.

**Where the token is kept:** `sessionStorage` (falling back to memory when storage is
blocked). It survives a reload but not closing the tab and is not sent automatically with
requests. Like any token readable by JavaScript it could be read by injected script; the
defences are the production Content-Security-Policy (`script-src 'self'`, no inline scripts),
React's output escaping (no `dangerouslySetInnerHTML` is used), short token lifetime and
server-side revocation. Moving to an HttpOnly-cookie session would need backend changes
(CSRF handling for the API) and is left as a possible later hardening step.

## Roles, portals and capabilities

A user may hold several roles (e.g. a coach who is also a parent) and sees every section they
are entitled to under one sign-in. Nothing in the app grants access: it only hides what the
API would refuse anyway.

* **Sections come from roles** (`src/auth/access.ts`):

  | Section (portal) | Roles |
  | --- | --- |
  | Academy staff | SUPER_ADMIN, ADMIN, FINANCE_ADMIN |
  | Coaching | COACH (substitute sessions appear in `me.substitute_sessions`) |
  | My family | PARENT |
  | My training | STUDENT |

  A SUPER_ADMIN holds every capability but is not given the parent or coach sections unless
  they actually hold those roles.
* **Pages come from capabilities.** Each entry in `src/nav/navigation.ts` lists the backend
  capability names that open it (any one is enough), matching `apps/accounts/capabilities.py`.
  The same list builds the navigation *and* the routes, so a page cannot be linked without
  being guarded.
* **Direct navigation** to a page the user may not open shows "You don’t have permission to
  access this page." Unknown URLs show "Page not found."

## Design system

* Tokens (`styles/tokens.css`): brand crimson, text, muted, border and status colours
  (success, warning, danger, info) chosen for WCAG AA contrast; spacing scale; type scale;
  radii; 44 px minimum tap targets.
* Components (`src/ui/`): buttons (primary, secondary, danger, ghost, busy state), text and
  text-area fields with hint and error, cards, badges (always text, never colour alone), alerts,
  KPI cards, data tables, modal dialog, confirmation dialog (optional required reason), and the
  standard states: "Loading…", "No records found.", access denied, not found, error with retry.
* Domain helpers (`src/domain/`):
  * money: decimal strings formatted as text (`"1234.5"` → `RM 1,234.50`), never floats;
  * dates in the academy time zone (Asia/Kuala_Lumpur);
  * invoice, payment, charge, registration and payroll status badges;
  * attendance status badges, where UNMARKED is shown as "Not marked" and is never counted as
    absent or included in the percentage; sheet state badges (Not started, Open, Complete,
    Locked, Cancelled), the locked notice and the correction-reason field. The 48-hour rule is
    enforced by the backend only.
  * family context (one family, several students; there is no billing contact).

## Responsive layout

* Wider than 900 px: fixed sidebar and top bar (staff desktops).
* 900 px and narrower: the sidebar becomes a menu drawer opened from the top bar (coaches,
  parents and students on phones). Tables of 640 px and narrower become stacked cards, each
  value labelled with its column name.

## Accessibility

Semantic landmarks (`header`, `nav`, `main`, `aside`), a skip link, visible focus rings,
labels on every input, errors linked to their inputs with `aria-describedby` and
`aria-invalid`, status messages in live regions, the modal dialog and the phone menu trap focus,
close with Escape and return focus to the button that opened them, and page titles follow the
current page.

## Security rules for UI work (all phases)

* Never show full IC numbers unless the API returns them (it masks them for most roles).
* Never show coach bank details outside the finance screens for users with
  `coaches.bank_details`.
* Never show medical notes outside the contexts the API returns them for.
* Never log tokens or API responses; never show raw API responses or stack traces.
* Never put secrets in the source code or `VITE_*` variables.
* Never create "billing contact" or "bill to parent" fields: invoices belong to a family.

## Production build and serving

```bash
cd frontend
npm ci
npm run build          # output: frontend/dist/
```

`dist/` is static files built for the `/app/` path. Serve it from the same host as Django;
the reverse proxy routes:

| Path | Goes to |
| --- | --- |
| `/app/assets/…` | files in `frontend/dist/assets/` (long cache: file names contain a content hash) |
| `/app/…` (anything else) | `frontend/dist/index.html` (single-page app fallback, `Cache-Control: no-cache`) |
| `/api/…`, `/admin/…`, `/static/…`, `/receipts/…`, `/invoices/…` | Django |

Send the same security headers for `/app/` as Django sends (see `docs/DEPLOYMENT.md`). The
build contains no inline scripts, so it works with the production CSP
(`script-src 'self'; connect-src 'self'`). This was verified in a browser with that policy:
no violations.

## Parent Portal (Phase 6B)

The Parent Portal is the PARENT role's section of the app. It is read-only except for
competition registration and withdrawal, the only parent actions the backend offers.

### Routes

All under `/app`, all guarded by `RequireAccess` (PARENT role plus the capability shown). A
parent-only account is sent from `/dashboard` to `/parent/dashboard`; a user with other roles
too (e.g. coach and parent) keeps the general dashboard and has the parent section in the menu.

| Route | Page | Capability |
| --- | --- | --- |
| `/parent/dashboard` | Family overview | `students.view_own_children` |
| `/parent/family` | My family (students of the family) | `students.view_own_children` |
| `/parent/students` | redirects to `/parent/family` (no duplicate list) | – |
| `/parent/students/:studentId` | Student profile | `students.view_own_children` |
| `/parent/schedule` | Week-by-week schedule | `sessions.view_own_children` |
| `/parent/attendance` | Attendance summary and history | `attendance.view_own_children` |
| `/parent/finance` | Family finance overview (outstanding invoices, charges) | `finance.view_own_children` |
| `/parent/finance/invoices` | Invoice list with filters | `finance.view_own_children` |
| `/parent/finance/invoices/:invoiceId` | Invoice detail (printable) | `finance.view_own_children` |
| `/parent/finance/payments` | Payment history | `finance.view_own_children` |
| `/parent/finance/proofs` | Payment proofs uploaded by the family | `finance.view_own_children` |
| `/parent/finance/receipts` | Receipts | `finance.view_own_children` |
| `/parent/finance/receipts/:receiptId` | Receipt as issued (printable) | `finance.view_own_children` |
| `/parent/competitions` | Competitions and the children's entries | `competition.registrations.view_own_children` or `competition.register_own_children` |
| `/parent/competitions/:competitionId` | Competition detail, withdraw | as above |
| `/parent/competitions/:competitionId/register` | Registration | also `competition.register_own_children` |

Routes come from `NAV_ITEMS` (section pages) and `SUB_ROUTES` (detail pages, guarded like
their section) in `src/nav/navigation.ts`; `src/app/App.tsx` maps them to page components.

### API endpoints used (all existing, all scoped by the backend)

| Feature | Endpoint | Serializer | Capability | Record scope (backend) |
| --- | --- | --- | --- | --- |
| Children list | `GET /api/me/` (`children`) | MeView | any signed-in user | guardianships of the parent |
| Student profile, classes | `GET /api/students/:id/` | `OwnStudentSerializer` | `students.view_own_children` | own children, else 404 |
| Attendance summary | `GET /api/students/:id/attendance-summary/` | summary | as above | own children, else 404 |
| Attendance history | `GET /api/attendance/?student=&page=` | `AttendanceRecordSerializer` | `attendance.view_own_children` | own children's records |
| Family | `GET /api/families/` | `FamilySerializer` | `finance.view_own_children` | families of own children |
| Schedule | `GET /api/sessions/?start=&end=&page=` | `TrainingSessionSerializer` | `sessions.view_own_children` | sessions of the children's classes |
| Charges | `GET /api/charges/?student=&page=` | `ChargeSerializer` | `finance.view_own_children` | own children's charges |
| Invoices | `GET /api/invoices/?outstanding=1|status=&page=`, `GET /api/invoices/:id/` | `InvoiceSerializer` | `finance.view_own_children` | own families, never drafts |
| Payments | `GET /api/payments/?page=` | `PaymentSerializer` | `finance.view_own_children` | own families |
| Receipts | `GET /api/receipts/?page=`, `GET /api/receipts/:id/` | `ReceiptSerializer` | `finance.view_own_children` | own families |
| Competitions | `GET /api/competitions/`, `GET /api/competitions/:id/` | `CompetitionSerializer` | `competition.view` | not drafts |
| Entries | `GET /api/competition-registrations/?competition=` | `CompetitionRegistrationSerializer` | `competition.registrations.view_own_children` | own children |
| Register | `POST /api/competition-registrations/` `{student, event, notes}` | same | `competition.register_own_children` | own children only (403 otherwise) |
| Withdraw | `POST /api/competition-registrations/:id/withdraw/` `{reason}` | same | `competition.register_own_children` | own children only (404 otherwise) |
| Payment information | `GET /api/payment-info/` | `AcademyPaymentInfoSerializer` | `finance.view_own_children` | academy-wide, read-only for parents |
| Payment proofs | `GET/POST /api/payment-proofs/`, `GET /api/payment-proofs/:id/file/` | `PaymentProofSerializer` | `finance.view_own_children` / `finance.proofs.upload_own` | own families' invoices only (404 otherwise) |

Phase 6B needed no new endpoint; the payment-proof correction added `payment-info` and
`payment-proofs`. Lists use the backend's pagination ("Show more" loads the next
page); bounded ranges (a week of sessions, a family's payments for an invoice) follow `next`
up to a fixed number of pages. Query keys start with `"parent"` (`src/parent/queries.ts`);
student details are cached per child and shared between pages.

### Family and children

One family, several students, one family invoice that can list charges for several children.
There is no billing contact, "bill to" or parent billing account anywhere. The children come
from `/api/me/`; the family page shows the family's students from `/api/families/` (a student
of the family linked only to another guardian is listed by name, without a profile link).

**Switching child:** a native `<select>` ("Viewing") on schedule, attendance, finance charges
and registration. The choice is kept in the URL (`?student=`) and remembered while moving
between portal pages (`ParentLayout` context). Only child-specific queries change; family data
comes from the cache. An id typed into the URL that is not one of the parent's children is
ignored by the selector and refused by the API (404, shown as "Record not found.").

### Schedule and attendance

* Sessions are shown in academy time with the backend's status (upcoming, in progress,
  completed, **cancelled**). Each row is labelled with the child(ren) enrolled in that class on
  that date; sessions of classes none of the children attend (e.g. a class the parent coaches)
  are not shown in the parent portal.
* Attendance percentages and counts are the backend's (`attendance-summary`). "Not marked"
  (UNMARKED) is always shown separately and is never counted as absent or included in the
  percentage. History lists the recorded marks; parents cannot change attendance (there is no
  endpoint for it, and no control in the UI). Coach remarks and who recorded a mark are not
  shown.

### Finance

Read-only except for uploading payment proof. Invoices list each line under its child (per-child subtotals are added exactly in
integer cents; totals, paid, balance due and refunds are the backend's figures). A void invoice
shows its reason and nothing due. Payments show what they were applied to and link to the
receipt. There is no refund action.

**Paying (no online gateway; see `docs/PAYMENT_PROOFS.md`).** An open invoice shows the amount
due, the academy's payment information from `GET /api/payment-info/` (bank, account name,
account number with a Copy button, the invoice number as payment reference, instructions and
the QR code, delivered as a `data:` image so it works under the CSP), then an "Upload payment
proof" form (`POST /api/payment-proofs/`, multipart: file, payment date, amount paid, bank
reference, note; type and size are checked in the browser and again by the backend). After
upload the page says the proof "has been submitted and is waiting for academy verification"
and lists it as **Pending review**; rejected proofs show the staff's reason. The invoice's own
status only changes when the academy records the payment, which also issues the official
receipt; nothing in the app marks anything as paid. Proof files download through the API
(blob download, never a public URL). The "Payment proofs" tab lists the family's proofs.
Accounts without `finance.proofs.upload_own` see the payment information but no upload form.

### Receipts

The Django print pages (`/receipts/<id>/`, `/invoices/<id>/`) need a Django session and are not
used by the app. `GET /api/receipts/:id/` already returns the receipt **exactly as issued**
(its frozen `content`), scoped to the parent's families (404 otherwise), so the portal renders
that content as a printable page (`window.print()` with a print stylesheet). No credentials
other than the parent's own token are used.

### Competition registration flow (the backend's, with payment proof)

Each competition has its **own registration form**, configured by academy staff and published
by the backend (`docs/COMPETITIONS.md`, "Registration forms"). The Parent Portal has no
competition-specific questions in its code: `src/parent/DynamicForm.tsx` renders whatever the
competition's `registration_form.fields` contain, by type (TEXT → text input, LONG_TEXT →
textarea, NUMBER → decimal input, DATE → date input, SINGLE_SELECT → select, MULTI_SELECT →
checkbox group, YES_NO → Yes/No radio group, EMAIL → email input, PHONE → tel input), with
label, help text (as the accessible description), placeholder, required marker and limits.

1. Choose the competition (the register page can switch between open competitions), the child
   and an event; the page shows **that event's fee** as it is chosen, and the competition's
   rules text.
2. Answer the competition's questions. Required answers, number ranges, lengths, email/phone
   formats and options are checked in the browser for convenience; the backend checks all of
   it again and its errors (`responses.<key>`) are shown at the matching question, which gets
   focus.
3. Confirm in a dialog that lists the answers and the fee and says an invoice is issued due
   today and the entry is confirmed only when paid in full.
3. `POST /api/competition-registrations/` `{student, event, competition, notes, responses}`:
   the backend checks ownership, the published form and every answer, eligibility (age,
   gender), duplicates, capacity and the per-child event limit, creates the entry as
   **PENDING ("Awaiting payment")** and issues a competition invoice. Its error messages are
   shown as returned; the app has no eligibility logic of its own.
4. The page shows the status, the answers as submitted, the amount due and the academy's
   payment information, with "Pay and upload proof" linking to the invoice. The
   parent pays manually and uploads payment proof there (Pending review). Staff review it and
   record the payment; when the invoice is paid in full the entry becomes **CONFIRMED**
   automatically. A proof alone, even accepted, never confirms the entry; a rejected proof
   leaves it awaiting payment. The app never shows a payment as made.
5. Withdrawal is offered only where the competition allows it (`allow_parent_withdrawal`) and
   registration is open; otherwise the page says withdrawal is handled by the academy. The
   backend decides either way. Unpaid: the invoice is voided; paid: nothing is refunded (the
   dialog says so). Exceptional refunds are staff-only.

The competition page lists each child's entry with its submitted answers ("View answers"), read
from the registration's own snapshot, so later form changes never alter them. Answers are
rendered as plain text by React (no `dangerouslySetInnerHTML` anywhere). A form that is not
published shows "The registration form for this competition is not available yet."

Statuses shown are the backend's enums: PENDING, CONFIRMED, WITHDRAWN, REJECTED, with the
charge status (UNPAID, PARTIAL, PAID, WAIVED, CANCELLED) as payment status.

### Security model

* Every record comes from an endpoint the backend scopes to the parent's own children or
  families; ids in URLs are only passed to the API, never trusted. Another family's records
  are 404 ("Record not found.").
* **Backend change in 6B:** staff-only `notes` on charges, invoices, payments and families
  were returned to parents (they are internal notes, not printed on invoices or receipts).
  They are now left out for users without `finance.view_all` (families: without
  `students.view_all` or `finance.view_all`); staff still see them. Tested in
  `apps/api/tests/test_parent_portal.py`.
* The IC number is shown masked (last 4 characters); medical notes are shown on the child's
  profile because the backend returns them to that child's own parents only.
* The parent's own contact details come from `/api/me/`; their IC number is never displayed.
* Backend tests (`test_parent_portal.py`, 17 tests): Family A with three children and one
  family invoice, Family B with two; Parent A reaches all of Family A's students, family,
  attendance, invoices, charges, payments, receipts and entries, and gets 404 for every Family
  B record and nothing from swapped filter ids; no attendance, refund or confirm action; no
  registering or withdrawing another family's child; no self-made guardianship; staff notes
  hidden from parents and visible to staff; the register → pay → CONFIRMED → withdraw (no
  refund) flow; eligibility and duplicate errors; drafts hidden.

## Coach Portal (Phase 6C)

For the COACH role, phone-first (coaches use it at the venue). It reuses the existing
session, roster, attendance and competition APIs; the backend decides every scope from the
signed-in coach and never trusts a coach id. A coach-only account is sent from `/dashboard`
to `/coach/dashboard`; a coach who is also a parent keeps the general dashboard and sees
both sections.

### Routes

| Route | Page | Capability |
| --- | --- | --- |
| `/coach` | redirects to `/coach/dashboard` | – |
| `/coach/dashboard` | Today (sessions, next session, cancelled / substitute marked, the session in progress with "Take attendance"), attendance to finish, next 7 days | `sessions.view_assigned` |
| `/coach/sessions` | Today / Upcoming / Past / Cancelled (`?view=`), with role and attendance state per session | `sessions.view_assigned` |
| `/coach/sessions/:sessionId` | Session detail: date, time, venue, status, regular and substitute coaches, notes, attendance summary, roster (training information only) | `sessions.view_assigned` |
| `/coach/attendance` | Sessions open for attendance (today and the previous two days) | `attendance.take_assigned` |
| `/coach/sessions/:sessionId/attendance` | Take and correct attendance | `attendance.take_assigned` |
| `/coach/competitions` | Competition entries of the students the coach coaches, with results | `competition.registrations.view_assigned` |

The menu shows Coach dashboard, My sessions, Attendance and Competitions only: no finance,
family, payroll, payment proof or settings pages (and those URLs show "access denied").

### API used

| Feature | Endpoint |
| --- | --- |
| Session lists (dashboard, sessions, attendance index) | `GET /api/sessions/coaching/?date=|start=&end=|status=CANCELLED&order=asc` (new in 6C; scope from `access.roster_sessions_for`) |
| Session detail | `GET /api/sessions/:id/` |
| Roster | `GET /api/sessions/:id/roster/` (roster serializer: training information only; no medical notes or emergency contacts for coaches) |
| Attendance sheet / submit | `GET` / `POST /api/sessions/:id/attendance/` |
| Competition entries | `GET /api/competition-registrations/` (coach scope; family-only fields removed), `GET /api/competitions/` |

### Attendance page

* One row per expected student (from the backend's sheet) with five large choices: Present,
  Late, Absent, Excused, Not marked (the backend's statuses; UNMARKED is shown as "Not
  marked", never as absent). Long names wrap; each choice is at least 44 px tall.
* "Mark all unmarked as present" fills only students who are not marked yet.
* A sticky counts bar shows expected / marked / not marked as the coach marks, and the saved
  attendance percentage exactly as the backend reports it (UNMARKED excluded; never
  recomputed in the browser).
* Optional remark per student (the existing attendance `remarks`).
* Saving sends only the changed students, all or nothing. Changing a mark that was already
  saved asks for a reason (the backend requires one and audits it). Success and errors are
  announced and receive focus.
* The state comes from the backend: NOT_STARTED (opens at the start time), OPEN / COMPLETE
  (editable until the 48-hour deadline shown), LOCKED (read-only: only an administrator can
  correct, with a reason), CANCELLED (no attendance). A late write the backend refuses (403)
  shows "can no longer be changed by coaches" and reloads the state.

### Substitutes

A substitute sees only the covered session (dashboard marks it "Substitute"), its roster and
attendance, and only while the authorization is open; the regular coach sees "Covered by a
substitute". Substitutes are assigned and revoked by staff only.

### Privacy

The roster shows name, Chinese name, student no. and age only. Medical notes and emergency
contacts are not sent to coaches: the only medical field is a general free-text note (not a
coaching restriction), and no capability authorizes coaches to see guardians' contacts, so the
roster serializer leaves both out unless the caller holds `students.view_all` (staff). The
page does not render them even if a response carried them. No IC, address, family or finance
data. Competition entries show student, event, entry status
("Not yet confirmed" / "Confirmed" / ...) and results; the family's form answers, notes, fees
and invoices are not sent to coaches at all.

## Student Portal (Phase 6D)

For the STUDENT role: a read-only view of the student's own training, phone-first. It uses
the narrow `/api/students/me/...` endpoints; the backend derives the student from the
signed-in account (`StudentAccount` + STUDENT role) and never reads a student id from the
request. A student-only account is sent from `/dashboard` to `/student/dashboard`; a student
who also has another role keeps the general dashboard and sees each section.

### Routes

| Route | Page | Capability |
| --- | --- | --- |
| `/student` | redirects to `/student/dashboard` | – |
| `/student/dashboard` | Sessions today, next session (the first not yet started), attendance percentage (backend), today's sessions, upcoming competitions and recent results | `sessions.view_self` |
| `/student/schedule` | Today / Upcoming / Past / Cancelled (`?view=`), own attendance per session | `sessions.view_self` |
| `/student/sessions/:sessionId` | Session detail: class, date, time, venue, status, coach names, own attendance. Another student's session = not found | `sessions.view_self` |
| `/student/attendance` | Backend summary (UNMARKED separate, not in the percentage) and one row per session that has taken place | `attendance.view_self` |
| `/student/competitions` | Own entries: competition, dates, venue, event, entry status, result, rules (plain text) | `competition.registrations.view_self` |
| `/student/profile` | Basic profile and current classes, read only | `students.view_self` |

### API

| Data | Endpoint |
| --- | --- |
| Profile | `GET /api/students/me/` (`SelfStudentSerializer`) |
| Sessions | `GET /api/students/me/sessions/?view=&page=`, `GET /api/students/me/sessions/:id/` (`StudentSessionSerializer`) |
| Attendance | `GET /api/students/me/attendance/` (summary from the attendance service + rows) |
| Competitions and results | `GET /api/students/me/competitions/` (`StudentCompetitionEntrySerializer`) |

### Privacy

No IC / passport, date of birth, address, phone, email, medical note, guardians, emergency
contacts, family, fees, invoices, payments, payment proofs, registration answers or notes,
staff session notes, coach remarks or other students are sent to a student login; the pages
could not show them. Nothing in the portal writes: no register, withdraw, attendance or
profile editing controls. The menu has only the student pages (no finance, family, coach,
admin or payroll).

## Staff Core Operations Portal (Phase 6E)

For academy staff (ADMIN, SUPER_ADMIN): the day's operations. Each page needs its own
existing capability, so FINANCE_ADMIN (`students.view_directory`) sees only the student
directory; nothing of classes, sessions or attendance. Every change goes to the existing
service endpoints (reasons, lifecycle rules and audit are the backend's); the React portal
does not replace Django Admin, which stays available for deeper administration (guardian
and personal-detail editing, class coach assignments, timetable slots, finance).

### Routes

| Route | Page | Capability |
| --- | --- | --- |
| `/staff` | redirects to `/staff/dashboard` | – |
| `/staff/dashboard` | "Operations": sessions today, in progress, next, cancelled, substitute-covered, active students / classes (when permitted), alerts (attendance not finished, locked with students not marked, sessions without a coach, classes without a coach), attendance completed recently, links to Django Admin | `sessions.view_all` |
| `/staff/students` | Search (name, Chinese name, student no.), status and class filters, paginated minimal rows | `students.view_all` or `students.view_directory` |
| `/staff/students/:studentId` | Full record for `students.view_all` (IC masked until "Show", guardians without their IC, health note, classes, family, recent audited changes); the directory for finance. Edit details (names, gender, date of birth, school), change status (optional reason), add to / end a class (`students.manage`) | same |
| `/staff/classes` | Active / inactive / all, coaches, timetable, current student count; create a class (`classes.manage`) | `classes.view_all` |
| `/staff/classes/:classId` | Details, coaches, timetable, roster (name, student no., status only), upcoming sessions; edit, activate / deactivate after confirmation (`classes.manage`) | `classes.view_all` |
| `/staff/timetable` | Weekly timetable from the classes' slots, filtered by day, class, coach, venue (read only) | `classes.view_all` |
| `/staff/sessions` | Today / upcoming / past / cancelled or a date; class and coach filters; attendance state per session | `sessions.view_all` |
| `/staff/sessions/:sessionId` | Session, coaches and substitute authorizations, attendance summary and expected students; reschedule, cancel, reinstate, reassign the regular coach, assign / revoke a substitute (`sessions.manage`, `substitute.assign` / `.revoke`), each with the reason the backend requires | `sessions.view_all` |
| `/staff/attendance` | Monitoring over a date range: expected / marked / not marked / percentage and state from the backend; class and coach filters; incomplete only; show cancelled | `attendance.view_all` |
| `/staff/sessions/:sessionId/attendance` | The attendance sheet (shared with the Coach Portal): recording with `attendance.take_any`, correction of a locked session with `attendance.correct` and a required reason; per-record change history (audit log) | `attendance.view_all` |

### API

Existing endpoints, plus three small read-only additions: `GET /api/staff/dashboard/`
(counts and alerts derived from current data; no notification model), `GET /api/programs/`
(to pick a program when creating a class) and staff-only query parameters (`coach` on
sessions, `class` / `family` on students). Staff student **lists** now return a minimal row
(`StaffStudentListSerializer`); the full record is only in the detail view.

### Privacy

The operations pages carry no invoices, payments, payment proofs, bank details or payroll.
The IC is masked on screen until "Show"; guardians' own IC numbers are never displayed. The
class roster shows names, student numbers and status only (the API's staff roster still
carries the health note and emergency contacts for `students.view_all`, as decided in 6C,
but the page does not display them).

## Tests

`npm test` runs 175 tests: the Staff Portal suite (24, `src/staff/test/`: dashboard KPIs,
backend alerts and zero-count alerts hidden, no finance/medical data, `/staff` redirect and
capability menu, error retry; student list without sensitive fields, search/status/class
filters sent to the API, detail with masked IC and guardians without IC, recent changes,
edit limited to allowed fields, status change with reason, backend refusal shown in the
dialog, finance directory without edit controls; classes list/inactive filter/create,
class roster without sensitive data, deactivation after confirmation, timetable filters;
session filters, detail with coaches and expected students, reschedule and cancel with
reasons and the backend's refusal, substitute and coach reassignment with active coaches
only; attendance monitoring counts and filters, administrator correction with a required
reason and change history, read-only locked sheet without `attendance.correct`; coach,
parent and student denied everywhere, finance admin limited to the directory, super admin
allowed; labelled cells for phones, phone menu, unknown student), the Phase 6A suite (61), the Parent Portal suites (55,
`src/parent/test/`: portal 31, payment proofs 14, competition registration forms 10), the
Student Portal suite (17, `src/student/test/`: dashboard KPIs and sections, landing and
`/student` redirect, menu without finance/family/coach/admin pages, loading and error states,
schedule filters sent to the API without any student id, session detail without roster or
notes, cancelled / future / unknown sessions, attendance with the backend percentage and
UNMARKED separate, no recalculation, competitions and results without fees or answers and
rules as plain text, read-only profile without private fields, route guards both ways, a
student who is also a parent, phone menu and labelled table cells) and the
Coach Portal suite (18, `src/coach/test/`: dashboard and menu, session filters, session
detail and roster without health or contact details (also when a response carried them), cancelled and unknown sessions, attendance counts and "mark all", saving
only changes, reason for saved-mark changes, 403 late correction, backend validation
messages, locked and cancelled sheets, attendance index, competition entries without payment
details, route guards both ways). The Phase 6A suite (Vitest, jsdom) covers: the API client (headers, token, 204, 401,
403, 404, 400 field errors, 429, 5xx without leaks, network failure, timeout); login
(validation, success, generic failure, throttle, connection failure, redirect back to the
requested page); logout (server revocation and offline sign-out); expired or revoked session
(401 on `/api/me/` and on later requests); a connection failure while checking the session;
role-based navigation for each role, a coach who is also a parent, and a super admin; direct
navigation (access denied, not-yet-built page, not found); phone menu (focus, Escape, close
button, closing on navigation); dialog focus trap; confirmation with a required reason; field
error association; money, date, percentage and status formatting; attendance summary with
unmarked students; and the dashboard (widgets per capability, requests never sent for widgets
the user cannot use, sorting, errors without leaks).

The Parent Portal suite uses a Family A fixture (three children, one family invoice) and
covers: the family overview (children, next session, attendance with "not marked", finance
and competition summaries, only supported quick actions, no children); the family page; the
student profile (masked IC, medical notes, switching child, another family's child = not
found); the schedule (child labels, cancellations, other classes left out, filter by child,
week navigation); attendance (backend percentage, UNMARKED separate, no edit controls,
switching child refetches only that child); finance (outstanding invoices, charges per child,
invoice filters sent to the API, invoice lines per child with exact totals, payments applied,
printing, void invoices, payments without staff notes, receipt as issued, not found for other
families); competitions (open/other, entries with payment status, closed competition,
withdrawal warning and backend call, registration validation → confirmation → "Awaiting
payment" with the issued invoice and no fake payment, backend eligibility errors); and
authorization and errors (registration page needs the register capability, non-parents are
denied, API 403, network failure with retry, `/parent/students` redirect, phone menu).

Tests use a fake `fetch` (`src/test/helpers.tsx`); requests to undeclared endpoints get 404.

## Known limitations

* Staff competition, finance, payroll and report pages are placeholders until their phase
  (6F–6J); the operations dashboard links to Django Admin for them meanwhile. Coach payslips
  (the backend's existing `payroll.view_own`) are not shown in the Coach Portal; payroll
  screens are Phase 6I.
  Notifications and recent activity are marked "not available yet": the backend has no
  endpoints for them.
* The Django print pages (`/receipts/<id>/`, `/invoices/<id>/`) need a Django session. The
  Parent Portal prints receipts and invoices from the API data instead; staff printing is
  handled with the finance UI (6F).
* Parent Portal API gaps (documented, not worked around):
  * attendance history lists recorded marks only; sessions not yet marked are counted in the
    summary ("Not marked") but the API has no per-session list of them for parents;
  * there is no online payment gateway: parents pay manually and upload payment proof, which
    staff review before recording the payment (`docs/PAYMENT_PROOFS.md`); the staff review
    screens come with Phase 6F (API and Django admin list/download exist now);
  * the withdraw response carries the charge status from before the withdrawal; the portal
    refetches afterwards, so the page is correct;
  * attendance `remarks` and `recorded_by_name` are returned to parents by the API but not
    displayed; session `notes` are returned and not displayed.
* The token is readable by JavaScript (sessionStorage); see "Where the token is kept".
* The dashboard's upcoming sessions show today and tomorrow, first page (50) of each day.
