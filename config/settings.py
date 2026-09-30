"""Django settings for the SR Wushu Academy management system.

Two modes, chosen with ``DJANGO_ENV``:

* ``development`` (default): convenient local defaults (DEBUG on, a dev-only
  secret key, SQLite or local PostgreSQL, plain HTTP).
* ``production``: every deployment-sensitive value comes from the environment
  and the process refuses to start if one is missing or unsafe (DEBUG on, weak
  or missing secret key, no allowed hosts, no PostgreSQL DATABASE_URL). HTTPS,
  secure cookies, HSTS and the security headers are on.

See docs/DEPLOYMENT.md for every variable.
"""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be an integer.") from None


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DJANGO_ENV = os.environ.get("DJANGO_ENV", "development").strip().lower()
if DJANGO_ENV not in {"development", "production"}:
    raise ImproperlyConfigured("DJANGO_ENV must be 'development' or 'production'.")
PRODUCTION = DJANGO_ENV == "production"

DEV_SECRET_KEY = "django-insecure-dev-only-never-use-in-production"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "" if PRODUCTION else DEV_SECRET_KEY)
DEBUG = env_bool("DJANGO_DEBUG", not PRODUCTION)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "" if PRODUCTION else "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

if PRODUCTION:
    problems = []
    if DEBUG:
        problems.append("DJANGO_DEBUG must be false in production.")
    if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith("django-insecure"):
        problems.append("DJANGO_SECRET_KEY must be a long random value (50+ characters) in production.")
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        problems.append("DJANGO_ALLOWED_HOSTS must list the site's host names (no '*') in production.")
    if any(not origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS):
        problems.append("DJANGO_CSRF_TRUSTED_ORIGINS must use https:// in production.")
    if not os.environ.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://")):
        problems.append("DATABASE_URL must point to PostgreSQL in production.")
    if problems:
        raise ImproperlyConfigured("Unsafe production configuration:\n- " + "\n- ".join(problems))

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "apps.audit",
    "apps.accounts",
    "apps.academy",
    "apps.attendance",
    "apps.finance",
    "apps.competitions",
    "apps.payroll",
    "apps.reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.accounts.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.audit.middleware.AuditActorMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database selection, in order of precedence:
#   1. DATABASE_URL, e.g. postgres://user:pass@localhost:5432/sr_academy (authoritative)
#   2. POSTGRES_DB (+ POSTGRES_USER/PASSWORD/HOST/PORT), kept for backward compatibility
#   3. SQLite file, a lightweight local option only. PostgreSQL is the reference database:
#      CI runs the full suite on PostgreSQL 16, and row-locking behaviour is only real there.
CONN_MAX_AGE = int(os.environ.get("DB_CONN_MAX_AGE", "60"))

if os.environ.get("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.parse(
            os.environ["DATABASE_URL"], conn_max_age=CONN_MAX_AGE, conn_health_checks=True,
            # Require TLS to the database server (set DATABASE_SSL_REQUIRE=false only for a
            # database on the same private host/network).
            ssl_require=env_bool("DATABASE_SSL_REQUIRE", PRODUCTION),
        )
    }
elif os.environ.get("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ.get("POSTGRES_USER", "postgres"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": CONN_MAX_AGE,
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# Behind a transaction-mode connection pooler (e.g. PgBouncer) server-side cursors must be off.
if env_bool("DB_DISABLE_SERVER_SIDE_CURSORS"):
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

AUTH_USER_MODEL = "accounts.User"

# Admin-site model permissions come from apps/accounts/capabilities.py, not from
# permission rows in the database.
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.CapabilityBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-my"
TIME_ZONE = "Asia/Kuala_Lumpur"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # API tokens expire (API_TOKEN_TTL_HOURS) and die with role, password or active changes.
        "apps.accounts.authentication.ExpiringTokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "COERCE_DECIMAL_TO_STRING": True,
    # The browsable HTML API is a development convenience only.
    "DEFAULT_RENDERER_CLASSES": (["rest_framework.renderers.JSONRenderer"] if PRODUCTION else
                                 ["rest_framework.renderers.JSONRenderer",
                                  "rest_framework.renderers.BrowsableAPIRenderer"]),
    # Generous per-client limits against scraping and abuse; login has its own lockout.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.environ.get("API_THROTTLE_ANON", "60/min"),
        "user": os.environ.get("API_THROTTLE_USER", "3000/min"),
        "login": os.environ.get("API_THROTTLE_LOGIN", "20/min"),
    },
}

# --------------------------------------------------------------------------- security

LOGIN_URL = "admin:login"

# Cookies. Session lifetime is a working day by default; staff sign in again the next day.
SESSION_COOKIE_AGE = env_int("DJANGO_SESSION_COOKIE_AGE", 12 * 60 * 60)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# The admin reads the CSRF token from the form, not the cookie, so the cookie can be HttpOnly.
CSRF_COOKIE_HTTPONLY = env_bool("DJANGO_CSRF_COOKIE_HTTPONLY", PRODUCTION)
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = PRODUCTION
if PRODUCTION:
    # "__Host-" cookies are only accepted over HTTPS, for this exact host, on path "/".
    SESSION_COOKIE_NAME = "__Host-sessionid"
    CSRF_COOKIE_NAME = "__Host-csrftoken"

# HTTPS. Behind a TLS-terminating reverse proxy set DJANGO_SECURE_PROXY_SSL_HEADER=true
# (the proxy must overwrite X-Forwarded-Proto on every request).
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", PRODUCTION)
if env_bool("DJANGO_SECURE_PROXY_SSL_HEADER", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 31536000 if PRODUCTION else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", PRODUCTION)
# Sends the "preload" directive only; nothing is submitted to browser preload lists.
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", PRODUCTION)

# Headers.
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# Content-Security-Policy (SecurityHeadersMiddleware). Inline styles are allowed because the
# Django admin uses style attributes; scripts must come from this site.
CONTENT_SECURITY_POLICY = os.environ.get(
    "DJANGO_CSP",
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'" if PRODUCTION else "",
)

# Client IP for the audit log and the login lockout. Only trust X-Forwarded-For behind a proxy
# that overwrites it (DJANGO_TRUST_X_FORWARDED_FOR=true): the right-most entry is used.
TRUST_X_FORWARDED_FOR = env_bool("DJANGO_TRUST_X_FORWARDED_FOR", False)

# Login brute-force protection (per username and per client IP, over a sliding window).
LOGIN_LOCKOUT = {
    "MAX_FAILURES_PER_USERNAME": env_int("LOGIN_MAX_FAILURES_PER_USERNAME", 5),
    "MAX_FAILURES_PER_IP": env_int("LOGIN_MAX_FAILURES_PER_IP", 20),
    "WINDOW_MINUTES": env_int("LOGIN_LOCKOUT_MINUTES", 15),
}

# API tokens (parent / coach / student apps) expire this many hours after they are issued.
API_TOKEN_TTL_HOURS = env_int("API_TOKEN_TTL_HOURS", 24 * 14)

# Uploads: the system accepts none; keep Django's small request limits explicit.
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
DATA_UPLOAD_MAX_NUMBER_FIELDS = 2000

# Logging: console only (collected by the process manager); secrets are redacted.
LOG_LEVEL = os.environ.get("DJANGO_LOG_LEVEL", "INFO" if PRODUCTION else "WARNING").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact": {"()": "apps.accounts.logging.RedactSecretsFilter"},
        "require_debug_true": {"()": "django.utils.log.RequireDebugTrue"},
    },
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {
        # Development prints only while DEBUG is on (as Django does by default); production always logs.
        "console": {"class": "logging.StreamHandler", "formatter": "plain",
                    "filters": ["redact"] if PRODUCTION else ["redact", "require_debug_true"]},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "django.security": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Never log SQL (it can contain personal data), whatever the log level.
        "django.db.backends": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}

# Academy-wide business settings.
ACADEMY = {
    "NAME": "SR Wushu Academy",
    "COUNTRY": "Malaysia",
    "CURRENCY": "MYR",
    "CURRENCY_SYMBOL": "RM",
    "ADDRESS": os.environ.get("ACADEMY_ADDRESS", ""),
    "PHONE": os.environ.get("ACADEMY_PHONE", ""),
    "EMAIL": os.environ.get("ACADEMY_EMAIL", ""),
    "REGISTRATION_NO": os.environ.get("ACADEMY_REGISTRATION_NO", ""),
    # Official document numbers look like INV-2026-000123 and restart each calendar year.
    # Receipts keep the existing SRWA prefix.
    "DOCUMENT_PREFIXES": {"INVOICE": "INV", "PAYMENT": "PAY", "RECEIPT": "SRWA", "REFUND": "RFD"},
    # How long before the session a substitute coach gains access, and how long
    # after the session ends the access remains (to finish attendance entry).
    "SUBSTITUTE_ACCESS_HOURS_BEFORE": 24,
    "SUBSTITUTE_ACCESS_HOURS_AFTER": 24,
    # Coaches (regular or authorized substitute) may record and change attendance
    # until this many hours after the session ends; afterwards only an
    # administrator correction with a reason is possible.
    "ATTENDANCE_COACH_EDIT_HOURS": 48,
    # Late counts as attended when calculating attendance percentage.
    "LATE_COUNTS_AS_PRESENT": True,
    "DEFAULT_TUITION_DUE_DAY": 7,
}
