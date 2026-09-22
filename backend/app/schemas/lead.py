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

    # Phase 2 provenance + canonical identity. NULL for every lead created
    # via CSV upload or the demo seed -- only the (future) Phase 3 PDL
    # importer populates these.
    company_identity_id: UUID | None = None
    source_snapshot_id: UUID | None = None
    import_run_id: UUID | None = None
    source_record_id: str | None = None
    source_raw_data: dict[str, Any] | None = None
