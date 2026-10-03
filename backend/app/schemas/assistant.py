from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: UUID
    request: str = Field(min_length=1, max_length=1000)
    max_leads: int = Field(default=5, ge=1, le=5, strict=True)

    @field_validator("request")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe which leads you want.")
        return value.strip()


class SearchLeads(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(max_length=100)
    industry: str | None = Field(max_length=100)
    priority: Literal["Hot", "Warm", "Cold"] | None


class LeadSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_ids: list[UUID] = Field(max_length=5)


class AssistantLead(BaseModel):
    id: UUID
    company_name: str
    industry: str | None
    location: str | None
    priority: str | None
    score: int | None
    evidence: list[str]
    unknowns: list[str]


class AssistantStep(BaseModel):
    tool: str
    status: Literal["ok", "refused"]
    detail: str
    duration_ms: int


class AssistantResult(BaseModel):
    run_id: UUID
    batch_id: UUID
    mode: Literal["mock", "openai"]
    model: str
    status: Literal["proposed", "no_matches", "stopped"]
    message: str
    leads: list[AssistantLead]
    steps: list[AssistantStep]
    duration_ms: int
    usage: dict[str, int]
