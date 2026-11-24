"""Bearer-token checks in constant time. Tokens are never logged, echoed or included in errors."""

from __future__ import annotations

import hmac


def bearer_ok(header: str | None, tokens: tuple[str, ...]) -> bool:
    """True when ``header`` is ``Bearer <t>`` for one of ``tokens``.

    Every configured token is compared (no early exit), with ``hmac.compare_digest`` on bytes, so the time taken does
    not reveal which token matched or how much of a token was right.
    """
    if not header or not tokens:
        return False
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not presented:
        return False
    candidate = presented.strip().encode("utf-8", "replace")
    ok = False
    for t in tokens:
        ok |= hmac.compare_digest(candidate, t.encode("utf-8"))
    return ok
