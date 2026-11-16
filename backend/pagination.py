"""Backward-compatible list pagination (limit/offset + X-Total-Count header).

Why this shape
--------------
The app is already live (Vercel frontend + Render API). Switching list
endpoints to an envelope object (``{items, total, ...}``) would break every
existing list fetch at once. Instead we keep the response body a PLAIN LIST and
layer pagination on top:

  * two optional query params - ``limit`` (default 50, max 200) and ``offset``
    (default 0) - so old callers that pass neither still get a sensible first
    page unchanged;
  * the grand total (ignoring limit/offset) is returned in the ``X-Total-Count``
    response header, the same convention the GitHub API uses, so a client can
    build "page 2 / load more" UI without any body-shape change.

Usage
-----
>>> @router.get("/things", response_model=list[ThingOut])
... def list_things(
...     response: Response,
...     db: Session = Depends(get_db),
...     page: Pagination = Depends(pagination_params),
... ) -> list[Thing]:
...     stmt = select(Thing).where(...).order_by(Thing.created_at.desc())
...     return list(paginate(db, response, stmt, page))

For endpoints that enrich rows into DTOs, use ``paginate`` on the base SELECT
first, then build DTOs for just the returned page. For an already-materialised
Python list, use ``paginate_list``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, TypeVar

from fastapi import Query, Response
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")

# Sensible defaults: a full first page for existing callers, with a hard ceiling
# so a client can never ask for an unbounded result set.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass(frozen=True)
class Pagination:
    """Resolved paging window for one request."""

    limit: int
    offset: int


def pagination_params(
    limit: int = Query(
        DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description="Max rows to return (1-200, default 50).",
    ),
    offset: int = Query(
        0, ge=0, description="Rows to skip before returning (default 0)."
    ),
) -> Pagination:
    """FastAPI dependency that parses+validates the ``limit``/``offset`` query."""
    return Pagination(limit=limit, offset=offset)


def _set_total(response: Response, total: int) -> None:
    """Publish the unpaged grand total on the response."""
    response.headers["X-Total-Count"] = str(total)


def count_query(db: Session, stmt: Select) -> int:
    """Return the total row count for a SELECT, ignoring limit/offset/order."""
    # order_by(None) strips ORDER BY (some DBs reject it inside COUNT subqueries)
    # and keeps the count cheap.
    return int(
        db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
        or 0
    )


def paginate(
    db: Session, response: Response, stmt: Select, page: Pagination
) -> Sequence:
    """Set X-Total-Count from ``stmt`` and return the requested page of scalars.

    Pass a SELECT for a single entity/column; the returned value is ready to
    wrap in ``list(...)``.
    """
    _set_total(response, count_query(db, stmt))
    return db.scalars(stmt.limit(page.limit).offset(page.offset)).all()


def paginate_rows(
    db: Session, response: Response, stmt: Select, page: Pagination
) -> Sequence:
    """Like ``paginate`` but for multi-column SELECTs (returns Row objects)."""
    _set_total(response, count_query(db, stmt))
    return db.execute(stmt.limit(page.limit).offset(page.offset)).all()


def paginate_list(
    response: Response, items: list[T], page: Pagination
) -> list[T]:
    """Paginate an already-materialised Python list (non-SQL sources).

    Use only when the full set is small/unavoidably in memory; prefer the
    SQL-level helpers so the DB does the slicing.
    """
    _set_total(response, len(items))
    return items[page.offset : page.offset + page.limit]
