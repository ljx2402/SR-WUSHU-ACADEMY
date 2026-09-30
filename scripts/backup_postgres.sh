#!/usr/bin/env bash
# Logical backup of the academy database (custom format, compressed).
# Usage: DATABASE_URL=postgres://... scripts/backup_postgres.sh [output-dir]
# Run from a scheduler (e.g. daily); copy the file off-site and encrypted (see docs/BACKUP_AND_RECOVERY.md).
set -euo pipefail
: "${DATABASE_URL:?DATABASE_URL must be set}"
OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"
umask 077                       # backups contain personal data: owner-only permissions
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$OUT_DIR/sr_academy_${STAMP}.dump"
pg_dump --format=custom --no-owner --no-privileges --dbname="$DATABASE_URL" --file="$FILE"
pg_restore --list "$FILE" > /dev/null   # the archive must be readable
sha256sum "$FILE" > "$FILE.sha256"
echo "$FILE"
