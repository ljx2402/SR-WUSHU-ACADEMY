# Roles and permissions

Authorization has two layers, and every API endpoint, service and admin page uses both.

1. **RBAC: may this kind of user do this at all?** A user holds one or more **roles**, stored as
   Django Groups. Each role grants a fixed set of **capabilities**. The only decision function is
   `can(user, "<capability>")` in `apps/accounts/capabilities.py`. That file is the single source
   of truth:
   * API views declare a capability per action (`capabilities = {...}`); `HasCapability` enforces
     it, and undeclared actions are denied.
   * Services call `require(actor, capability)`. `actor=None` means a trusted system job
     (management command, cron, migration).
   * The Django admin gets its model permissions from `MODEL_CAPABILITIES` through
     `apps.accounts.backends.CapabilityBackend`. Permission rows stored in the database are ignored.
2. **Record access: which specific records?** `apps/academy/access.py` limits records by
   relationship: own children, assigned classes, the one substitute session, own student record.
   Out-of-scope records return **404**. A missing capability returns **403**.

## Roles

| Role | Purpose | Admin site |
|---|---|---|
| `SUPER_ADMIN` | Everything, including users, roles, settings, the full audit log and **payroll finalization** | yes |
| `ADMIN` | Academy operations: students, parents, guardians, classes, timetables, sessions, attendance, competitions, substitutes, operational reports. **May record payments** (cash, transfer), view charges/invoices/receipts, and record an exceptional refund with a reason. No payroll, no coach bank details, no role management | yes |
| `FINANCE_ADMIN` | Fee setup, charges, billing, family invoices (draft / issue / void), payments, voids, exceptional refunds, receipts, coach rates and bank details, payroll **preparation**, finance and payroll reports. Sees a student *directory* only; cannot manage academic records or classes; cannot finalize payroll | yes |
| `COACH` | Own assigned classes, their sessions and rosters, attendance for those classes and for substitute sessions (session only, time-limited), own athletes' competition entries, own finalized payslips | no |
| `PARENT` | Own children only: profile, timetable, attendance, competition entries and registration, charges, payments and receipts | no |
| `STUDENT` | Own record only, read only (Student Portal, Phase 6D): basic profile, own sessions, own attendance, own competition entries and results. No finance, family, registration or attendance-taking | no |

A user may hold several roles, for example `COACH` + `PARENT` or `ADMIN` + `COACH`. Visibility is
the union of the roles, but **the detail shown depends on the relationship to each record**. A
coach who is also a parent sees their own child with the parent view and their roster with the
coach view. Being a parent never opens coach access to the child's class. Coaching never opens
another family's finance data.

Student detail levels (`access.StudentScope`):

| Level | Who | Fields |
|---|---|---|
| FULL | staff with `students.view_all` | complete record, guardians with full details |
| OWN | own child (parent) | personal details; guardians as contacts only (name, relationship, phone) |
| SELF | own record (student login) | basic training profile only: student no., names, gender, age, status, join date, current classes. No IC / passport, date of birth, address, phone, email, medical note, guardians, emergency contacts or family |
| ROSTER | coach, for students in their classes / substitute session | training info only (no medical notes, no emergency contacts) |
| DIRECTORY | finance | student no., names, status, guardian contacts |

## Changing roles

Roles change **only** through `apps.accounts.services.set_roles(user, roles, actor, reason)`:
the admin "Change roles" page on a user, or `POST /api/users/{id}/roles/`. It:

* requires `roles.manage` (super admin) and a non-empty **reason**;
* refuses to remove the last active super admin;
* writes an audit event (category `SECURITY`) with actor, affected user, old roles, new roles,
  reason and number of tokens revoked. It never records the password hash, tokens or last login;
* **revokes every DRF API token** of the user and **invalidates every web session**
  (`User.auth_version` is part of the session hash), so no login keeps working under old roles.

Any other attempt to change a user's groups (shell, admin group editing, `user.groups.add`) raises
`PermissionDenied`. `User.is_staff`, `User.is_superuser` and the display-only `User.role` are
re-derived from the groups on every save, so they can never contradict the roles. `createsuperuser`
still works: the new account is promoted to `SUPER_ADMIN` through `set_roles`.

`StudentAccount` links one login to one student (one-to-one both ways). Linking grants nothing
by itself: the user also needs the `STUDENT` role.

## Existing-user migration (`accounts/0003_role_groups`)

| Before | After |
|---|---|
| `is_superuser` | `SUPER_ADMIN` |
| `role = ADMIN` | `ADMIN` (now also admin-site access) |
| `role = COACH` | `COACH` |
| `role = PARENT` | `PARENT`, except on a superuser where it was only the old default and no parent profile exists |

Passwords, usernames and profiles are untouched and no accounts are created. `auth_version` is
bumped, so existing sessions must sign in again. The migration is reversible (staff roles → `ADMIN`,
`COACH` → `COACH`, otherwise `PARENT`).

## Capability matrix (generated from `ROLE_CAPABILITIES`)

| Capability | SUPER_ADMIN | ADMIN | FINANCE_ADMIN | COACH | PARENT | STUDENT |
|---|---|---|---|---|---|---|
| `attendance.correct` | ✅ | ✅ |  |  |  |  |
| `attendance.take_any` | ✅ | ✅ |  |  |  |  |
| `attendance.take_assigned` | ✅ |  |  | ✅ |  |  |
| `attendance.view_all` | ✅ | ✅ |  |  |  |  |
| `attendance.view_assigned` | ✅ |  |  | ✅ |  |  |
| `attendance.view_own_children` | ✅ |  |  |  | ✅ |  |
| `attendance.view_self` | ✅ |  |  |  |  | ✅ |
| `audit.view_all` | ✅ |  |  |  |  |  |
| `audit.view_finance` | ✅ |  | ✅ |  |  |  |
| `audit.view_operations` | ✅ | ✅ |  |  |  |  |
| `classes.manage` | ✅ | ✅ |  |  |  |  |
| `classes.view_all` | ✅ | ✅ |  |  |  |  |
| `classes.view_assigned` | ✅ |  |  | ✅ |  |  |
| `classes.view_own_children` | ✅ |  |  |  | ✅ |  |
| `classes.view_self` | ✅ |  |  |  |  | ✅ |
| `coaches.bank_details` | ✅ |  | ✅ |  |  |  |
| `coaches.manage` | ✅ | ✅ |  |  |  |  |
| `coaches.view_all` | ✅ | ✅ | ✅ |  |  |  |
| `competition.manage` | ✅ | ✅ |  |  |  |  |
| `competition.register_own_children` | ✅ |  |  |  | ✅ |  |
| `competition.registrations.manage` | ✅ | ✅ |  |  |  |  |
| `competition.registrations.view_all` | ✅ | ✅ |  |  |  |  |
| `competition.registrations.view_assigned` | ✅ |  |  | ✅ |  |  |
| `competition.registrations.view_own_children` | ✅ |  |  |  | ✅ |  |
| `competition.registrations.view_self` | ✅ |  |  |  |  | ✅ |
| `competition.results.manage` | ✅ | ✅ |  |  |  |  |
| `competition.view` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `finance.charges.manage` | ✅ |  | ✅ |  |  |  |
| `finance.invoices.manage` | ✅ |  | ✅ |  |  |  |
| `finance.payments.record` | ✅ | ✅ | ✅ |  |  |  |
| `finance.payment_info.manage` | ✅ |  | ✅ |  |  |  |
| `finance.payments.void` | ✅ |  | ✅ |  |  |  |
| `finance.proofs.review` | ✅ | ✅ | ✅ |  |  |  |
| `finance.proofs.upload_own` | ✅ |  |  |  | ✅ |  |
| `finance.refunds.record` | ✅ | ✅ | ✅ |  |  |  |
| `finance.setup` | ✅ |  | ✅ |  |  |  |
| `finance.view_all` | ✅ | ✅ | ✅ |  |  |  |
| `finance.view_own_children` | ✅ |  |  |  | ✅ |  |
| `parents.manage` | ✅ | ✅ |  |  |  |  |
| `parents.view_all` | ✅ | ✅ | ✅ |  |  |  |
| `payroll.finalize` | ✅ |  |  |  |  |  |
| `payroll.prepare` | ✅ |  | ✅ |  |  |  |
| `payroll.rates.manage` | ✅ |  | ✅ |  |  |  |
| `payroll.view_all` | ✅ |  | ✅ |  |  |  |
| `payroll.view_own` | ✅ |  |  | ✅ |  |  |
| `reports.attendance` | ✅ | ✅ |  |  |  |  |
| `reports.competitions` | ✅ | ✅ |  |  |  |  |
| `reports.finance` | ✅ |  | ✅ |  |  |  |
| `reports.payroll` | ✅ |  | ✅ |  |  |  |
| `reports.students` | ✅ | ✅ |  |  |  |  |
| `roles.manage` | ✅ |  |  |  |  |  |
| `roster.view_all` | ✅ | ✅ |  |  |  |  |
| `roster.view_assigned` | ✅ |  |  | ✅ |  |  |
| `sessions.manage` | ✅ | ✅ |  |  |  |  |
| `sessions.view_all` | ✅ | ✅ |  |  |  |  |
| `sessions.view_assigned` | ✅ |  |  | ✅ |  |  |
| `sessions.view_own_children` | ✅ |  |  |  | ✅ |  |
| `sessions.view_self` | ✅ |  |  |  |  | ✅ |
| `settings.manage` | ✅ |  |  |  |  |  |
| `settings.view` | ✅ |  |  |  |  |  |
| `students.history` | ✅ | ✅ |  |  |  |  |
| `students.manage` | ✅ | ✅ |  |  |  |  |
| `students.view_all` | ✅ | ✅ |  |  |  |  |
| `students.view_assigned` | ✅ |  |  | ✅ |  |  |
| `students.view_directory` | ✅ |  | ✅ |  |  |  |
| `students.view_own_children` | ✅ |  |  |  | ✅ |  |
| `students.view_self` | ✅ |  |  |  |  | ✅ |
| `substitute.assign` | ✅ | ✅ |  |  |  |  |
| `substitute.revoke` | ✅ | ✅ |  |  |  |  |
| `users.manage` | ✅ |  |  |  |  |  |
| `users.view` | ✅ |  |  |  |  |  |

## Django admin (Phase 2 guardrails)

The admin is a second entry point to the same rules, not a separate permission system. It adds no
permission of its own: every page, action and custom view asks `can()` (through
`CapabilityBackend` and the model admins), and money-changing work goes through the same services
as the API.

* **Who reaches it.** Only staff roles (`SUPER_ADMIN`, `ADMIN`, `FINANCE_ADMIN`) have `is_staff`,
  which is derived from roles. `COACH`, `PARENT`, `STUDENT`, and combinations of those, are sent to
  the admin login page for every URL and POST. A combination that includes a staff role (for
  example `FINANCE_ADMIN` + `COACH`) gets exactly the staff role's admin.
* **Server-side, not just menus.** A missing capability returns 403 for a direct URL and for a
  crafted POST, including admin actions not offered to that role (for example, `ADMIN` posting
  `invoice_charges`, `FINANCE_ADMIN` posting payroll `finalize` or competition `confirm`). An unknown
  object id in a custom view returns 404. All POSTs need a CSRF token.
* **Finance student picker.** `FINANCE_ADMIN` has no student records (`students.view_directory`
  only). On the charge form they can use the student autocomplete, which returns only
  "name [student no.]" and searches only by name and number. That exception covers the
  `finance.charge.student` picker only; the student list and pages stay 403.
* **Read-only documents.** Issued invoices, payments, receipts and refunds cannot be changed through
  the admin change form (403 on POST); they change only through the issue, void, record-payment and
  refund views, which call the services. See `FINANCE_ARCHITECTURE.md` for the database-level
  protection.

Tests: `apps/accounts/tests/test_admin_access.py` (every role and combination, by direct URL and
POST) and `apps/finance/tests/test_admin_guardrails.py` (tampered forms, bulk deletes, raw SQL).

## Attendance and substitutes (Phase 3)

Record access for attendance and substitutes, in the API, the services and the admin:

| Action | Who |
|---|---|
| Record attendance within 48 h of the session end | Class's regular coaches; the session's authorized substitute (inside their access window); ADMIN, SUPER_ADMIN |
| Correct attendance after 48 h (reason required) | ADMIN, SUPER_ADMIN (`attendance.correct`) |
| Authorize / revoke a substitute (revoke needs a reason) | ADMIN, SUPER_ADMIN (`substitute.assign` / `substitute.revoke`) |
| Attendance of own child / self (read only) | PARENT / STUDENT |

FINANCE_ADMIN has no attendance capability, and finance capabilities never grant any. A revoked,
cancelled or expired substitute authorization grants nothing. A substitute never gets the class, its
other sessions, families or finance. COACH + PARENT combines the two contexts without either one
widening the other: parent access never shows a class roster, and coaching never shows family
finance. Details: `ATTENDANCE_AND_SUBSTITUTES.md`.

## Sessions, payroll and competition results (Phase 4)

| Action | Who |
|---|---|
| Cancel / reinstate / reschedule a session, reassign its regular coach (reason required) | ADMIN, SUPER_ADMIN (`sessions.manage`) |
| Calculate payroll, manage rates and adjustments | FINANCE_ADMIN, SUPER_ADMIN (`payroll.prepare`, `payroll.rates.manage`) |
| Finalize payroll | **SUPER_ADMIN only** (`payroll.finalize`); FINANCE_ADMIN cannot, by API, admin action or service |
| View payroll runs, all payslips, payroll reports (with bank details) | FINANCE_ADMIN, SUPER_ADMIN |
| Own finalized payslips (no bank details of anyone) | COACH |
| Record competition results (confirmed registrations only) | ADMIN, SUPER_ADMIN (`competition.results.manage`) |

ADMIN, parents and students have no payroll access and never see coach bank details. Details:
`SESSION_LIFECYCLE.md`, `PAYROLL.md`, `COMPETITIONS.md`.

## Authentication and sensitive data (Phase 5)

Sign-in, token expiry, the brute-force lockout, revocation rules, audit masking, and who may see
IC numbers, medical notes and bank details are described in `SECURITY.md` ("Controls now in place").
In short:
* medical notes are visible to staff and the student's own parents (not to the student's own
  login, which gets the basic profile only, Phase 6D). Coaches and
  substitutes do not receive them (nor emergency contacts): the note is general health
  information, not a coaching restriction, and no capability authorizes coach access. A
  dedicated training-restriction field with its own capability is a deferred design item;
* bank details are visible to `coaches.bank_details` only;
* audit entries show identity and account numbers as `****1234`, medical notes as a length only,
  and never credentials.

## Finance Staff Portal (Phase 6F)

No new capabilities. The finance menu shows each page only with its existing capability and
the API refuses the rest: ADMIN views finance, records payments, reviews proofs and records
exceptional refunds, but cannot void payments, issue or void invoices, or change the academy's
payment information (read only). FINANCE_ADMIN and SUPER_ADMIN can do all of these. COACH and
STUDENT have no finance access; PARENT only its own families through the Parent Portal. The
full matrix is in `FINANCE_ARCHITECTURE.md` ("Finance Staff Portal").

## Staff Core Operations Portal (Phase 6E)

No new capabilities; every page and action uses the existing ones, never the job title:

| Area | Needs | ADMIN | SUPER_ADMIN | FINANCE_ADMIN |
| --- | --- | --- | --- | --- |
| Operations dashboard | `sessions.view_all` | ✅ | ✅ | — (403) |
| Student list / detail | `students.view_all` (full) or `students.view_directory` (directory) | full | full | directory only |
| Edit student, status, class membership | `students.manage` | ✅ | ✅ | — |
| Classes, timetable, roster | `classes.view_all`, `roster.view_all` | ✅ | ✅ | — |
| Create / edit / (de)activate class | `classes.manage` | ✅ | ✅ | — |
| Sessions: view | `sessions.view_all` | ✅ | ✅ | — |
| Reschedule, cancel, reinstate, reassign coach | `sessions.manage` | ✅ | ✅ | — |
| Substitute assign / revoke | `substitute.assign` / `substitute.revoke` | ✅ | ✅ | — |
| Attendance monitoring, sheet, change history | `attendance.view_all` | ✅ | ✅ | — |
| Record attendance / correct after 48 h | `attendance.take_any` / `attendance.correct` (reason required) | ✅ | ✅ | — |

The student list returns a minimal staff row (no IC, contact details, medical note or
guardians); the full record is only in the detail view for `students.view_all`. Guardians'
IC numbers are masked for anyone without `parents.manage` (unchanged) and are not shown by
the portal at all. Staff-only query parameters (`coach` on sessions, `class` and `family` on
students) only narrow the caller's existing scope; for a coach the `coach` parameter is
ignored. Regular class-coach assignments and timetable slots have no service or API yet and
stay in Django Admin.

## Student Portal (Phase 6D)

No new capabilities: the Student Portal uses the STUDENT role's existing ones
(`students.view_self`, `classes.view_self`, `sessions.view_self`, `attendance.view_self`,
`competition.view`, `competition.registrations.view_self`). The student is always the
signed-in user's own linked record: `StudentAccount` (one login ↔ one student) **and** the
STUDENT role (`access.student_of`); no student id is ever read from the request. The narrow
read-only endpoints are `GET /api/students/me/`, `/api/students/me/sessions/`
(`?view=today|upcoming|past|cancelled`), `/api/students/me/sessions/<id>/`,
`/api/students/me/attendance/` and `/api/students/me/competitions/`. A STUDENT without a
linked record gets 404; a linked user without the role gets 403.

| Data | Student sees |
| --- | --- |
| Profile | SELF level only (above). Read only: no edit endpoint for students |
| Sessions | Only sessions they were expected at (a member of the class on the session date, or with an attendance record): `access.student_sessions`, also used for the student branch of `sessions_for`. Class, date, time, venue, status and the names of the assigned coaches. **Not** staff session notes, substitute authorizations, rosters or other students |
| Attendance | Own statuses, the attendance service's summary (UNMARKED separate, not in the percentage). **Not** the coach's remarks or who recorded it. Cannot mark, change or correct (403) |
| Competitions | Own entries: competition, dates, venue, rules, event, entry status and result (placing, medal, score). **Not** fee, fee status, invoice, payment, the family's form answers or notes, staff result remarks, the registration form or event fees. Cannot register, withdraw or edit (403) |
| Finance, families, parents, payment proofs, payment information, receipts, refunds, payroll, reports | Nothing (403) |
| Rosters, coach sessions, attendance sheets, substitutes | Nothing (403) |

The generic endpoints (`/api/students/<id>/`, `/api/sessions/`, `/api/attendance/`,
`/api/competition-registrations/`, `/api/competition-results/`, `/api/competitions/`) give a
student-only viewer the same student-safe representation (`access.student_view`: a student
account without any staff, coach or parent capability for that data). A user who is also a
parent or coach keeps their fuller views.

## Coach Portal (Phase 6C)

No new capabilities: the Coach Portal uses the COACH role's existing ones
(`sessions.view_assigned`, `roster.view_assigned`, `attendance.view_assigned`,
`attendance.take_assigned`, `students.view_assigned`, `classes.view_assigned`,
`competition.view`, `competition.registrations.view_assigned`, `payroll.view_own`).
Which records a coach reaches is decided by `apps.academy.access` from the signed-in
coach, never from an id in the request:

| Data | Coach sees |
| --- | --- |
| Sessions, rosters, attendance | Their current classes' sessions; a substitute session only while the authorization is open (`roster_sessions_for`). Others → 404 |
| Students | Roster level only: name, Chinese name, student no., gender, age, status. No medical notes, emergency contacts, IC, address, contact details, family or guardians |
| Competition entries | Entries of students they coach: student, event, status, result. **Not** the family's form answers, notes, fee, fee status or invoice (removed from the response for coach-only viewers in 6C) |
| Finance, families, parents, payment proofs, payment information, receipts, refunds | Nothing (403) |
| Payroll | Only their own finalized payslips (`payroll.view_own`, unchanged); no payroll runs, no other coach's payslips. Not shown in the Coach Portal (payroll screens are Phase 6I) |
| Competition form configuration | Nothing (403) |

Coaches cannot manage substitutes, cancel or reschedule sessions, register or withdraw
competition entries, or correct attendance after the 48-hour window.
