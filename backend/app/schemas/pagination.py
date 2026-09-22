from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


class Page(BaseModel, Generic[T]):
    """Consistent envelope for every paginated list endpoint (Part E.5/E.6).

    `total` is always the count across the full requested dataset (computed
    server-side by a separate COUNT query, not len(items)) -- Part E.7:
    aggregate counts must reflect the whole dataset, never just the
    returned page.
    """

    items: list[T]
    total: int
    limit: int
    offset: int
    has_more: bool
