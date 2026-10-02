"""Shared pagination helper (Part E.5): every list endpoint uses this, so
ordering/limit/offset/count semantics stay identical across batches, leads,
AI-output history, and push history.
"""
from __future__ import annotations

from typing import Any, TypeVar

from fastapi import Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.schemas.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page

T = TypeVar("T")


def pagination_params(
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
) -> tuple[int, int]:
    return limit, offset


def paginate(
    session: Session,
    stmt: Select[Any],
    *,
    limit: int,
    offset: int,
    schema: type[T],
) -> Page[T]:
    """`stmt` must already be ordered with a unique tie-breaker (e.g.
    `.order_by(Model.created_at.desc(), Model.id.desc())`) -- this function
    doesn't add one, since the right tie-breaker column varies per model.
    """
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(stmt.limit(limit).offset(offset)).scalars().all()
    items = [schema.model_validate(row) for row in rows]
    return Page[schema](
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + len(rows)) < total,
    )
