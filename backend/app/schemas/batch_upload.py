from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class BatchUploadError(BaseModel):
    row_number: int
    field: str
    message: str


class BatchUploadResponse(BaseModel):
    batch_id: UUID
    batch_name: str | None = None
    total_rows: int
    valid_rows: int
    invalid_rows: int
    errors: list[BatchUploadError] = []
