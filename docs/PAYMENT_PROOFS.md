# Payment proofs and the academy's payment information

There is no online payment gateway. Families pay the academy manually (bank transfer, DuitNow
QR, cash, ...) and upload **payment proof**. Academy staff check the proof and record the
actual payment, which issues the **official receipt**.

## Two different documents

| | Payment proof | Official receipt |
| --- | --- | --- |
| What | The parent's evidence: "I have paid" (transfer screenshot, bank confirmation, ATM slip) | The academy's confirmation that it recorded the payment |
| Created by | PARENT (upload) | ADMIN / FINANCE_ADMIN / SUPER_ADMIN, by recording a payment (`POST /api/payments/`) |
| Model | `finance.PaymentProof` (new) | `finance.Receipt` (unchanged, immutable) |
| Changes money? | **Never**, not even when accepted | Yes: the recorded payment updates the invoice and may confirm a competition entry |

## Workflow

```
Invoice (or competition registration → competition invoice)
  → parent sees the amount due and the academy's payment information
  → parent pays manually
  → parent uploads payment proof            PaymentProof = PENDING_REVIEW
  → staff review:  accept                    ACCEPTED   (no money recorded)
                   reject (reason required)   REJECTED   (the invoice stays unpaid)
  → staff record the payment with the existing operation (POST /api/payments/ or the admin page)
  → official receipt issued; invoice becomes PARTIALLY_PAID / PAID (existing finance rules)
  → a fully paid competition invoice confirms the registration (existing competition rule)
```

Accepting a proof may optionally be linked to the payment staff recorded (`payment` in the
accept request; same family, valid payment only), but it never creates one. There is no second
payment system.

* A partially paid competition invoice leaves the registration PENDING ("Awaiting payment").
* A rejected proof leaves the invoice and the registration exactly as they were; the parent
  can upload another proof. Nothing is cancelled automatically.
* Several proofs per invoice are kept (history is never overwritten). At most 5 may wait for
  review at once per invoice.
* Proofs can only be uploaded for the family's own invoices that are open for payment
  (ISSUED or PARTIALLY_PAID).

## What parents cannot do (enforced by the API)

Mark anything paid, record or change a payment, issue or change a receipt, change an invoice's
status, confirm a competition registration, accept or reject a proof (their own included),
edit or delete a proof, change the payment information, or see another family's proofs.
Proofs are evidence: the model refuses every edit after upload except the one-time review,
and refuses deletion (including bulk deletes).

## API

| Endpoint | Who | Notes |
| --- | --- | --- |
| `GET /api/payment-info/` | parents (`finance.view_own_children`), finance staff | Bank name, account name/number, instructions, reference instructions, QR code as a `data:` URI (so it displays under the Content-Security-Policy). `updated_by_name` only for managers |
| `PATCH /api/payment-info/` (multipart or JSON) | `finance.payment_info.manage` (FINANCE_ADMIN, SUPER_ADMIN) | `qr_code` upload (PNG/JPEG, 1 MB), `remove_qr_code` |
| `POST /api/payment-proofs/` (multipart) | `finance.proofs.upload_own` (PARENT) | `invoice`, `file`, optional `amount_claimed`, `payment_date`, `reference`, `note`. Another family's (or unknown) invoice → **404**, checked before anything else |
| `GET /api/payment-proofs/?invoice=&family=&status=` | parents (own families), `finance.proofs.review` (all) | No storage path is ever returned; `sha256` and `reviewed_by_name` only for reviewers |
| `GET /api/payment-proofs/:id/file/` | same scope as above | Download (`Content-Disposition: attachment`, `nosniff`, `CSP: default-src 'none'; sandbox`, `Cache-Control: private, no-store`) |
| `POST /api/payment-proofs/:id/accept/` `{note?, payment?}` | `finance.proofs.review` (ADMIN, FINANCE_ADMIN, SUPER_ADMIN) | Pending proofs only |
| `POST /api/payment-proofs/:id/reject/` `{reason}` | `finance.proofs.review` | Reason required; pending proofs only |

Coaches and students have none of these capabilities (403). Staff review proofs; they do not
upload them on a family's behalf. In the Django admin, reviewers can list proofs and download
files (permission-checked); finance can edit the payment information (the QR upload is
validated and the change goes through the same audited service). The full staff review
screens are part of Phase 6F.

## File security

* Types: PDF, JPG/JPEG, PNG only (QR: PNG, JPG). The **file's first bytes must match** the type
  its extension claims: renamed executables, HTML or SVG are refused. No SVG (it can carry
  script).
* Size: 5 MB per proof (`PAYMENT_PROOF_MAX_BYTES`), 1 MB per QR (`PAYMENT_QR_MAX_BYTES`).
* Names: stored as `payment-proofs/YYYY/MM/<random 32 hex>.<ext>`; the user's file name is only
  kept as a sanitized label for downloads. A SHA-256 checksum is stored (reviewers can spot
  the same file uploaded twice).
* Storage: the Django storage alias **`private`** (`settings.STORAGES`), a directory
  (`PRIVATE_MEDIA_ROOT`, default `private_media/`, git-ignored) that is **never served by the
  web server**. Files are only returned by the permission-checked download view. There are no
  public URLs.
* If the database write fails after the file was saved, the file is removed.

### External storage later (e.g. Google Drive)

The code only uses the `private` storage alias through a lazy proxy
(`apps.finance.uploads.private_storage`). An external store can be added later as a Django
storage backend and selected with `PRIVATE_STORAGE_BACKEND` (and its options) without code
changes to models, services or views. No Google Drive integration, credentials or URLs exist
now.

## Audit

Every upload, acceptance and rejection is written to the audit log (category Finance) with the
actor, time, IP and user agent: "Payment proof uploaded", "Payment proof accepted: <note>",
"Payment proof rejected: <reason>". Payments and receipts are audited as before. The file, its
storage path and checksum are excluded from the audit log; file contents are never stored
there. Changes to the payment information are audited ("Academy payment information updated").

## Parent Portal

On an open invoice: amount due, the academy's bank details (account number with a Copy
button), the payment reference (the invoice number), the QR code and instructions, then the
upload form (file, payment date, amount paid, bank reference, note). After upload: "Your
payment proof has been submitted and is waiting for academy verification." The proof history
shows each proof's status (Pending review / Checked by the academy / Rejected with the reason).
The invoice's own status only changes when the academy records the payment. Family finance has
a "Payment proofs" tab listing all of them. See `docs/FRONTEND.md`.

## Tests

`apps/api/tests/test_payment_proofs.py` (21 tests, direct API requests): upload and view own
proof with nothing financial changing; download headers; repeated uploads kept and bounded;
open invoices only; future dates refused; accepted types; executables, HTML, SVG, disguised
and empty files refused; size limit; generated storage name and sanitized label; upload to
another family's invoice (404, even with a bad file), unknown and malformed ids; another
family's proof and file (404) and swapped filters; parent attempts to accept, reject, edit,
delete, create/void/edit payments, create/edit receipts, issue/void invoices, change the
payment information (all refused); model-level immutability; staff listing with reviewer
details; rejection requires a reason and happens once; acceptance records no payment and only
links a same-family payment; recording the payment is what pays the invoice and issues the
receipt; coach and student refused everywhere; finance cannot upload; super admin can review;
audit entries without file data; competition flow (rejected proof → still pending, accepted
proof → still pending, partial payment → still pending, full payment → CONFIRMED with receipt,
history kept); payment information read by parents, managed by finance only, QR validated.
