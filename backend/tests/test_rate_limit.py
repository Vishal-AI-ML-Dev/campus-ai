"""Rate limiting: /auth/login is capped per client IP and returns 429 past it.

This guards the slowapi layer added in Step 34 against regressions. The
limiter's counters are reset before every test (conftest), so a burst here
cannot exhaust the quota for other tests.

Phase 6: X-Forwarded-For is only trusted for TRUSTED_PROXY_HOPS appended
entries (default 0 = ignored), so a client cannot rotate spoofed addresses to
escape the limit.
"""

import pytest
from starlette.requests import Request

from app.core.config import settings
from app.core.rate_limit import client_ip


def _req(xff=None, peer="198.51.100.9"):
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


def test_login_rate_limited_after_quota(client):
    headers = {}
    payload = {"username": "ghost@test.dev", "password": "whatever"}

    statuses = [
        client.post("/auth/login", data=payload, headers=headers).status_code
        for _ in range(35)
    ]

    # The login cap is 30/minute: early attempts are normal auth failures (401),
    # then slowapi starts returning 429 once the quota is exhausted.
    assert 429 in statuses
    assert statuses.count(401) >= 1
    # 429s must come AFTER the allowed window, never on the very first request.
    assert statuses[0] == 401


def test_rotating_spoofed_forwarded_for_does_not_escape_limit(client):
    """Default (0 trusted hops): every request is keyed on the socket peer."""
    payload = {"username": "ghost@test.dev", "password": "whatever"}
    statuses = [
        client.post(
            "/auth/login",
            data=payload,
            headers={"X-Forwarded-For": f"203.0.113.{i}"},
        ).status_code
        for i in range(35)
    ]
    assert 429 in statuses


@pytest.mark.parametrize(
    "hops,xff,expected",
    [
        (0, "203.0.113.5", "198.51.100.9"),  # header ignored
        (0, None, "198.51.100.9"),
        (1, "1.1.1.1, 203.0.113.5", "203.0.113.5"),  # right-most = proxy-appended
        (1, "203.0.113.5", "203.0.113.5"),
        (2, "6.6.6.6, 203.0.113.5, 10.0.0.2", "203.0.113.5"),
        (2, "203.0.113.5", "198.51.100.9"),  # fewer entries than hops -> peer
        (1, "", "198.51.100.9"),
        (1, None, "198.51.100.9"),
    ],
)
def test_client_ip_trusts_only_configured_hops(monkeypatch, hops, xff, expected):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", hops)
    assert client_ip(_req(xff)) == expected


def test_one_trusted_hop_ignores_client_supplied_prefix(client, monkeypatch):
    """Behind one proxy, rotating the spoofable left part changes nothing."""
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    payload = {"username": "ghost@test.dev", "password": "whatever"}
    statuses = [
        client.post(
            "/auth/login",
            data=payload,
            headers={"X-Forwarded-For": f"10.9.9.{i}, 203.0.113.50"},
        ).status_code
        for i in range(35)
    ]
    assert 429 in statuses
    # A different real client (different proxy-appended entry) is unaffected.
    other = client.post(
        "/auth/login",
        data=payload,
        headers={"X-Forwarded-For": "203.0.113.51"},
    )
    assert other.status_code == 401
