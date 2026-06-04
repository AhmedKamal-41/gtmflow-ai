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
    processed_leads: int
    status: str
    created_at: datetime
    updated_at: datetime
