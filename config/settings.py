"""Django settings for the SR Wushu Academy management system."""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]

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
            os.environ["DATABASE_URL"], conn_max_age=CONN_MAX_AGE, conn_health_checks=True
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
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
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
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "COERCE_DECIMAL_TO_STRING": True,
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
    # Receipt numbers look like SRWA-2026-000123 and restart each calendar year.
    "RECEIPT_PREFIX": "SRWA",
    # How long before the session a substitute coach gains access, and how long
    # after the session ends the access remains (to finish attendance entry).
    "SUBSTITUTE_ACCESS_HOURS_BEFORE": 24,
    "SUBSTITUTE_ACCESS_HOURS_AFTER": 24,
    # Late counts as attended when calculating attendance percentage.
    "LATE_COUNTS_AS_PRESENT": True,
    "DEFAULT_TUITION_DUE_DAY": 7,
}
