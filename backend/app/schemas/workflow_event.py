from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class WorkflowEventBase(BaseModel):
    event_type: str
    event_data: dict[str, Any] | None = None


class WorkflowEventCreate(WorkflowEventBase):
    lead_id: UUID | None = None
    batch_id: UUID | None = None


class WorkflowEventRead(WorkflowEventBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    lead_id: UUID | None = None
    batch_id: UUID | None = None
    created_at: datetime
