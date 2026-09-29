# SR Wushu Academy – Management System

Back office and app backend for SR Wushu Academy (Malaysia · MYR · Asia/Kuala_Lumpur).

* **Admin site** (`/admin/`): where staff key in and manage everything.
* **REST API** (`/api/`): backend for the **Parent App** and **Coach App** (and any
  integrations), with token authentication.

Built with Django 5.2 and Django REST Framework. SQLite for development; PostgreSQL for production.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py setup_academy      # programs + starter price list
.venv/bin/python manage.py createsuperuser    # set role "Admin" for the account
.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000/admin/. Run the tests with `.venv/bin/python manage.py test`.

For production set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false`, `DJANGO_ALLOWED_HOSTS`, the
`POSTGRES_*` variables (`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`,
`POSTGRES_PORT`) and install `psycopg`. Receipt header details come from `ACADEMY_ADDRESS`,
`ACADEMY_PHONE`, `ACADEMY_EMAIL` and `ACADEMY_REGISTRATION_NO`.

Scheduled jobs (e.g. cron):

```bash
python manage.py generate_sessions --days 14    # daily: create sessions from class timetables
python manage.py generate_monthly_fees          # 1st of the month: bill monthly class fees
```

## What's in it

| Area | Features |
|---|---|
| Students | Personal info, parents (many-to-many), class, team, coach, join date, status. Status history kept; students are never deleted; every change is in the audit log. |
| Parents | One parent → many children; one child → many parents. Relationship, primary / emergency / billing contact. |
| Classes | School / Additional / Elite; programs (Wushu Taolu, Sanda, Taiji, Changquan, Nanquan, Weapons); teams; weekly timetable with several slots per week; several coaches per class, several classes per coach. |
| Class membership | Dated enrollments. Transfers and withdrawals end the old membership rather than removing it, so past rosters stay correct. |
| Training sessions | Generated from the timetable; each session records which coaches taught it. |
| Substitute coaches | Admin assigns a substitute to one session. See the access rules below. |
| Attendance | Present / Absent / Late / Excused. Changes after first entry need a reason and are audited. Attendance % per student, class or period. |
| Fees | Per-class fee rates with effective dates (monthly or per-session), per-student fee plans and discounts, automatic monthly billing, one-off charges (registration, uniform, weapons, competition, other). See **[docs/FEES_GUIDE.md](docs/FEES_GUIDE.md)**. |
| Payments & receipts | Payments allocated to charges, official receipts with yearly numbering (`SRWA-2026-000001`), printable receipt page, void with reason. |
| Competitions | Competitions with multiple events (Changquan, Nanquan, Jianshu, Daoshu, Gunshu, Qiangshu, Nandao, ...), age and gender eligibility, deadlines, parent self-registration in the app, automatic fee charge, results and medals. |
| Coach payroll | Hourly, per-session, monthly and substitute rates (per coach, optionally per class), allowances, bonuses, deductions; monthly payroll run → payslips; finalize to lock. |
| Reports | Students, attendance, fees, payments, receipts, competitions, results, payroll, as JSON or CSV (Excel-friendly, including Chinese names). |

## Access rules

| | Admin | Coach | Substitute coach | Parent |
|---|---|---|---|---|
| Students | all | current members of own classes: training info, medical notes, emergency contacts | roster of the covered session only | own children only |
| Classes / sessions | all | own classes and their sessions | **only the one session**, from 24 h before start to 24 h after end | children's classes (no rosters) |
| Attendance | all | own classes | that session only, inside the window | own children |
| Fees, payments, receipts | all | none | none | own children |
| Payroll | all | own finalized payslips | none (no payroll admin) | none |
| Academy administration | yes | no | no | no |

The substitute window is configurable in `config/settings.py` (`SUBSTITUTE_ACCESS_HOURS_BEFORE/AFTER`).
Admins can also revoke a substitute early. The rules live in `apps/academy/access.py` and are used
by every API endpoint.

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

Attendance % = (Present + Late) ÷ (Present + Late + Absent) × 100. Excused sessions are left out.
Set `LATE_COUNTS_AS_PRESENT = False` to count Late as not attended.

## API overview

Authenticate with `POST /api/auth/token/` (`username`, `password`) and send
`Authorization: Token <key>`.

| Endpoint | Who | Purpose |
|---|---|---|
| `GET /api/me/` | all | Role, profile, children (parent), classes and open substitute sessions (coach) |
| `/api/students/` | all (scoped) | List/view; admin create/update. `…/{id}/attendance-summary/`, `…/{id}/history/`, `…/{id}/change-status/`, `…/{id}/guardians/` |
| `/api/enrollments/` | admin | Enroll; `…/{id}/end/`, `…/{id}/transfer/` |
| `/api/classes/` | all (scoped) | `…/{id}/students/`, `…/{id}/generate-sessions/` |
| `/api/sessions/` | all (scoped) | `…/{id}/roster/`, `…/{id}/attendance/` (GET, POST), `…/{id}/assign-substitute/`, `…/{id}/revoke-substitute/` |
| `/api/attendance/` | scoped | Attendance records; `…/{id}/history/` |
| `/api/charges/` | admin, parent | Fees owed; `?outstanding=1`; admin: create, `…/{id}/cancel/`, `generate-monthly/` |
| `/api/payments/` | admin, parent | Admin records a payment (issues receipt); `…/{id}/void/` |
| `/api/receipts/` | admin, parent | Receipt content; printable page at `/receipts/{id}/` |
| `/api/competitions/`, `/api/competition-events/` | all | Open competitions and events |
| `/api/competition-registrations/` | admin, parent | Parent registers own child; `…/{id}/withdraw/`, `…/{id}/confirm/` |
| `/api/competition-results/` | scoped | Results and medals |
| `/api/payslips/` | admin, coach | Payslips |
| `/api/reports/{name}/` | admin | `students`, `attendance`, `fees`, `payments`, `receipts`, `competitions`, `results`, `payroll`; `?start=&end=`, `?export=csv` |

Example: a coach submits attendance.

```http
POST /api/sessions/42/attendance/
{"records": [{"student": 7, "status": "PRESENT"}, {"student": 9, "status": "LATE", "remarks": "traffic"}]}
```

Changing a record that already exists requires `"reason": "..."`.

## Payroll rules

For each session a coach actually taught (not replaced, not cancelled):

* **Regular session**: class-specific per-session rate → class-specific hourly rate → covered by a
  monthly salary → general per-session rate → general hourly rate.
* **Substitute session**: substitute per-session / hourly rate (class-specific, then general),
  otherwise the per-session / hourly rates. A monthly salary never covers substitute work.
* Plus the monthly salary, allowances and bonuses, minus deductions.

Workflow: add rates (**Coach rates**) and adjustments, create a **Payroll run** for the month, run
the **Calculate** action (repeat as needed), then **Finalize** to lock it. Coaches see finalized
payslips in the app.

## Project layout

```
config/            settings, URLs
apps/audit/        append-only audit log + automatic change tracking
apps/accounts/     users (Admin / Coach / Parent), parent and coach profiles
apps/academy/      programs, teams, classes, timetables, students, guardians, enrollments,
                   sessions, substitute assignment, access rules (access.py)
apps/attendance/   attendance records, percentage calculation
apps/finance/      class fees, fee plans, charges, payments, receipts
apps/competitions/ competitions, events, registrations, results
apps/payroll/      coach rates, adjustments, payroll runs, payslips
apps/reports/      report builders + JSON/CSV endpoint
apps/api/          REST API for the Parent and Coach apps
docs/FEES_GUIDE.md step-by-step guide for keying in fees
```

## Not included yet

* The Parent App and Coach App front-ends themselves (this repository provides their API).
* Online payment gateway (FPX/DuitNow) integration: payments are recorded by staff.
* SST/e-Invoice (LHDN MyInvois) submission.
* Notifications (WhatsApp/SMS/email reminders for fees or competitions).
