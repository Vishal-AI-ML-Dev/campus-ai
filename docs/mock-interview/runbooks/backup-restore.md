# 13. Backup and recovery (Phase 10.6, owner decision D6)

Targets: **daily** backups, **RPO 24 h**, **RTO 8 h**. Readiness is claimed only after a successful isolated restore
drill. **D7 (destination, retention, key custody) is PENDING** - until the owner decides, bundles are written to a
local directory only and no external destination is configured.

## What is backed up

| Data | How | Notes |
|---|---|---|
| Postgres (all tables, RLS policies, Alembic head) | `pg_dump --format=custom` | Needs a role with **BYPASSRLS** (tables use FORCE RLS; `pg_dump` refuses rather than dump a partial copy). On Supabase: the `postgres` owner role. |
| Uploaded files (private Storage bucket) | `scripts/backup/storage_backup.py backup` | Read-only listing + download; manifest with size and SHA-256 per object. |
| Qdrant `career_kb` | not backed up | Rebuilt from `ai-worker/kb.py` on worker start. |
| Qdrant `student_faces` | `scripts/backup/qdrant_snapshot.sh` | Only if face attendance is ever enabled (it is disabled). |
| Roles (`app_user`) | not in a dump | Re-created from `backend/scripts/app_role.template.sql` during restore. |

`scripts/backup/backup_all.sh` puts `db.dump`, `storage/` and `manifest.json` (UTC time, Alembic head, exact row count
per table, checksums - never row contents) into one tar, **encrypts it to a GPG public key**, writes a `.sha256`, applies
retention (14 daily + 8 weekly, `retention.py`) and pings `BACKUP_HEARTBEAT_URL` (success) or `.../fail` (failure).
Any failing step leaves no bundle, prints a `campus.alert backup_failed` line and exits non-zero.

## Daily run (after D7)

```bash
BACKUP_DATABASE_URL=<owner role URL> BACKUP_GPG_PUBLIC_KEY=keys/backup_pub.asc BACKUP_DIR=<private dir> \
SUPABASE_URL=... SUPABASE_SERVICE_KEY=... BACKUP_HEARTBEAT_URL=<heartbeat monitor URL> \
  scripts/backup/backup_all.sh
```

Owner actions (D7): choose the destination (never public), create the GPG key pair **offline** and keep the private
key offline (two copies), schedule the job (the owner's PC task scheduler, or a provider-side schedule - GitHub
scheduled workflows run only from `main`, see OQ-6), and create the heartbeat monitor that e-mails when a ping is
missed or `/fail` arrives (D5).

## Restore (disaster: new free project)

1. Create a new database project; note its owner URL. Do **not** reuse a compromised project.
2. Decrypt and verify: `scripts/backup/restore_drill.sh <bundle>` first, to prove the bundle is good (isolated).
3. `pg_restore --no-owner --no-privileges --exit-on-error -d <new owner URL> db.dump` (from the decrypted bundle).
4. Create the runtime role: run `backend/scripts/app_role.template.sql` (interactive password) against the new DB.
5. Check: Alembic head equals the manifest; row counts equal the manifest (`restore_drill.sh` does both).
6. Storage: `python scripts/backup/storage_backup.py restore <bundle>/storage` (dry run), then `--apply` against the
   NEW bucket (never overwrites).
7. Repoint the backend/worker env (`DATABASE_URL`, `SUPABASE_*`), redeploy, then run the staging checks (`/health/ready`,
   login, one non-AI page, one AI call within the caps).

## Drill evidence

| Date | Environment | Result |
|---|---|---|
| 2026-11-25 | Local, disposable `postgres:16` (digest-pinned) source with synthetic data (3 institutes, 60 users, 25 ledger rows; 89 rows in 55 tables) + loopback fake Storage (15 synthetic files); throwaway drill key | **PASS** - see `../specs/phase-10-production-readiness.md` §10.6 |

Not yet drilled: a restore from a real Supabase backup into a new Supabase project (needs D7 + owner approval).
