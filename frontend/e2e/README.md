# Campus AI frontend end-to-end specs (Playwright)

> Status: these specs were carried over / restored during the Phase 3 security
> cleanup and have **not been run** in this repository yet. Nothing here is a
> claim that the UI works.

| Spec | Purpose |
| --- | --- |
| `smoke.spec.ts` | Logs in as each role and opens every dashboard page, recording HTTP 4xx/5xx, uncaught JS errors, `console.error` and error-boundary text. |
| `demo-capture.spec.ts` | Captures screenshots for documentation. Configured through `DEMO_*` variables (see `../demo.env.example`). |

## Targets

No deployment URL is built in. Defaults are local:

- `FRONTEND_URL` - default `http://localhost:3000`
- `API_URL` - default `http://localhost:8000`

Use `localhost` for both (not `127.0.0.1`): auth cookies are `SameSite=Lax`.
Only point these at another environment if you own it and intend to test it.

## Credentials

There are no default accounts or passwords. Create fresh test accounts (for
example with `backend/scripts/seed_demo.py` and your own `DEMO_SEED_PASSWORD`)
and supply them through the environment:

```powershell
$env:SMOKE_PASSWORD      = "<the password you chose>"
$env:SMOKE_ADMIN_EMAIL   = "admin@demo.example.com"
$env:SMOKE_TEACHER_EMAIL = "teacher@demo.example.com"
$env:SMOKE_TPO_EMAIL     = "tpo@demo.example.com"
$env:SMOKE_STUDENT_EMAIL = "aarav@demo.example.com"
npx playwright test e2e/smoke.spec.ts
```

Never commit real values; keep them in your shell or an untracked env file.

## Known gaps (to verify before relying on these)

- `smoke.spec.ts` now logs in via cookies instead of the old localStorage
  token. That change is untested.
- `demo-capture.spec.ts` still injects a localStorage token, which the current
  cookie-based frontend no longer reads; it probably needs the same change.
- `@playwright/test` is not listed in `package.json`; adding it (and its
  lockfile entry) is deferred to the dependency-pinning review.
