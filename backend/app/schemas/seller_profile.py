from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ProofPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim: ShortText
    source: str = Field(min_length=1, max_length=1000)


class SellerProfileContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    profile_kind: Literal["seller", "demo"]
    company_name: Name
    product_name: Name
    value_proposition: str = Field(min_length=1, max_length=2000)
    target_customer: str = Field(min_length=1, max_length=3000)
    capabilities: list[ShortText] = Field(default_factory=list, max_length=12)
    proof_points: list[ProofPoint] = Field(default_factory=list, max_length=12)
    exclusions: list[ShortText] = Field(default_factory=list, max_length=12)


class SellerProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0, strict=True)
    profile: SellerProfileContent


class SellerProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    # Every stored revision is a draft until explicitly activated; the
    # router marks the one revision that is currently active.
    status: Literal["draft", "active"] = "draft"
    profile: SellerProfileContent
    content_hash: str
    editor_label: str
    created_at: datetime


class SellerProfileActivate(BaseModel):
    """Explicit activation of one immutable revision.

    `confirm_reviewed` must be true: the operator states they reviewed this
    exact revision. `acknowledge_demo` is additionally required for a
    demonstration profile, so one is never activated by accident.
    `expected_activation_sequence` is the activation state the operator saw
    (0 = never activated); a different current state returns 409.
    """

    model_config = ConfigDict(extra="forbid")

    seller_profile_id: UUID
    expected_activation_sequence: int = Field(ge=0, strict=True)
    confirm_reviewed: Literal[True]
    acknowledge_demo: bool = False


class SellerProfileDeactivate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_activation_sequence: int = Field(ge=1, strict=True)


class SellerProfileActivationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sequence: int
    action: Literal["activate", "deactivate"]
    seller_profile_id: UUID | None
    seller_profile_version: int | None
    content_hash: str | None
    reviewed_confirmation: bool
    demo_acknowledged: bool
    actor_label: str
    created_at: datetime


class SellerProfileStatus(BaseModel):
    """What generation would use right now.

    state: `missing` (nothing saved), `draft_only` (revisions exist, none
    active), `active` (a revision is active; `active_profile.profile_kind`
    says whether it is a demonstration).
    """

    state: Literal["missing", "draft_only", "active"]
    latest_version: int | None
    activation_sequence: int
    last_activation: SellerProfileActivationRead | None
    active_profile: SellerProfileRead | None
    latest_is_active: bool
