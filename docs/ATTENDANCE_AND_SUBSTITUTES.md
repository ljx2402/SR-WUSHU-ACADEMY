# Attendance and substitute coaches (Phase 3)

## Substitute coach authorization

A substitute is a **temporary, session-level authorization**, stored as a `SessionCoach` row with
`role = SUBSTITUTE`. Being present at a session never grants access; only an active authorization
does.

| Question | Field |
|---|---|
| Original coach | `replaces` (optional: a substitute may also cover without replacing anyone) |
| Substitute | `coach` |
| Session (and so its class) | `session` |
| Authorized by / when / why | `assigned_by`, `authorized_at`, `reason` |
| Effective period | `access_starts_at` → `access_ends_at` (default: 24 h before the session starts until 24 h after it ends; `SUBSTITUTE_ACCESS_HOURS_BEFORE/AFTER`) |
| Status | `ASSIGNED` (active), `REVOKED`, `CANCELLED` |
| Revoked or cancelled by / when / why | `revoked_by`, `revoked_at`, `revocation_reason` |

### Lifecycle

```
ASSIGNED ──revoke_substitute (reason required)──▶ REVOKED    (final)
    └──────session cancelled (any path)─────────▶ CANCELLED  (final)
```

* **Authorize** (`academy.services.assign_substitute`; API `POST /api/sessions/{id}/assign-substitute/`;
  admin session page → "Assign substitute coach"). Needs `substitute.assign` (ADMIN, SUPER_ADMIN).
  It is refused when:
  * the session is cancelled or has already ended (Phase 4; see `SESSION_LIFECYCLE.md`);
  * the session already has an active substitute (**one active substitute per session**), including
    the same coach twice;
  * the coach already coaches the session, or is inactive;
  * `replaces` is not a regular coach currently assigned to the session.

  The replaced coach's slot becomes `REPLACED`, so they are not paid for that session.
* **Revoke** (`revoke_substitute`; API `POST …/revoke-substitute/` with `substitute` and `reason`;
  admin coach-slot page → "Revoke substitute"). Needs `substitute.revoke` and a reason. Access stops
  immediately. The row stays as history with its original access period. The replaced coach's slot
  goes back to `ASSIGNED`. A second revoke is refused.
* **Session cancelled.** Whenever a session's status becomes `CANCELLED` (API `PATCH`, admin form or
  service), its active substitute becomes `CANCELLED` with `revocation_reason = "Session cancelled"`
  and the replaced coach is restored. Re-opening the session does **not** reactivate it. Access checks
  also ignore cancelled sessions, so even a bulk status update that skips `save()` gives no access.
* **No reactivation, no editing, no deletion.** Authorizing the same coach again creates a new row.
  After authorization, its session, coach, replaced coach, period, time and reason are fixed.

Enforcement layers:
* **Service:** checks, plus a lock on the session row so simultaneous authorizations are handled one
  at a time.
* **Model:** `save()` / `delete()` guards.
* **Database constraints:**
  * `one_active_substitute_per_session`;
  * `unique_live_coach_per_session`;
  * status validity for regular and substitute rows;
  * ended rows must have `revoked_at`;
  * a valid access window.
* **PostgreSQL trigger:** `sr_sessioncoach_guard` refuses deleting, editing or reactivating a
  substitute row, even from raw SQL.

### What a substitute can see

Only while the authorization is `ASSIGNED`, the session is not cancelled and the time is inside the
access window:
* that one session;
* its roster, at the coach "ROSTER" detail level: training info only (no medical notes or emergency contacts);
  this is the same detail a regular coach gets, needed for safety;
* its attendance.

Never:
* the class itself, or its other sessions;
* other classes' students;
* families or parents' details;
* finance or payroll of others.

The same rules apply in the API, the services and the admin. Coaches cannot open the admin.

**COACH + PARENT.** Parent access (own children, their families' finance) and coach access (roster
of coached classes and the authorized session) are combined without widening each other:
* A parent's child's classmates stay hidden unless the user also coaches that class or session.
* Coaching never exposes a family's finance.

## Attendance

* **Expected roster.** A session's expected students are the class members on the session date
  (`TrainingSession.roster()`, from enrollment history). Only they can be marked:
  * enforced in the service, the model (`save()` on create) and the admin sheet;
  * the API and admin return an error for anyone else;
  * one record per session and student (database unique constraint).
* **Statuses:** `UNMARKED`, `PRESENT`, `LATE`, `ABSENT`, `EXCUSED` (database check constraint).
* **UNMARKED is never Absent.** An expected student with no record, or with an `UNMARKED` record, is
  unmarked. No rows are created in advance. The sheet (`GET /api/sessions/{id}/attendance/`, admin
  session → "Attendance") lists every expected student with their status.
* **Percentage** = attended ÷ (Present + Late + Absent) × 100, 2 decimal places, rounded half-up:
  * attended = Present + Late (`LATE_COUNTS_AS_PRESENT`);
  * Excused and Unmarked are left out;
  * no marked sessions gives no percentage (shown as "–");
  * e.g. 10 expected: 7 Present, 1 Absent, 2 Unmarked → **87.50%**, with "Unmarked: 2" shown
    alongside.

  Session summaries, student summaries (`…/attendance-summary/`) and the attendance report all give
  `expected`, `marked` and `unmarked` next to the counts. For student summaries and reports, only
  sessions that have started and were not cancelled are expected.

### Who may record, and until when

| When | Who | Reason |
|---|---|---|
| Until 48 h after the session ends (exclusive) | The class's regular coaches (including a coach currently replaced); the session's authorized substitute inside their access window; ADMIN / SUPER_ADMIN (`attendance.take_any`) | Only when changing a mark already recorded |
| From 48 h after the session ends | ADMIN / SUPER_ADMIN only (`attendance.correct`): an **administrator correction** | **Always** required, including first-time late entries |

* The deadline is `session end + ATTENDANCE_COACH_EDIT_HOURS` (48). It is computed in the academy
  time zone (`TIME_ZONE = Asia/Kuala_Lumpur`), never the server's or the request's time zone.
  Exactly at the deadline the window is closed.
* A substitute is limited by both windows: their access window (24 h after the session by default)
  usually closes first.
* FINANCE_ADMIN, parents and students cannot record attendance.
* Each write is audited: actor, previous and new value, time, and reason. Corrections are prefixed
  "Administrative correction after the 48-hour coach edit window: …".

### Session attendance state (derived, no separate submit step)

| State | Meaning |
|---|---|
| `OPEN` | Inside the coach window, some expected students unmarked |
| `COMPLETE` | Inside the coach window, every expected student marked (coaches may still correct with a reason) |
| `LOCKED` | Coach window closed: administrator corrections with a reason only |
| `CANCELLED` | Session cancelled: no attendance |

### Bulk and direct writes

* **Services:** all writes go through `attendance.services.record_session_attendance` (or
  `mark_attendance`, one student). A submission is all or nothing, under a lock on the session row;
  a student listed twice is refused.
* **API:** `/api/attendance/` is read-only.
* **Admin:** attendance records are read-only there. Recording uses the session "Attendance" sheet,
  correcting uses the record's "Correct attendance" page, both through the service. There are no
  bulk admin actions.
* **ORM:** `QuerySet.update()`, `delete()`, `bulk_create()` and `bulk_update()` on attendance are
  refused, and deleting a record is refused.
* **PostgreSQL trigger:** `sr_attendance_no_delete` blocks deletion from raw SQL.

## Payroll

Since Phase 4 the substitute authorization is the payroll source of truth (`PAYROLL.md`):
* an active (`ASSIGNED`) substitute is paid their substitute per-session rate;
* the replaced coach (`REPLACED`) is not paid for that session;
* a revoked or cancelled authorization is not paid, and the original coach is paid again unless
  the session was cancelled;
* each paid line keeps the substitute, the original coach, the session, the rate, the rule and the
  amount.

## Phase 4 timing changes

* Attendance can be recorded only from the session start (inclusive), never before, by anyone.
* A substitute can be authorized only before the session ends.

## Coach Portal (Phase 6C)

The Coach Portal (`/app/coach/...`, see `docs/FRONTEND.md`) takes attendance through the
same endpoints and services; it adds no rules of its own:

* `GET /api/sessions/coaching/` lists the sessions the caller works: the coach's current
  classes and open substitute sessions (`access.roster_sessions_for`), with the caller's
  role on each (`REGULAR` / `SUBSTITUTE`, slot status such as `ASSIGNED`, `REPLACED`) and
  the attendance state and counts from `attendance.services` (`session_state`,
  `session_summary`, `coach_edit_deadline`). Filters: `date`, `start`, `end`, `class`,
  `status` (`SCHEDULED` / `CANCELLED`), `order=asc`. Staff see every session; parents and
  students get 403.
* The sheet (`GET /api/sessions/:id/attendance/`) and the all-or-nothing submission
  (`POST`, `{records: [{student, status, remarks}], reason}`) are unchanged. The page sends
  only changed students, asks for a reason when a saved mark changes (the backend requires
  it), shows expected / marked / not marked counts separately, and shows the saved
  percentage exactly as the backend reports it (UNMARKED excluded).
* The 48-hour window is computed only by the backend (`coach_edit_deadline`); the page shows
  the state it reports (OPEN, COMPLETE, LOCKED, NOT_STARTED, CANCELLED) and a coach's late
  write is refused by the service (403) whatever the page shows.
* Session attendance remarks (`remarks`) are the existing per-student field: coaches of the
  session read and write them with the attendance; no second notes system exists.

## Student view (Phase 6D)

A student login sees only its own attendance, read only:

* `GET /api/students/me/attendance/` returns the summary from `attendance.services.student_summary`
  (the same calculation as `…/attendance-summary/`: UNMARKED reported separately and never in the
  percentage) and one row per session the student was expected at that has **started and was not
  cancelled** (`student_history`), with their status or `UNMARKED`. Future and cancelled sessions
  have no row, so they never read as absences. The page never recalculates the percentage.
* `GET /api/students/me/sessions/<id>/` gives `my_attendance`: their own status once the session
  has started, `null` before it starts or when it is cancelled.
* Coach remarks and who recorded a mark are not sent to student-only viewers (also on
  `GET /api/attendance/`).
* Students cannot record, change or correct attendance, read attendance sheets or change history
  (403), and other students' records are 404.

