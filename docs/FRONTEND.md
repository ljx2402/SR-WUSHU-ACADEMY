# Web app (frontend) – Phase 6A foundation

The web app in `frontend/` is the user interface for staff, finance, coaches, parents and
students. It is a single-page app that talks only to the existing Django REST API. **The
backend is the source of truth for every permission and business rule**: the app decides what
to *show*, the API decides what is *allowed*.

Phase 6A delivers the foundation only: sign-in, the application shell, navigation, the
design system, the API client, route protection and a dashboard built from real API data.
The portal screens are built in later phases (6B–6J); until then each page exists, is
protected, and says "This page is not available yet" (no invented data).

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

## Tests

`npm test` runs 61 tests (Vitest, jsdom) covering: the API client (headers, token, 204, 401,
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

Tests use a fake `fetch` (`src/test/helpers.tsx`); requests to undeclared endpoints get 404.

## Known limitations (Phase 6A)

* Portal pages are placeholders until their phase (6B–6J). Notifications and recent activity
  are marked "not available yet": the backend has no endpoints for them.
* The printable receipt and invoice pages (`/receipts/<id>/`, `/invoices/<id>/`) are Django
  pages that need a Django admin session, not an API token. The finance UI (6F) needs either an
  API endpoint for the printable document or a sign-in bridge.
* The token is readable by JavaScript (sessionStorage); see "Where the token is kept".
* The dashboard's upcoming sessions show today and tomorrow, first page (50) of each day.
