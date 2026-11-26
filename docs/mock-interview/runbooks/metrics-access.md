# 5. Protected metrics (`/metrics`) configuration (decision P7-D7)

| Environment | `/metrics` |
|---|---|
| development / test | open, as before |
| production, `METRICS_TOKEN` empty | **not registered** (backend 404; worker 401 before routing / 404 with the worker token) |
| production, `METRICS_TOKEN` invalid | not registered; startup log `"/metrics stays disabled: <reason>"` (never the value) |
| production, `METRICS_TOKEN` valid | `Authorization: Bearer <METRICS_TOKEN>` required; wrong/missing → bare 401 with `WWW-Authenticate: Bearer`; `Cache-Control: no-store` |

## Token rules (fail closed)

- Dedicated secret: **≥ 32 characters, no whitespace**, not a placeholder.
- Must differ from every other secret: backend `SECRET_KEY`, `AI_WORKER_TOKEN`, `STAGING_GATE_TOKEN`, `SUPABASE_SERVICE_KEY`; worker `AI_WORKER_TOKEN`.
- Use **different** tokens for backend and worker if possible. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- Compared in constant time; never logged or echoed.
- On the worker, `/metrics` is exempt from `X-Worker-Token` and checks only its own token; the worker token alone is not enough.

## Prometheus scrape example

```yaml
scrape_configs:
  - job_name: campus-ai-backend
    scheme: https
    metrics_path: /metrics
    authorization: { type: Bearer, credentials_file: /etc/prometheus/campus_backend_metrics_token }
    static_configs: [{ targets: ["<backend host>"] }]
```

If `STAGING_GATE_TOKEN` is set on that deployment, the scraper must also send the staging gate header.

## Content

Labels are low-cardinality and content-free: route templates, methods, status codes, `step`, `outcome`,
`provider`, `model`, `direction`, `result`, `reason`. No tenant, user, session, request ids or text.

## Troubleshooting

| Symptom | Cause |
|---|---|
| 404 in production | Token empty or invalid → check the startup warning |
| 401 | Wrong header/scheme; token mismatch; on the worker, sending `X-Worker-Token` instead of the bearer |
| Only `http_*` series | The service has not served interview traffic since start (AI series appear on first use) |
