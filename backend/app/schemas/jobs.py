"""Phase 10: background job requests and progress responses."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

JobType = Literal["fit_score", "legacy_score", "generate_summary", "generate_outreach", "push_hot", "assistant_outreach"]


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_type: JobType
    params: dict[str, Any] = Field(default_factory=dict)


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_type: str
    batch_id: UUID | None
    params: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    max_item_attempts: int
    total_items: int | None
    counts: dict[str, int]
    result: dict[str, Any] | None
    last_error: str | None
    cancel_requested: bool
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # Set on POST responses: True when an identical active job was returned.
    deduplicated: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def done_items(self) -> int:
        return sum(v for k, v in (self.counts or {}).items() if k != "pending")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def progress_pct(self) -> float | None:
        if not self.total_items:
            return 100.0 if self.total_items == 0 else None
        return round(100 * self.done_items / self.total_items, 1)


class JobItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_id: UUID
    position: int
    status: str
    attempts: int
    outcome: dict[str, Any] | None
    error: str | None
    updated_at: datetime
