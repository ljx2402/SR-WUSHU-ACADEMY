#!/usr/bin/env bash
# Restore a backup made by backup_postgres.sh into an EMPTY database, then check it.
# Usage: TARGET_DATABASE_URL=postgres://owner@host/new_db scripts/restore_postgres.sh FILE.dump
# Restore as the schema owner role (sr_owner); re-apply scripts/db_roles.sql grants if needed.
set -euo pipefail
: "${TARGET_DATABASE_URL:?TARGET_DATABASE_URL must be set}"
FILE="${1:?backup file required}"
if [ -f "$FILE.sha256" ]; then sha256sum --check --quiet "$FILE.sha256"; fi
pg_restore --exit-on-error --no-owner --no-privileges --dbname="$TARGET_DATABASE_URL" "$FILE"
psql "$TARGET_DATABASE_URL" -v ON_ERROR_STOP=1 -tAc \
  "SELECT 'migrations: ' || count(*) FROM django_migrations;
   SELECT 'protective triggers: ' || count(*) FROM pg_trigger WHERE tgname LIKE 'sr_%';"
echo "Restored. Run 'python manage.py migrate --check' and 'python manage.py check' against it."
