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
    status: Literal["draft"] = "draft"
    profile: SellerProfileContent
    content_hash: str
    editor_label: str
    created_at: datetime
