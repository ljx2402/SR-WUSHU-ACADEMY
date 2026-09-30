# Session lifecycle (Phase 4)

## One rule for "where is this session"

The only stored state is `status`: `SCHEDULED` or `CANCELLED`. Everything else is derived from the
session's date and times in the academy time zone (`Asia/Kuala_Lumpur`), so it can never disagree
with the clock (`TrainingSession.phase()`, API field `phase`):

| Phase | When |
|---|---|
| `UPCOMING` | now < start |
| `IN_PROGRESS` | start ≤ now < end |
| `COMPLETED` | now ≥ end |
| `CANCELLED` | status is `CANCELLED` (whatever the time) |

There is no manual "completed" status any more. The Phase 4 migration turned legacy `COMPLETED`
rows into `SCHEDULED`; a past session is completed by definition, so no information was lost.

## What can change, and how

| Change | How | Allowed when |
|---|---|---|
| Venue, notes | Admin form / API `PATCH` | Always |
| Cancel | `cancel_session` (reason); API `POST …/cancel/`; admin "Cancel session"; also a status change on the admin form or API `PATCH` | The session's payroll month is not finalized |
| Reinstate | `reinstate_session` (reason); API `POST …/reinstate/`; admin "Reinstate" | Same |
| Date, times, class | `reschedule_session` (reason); API `POST …/reschedule/`; admin "Reschedule" | Only an **upcoming** session with **no attendance, no substitute history and no payroll lines**; never into the past, never onto another session's slot, never into or out of a finalized payroll month |
| Regular coach | `reassign_regular_coach` (reason); API `POST …/reassign-coach/`; admin "Reassign coach" | Only before the session starts, and only while the coach's slot is still `ASSIGNED` (not replaced by a substitute) |

* On the API `PATCH` and the admin change form, class, date and times are read-only. A crafted
  request is refused, because `TrainingSession.save()` enforces the same rules on every path
  (API, admin, services, shell).
* Moving a session to another class or date re-derives its regular coaches from the new class.
* For an absence on the day, use a substitute authorization, not a reassignment. That keeps the
  original coach's identity on the session for history and payroll.
* The regular coach slots of a session that has started cannot be deleted or moved to another
  coach.
* A finalized payroll month is closed. Its sessions cannot be added, moved, cancelled, reinstated
  or deleted. This is enforced by the model and, on PostgreSQL, by the `sr_session_payroll_guard`
  trigger. `generate_sessions` skips finalized months.
* All changes are audited: actor, before/after values, and the reason for service operations.

## Cancellation

A cancelled session:
* keeps all its records;
* its active substitute authorization becomes `CANCELLED` (Phase 3), so access stops;
* no attendance can be recorded;
* existing attendance is not counted (summaries and reports skip cancelled sessions);
* payroll does not pay it.

Reinstating does not reactivate the cancelled substitute.

## Attendance timing

Attendance can be recorded from the session **start** (inclusive), by anyone. Before the start,
the session state is `NOT_STARTED` and every write is refused, including administrators'. From the
start until 48 hours after the end, coaches record; after that it is `LOCKED` (administrator
correction with a reason; see `ATTENDANCE_AND_SUBSTITUTES.md`).

## Substitute timing

A substitute can be authorized only for a scheduled session that has **not ended** (upcoming or
in progress). Their access window stays as in Phase 3: by default from 24 hours before the start
until 24 hours after the end. A substitution for a session in a finalized payroll month cannot be
revoked, because it has been paid.
