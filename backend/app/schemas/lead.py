from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LeadBase(BaseModel):
    company_name: str
    website: str | None = None
    industry: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_title: str | None = None
    company_size: str | None = None
    location: str | None = None
    source: str | None = None


class LeadCreate(LeadBase):
    batch_id: UUID


class LeadRead(LeadBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_id: UUID
    status: str
    cleaned_data: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
