# Security (Phase 5)

This document records the Phase 5 security audit, the threat model, and the controls now in
place. Deployment settings: `DEPLOYMENT.md`. Backups: `BACKUP_AND_RECOVERY.md`.

**Status:** hardened and tested, but **not yet cleared for public deployment**. See "Production
blockers" at the end.

## 1. Audit findings (before Phase 5) and what was done

| # | Area | Finding | Severity | Status |
|---|---|---|---|---|
| F1 | Settings | Fallback `SECRET_KEY` hard-coded; `DEBUG` defaulted to on; no HTTPS, HSTS, secure-cookie, referrer or CSP settings. 5 `check --deploy` warnings | High | Fixed: `DJANGO_ENV=production` refuses to start with an unsafe configuration; `check --deploy` is clean |
| F2 | Authentication | API tokens never expired and survived password changes and deactivation. Sign-in always returned the same token | High | Fixed: 14-day expiry, rotation on every sign-in, revoked on password / role / active change, logout endpoint |
| F3 | Brute force | No limit on admin or API sign-in attempts | High | Fixed: database-backed lockout (per username and per IP) plus a DRF throttle on the login endpoint |
| F4 | Audit log | Snapshots copied every field in the clear: IC / passport numbers, bank account, EPF / SOCSO, full medical notes | High | Fixed: masked on write, existing entries redacted once (migration `audit/0003`) |
| F5 | Audit log | Bulk `QuerySet.update/delete` could rewrite or delete entries; no database protection | High | Fixed: queryset guards and a PostgreSQL append-only trigger |
| F6 | Audit log | No IP / user agent; sign-in, sign-out and password events not recorded | Medium | Fixed: IP and user agent on every entry; security events (category SECURITY, super admin only) |
| F7 | IDOR | Receipt / invoice print pages answered 403 for another family's document and 404 for a missing one, revealing which ids exist | Medium | Fixed: 404 for both |
| F8 | IDOR | A parent registering someone else's child for a competition got "already registered" (uniqueness error) before the ownership check, revealing registration status | Medium | Fixed: ownership checked first; duplicates reported by the service |
| F9 | API | Non-numeric id filters (`?student=abc`, `?family=x`, `?class=`, `?competition=`) raised an uncaught `ValueError` (HTTP 500) | Low | Fixed: 400 |
| F10 | Data minimization | Finance staff (who may view parents) received parents' full IC numbers | Low | Fixed: masked to the last 4 characters unless the viewer manages parents, or is that parent |
| F11 | Concurrency | Two simultaneous sign-ins for one user could fail with a duplicate-token error (found while adding rotation) | Low | Fixed: user row locked during rotation; concurrent test |
| F12 | Browser | Receipt / invoice print pages used inline `onclick` (incompatible with a strict CSP) | Low | Fixed: static script; CSP verified in headless Chromium with no violations on 14 admin and print pages |
| F13 | Logging | No logging configuration: no guarantee secrets stay out of logs; SQL could be logged at DEBUG | Low | Fixed: redaction filter, SQL logger pinned to WARNING |
| F14 | API | Browsable HTML API enabled in all environments | Low | Fixed: JSON only in production |
| — | Secrets in git | None found. `.env` and `db.sqlite3` are git-ignored. `.env.example` and `docker-compose.yml` hold only local development credentials, labelled as such | — | OK |
| — | File uploads | None: no FileField/ImageField, no `request.FILES`. Request size limits are explicit | — | OK (N/A). **Update (payment proofs):** parents now upload payment proofs; see `docs/PAYMENT_PROOFS.md` (type + content checks, size limits, generated names, private storage, permission-checked downloads, audit) |
| — | CORS | Not installed: browsers cannot call the API cross-origin. A separate web front end must be same-origin or add CORS deliberately later | — | OK |
| — | Dependencies | `pip-audit`: no known vulnerabilities. Django 5.2.17 is the newest 5.2 LTS patch; DRF, psycopg, dj-database-url, sqlparse and asgiref are at their latest releases | — | OK, unchanged |

Already sound before Phase 5, and verified again by tests:
* capability RBAC (`capabilities.py`), used by API, services and admin;
* record scoping (`access.py`, `finance/access.py`), with 404 for hidden records;
* admin guardrails and financial immutability (Phase 2 triggers);
* substitute lifecycle (Phase 3);
* session, payroll and competition integrity (Phase 4);
* CSRF on session-authenticated writes;
* sessions invalidated on role change (`auth_version`).

## 2. Threat model

| Threat | Asset | Attacker / path | Protection | Remaining risk → mitigation |
|---|---|---|---|---|
| 1 Unauthenticated attacker | All data | Calls the API or admin without credentials | Every API view requires authentication (401). Admin redirects to login. Print pages need a login. Anonymous API calls throttled 60/min | Internet-exposed login page → lockout and throttle (below). A WAF or reverse-proxy rate limit is recommended |
| 2 Parent | Other families' children, invoices, payments, receipts, registrations, attendance | Changes ids in URLs, filters and POST bodies | Record scoping returns 404 (tested for 12 endpoints and 3 filters). Ownership is checked before validation | — |
| 3 Student | Other students, own family's private data | Changes ids, query parameters, nested URLs | Own record only, derived from `StudentAccount` + role (never from an id); other students' records 404; no finance, family, parent, roster, attendance-taking, registration or payslip capability (403). Own record at the SELF level only (no IC, contacts, guardians, medical, family) and no family finance or registration answers (Phase 6D, tested) | — |
| 4 Coach | Other coaches' classes, finance, bank details | Changes session / student / attendance ids | Assigned classes only (404). No finance capability (403). No bank fields | No medical notes or emergency contacts (roster serializer omits them for anyone without `students.view_all`) |
| 5 ADMIN | Payroll, bank details, role management, payment voids | Direct API or admin requests | Capabilities: 403 on payroll, roles, voids, bank details (tested). Staff Portal (6E) changes go through the existing services (reasons, lifecycle, audit); student lists are minimal rows, the full record only in the detail view; operations responses carry no finance or bank fields (tested) | Can record payments and exceptional refunds by design: these need a reason and are audited. Reading a student's full record is not itself audited (there is no read-audit mechanism) |
| 6 FINANCE_ADMIN | Academic records, attendance, roles, payroll finalization | Direct requests | Student directory only; no attendance; `payroll.finalize` is super admin only (tested). In the Staff Portal (6E): the directory only; operations dashboard, classes, programs, sessions, attendance and every management action are 403 (tested) | — |
| 7 SUPER_ADMIN | Everything | Account takeover or misuse | Everything audited (security events super admin only). The last super admin cannot be removed | No two-person rule or 2FA → **production blocker P2** |
| 8 Compromised staff account | Data within that role | Stolen password or session | Lockout, 12-hour sessions, 14-day tokens, revocation on password or role change, audit with IP | No 2FA (P2). Password reset is manual (super admin) |
| 9 Malicious API request | Business rules | Crafted JSON, mass assignment, bad ids | Services enforce every rule. Read-only serializer fields. No update/delete endpoints for financial history. Bad ids give 400 (tested) | — |
| 10 Malicious admin request | Business rules | Forged POST, bulk actions, guessed URLs | Admin actions go through the same services. Read-only forms. 403 / 302 for out-of-role URLs (tested for 5 roles) | — |
| 11 Horizontal escalation | Peer data | See 2–4 | Record scoping | — |
| 12 Vertical escalation | Staff actions | Lower role calls staff endpoints | 403 on 10 staff endpoints for parent, student and coach (tested). Roles can't be forged in the admin form | — |
| 13 IDOR | Any object | Id enumeration | 404 for hidden and missing records alike (print pages fixed, F7). No existence oracle in registration (F8) | Sequential ids remain visible: acceptable because every read is scoped |
| 14 CSRF | Session users | Cross-site form post | Django CSRF middleware (tested in Phase 2), SameSite=Lax cookies, `__Host-` cookie names in production | — |
| 15 Session / token theft | Accounts | Network sniffing, XSS, stolen device | HTTPS only (redirect and HSTS), Secure + HttpOnly cookies, CSP `script-src 'self'` (inline script injection blocked; verified in a browser), token expiry and rotation, logout | Tokens are stored unhashed in the database (DRF default) → P4 |
| 16 Brute-force login | Accounts | Password guessing | 5 failures per username or 20 per IP in 15 minutes locks sign-in, without checking the password. Same answer for every failure. DRF login throttle 20/min per IP | Distributed guessing below per-IP limits → reverse-proxy limits; P2 |
| 17 Sensitive data leakage | IC, bank, medical, credentials | API over-exposure, audit log, logs, errors | Role-based serializers. Masking in the audit log (F4). Log redaction. No debug pages in production (tested) | — |
| 18 Financial manipulation | Invoices, payments, receipts, refunds | Edit history, over-refund, cross-family allocation | Phase 1–2 services, checks, triggers (tested again: allocation tampering, over-refund, void a paid invoice, PUT/PATCH/DELETE) | — |
| 19 Attendance manipulation | Attendance | Late edits, other classes, future dates, revoked substitute | 48-hour window, reasons, start boundary, roster, audit (tested again via API) | — |
| 20 Payroll manipulation | Pay | Edit finalized pay, finance finalizing, rewrite rates | Phase 4 lifecycle, finalize = super admin, triggers (tested again) | — |
| 21 Competition payment manipulation | Registrations | Confirm unpaid, set status, results | Services, read-only status, results only for confirmed (tested again) | — |
| 22 Audit-log tampering | Audit trail | Edit or delete entries | No change or delete through model, queryset, admin or API. PostgreSQL trigger (tested with raw SQL) | A database superuser can still disable triggers → P3 |

## 3. Controls now in place

### Authentication
* **Passwords:** Django PBKDF2-SHA256 (Django 5.2 default iterations). Validators: similarity,
  minimum 10 characters, common passwords, all-numeric.
* **Sessions (admin):** 12 hours (`DJANGO_SESSION_COOKIE_AGE`). The session hash covers the
  password and `auth_version`, so a password change, role change or deactivation logs the user
  out everywhere. Inactive users cannot authenticate or keep a session.
* **API tokens:**
  * `POST /api/auth/token/` issues a new token and revokes the user's previous one;
    `POST /api/auth/logout/` revokes it.
  * Tokens expire after `API_TOKEN_TTL_HOURS` (default 336 hours = 14 days).
  * They are deleted on role, password or active-status change.
  * Every refusal answers "Invalid or expired token."
* **Brute force:**
  * `LoginFailure` rows are stored in the database, so the limit works across worker processes.
    A username locks after 5 failures in 15 minutes, a client IP after 20; each limit is
    configurable.
  * While locked, the password is not checked, and the answer is identical to a wrong password,
    so it does not reveal whether an account exists.
  * A successful sign-in clears that username's failures.
  * `LoginFailure` rows are visible read-only to super admin and pruned after 30 days.
* **API throttling (DRF):** anonymous 60/min, authenticated 3,000/min, login 20/min per client.
  All configurable. Counters live in the process-local cache; see `DEPLOYMENT.md`.
* **Security events:** written to the audit log with category SECURITY (super admin only), with
  IP and user agent. Events: sign-in, sign-out, API token issued, API logout, password changed,
  account deactivated or reactivated, role changed.

### Sensitive data
| Field | Who sees it in full |
|---|---|
| Student IC, address, phone, email, DOB | Staff with `students.view_all` (ADMIN, SUPER_ADMIN); the student's own parents. Not the student's own login (basic profile only, Phase 6D) |
| Student medical notes | The above only (not the student's own login). Not coaches or substitutes: the note is general health information, not a coaching restriction, and no capability authorizes it for coaches (a separate training-restriction field with its own capability is deferred). Never the finance directory |
| Student emergency contacts (guardian name, relationship, phone) | Staff with `students.view_all` and the student's own parents; the finance directory sees guardian contacts. Not coaches or substitutes (no capability authorizes it) |
| Parent IC | Parent managers (ADMIN, SUPER_ADMIN) and the parent themselves; finance sees `**********1234` |
| Coach IC, address | Coach managers (admin); not exposed by the API at all |
| Coach bank, EPF, SOCSO | `coaches.bank_details` only (FINANCE_ADMIN, SUPER_ADMIN), including the payroll report (needed to pay coaches). Never ADMIN, coaches, parents or payslips |
| Audit log | Masked for everyone: IC/bank/EPF/SOCSO `********1234`; medical `[MEDICAL NOTE – n characters]`; passwords, tokens, keys `[SECRET]`. User passwords and tokens are never audited |

### HTTP / cookies (production)
* HTTPS redirect.
* HSTS for 1 year with `includeSubDomains` and `preload`. The preload directive only: nothing is
  submitted to browser preload lists.
* `Secure`, `HttpOnly` and `SameSite=Lax` cookies named `__Host-sessionid` and `__Host-csrftoken`.
* `X-Frame-Options: DENY` and `frame-ancestors 'none'`.
* `X-Content-Type-Options: nosniff`.
* `Referrer-Policy: same-origin`.
* `Cross-Origin-Opener-Policy: same-origin`.
* `Permissions-Policy` disabling camera, microphone, geolocation, payment and USB.
* **CSP:** `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'
  data:; object-src 'none'; base-uri 'self'; form-action 'self'`.

  **Intentional exception:** `style-src 'unsafe-inline'`, because the Django admin uses inline
  style attributes. Scripts are strict.

### Errors and logging
* **Production:**
  * `DEBUG` cannot be on, so there are no debug pages or tracebacks in responses.
  * DRF answers errors with short JSON messages.
  * Bad ids give 400, never 500.
* **Logs:**
  * go to the console (collected by the process manager);
  * a redaction filter removes `Token …` / `Bearer …` values, password, secret, key, session and
    CSRF values, and passwords in database URLs;
  * SQL is never logged;
  * Django's error reporter already hides settings named like KEY, SECRET, PASS and TOKEN.

### Database
Unchanged from Phases 1–4 and re-verified:
* **Transactions and row locks** (`select_for_update`) on payments, invoices, refunds, roles,
  substitutes, attendance, sessions and payroll; since Phase 5 also on token rotation.
* **Constraints:** CHECK and unique, including partial unique.
* **Triggers:**
  * `sr_*` financial history (Phase 2);
  * substitute history (Phase 3);
  * attendance delete (Phase 3);
  * payroll and finalized-month sessions (Phase 4);
  * competition results (Phase 4);
  * the audit log (Phase 5).
* **TLS:** `DATABASE_SSL_REQUIRE` (on in production) requires TLS to PostgreSQL.

## 4. Remaining limitations and production blockers

**Production blockers (must be done before real public deployment):**
* **P1: Hosting not set up and not tested.** This includes:
  * the TLS-terminating reverse proxy;
  * a WSGI server (e.g. gunicorn);
  * static files served from `STATIC_ROOT`;
  * running `check --deploy` in the real environment;
  * an HTTPS smoke test.

  None of this exists yet (hosting provider undecided).
* **P2: No two-factor authentication for staff (SUPER_ADMIN, FINANCE_ADMIN, ADMIN).** For a system
  holding IC numbers, medical notes, bank accounts and finance, staff 2FA (TOTP) should be added,
  or enforced at an SSO / reverse-proxy layer, before going live.
* **P3: Database roles and backups.** The application must connect with a role that does **not**
  own the tables or have superuser rights; otherwise it could drop the protective triggers. The
  backup and restore procedure must run and be restore-tested in the real environment. See
  `DEPLOYMENT.md` and `BACKUP_AND_RECOVERY.md`.

**Accepted limitations / later work:**
* **P4:** API tokens are stored unhashed (DRF default). The mitigations are expiry, rotation,
  revocation and database access control. Hashed tokens would need a token model change.
* No self-service password reset: a super admin sets passwords. A reset flow needs outgoing email,
  which is not configured.
* API throttle counters are per process (local memory cache). The login lockout is exact (database).
  For strict API rate limits use a shared cache or reverse-proxy limits.
* Free-text fields (reasons, notes, remarks) are stored as typed. Staff are asked not to put IC or
  medical details there.
* Existing audit entries were redacted once by migration `audit/0003`. The original sensitive values
  are no longer in the audit trail (by design).
