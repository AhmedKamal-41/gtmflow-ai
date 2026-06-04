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
