# Deployment configuration (Phase 5)

The backend runs in one of two modes, chosen by `DJANGO_ENV`:

| | `development` (default) | `production` |
|---|---|---|
| DEBUG | on (override `DJANGO_DEBUG`) | **must** be off, or the process refuses to start |
| SECRET_KEY | built-in dev-only key | **required**: 50+ random characters (not `django-insecure…`) |
| ALLOWED_HOSTS | localhost, 127.0.0.1 | **required**, no `*` |
| Database | `DATABASE_URL`, or SQLite fallback | **required** PostgreSQL `DATABASE_URL`, TLS required (`DATABASE_SSL_REQUIRE`, default on) |
| HTTPS redirect, HSTS, Secure cookies | off | on |
| Cookies | `sessionid`, `csrftoken` | `__Host-sessionid`, `__Host-csrftoken` (Secure, HttpOnly, SameSite=Lax) |
| Content-Security-Policy | off (browsable API needs inline script) | on (see `SECURITY.md`) |
| API renderers | JSON + browsable HTML | JSON only |
| Console logging | only while DEBUG | always, level `DJANGO_LOG_LEVEL` (INFO) |

If any required value is missing or unsafe, production start-up fails with `ImproperlyConfigured`,
listing every problem. The secret key itself is never printed.

## Environment variables

| Variable | Default (production) | Purpose |
|---|---|---|
| `DJANGO_ENV` | `development` | Set to `production` on servers |
| `DJANGO_SECRET_KEY` | — (required) | e.g. `python -c "import secrets; print(secrets.token_urlsafe(64))"`. Keep it in a secret manager |
| `DJANGO_ALLOWED_HOSTS` | — (required) | Comma-separated host names |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | empty | Comma-separated `https://` origins (needed when the admin sits behind a proxy on another host or port) |
| `DATABASE_URL` | — (required) | `postgres://sr_app:…@db-host:5432/sr_academy` (application role, see below) |
| `DATABASE_SSL_REQUIRE` | `true` | Require TLS to PostgreSQL (false only for a database on the same private host) |
| `DB_CONN_MAX_AGE` | `60` | Persistent connection lifetime (seconds) |
| `DB_DISABLE_SERVER_SIDE_CURSORS` | `false` | `true` behind PgBouncer in transaction mode |
| `DJANGO_SECURE_PROXY_SSL_HEADER` | `false` | `true` behind a TLS-terminating proxy that **overwrites** `X-Forwarded-Proto` |
| `DJANGO_TRUST_X_FORWARDED_FOR` | `false` | `true` if that proxy overwrites or appends `X-Forwarded-For` (used for audit IP and lockout) |
| `DJANGO_SECURE_SSL_REDIRECT` | `true` | Redirect HTTP to HTTPS (may be false if the proxy already redirects) |
| `DJANGO_HSTS_SECONDS` | `31536000` | HSTS max-age; start lower (e.g. 3600) on the first deployment |
| `DJANGO_HSTS_INCLUDE_SUBDOMAINS` / `DJANGO_HSTS_PRELOAD` | `true` / `true` | Only if every subdomain is HTTPS. Preload only sends the directive |
| `DJANGO_CSP` | strict policy | Override the Content-Security-Policy |
| `DJANGO_CSRF_COOKIE_HTTPONLY` | `true` | Set false only if a same-origin JavaScript client must read the CSRF cookie |
| `DJANGO_SESSION_COOKIE_AGE` | `43200` (12 h) | Admin session lifetime |
| `API_TOKEN_TTL_HOURS` | `336` (14 days) | API token lifetime |
| `LOGIN_MAX_FAILURES_PER_USERNAME` / `LOGIN_MAX_FAILURES_PER_IP` / `LOGIN_LOCKOUT_MINUTES` | `5` / `20` / `15` | Brute-force lockout |
| `API_THROTTLE_ANON` / `API_THROTTLE_USER` / `API_THROTTLE_LOGIN` | `60/min` / `3000/min` / `20/min` | DRF throttles |
| `DJANGO_LOG_LEVEL` | `INFO` | Log level |

## Runtime architecture (required, not yet provisioned)

```
Internet ──HTTPS──▶ reverse proxy (TLS, HTTP→HTTPS, static files, request size and rate limits)
                        │  X-Forwarded-Proto / X-Forwarded-For overwritten
                        ▼
                   WSGI server (e.g. gunicorn, several workers) running config.wsgi
                        │  TLS
                        ▼
                   PostgreSQL 16 (application role sr_app; migrations as sr_owner)
```

* **Static files:** `python manage.py collectstatic`, served by the proxy from `STATIC_ROOT`
  (`staticfiles/`). There are no user uploads.
* **Migrations:** run as the owner role before the new code starts
  (`DATABASE_URL=…sr_owner… python manage.py migrate`). The application runs as `sr_app`.
* **Checks at every deployment:**
  * `DJANGO_ENV=production python manage.py check --deploy --fail-level WARNING` must report no
    issues (this is what the Phase 5 test runs);
  * `python manage.py migrate --check`.
* **Throttle counters** use Django's per-process memory cache: with N workers the effective API
  limit is up to N× the setting. The login lockout is exact, because it is stored in the database.
  For strict API limits add a shared cache (e.g. `DatabaseCache` plus `createcachetable`) or rate
  limits on the proxy.

## Database roles (least privilege)

`scripts/db_roles.sql` creates:
* `sr_owner`, which owns the schema and runs migrations;
* `sr_app`, which the application uses: SELECT/INSERT/UPDATE/DELETE on tables, sequence usage,
  function execute.

Verified in Phase 5 on PostgreSQL 16: with `sr_app` the application works normally, but it
**cannot**:
* `ALTER TABLE … DISABLE TRIGGER` or `DROP TRIGGER`;
* `TRUNCATE` the audit log;
* `DROP TABLE`;
* `SET session_replication_role`.

So the financial-history, substitute, payroll, result and audit-log triggers cannot be switched off
from the application. If the application connected as the table owner or a superuser, it could do
all of that. This is production blocker P3 in `SECURITY.md`.

## Not included (see `SECURITY.md` for blockers)

Hosting, TLS certificates, the proxy and WSGI server configuration, staff 2FA, outgoing email
(needed for password reset) and a shared cache are not part of this repository yet.
