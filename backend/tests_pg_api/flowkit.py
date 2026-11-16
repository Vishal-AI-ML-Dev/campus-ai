"""Shared helpers for the API flow tests (no fixtures here; see conftest.py).

Extracted unchanged from test_core_flows.py (Phase 5J) so several test modules
can build API-driven synthetic institutes without importing each other.
"""

from __future__ import annotations

from types import SimpleNamespace

MARK = "zz-api-iso"
EMAIL_DOMAIN = "zz-api-iso.example.com"


def _d(r) -> str:
    try:
        d = r.json().get("detail")
    except Exception:  # noqa: BLE001
        d = None
    return f"http={r.status_code} detail={d!r}"


class Actor(SimpleNamespace):
    """A logged-in user: own cookie session + ids. Password never printed.

    __repr__ is overridden: SimpleNamespace's default repr would include the
    password (and pytest prints reprs in assertion failures).
    """

    def __repr__(self) -> str:
        return f"Actor(role={getattr(self, 'role', '?')!r}, id={getattr(self, 'id', '?')!r})"

    __str__ = __repr__

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, **kw):
        return self.client.post(url, **kw)

    def patch(self, url, **kw):
        return self.client.patch(url, **kw)

    def delete(self, url, **kw):
        return self.client.delete(url, **kw)

    def put(self, url, **kw):
        return self.client.put(url, **kw)
