from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.hashing import content_hash as _content_hash


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

    # Phase 6. `purpose`: "operational" (a lead's drafts) or "annotation"
    # (training candidates, never delivered). `author_label` is set on human
    # revisions. `review_status` is filled by endpoints that list drafts:
    # pending / approved / rejected / superseded, or None when the output
    # isn't an operational outreach draft.
    purpose: str = "operational"
    author_label: str | None = None
    review_status: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        """Identity of the exact content shown; reviews must send it back."""
        return _content_hash(self.content)


class AIOutputRevisionCreate(BaseModel):
    """A human correction. Creates a new immutable revision; the output it
    revises (and the original model response) are never changed."""

    model_config = ConfigDict(extra="forbid")

    expected_content_hash: str = Field(min_length=64, max_length=64)
    content: dict[str, Any]
