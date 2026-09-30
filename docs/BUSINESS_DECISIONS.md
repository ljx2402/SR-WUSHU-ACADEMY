# Finalized business decisions

Approved rules that later phases must follow. Items marked *(later phase)* are **not implemented
yet**; the current code may still behave differently until that phase lands.

1. **Invoice grouping** *(implemented in Phase 1: `Family`, `Invoice`)*: one family receives one invoice covering all of their children,
   e.g. `INV-2026-000123` with lines for Student A RM220, Student B RM280, Student C RM50 =
   RM550. Invoices are academy billing documents associated with the students. There is **no
   "Bill To Parent"** field and **no billing-contact** requirement or fallback logic.
2. **Receipts** are issued for the student(s)/charges they pay, not "billed to a parent".
   *(Implemented in Phase 1: receipts list student, invoice and amount per line; the old unused
   `Guardianship.is_billing_contact` flag was removed.)*
3. **Payment collection**: `ADMIN` may record payments (cash, bank transfer, other approved
   methods); depositing cash to the academy account afterwards is an operational process.
   `FINANCE_ADMIN` has full finance capabilities; `SUPER_ADMIN` has everything. *(Implemented in
   Phase 0 RBAC.)*
4. **Student accounts**: `STUDENT` is a real role for older (Elite/senior) students, linked
   one-to-one to a Student, limited to their own information. *(Foundation in Phase 0; student UI
   later.)*
5. **Attendance** *(implemented in Phase 3, see `ATTENDANCE_AND_SUBSTITUTES.md`)*: coaches may edit
   attendance until **48 hours after the session ends**; afterwards only an administrator correction
   with a reason. `UNMARKED` is neither Present nor Absent in the percentage and is shown separately.
6. **Competition payment** *(implemented in Phase 1; results only for confirmed registrations since Phase 4, see `COMPETITIONS.md`)*: the fee is paid **at registration**: registration submitted →
   fee generated → payment required immediately → paid → `CONFIRMED`. (Not "approve first, charge
   later".)
7. **Competition refunds** *(Phase 1: `Refund` + `record_exceptional_refund`, never automatic; P7 builds the workflow)*: paid registrations are generally **non-refundable**. `ADMIN`
   or `FINANCE_ADMIN` may authorize an exceptional refund with a reason; it is audited, keeps the
   original receipt and follows proper finance records.
8. **Coach payroll** *(implemented in Phase 4, see `PAYROLL.md`)*: **all coaches are paid per training session**; there is no monthly
   salary model and no proration. Only completed eligible sessions are paid. Regular and substitute
   rates may differ (e.g. 18 × RM80 + 5 × RM120 = RM2,040).
9. **Payroll approval**: `FINANCE_ADMIN` prepares/calculates; `SUPER_ADMIN` finalizes.
   *(Implemented in Phase 0: `payroll.finalize` is super admin only.)*
10. **Audit masking** *(P10)*: IC numbers (`******1234`), bank account numbers (`******7890`),
    medical details (`[CHANGED]`) and other highly sensitive data must be masked in audit logs.
11. **Stack**: stay on Django + Django REST Framework + PostgreSQL. Front-end / mobile apps come
    later against the API. Hosting provider not decided yet.

**Phase 2 (admin guardrails)** added no new business rules. It makes the Django admin enforce the
rules above in the same way as the API. See the "Django admin" section of `ROLES_AND_PERMISSIONS.md`
and "Protection of financial history" in `FINANCE_ARCHITECTURE.md`. (Phase 3 closed the coach-slot and attendance admin paths; Phase 4 closed session
date/time/class editing and results for unconfirmed registrations.)

**Phase 3 (substitute coach + attendance)** implements decision 5 and these rules, documented in
`ATTENDANCE_AND_SUBSTITUTES.md`:
* A substitute is a time-limited authorization for **one session**, with an explicit lifecycle
  (`ASSIGNED` → `REVOKED` or `CANCELLED`, both final). There is at most **one active substitute per
  session**. Revoking needs a reason. Cancelling a session cancels its substitute. History is never
  deleted or reactivated.
* A substitute could only be authorized while the session's access window was open. Phase 4
  tightened this: only before the session **ends**.
* The 48-hour window ends exactly 48 hours after the session's end time in Malaysia time. At that
  moment it is closed. After it, even a first-time entry is an administrator correction and needs a
  reason.
* Only students on the session's expected roster can be marked. Unmarked students are reported
  separately and are left out of the percentage.

**Phase 4 (session lifecycle, payroll, competition integrity)**, documented in
`SESSION_LIFECYCLE.md`, `PAYROLL.md` and `COMPETITIONS.md`:
* A session is upcoming, in progress, completed (all derived from its times in Malaysia time) or
  cancelled. Legacy stored "completed" values became "scheduled".
* Date, time and class change only for an upcoming session with no attendance, substitute or
  payroll history, through the reschedule service with a reason. A regular coach can be reassigned
  only before the session starts.
* A finalized payroll month freezes its sessions.
* Attendance can be recorded from the session start, never before.
* Payroll is per session only: explicit rate rules, and a missing rate blocks finalization. A
  period can be finalized only after it has ended, and only if nothing changed since it was
  calculated. Finalized payroll is immutable.
* A competition result needs a confirmed (paid) registration, and a registration with a result
  cannot be withdrawn.
