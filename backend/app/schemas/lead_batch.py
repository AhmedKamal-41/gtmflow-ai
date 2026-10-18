from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LeadBatchBase(BaseModel):
    name: str | None = None
    source: str = "csv"


class LeadBatchCreate(LeadBatchBase):
    pass


class LeadBatchRead(LeadBatchBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    total_leads: int
    # Rows successfully ingested into this batch (CSV rows that passed
    # validation, or PDL leads committed) -- NOT the same thing as "scored."
    # It answers "how many rows exist in this batch", not "how many are
    # ranked" -- see `scored_leads` below for the latter.
    processed_leads: int
    # Computed at read time: this batch's leads with a current ranking
    # against the active seller profile -- never persisted, always current,
    # so a client never has to infer "scored" from "processed."
    scored_leads: int = 0
    logical_key: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
