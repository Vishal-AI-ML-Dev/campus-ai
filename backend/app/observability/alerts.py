"""Lightweight alerting: emit a signal when something is wrong.

Two sinks, both operable on the free tier:
  1. A structured log line on the ``campus.alert`` logger with ``alert=true`` in
     its fields. Log-based alerting (Render, Logtail, Grafana, Datadog, ...)
     can trigger on that field with zero extra infra - this ALWAYS happens.
  2. An HTTP webhook (Slack/Discord/generic) IFF ``ALERT_WEBHOOK_URL`` is set.
     Fire-and-forget with a short timeout; a webhook failure is itself logged
     but never propagates - alerting must never break the caller.

Location:
    E:\\campus-ai\\backend\\alerts.py
"""

from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger("campus.alert")


def emit_alert(
    name: str,
    message: str,
    *,
    severity: str = "warning",
    **fields,
) -> None:
    """Record an alert: always logs a structured line; also POSTs to the webhook
    when one is configured. Never raises.

    Args:
        name: short machine-friendly alert name (e.g. "readiness_failed").
        message: human-readable description.
        severity: "warning" | "error" | "critical" (drives the log level).
        **fields: extra structured context (attached to both sinks).
    """
    extra_fields = {
        "alert": True,
        "alert_name": name,
        "severity": severity,
        **fields,
    }
    log = logger.error if severity in ("critical", "error") else logger.warning
    log(message, extra={"extra_fields": extra_fields})

    url = settings.ALERT_WEBHOOK_URL
    if not url:
        return
    try:
        import httpx

        httpx.post(
            url,
            json={"text": f"[{severity.upper()}] {name}: {message}", **fields},
            timeout=3.0,
        )
    except Exception as exc:  # noqa: BLE001 - alerting must never crash caller
        logger.warning(
            "alert webhook failed",
            extra={"extra_fields": {"alert_name": name, "error": str(exc)}},
        )
