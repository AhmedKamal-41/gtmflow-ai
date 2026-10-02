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
    # Phase 3 closeout Part F.4: the importer setting this to 5,000 while
    # the metrics dashboard's total_leads_processed (COUNT from
    # lead_scores) reads 0 is correct, not a bug -- they answer different
    # questions ("how many rows exist in this batch" vs "how many have
    # been scored"). See `scored_leads` below for the latter, at the
    # batch level.
    processed_leads: int
    # Computed at read time (COUNT of this batch's leads that have a
    # LeadScore row) -- never persisted, always current. Added specifically
    # so a client never has to infer "scored" from "processed."
    scored_leads: int = 0
    logical_key: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
