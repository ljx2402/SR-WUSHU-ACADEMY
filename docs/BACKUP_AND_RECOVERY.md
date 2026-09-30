# Backup and recovery (Phase 5)

The database holds the academy's legal and financial records (invoices, receipts, payments,
refunds, payroll), personal data (IC numbers, medical notes, bank accounts) and the audit trail.
**The system must not go live without the backups below running and a restore having been
tested in the production environment** (production blocker P3 in `SECURITY.md`).

## Requirements

| Requirement | Target |
|---|---|
| Automated backups | Daily logical backup (`scripts/backup_postgres.sh`) **plus** continuous WAL archiving / point-in-time recovery, from the managed database service or pgBackRest / WAL-G |
| Recovery point objective (RPO) | ≤ 15 minutes with PITR (≤ 24 hours with the daily dump alone) |
| Recovery time objective (RTO) | ≤ 4 hours to a working system |
| Retention | Daily for 35 days, monthly for 12 months, yearly for 7 years (Malaysian financial records are generally kept 7 years; confirm with the academy's accountant) |
| Off-site storage | A second location or region, separate credentials; the application server has write-only access to it |
| Encryption | At rest (storage-level plus encryption of the dump, e.g. `age` / `gpg` or the provider's KMS) and in transit (TLS). Backup files are created owner-only (`umask 077`) |
| Integrity | SHA-256 file next to each dump; `pg_restore --list` check at backup time (both in the script) |
| Restore testing | Monthly: restore the latest backup into a scratch database, run the checks below, record the result and the time taken |
| Access | Only named administrators can read backups; every restore is logged |

## Procedure

**Backup** (as a role that can read every table, e.g. `sr_owner`):
```bash
DATABASE_URL=postgres://sr_owner:…@db:5432/sr_academy scripts/backup_postgres.sh /secure/backups
# → /secure/backups/sr_academy_20261001T020000Z.dump (+ .sha256), then copy off-site encrypted
```

**Restore** into an **empty** database:
```bash
createdb -O sr_owner sr_academy_restore
TARGET_DATABASE_URL=postgres://sr_owner:…@db:5432/sr_academy_restore \
    scripts/restore_postgres.sh /secure/backups/sr_academy_20261001T020000Z.dump
DATABASE_URL=postgres://sr_owner:…@db:5432/sr_academy_restore python manage.py migrate --check
```

After a restore:
* re-apply the `sr_app` grants from `scripts/db_roles.sql`;
* check the protective triggers were restored (the restore script counts them);
* spot-check the latest invoices, receipts and payroll;
* only then point the application at the database.

Verified in Phase 5 on PostgreSQL 16:
* backup, then restore into a new database: identical row counts;
* all 24 `sr_*` triggers present;
* `migrate --check` clean;
* the backup file created with mode 0600.

## Migrations and recovery

* Every migration is reversible where the data allows it, and was tested forward, backward and
  forward again with realistic data in Phases 1–5.
* The exception: `audit/0003` redacts sensitive values in old audit entries, which cannot be
  undone.
* **Before each production migration:** take a backup, run it on a restored copy first, then
  migrate production.
* If a release must be rolled back, prefer restoring the pre-migration backup (or PITR to just
  before the migration) over reverse migrations on live data.
