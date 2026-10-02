"""Within-snapshot identity: a deterministic key for each eligible record so
duplicates and conflicts can be detected during a single streaming pass,
without accumulating full records.

Two identity strengths:

  - "source_id": the PDL `id` field is present -- used directly. Not assumed
    stable across dataset releases (Part D.1), but that doesn't matter for
    detecting duplicates *within* a single snapshot's scan, which is all
    this is used for.
  - "fingerprint": no `id` present -- a deterministic hash of normalized
    name + locality + region + country stands in for identity, with lower
    confidence recorded on the resulting row (Part D.2). Two records that
    hash the same are treated as the same candidate identity for this
    snapshot; this is a heuristic, not a guarantee, and is labeled as such.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from app.pdl.normalize import NormalizedCompany

IdentityConfidence = Literal["source_id", "fingerprint"]


@dataclass(frozen=True)
class RecordIdentity:
    key: str
    confidence: IdentityConfidence


def compute_identity(company: NormalizedCompany) -> RecordIdentity:
    if company.source_record_id:
        return RecordIdentity(key=f"id:{company.source_record_id}", confidence="source_id")

    fingerprint_input = "|".join(
        [
            (company.company_name or "").strip().lower(),
            (company.locality or "").strip().lower(),
            (company.region or "").strip().lower(),
            (company.country_raw or "").strip().lower(),
        ]
    )
    digest = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()[:32]
    return RecordIdentity(key=f"fp:{digest}", confidence="fingerprint")


def compute_content_hash(company: NormalizedCompany) -> str:
    """Hash of the normalized facts (not raw JSON, so field-order/whitespace
    differences in the source don't register as false conflicts). Used to
    tell "exact duplicate" (same hash, silently skip the repeat) apart from
    "conflicting content under the same identity" (different hash, report a
    conflict and keep the first-seen version -- see Part D.3)."""
    payload = {
        "company_name": company.company_name,
        "domain": company.domain,
        "raw_industry": company.raw_industry,
        "company_size": company.company_size,
        "locality": company.locality,
        "region": company.region,
        "country_raw": company.country_raw,
        "founded": company.founded,
        "linkedin_url": company.linkedin_url,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
