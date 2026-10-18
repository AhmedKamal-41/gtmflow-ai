from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ProofPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim: ShortText
    source: str = Field(min_length=1, max_length=1000)


Phrase = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class CompanySizeRange(BaseModel):
    """Employees, inclusive. `max_employees` None means "and larger"."""

    model_config = ConfigDict(extra="forbid")

    min_employees: int = Field(ge=1, le=10_000_000, strict=True)
    max_employees: int | None = Field(default=None, ge=1, le=10_000_000, strict=True)

    @model_validator(mode="after")
    def ordered(self) -> "CompanySizeRange":
        if self.max_employees is not None and self.max_employees < self.min_employees:
            raise ValueError("max_employees must be at least min_employees")
        return self


class TargetPreferences(BaseModel):
    """Who the seller wants to sell to, as structured criteria the ranking
    compares with each lead's record (app/scoring/fit.py). An empty list
    means "no preference" and that criterion does not count. With every list
    empty, leads cannot be ranked until preferences are added."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    industries: list[Phrase] = Field(default_factory=list, max_length=20)
    locations: list[Phrase] = Field(default_factory=list, max_length=20)
    company_sizes: list[CompanySizeRange] = Field(default_factory=list, max_length=8)
    contact_roles: list[Phrase] = Field(default_factory=list, max_length=20)
    keywords: list[Phrase] = Field(default_factory=list, max_length=20)

    @field_validator("industries", "locations", "contact_roles", "keywords")
    @classmethod
    def has_words(cls, values: list[str]) -> list[str]:
        for value in values:
            if not any(ch.isalnum() for ch in value):
                raise ValueError(f"'{value}' contains no letters or digits")
        return values


class SellerProfileContent(BaseModel):
    """`value_proposition`, `capabilities` and `proof_points` say what is
    sold (the only claims a draft may make); `target_customer` (free text,
    passed to drafting) and `target_preferences` (structured, used for
    ranking) say who it is for."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    profile_kind: Literal["seller", "demo"]
    company_name: Name
    product_name: Name
    value_proposition: str = Field(min_length=1, max_length=2000)
    target_customer: str = Field(min_length=1, max_length=3000)
    capabilities: list[ShortText] = Field(default_factory=list, max_length=12)
    proof_points: list[ProofPoint] = Field(default_factory=list, max_length=12)
    exclusions: list[ShortText] = Field(default_factory=list, max_length=12)
    target_preferences: TargetPreferences = Field(default_factory=TargetPreferences)


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
