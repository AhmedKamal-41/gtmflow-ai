from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class DemoRunResponse(BaseModel):
    batch_id: UUID
    batch_name: str
    total_leads: int
    hot: int
    warm: int
    cold: int
    outreach_generated: int
    outreach_approved: int
    leads_pushed: int
