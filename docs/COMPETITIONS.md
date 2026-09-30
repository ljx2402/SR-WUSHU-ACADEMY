# Competitions (Phase 1 flow, Phase 4 integrity)

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
