# Operational & diagnostic scripts

Ye folder backend ke **one-off / operational scripts** rakhta hai (seeding,
user-admin, RLS diagnostics, health checks). App runtime inpe depend nahi karta -
ye sirf manually chalane ke liye hain. (App ka background worker `worker.py`
hai jo runtime service hai, isliye wo backend root me hi rehta hai.)

## Chalane ka tareeka

Hamesha **backend/ folder se**, module ke roop me chalao (taaki `from db import
...` jaise flat imports resolve ho):

```bash
cd backend
uv run python -m scripts.<name>   #  .py mat lagao, aur "scripts/" slash ke bajaye dot
```

Zyaadatar scripts `DATABASE_URL` env se connect karte hain (prod me `app_user`).
Kuch ko `MIGRATION_DATABASE_URL` (owner role) bhi chahiye. Ye backend ke
`.env` / environment se aa jaate hain.

## Catalogue

### Seeding
| Script | Kaam |
| --- | --- |
| `seed_demo` | Ek self-contained **demo institute** (slug `demo`) seed karta hai - users, departments, skills, results, attendance, drives, applications. Idempotent (dobara chalana safe). RLS se real `default` tenant se poori tarah isolated. |

Run: `uv run python -m scripts.seed_demo`

> Phase 3: restored without the old hard-coded password/accounts. Requires
> `DEMO_SEED_PASSWORD` (>= 12 chars, your own fresh value); optional
> `DEMO_EMAIL_DOMAIN` (default `demo.example.com`). Refuses to run with
> `ENVIRONMENT=production` unless explicitly overridden. Not executed since
> restoration.

### User admin
| Script | Kaam |
| --- | --- |
| `activate_user` | User account activate/deactivate. `... scripts.activate_user <email>` = on, `... <email> off` = off. |
| `reset_password` | Ek user ka password reset. `... scripts.reset_password <email> <new_password>`. |
| `list_users` | DATABASE_URL wale DB ke saare tenants + users list karta hai. |

### DB / users diagnostics
| Script | Kaam |
| --- | --- |
| `diag_active` | Har user ka `is_active` dikhata hai. |
| `diag_users` | app role (`app_user`) vs owner role (`postgres`) ke beech row-visibility compare karta hai - pata chalta hai table khaali hai ya RLS/permission ne chhupaya. |

### RLS diagnostics & verifiers
| Script | Kaam |
| --- | --- |
| `diag_rls` | Sirf `app_user` se har table ka RLS picture: enabled/forced?, SELECT grant?, kitni policies?, visible row count. RLS-enabled-but-no-policy (jo sab rows deny karta hai) pakadta hai. |
| `rls_diag` | `announcements` ke liye deep RLS diagnosis (role, superuser/BYPASSRLS?, enabled+forced?, policies, alembic head, owner). |
| `rls_verify` | `announcements` ka prod RLS verifier - GUC `app.current_tenant_id` toggle karke tenant='1', '2', aur ''(default-deny, MUST be 0) counts. |
| `rls_verify_all` | Wahi check **saari** tenant-isolated tables ke liye. |
| `rls_verify_assignments` | Wahi check `assignments` table ke liye. |

Note: verifiers `uv run alembic upgrade head` ke baad chalao.

### Health / smoke checks
| Script | Kaam |
| --- | --- |
| `e2e_smoke` | Poore backend ka end-to-end smoke test - har role login + major reads + key write/AI flows -> PASS/FAIL report. Target defaults to `http://127.0.0.1:8000`; non-local hosts need `SMOKE_ALLOW_REMOTE=1`. Credentials come only from `SMOKE_PASSWORD` + `SMOKE_{ADMIN,TEACHER,TPO,STUDENT}_EMAIL`. Not executed since the Phase 3 restore. |
| `worker_check` | Seedha AI worker ke endpoints (score/proof, resume, mentor) ko hit karke uski health check karta hai. URL+TOKEN args / `AI_WORKER_URL`+`AI_WORKER_TOKEN` env / backend `.env` se. |

## SQL helpers (isi folder me)
| File | Kaam |
| --- | --- |
| `app_role.template.sql` | **Unverified draft.** Provisions a NOBYPASSRLS application role + grants via `psql`, prompting for the password at run time (no credential in the file). Replaces the old role script, which was not restored because it embedded a password. |

Deliberately **not** carried over from the old project: the RLS-disabling
helper (turning RLS off is not an acceptable fix for a missing policy - add the
policy instead) and the old role script. See `PHASE3_REPORT.md` at the repo root.
