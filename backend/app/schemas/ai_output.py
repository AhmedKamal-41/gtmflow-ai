from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AIOutputBase(BaseModel):
    output_type: str
    content: dict[str, Any]
    model_used: str | None = None
    prompt_version: str | None = None


class AIOutputCreate(AIOutputBase):
    lead_id: UUID


class AIOutputRead(AIOutputBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_id: UUID
    created_at: datetime

    # Phase 2 identity + provenance. `id` + `created_at` together already
    # identify the exact revision; these fields explain how it was produced.
    parent_output_id: UUID | None = None
    origin: str = "generated"
    input_snapshot: dict[str, Any] | None = None
    input_hash: str | None = None
    output_schema_version: str | None = None
    model_revision: str | None = None
    adapter_revision: str | None = None

    # Phase 5 seller provenance: NULL on outputs generated before grounded
    # prompting (prompt_version "v1") -- those are never relabeled.
    seller_profile_id: UUID | None = None
    seller_profile_version: int | None = None
    seller_profile_content_hash: str | None = None
    seller_profile_kind: str | None = None
