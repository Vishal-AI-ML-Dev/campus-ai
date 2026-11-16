"""Metrics endpoint tests - Prometheus counters exposed at /metrics.

Skipped automatically when prometheus_client isn't installed (the app degrades
to no-op metrics in that case).
"""

import pytest

pytest.importorskip("prometheus_client")


def test_metrics_endpoint_exposes_prometheus_text(client):
    # Generate at least one request so a counter series exists.
    client.get("/health")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body


def test_metrics_records_matched_route_template(client):
    client.get("/health")
    body = client.get("/metrics").text
    # The liveness route is recorded by its template, and /metrics excludes
    # itself so it never appears as a counted path.
    assert 'path="/health"' in body
    assert 'path="/metrics"' not in body
