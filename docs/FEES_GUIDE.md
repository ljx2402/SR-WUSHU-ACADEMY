# How to key in fees

All fee setup is done in the admin site (`/admin/`) under **Fees, payments and receipts**.
Amounts are in MYR (RM).

## 1. Class fees (tuition)

Each class has its own fee. Go to **Class fees → Add** and fill in:

| Field | What to enter |
|---|---|
| Training class | The class, e.g. *Elite Changquan* |
| Name | A label, e.g. `Monthly fee` |
| Fee type | `Tuition / class fee` |
| Billing cycle | `Per month` (most classes) or `Per session attended` (pay-per-class) |
| Amount | e.g. `120.00` |
| Is default | Ticked: this fee applies to every student in the class unless they have their own fee plan |
| Effective from | The first day this rate applies, e.g. `2026-01-01` |
| Effective to | Leave empty |

**Examples**

| Class | Name | Billing cycle | Amount |
|---|---|---|---|
| School – SJK(C) Taolu | Monthly fee | Per month | 60.00 |
| Additional – Sanda | Monthly fee | Per month | 120.00 |
| Elite – Changquan | Monthly fee | Per month | 250.00 |
| Additional – Taiji (drop-in) | Per session | Per session attended | 25.00 |

### Classes with several fee options

If one class has several fee options (e.g. 2×/week vs 3×/week), add one class fee per option
(`Monthly fee (2x/week)`, `Monthly fee (3x/week)`), tick **Is default** on the usual option only, and
give the other students a **Student fee plan** that points to their option (see 2).

### Changing a fee (price increase)

Do **not** edit the amount of a fee that has already been billed; the system will refuse, because
historical fees must be preserved. Instead:

1. Open the current fee and set **Effective to** to the last day of the old price (e.g. `2026-12-31`).
2. Add a new class fee with the same name, the new amount and **Effective from** `2027-01-01`.

Bills already issued keep the old amount; bills from January 2027 use the new one.

## 2. Student fee plans (discounts, scholarships, special rates)

**Student fee plans → Add**, pick the student's class membership (enrollment), then either:

* choose a different **Class fee** option for that student,
* enter a **Custom amount** (special rate), and/or
* enter a **Discount %** (e.g. `10` for a sibling discount).

Set **Start date** (and an end date if the arrangement is temporary) and a **Reason**.

## 3. Monthly billing

Once class fees are set up, bill the month in one step, either:

* admin API: `POST /api/charges/generate-monthly/` with `{"year": 2026, "month": 10}`, or
* command line / scheduled job: `python manage.py generate_monthly_fees --year 2026 --month 10`.

Every active student in every class gets one charge for the month. Per-session fees are billed by
the number of sessions attended (Present or Late) in that month, so run those after the month ends.
Running it again for the same month does not bill anyone twice.

## 4. Other charges: registration, uniform, weapons, competition, other

1. Set up the price list once in **Charge items**, e.g. `Registration fee – 100.00`,
   `Uniform (full set) – 150.00`, `Jian (straight sword) – 180.00`
   (`python manage.py setup_academy` creates the common items with price 0 for you to fill in).
2. To bill a student: **Charges → Add**, choose the student, the fee type
   (Registration / Uniform / Weapon / Competition / Other), the charge item (optional),
   a description, quantity, unit amount and any discount.

Competition fees are billed automatically when a student is registered for an event that has a fee.

## 5. Families: grouping siblings for one invoice

Each student belongs to a **family** (household). One family gets **one invoice** covering all of
its children. There is no "bill to parent": the invoice is for the students.

* A new student is automatically their own family.
* To put siblings together, open each sibling's student page and choose the same **Family**, or
  rename a family under **Families**.
* The system never groups students automatically just because they share a parent.

## 6. Invoices

1. Bill the charges for the month (section 3) and add any one-off charges (section 4).
2. **Invoices → Generate draft family invoices**. This creates one **draft** per family, listing
   every child's unpaid charges that are not yet on an invoice. Alternatively, select charges under
   **Charges** and use the action **Create draft invoices for selected charges**.
3. Check the drafts, then **Issue** them (the action on the list, or the button on an invoice). An
   issued invoice gets its number, e.g. `INV-2026-000123`, and can no longer be changed. Click
   **Print** to print it or save it as PDF.
4. If an unpaid invoice is wrong, **Void** it, giving a reason. The voided invoice stays on
   record, and its charges can be invoiced again.

Invoicing and voiding are done by finance staff. Admins can see all invoices.

## 7. Receiving payment and issuing receipts

**Payments → Add** (admins and finance staff):
1. Enter the amount received, the method (cash, bank transfer, DuitNow, FPX, card, cheque or
   e-wallet), the date and time, and any bank reference.
2. Choose the invoice(s) it pays and how much goes to each.
3. The amounts must add up to the amount received; overpayment is not accepted. One payment can
   pay several invoices of the same family, and can be a part payment.

An official receipt (e.g. `SRWA-2026-000123`) is issued automatically. It lists each student,
invoice and amount. Click **Print receipt** to print it.

Recording cash is not the same as banking it: an admin may record cash taken at the desk and
deposit it later.

Receipts cannot be edited or deleted. If a payment was entered wrongly, or a cheque bounces,
finance staff open the payment and click **Void payment & receipt**, give a reason, and enter the
payment again correctly. The voided receipt stays on record, marked VOID, and the invoice becomes
payable again.

**Competition fees** are paid when registering. Registration creates the fee and a competition
invoice due the same day, and the entry is confirmed automatically once that invoice is paid.
Competition fees are **non-refundable**. For an authorized exception, admins or finance staff use
**Exceptional refund** on the payment and must give a reason. The original receipt is kept, and
the refund is recorded separately.

## 8. Cancelling or waiving a charge

In **Charges**, select the charge and choose the **Cancel** or **Waive** action. Charges are never
deleted.
* A charge on an invoice must have that invoice voided first.
* A charge that already has payments must have those payments voided first.
