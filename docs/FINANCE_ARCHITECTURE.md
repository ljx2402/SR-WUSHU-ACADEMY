# Finance architecture (Phase 1)

```
Charge ──(snapshot)──▶ InvoiceItem ─┐
 (owed by one student)              ├─▶ Invoice (one family, many students)
                                    │
Payment ─▶ PaymentAllocation ───────┘   (which invoice line each sen paid)
   │
   └─▶ Receipt (immutable proof of the payment)      Refund (exceptional, separate record)
```

| Concept | Model | Meaning |
|---|---|---|
| Charge | `Charge` | An amount a **student** owes (monthly fee, uniform, competition fee...). Its amount is a copy, never recalculated from current fee settings. |
| Invoice | `Invoice` + `InvoiceItem` | The billing document for one **family**. Each line is a frozen copy of one charge (student no. and name, description, quantity, unit price, discount, amount). |
| Payment | `Payment` | Money actually received for one family's invoices. Recording it is not a bank deposit: an admin may take cash and bank it later. |
| Allocation | `PaymentAllocation` | How much of a payment went to which invoice line, and so to which student. The allocations always add up to the payment. |
| Receipt | `Receipt` | Created only when a payment is recorded. Its content is frozen; a mistake is fixed by voiding (`ReceiptVoid`), never by editing. |
| Refund | `Refund` | Exceptional money returned against one payment line, with a reason. It is never automatic. |

## Families: how siblings are grouped

`Family` is a household. Every student belongs to exactly one (`Student.family`).

* Membership is set **explicitly by staff**: on the student page (Family field) or via
  `PATCH /api/students/{id}/` with `family`.
* It is **never inferred**. Two children of the same parent may live in different households
  (for example, separated parents), so sharing a guardian does not merge families.
* A student saved without a family gets their own new single-student family. This is the only
  automatic behaviour, and it never merges anyone. The Phase 1 migration applied the same rule to
  existing students, so staff now group siblings by moving them into one family.
* There is **no bill-to parent or billing contact**. The old unused `Guardianship.is_billing_contact`
  flag was removed. Invoices and receipts are issued for the students; a payment records an
  optional "paid by (as stated)" reference only.

Who sees a family's invoices: finance staff and admins all of them. A parent sees the families
that their own children belong to, and sees the whole family invoice (every sibling's line). Drafts
are staff-only. Coaching a student never gives finance access. Student logins have no finance
access yet.

## Invoice lifecycle

```
DRAFT ──issue──▶ ISSUED ──payment──▶ PARTIALLY_PAID ──payment──▶ PAID
  │                 │                   ▲    │                     │
  └──void──▶ VOID ◀─┘ (only if unpaid)  └────┴── payment voided ◀──┘
```

* **DRAFT**: has no number, and staff can review it. It is created per family from uninvoiced
  unpaid charges ("Generate draft family invoices", `generate_invoices`, `POST
  /api/invoices/generate-drafts/`) or from selected charges.
* **ISSUED**: gets the next number (`INV-2026-000123`) and freezes the family name and all lines.
  From then on the invoice and its lines cannot be edited or deleted, and its charges are locked.
* **PARTIALLY_PAID / PAID**: driven only by payments. They move back only when a payment is voided.
* **VOID**: allowed for a draft or an unpaid issued invoice, with a reason. The invoice stays on
  record with its number, and its charges become free to invoice again. A paid invoice cannot be
  voided: void its payments first.

Allowed transitions are enforced on the model (`Invoice.TRANSITIONS`). Database check constraints
also guarantee `total = subtotal − discount`, `0 ≤ amount_paid ≤ total`,
`balance_due = total − amount_paid`, that drafts have no number and issued invoices do, and that a
charge is on at most one active invoice.

## Payments and concurrency

`record_payment(allocations, method, actor, amount=..., ...)` takes `(invoice, amount)` pairs,
all for **one family**:

1. It locks the invoices with `SELECT … FOR UPDATE` (ascending id), then re-reads their balances.
2. It refuses: invoices that are not ISSUED or PARTIALLY_PAID, amounts above the balance,
   unallocated remainders (overpayment and credit are not supported), zero, negative or sub-sen
   amounts, floats, naive datetimes, unknown methods, and the same invoice twice.
3. It spreads each amount over that invoice's open lines in line order, under line locks.
4. It creates the payment (`PAY-2026-…`), the allocations and the receipt (`SRWA-2026-…`), then
   updates the stored line, invoice and charge balances.
5. It sends `charge_settled` for every charge that became fully paid.

An optional idempotency key (`Idempotency-Key` header) makes a retried or double-tapped request
return the original payment. It is checked again after the lock is taken, so even two identical
simultaneous requests record only one payment.

`void_payment` (finance only) marks the payment VOIDED, voids the receipt, re-opens the invoices
and sends `charge_unsettled`. Nothing is edited or deleted.

Proven on PostgreSQL 16 by `apps/finance/tests/test_concurrency.py`:
* Two admins paying the same invoice at the same moment: exactly one succeeds.
* Five parallel partial payments: they never exceed the balance.
* Eight invoices issued in parallel: numbers stay gapless.
* The same charge invoiced twice at once: only one invoice gets it.

With the row locks removed, those tests fail, and the database check constraints still refuse the
overpayment.

## Document numbers

`DocumentSequence(doc_type, year)` is incremented under a row lock inside the caller's
transaction, so numbers are unique and gapless and restart each year:

| Document | Format |
|---|---|
| Invoice | `INV-2026-000001` |
| Payment | `PAY-2026-000001` |
| Receipt | `SRWA-2026-000001` (existing receipt convention, continued) |
| Refund | `RFD-2026-000001` |

Prefixes are set in `settings.ACADEMY["DOCUMENT_PREFIXES"]`.

## Money

MYR with 2 decimal places, `NUMERIC(10,2)`, maximum RM 99,999,999.99 (`apps/finance/money.py`).
* Entered amounts must be exact to the sen: RM 1.005 is rejected.
* Computed amounts (quantity × price, percentage discounts) are rounded **half-up** once.
* Floats are refused.

## Competition fees are paid at registration

1. `register()` checks eligibility, then creates the COMPETITION charge and an **issued
   COMPETITION invoice due today** for the student's family. The registration is `PENDING`
   ("Awaiting payment"). This applies even when staff register.
2. Payment is recorded against that invoice.
3. When the charge is fully paid, `charge_settled` moves the registration to **CONFIRMED** in the
   same transaction.
4. A partial payment does not confirm. Manual confirm (`services.confirm`, API `…/confirm/`, admin
   action) refuses unpaid, withdrawn or rejected registrations. Registration status is read-only in
   the admin.
5. If the payment is voided, the registration goes back to PENDING.
6. A free event (fee RM0) is confirmed immediately with no invoice.
7. Withdrawal:
   * **unpaid**: the competition invoice is voided and the charge cancelled;
   * **paid**: nothing financial happens. Fees are **non-refundable**, and no refund is ever
     created automatically.

**Exceptional refund:** `record_exceptional_refund` (ADMIN, FINANCE_ADMIN, SUPER_ADMIN) requires a
reason and is limited to what was paid on that line. It creates a numbered `Refund` and increases
the invoice's `amount_refunded`. The payment, allocation, invoice lines and receipt are untouched,
and so is the registration status. The refund is audited.

## Permissions (finance)

| Capability | SUPER_ADMIN | ADMIN | FINANCE_ADMIN | COACH | PARENT | STUDENT |
|---|---|---|---|---|---|---|
| View all charges / invoices / payments / receipts / refunds | ✅ | ✅ | ✅ | | | |
| View own families' invoices, payments, receipts; own children's charges | | | | | ✅ | |
| Fee setup (class fees, fee plans, price list) | ✅ | | ✅ | | | |
| Add / cancel charges, monthly billing | ✅ | | ✅ | | | |
| Draft / issue / void invoices | ✅ | | ✅ | | | |
| Record payments (receipt issued automatically) | ✅ | ✅ | ✅ | | | |
| Void payments | ✅ | | ✅ | | | |
| Exceptional refunds | ✅ | ✅ | ✅ | | | |
| Finance reports | ✅ | | ✅ | | | |
| Coach bank details | ✅ | | ✅ | | | |

## Audit

Charge, Invoice, InvoiceItem, Payment, PaymentAllocation and Refund are audited models: every
create and change is logged with actor, field changes and reason. Issuing, voiding, payment voids
and refunds carry their reason. Receipt issue and void are logged as events. Passwords and tokens
are never logged. Masking of IC, bank and medical values in audit entries is Phase 10.

## API

| Endpoint | Purpose |
|---|---|
| `/api/families/` | Households (staff manage; parents see their own) |
| `/api/invoices/` | List/retrieve. Staff with invoice rights: `POST` (family + charges → draft), `…/{id}/issue/`, `…/{id}/void/` (reason), `generate-drafts/` |
| `/api/payments/` | List/retrieve. `POST` with `amount`, `method`, `allocations: [{invoice, amount}]`, optional `received_at`, `reference`, `payer_name`, `Idempotency-Key` header. `…/{id}/void/`, `…/{id}/refund/` |
| `/api/receipts/`, `/receipts/{id}/` | Receipt content / printable page |
| `/invoices/{id}/` | Printable invoice |
| `/api/refunds/` | Refund records |
| `/api/reports/invoices/`, `/api/reports/refunds/` | Finance reports |
