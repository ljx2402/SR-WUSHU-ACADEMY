# SR Wushu Academy – Management System

Back office and app backend for SR Wushu Academy (Malaysia · MYR · Asia/Kuala_Lumpur).

* **Admin site** (`/admin/`): where staff key in and manage everything.
* **REST API** (`/api/`): backend for the **Parent App** and **Coach App** (and any
  integrations), with token authentication.

Built with Django 5.2, Django REST Framework and **PostgreSQL 16** (the reference database;
SQLite remains available as a lightweight local option).

## Quick start (PostgreSQL)

```bash
cp .env.example .env                      # development credentials; never commit .env
docker compose up -d db                   # PostgreSQL 16 on localhost:5432
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
set -a; . ./.env; set +a                  # exports DATABASE_URL
.venv/bin/python manage.py migrate
.venv/bin/python manage.py setup_academy      # programs + starter price list
.venv/bin/python manage.py createsuperuser    # becomes SUPER_ADMIN automatically
.venv/bin/python manage.py runserver
```

Without Docker, any local PostgreSQL 16 works. Create a role with `CREATEDB` (the tests create
`test_<dbname>`) and point `DATABASE_URL` at it. Without `DATABASE_URL` (or `POSTGRES_DB`) Django
falls back to `db.sqlite3`.

Open http://127.0.0.1:8000/admin/. Run the tests with `.venv/bin/python manage.py test`. With
`REQUIRE_POSTGRES=1` the run fails if it is not really on PostgreSQL 16. A handful of
database-behaviour tests (numeric overflow, row locking) only run on PostgreSQL. CI
(`.github/workflows/ci.yml`) runs the whole suite on PostgreSQL 16 and checks that every migration
rolls back to an empty database and applies again.

Database settings: `DATABASE_URL` (preferred), or the legacy `POSTGRES_DB` / `POSTGRES_USER` /
`POSTGRES_PASSWORD` / `POSTGRES_HOST` / `POSTGRES_PORT`; `DB_CONN_MAX_AGE` (default 60 s);
`DB_DISABLE_SERVER_SIDE_CURSORS=true` behind a transaction-mode pooler such as PgBouncer.

For production also set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false` and `DJANGO_ALLOWED_HOSTS`
(full hardening is a later phase). Receipt header details come from `ACADEMY_ADDRESS`,
`ACADEMY_PHONE`, `ACADEMY_EMAIL` and `ACADEMY_REGISTRATION_NO`.

Scheduled jobs (e.g. cron):

```bash
python manage.py generate_sessions --days 14    # daily: create sessions from class timetables
python manage.py generate_monthly_fees          # 1st of the month: bill monthly class fees
python manage.py generate_invoices              # then: one draft invoice per family (--issue to issue)
```

## What's in it

| Area | Features |
|---|---|
| Students | Personal info, parents (many-to-many), class, team, coach, join date, status. Status history kept; students are never deleted; every change is in the audit log. |
| Parents & families | One parent → many children; one child → many parents. Relationship, primary / emergency contact. Siblings are grouped explicitly into a **Family** (household) for invoicing; there is no bill-to parent. |
| Classes | School / Additional / Elite; programs (Wushu Taolu, Sanda, Taiji, Changquan, Nanquan, Weapons); teams; weekly timetable with several slots per week; several coaches per class, several classes per coach. |
| Class membership | Dated enrollments. Transfers and withdrawals end the old membership rather than removing it, so past rosters stay correct. |
| Training sessions | Generated from the timetable; each session records which coaches taught it. |
| Substitute coaches | Admin assigns a substitute to one session. See the access rules below. |
| Attendance | Present / Absent / Late / Excused, Unmarked until marked. Only the session's expected roster; coaches may edit for 48 h after the session, then administrator corrections with a reason. Every change audited. Attendance % per student, class or period (Unmarked shown separately). See `docs/ATTENDANCE_AND_SUBSTITUTES.md`. |
| Fees | Per-class fee rates with effective dates (monthly or per-session), per-student fee plans and discounts, automatic monthly billing, one-off charges (registration, uniform, weapons, competition, other). See **[docs/FEES_GUIDE.md](docs/FEES_GUIDE.md)**. |
| Invoices | One invoice per family covering all its children (`INV-2026-000001`), draft → issued → partially paid → paid / void, frozen snapshot of each student's charges, printable. See **[docs/FINANCE_ARCHITECTURE.md](docs/FINANCE_ARCHITECTURE.md)**. |
| Payments & receipts | Payments (`PAY-2026-…`) applied to issued invoices under row locks, one payment across several invoices of a family, partial payments, idempotency keys, official receipts (`SRWA-2026-…`) listing student and invoice per line, void with reason, exceptional refunds (`RFD-2026-…`) with reason. |
| Competitions | Competitions with multiple events (Changquan, Nanquan, Jianshu, Daoshu, Gunshu, Qiangshu, Nandao, ...), age and gender eligibility, deadlines, parent self-registration in the app, results and medals. Fees are paid at registration: the entry is confirmed only once its competition invoice is paid; fees are non-refundable. |
| Coach payroll | Per-session pay only (regular and substitute rates, per coach, optionally per class), allowances, bonuses, deductions; monthly payroll period → payslips; finance calculates, super admin finalizes to lock. See `docs/PAYROLL.md`. |
| Reports | Students, attendance, fees, payments, receipts, competitions, results, payroll, as JSON or CSV (Excel-friendly, including Chinese names). |

## Roles and access

Six roles: `SUPER_ADMIN`, `ADMIN`, `FINANCE_ADMIN`, `COACH`, `PARENT`, `STUDENT`. A user may hold
several. Every API action, service and admin page asks `can(user, capability)` against the single
map in `apps/accounts/capabilities.py`. `apps/academy/access.py` then limits the records to the
ones the user is related to. See **[docs/ROLES_AND_PERMISSIONS.md](docs/ROLES_AND_PERMISSIONS.md)**
for the full matrix. Approved business rules for later phases are in
**[docs/BUSINESS_DECISIONS.md](docs/BUSINESS_DECISIONS.md)**.

| | Admin | Finance admin | Coach | Substitute coach | Parent | Student |
|---|---|---|---|---|---|---|
| Students | all | directory only | current members of own classes: training info, medical notes, emergency contacts | roster of the covered session only | own children | self |
| Classes / sessions | all | none | own classes and their sessions | **only the one session**, from 24 h before start to 24 h after end | children's timetable (no rosters) | own timetable |
| Attendance | all | none | own classes | that session only, inside the window | own children | self |
| Fees, payments, receipts | view + record payments | full | none | none | own children | not yet (P1) |
| Payroll | none | prepare | own finalized payslips | none | none | none |
| Users and roles | no | no | no | no | no | no |

Only `SUPER_ADMIN` manages users and roles and finalizes payroll. The substitute window is
configurable in `config/settings.py` (`SUBSTITUTE_ACCESS_HOURS_BEFORE/AFTER`).

## How the business rules are enforced

1. **Historical student information**: students cannot be deleted; every field change is logged
   (who, when, old → new); status changes are also kept in `StudentStatusHistory`.
2. **Historical class membership**: `Enrollment` rows have start/end dates and cannot be deleted.
3. **Historical fees**: fee rates have effective dates; a billed rate cannot be edited; each charge
   stores its own amount.
4. **Immutable receipts**: receipt content is frozen when issued; the model refuses updates and
   deletes. Corrections are made by voiding (a separate record), never by editing.
5. **Auditable attendance**: every create/change is in the audit log with actor and reason;
   records cannot be deleted.
6. **Auditable finance**: fees, charges, payments, allocations, receipt issue/void and payroll are
   all audit-logged. The audit log itself cannot be edited or deleted.
7. **Parents see only their own children**, enforced on every query.
8. **Coaches see only authorized classes and students**, and never finance data.
9. **Substitutes get temporary, session-level access only.**

Attendance % = (Present + Late) ÷ (Present + Late + Absent) × 100. Excused and Unmarked are left out;
Unmarked students are reported separately and are never counted as Absent.
Set `LATE_COUNTS_AS_PRESENT = False` to count Late as not attended.

## API overview

Authenticate with `POST /api/auth/token/` (`username`, `password`) and send
`Authorization: Token <key>`. A role change revokes the user's tokens; they sign in again.

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /api/auth/token/`, `POST /api/auth/logout/` | anyone / signed in | Sign in (new 14-day token, previous revoked; brute-force lockout) and sign out |
| `GET /api/me/` | all | Roles, capabilities, profile, children (parent), classes and open substitute sessions (coach), own record (student) |
| `/api/users/` | super admin | Accounts; `POST …/{id}/roles/` with `roles` and `reason` |
| `/api/students/` | all (scoped) | List/view; admin create/update. `…/{id}/attendance-summary/`, `…/{id}/history/`, `…/{id}/change-status/`, `…/{id}/guardians/` |
| `/api/enrollments/` | admin | Enroll; `…/{id}/end/`, `…/{id}/transfer/` |
| `/api/classes/` | all (scoped) | `…/{id}/students/`, `…/{id}/generate-sessions/` |
| `/api/sessions/` | all (scoped) | `…/{id}/roster/`, `…/{id}/attendance/` (GET, POST), `…/{id}/assign-substitute/`, `…/{id}/revoke-substitute/`, `…/{id}/cancel/`, `…/{id}/reinstate/`, `…/{id}/reschedule/`, `…/{id}/reassign-coach/` |
| `/api/attendance/` | scoped | Attendance records; `…/{id}/history/` |
| `/api/charges/` | staff, parent | Fees owed; `?outstanding=1`; finance: create, `…/{id}/cancel/`, `generate-monthly/` |
| `/api/families/` | staff, parent | Households; staff move students between families |
| `/api/invoices/` | staff, parent | Family invoices; finance: create draft, `…/{id}/issue/`, `…/{id}/void/`, `generate-drafts/`; printable at `/invoices/{id}/` |
| `/api/payments/` | staff, parent | Admin/finance record a payment against invoices (`Idempotency-Key` supported; receipt issued); `…/{id}/void/` (finance), `…/{id}/refund/` |
| `/api/receipts/` | staff, parent | Receipt content; printable page at `/receipts/{id}/` |
| `/api/refunds/` | staff, parent | Exceptional refund records |
| `/api/competitions/`, `/api/competition-events/` | all | Open competitions and events |
| `/api/competition-registrations/` | admin, parent | Parent registers own child; `…/{id}/withdraw/`, `…/{id}/confirm/` |
| `/api/competition-results/` | scoped | Results and medals |
| `/api/payslips/` | admin, coach | Payslips |
| `/api/reports/{name}/` | per report capability | `students`, `attendance`, `fees`, `invoices`, `payments`, `receipts`, `refunds`, `competitions`, `results`, `payroll`, `payroll_lines`; `?start=&end=`, `?export=csv` |

Example: a coach submits attendance.

```http
POST /api/sessions/42/attendance/
{"records": [{"student": 7, "status": "PRESENT"}, {"student": 9, "status": "LATE", "remarks": "traffic"}]}
```

Changing a record that already exists requires `"reason": "..."`.

## Web app (frontend)

`frontend/` holds the web app (React + TypeScript + Vite) for staff, coaches, parents and
students, served under `/app/` and talking only to this API. Phase 6A provides the foundation:
sign-in, the application shell, role- and capability-aware navigation, route protection,
the design system and a dashboard from real API data. Phase 6B adds the Parent Portal
(family overview, children's profiles, schedule, attendance, family finance with invoices,
payments and printable receipts, competition registration). Phase 6C adds the Coach Portal
(today's and upcoming sessions, rosters, attendance on a phone, substitute sessions,
athletes' competition entries). Other portals follow in 6D–6J.

```bash
cd frontend && npm ci && npm run dev      # http://localhost:5173/app/ (proxies /api to :8000)
npm test && npm run build                 # tests; production build in frontend/dist/
```

See `docs/FRONTEND.md` (architecture, auth flow, roles, environment variables, serving).

## Security and deployment

See `docs/SECURITY.md` (audit, threat model, controls, **production blockers**),
`docs/DEPLOYMENT.md` (production settings) and `docs/BACKUP_AND_RECOVERY.md`.

## Payroll rules

All coach fees are **per session** (optionally per class); there is no monthly salary and no
hourly pay. For each session a coach actually taught (an `ASSIGNED` regular slot or an active
substitute authorization, session not cancelled and already ended):

* **Regular session**: class per-session rate → general per-session rate.
* **Substitute session**: class substitute rate → general substitute rate → (none set) class,
  then general, per-session rate.
* No rate found: the line is flagged `MISSING_RATE` and the payroll cannot be finalized.
* Plus allowances and bonuses, minus deductions.

Workflow: add rates (**Coach rates**) and adjustments, create a **Payroll run** for the month, run
**Calculate** (finance; repeat as needed), then **Finalize** (super admin only, after the month has
ended) to lock it. Coaches see finalized payslips in the app. Details: `docs/PAYROLL.md`,
sessions: `docs/SESSION_LIFECYCLE.md`, competitions: `docs/COMPETITIONS.md`.

## Project layout

```
config/            settings, URLs
apps/audit/        append-only audit log + automatic change tracking
apps/accounts/     users, roles & capabilities (capabilities.py), role changes (services.py),
                   admin permission backend, parent and coach profiles
apps/academy/      programs, teams, classes, timetables, students, guardians, enrollments,
                   sessions, substitute assignment, access rules (access.py)
apps/attendance/   attendance records, percentage calculation
apps/finance/      class fees, fee plans, charges, family invoices, payments, receipts, refunds,
                   document numbering, money rules (money.py), record scoping (access.py)
apps/competitions/ competitions, events, registrations, results
apps/payroll/      coach rates, adjustments, payroll runs, payslips
apps/reports/      report builders + JSON/CSV endpoint
apps/api/          REST API used by the web app
frontend/          web app (React + TypeScript + Vite), see docs/FRONTEND.md
docs/FEES_GUIDE.md step-by-step guide for keying in fees, invoicing and payments
docs/FINANCE_ARCHITECTURE.md    charges → invoices → payments → receipts, families, locking
docs/PAYMENT_PROOFS.md          parents' payment proofs (upload, staff review) vs official receipts
docs/ROLES_AND_PERMISSIONS.md   roles, capability matrix, role changes, migration
docs/BUSINESS_DECISIONS.md      approved business rules for later phases
docs/FRONTEND.md                web app architecture, development, build and serving
docker-compose.yml local PostgreSQL 16
.github/workflows/ci.yml        PostgreSQL 16 + SQLite test runs, migration round-trip, web app tests + build
```

## Not included yet

* The staff and student portal screens of the web app (Phase 6D onwards).
* Online payment gateway (FPX/DuitNow) integration: parents pay manually and upload payment
  proof; staff review it and record the payment (see docs/PAYMENT_PROOFS.md).
* SST/e-Invoice (LHDN MyInvois) submission.
* Notifications (WhatsApp/SMS/email reminders for fees or competitions).
