"""Field mapping + normalization from a raw PDL source record to the shape
GTMFlow's Lead model expects, plus eligibility/segment classification.

Never invents a value: every normalized field is either directly derived
from the source record or left None. Raw source values are always preserved
separately (`source_raw_data`) so normalization decisions stay explainable
and reversible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.pdl.config import COUNTRY_ALIAS_MAP, INDUSTRY_ALIAS_MAP, MAPPING_VERSION

_WS_RE = re.compile(r"\s+")


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    text = _WS_RE.sub(" ", value).strip()
    return text or None


def normalize_domain(raw_website: Any) -> str | None:
    """Conservative domain extraction. Returns None (not a guess) for
    anything that doesn't clearly look like a bare domain or URL -- a
    website URL is not retrieved website content, and an unparseable value
    should never be silently coerced into something that looks valid."""
    text = _clean_str(raw_website)
    if not text:
        return None

    candidate = text
    if "://" not in candidate:
        candidate = f"//{candidate}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None

    host = (parsed.netloc or parsed.path.split("/")[0]).lower().strip()
    host = host.split(":")[0]  # drop a port, if present
    if host.startswith("www."):
        host = host[4:]

    # Minimal sanity check: looks like host.tld, no spaces, no stray path
    # characters that leaked in because the value wasn't actually a URL.
    if not host or " " in host or "/" in host or "@" in host:
        return None
    if "." not in host or host.startswith(".") or host.endswith("."):
        return None
    return host


@dataclass
class NormalizedCompany:
    company_name: str
    website: str | None
    domain: str | None
    raw_industry: str | None
    normalized_industry: str | None
    candidate_segment: str | None
    company_size: str | None
    location: str | None
    locality: str | None
    region: str | None
    country_raw: str | None
    country_normalized: str | None
    source_record_id: str | None
    founded: Any
    linkedin_url: str | None
    source_raw_data: dict[str, Any]
    eligibility_rejections: list[str] = field(default_factory=list)

    @property
    def is_eligible(self) -> bool:
        return not self.eligibility_rejections


def normalize_record(raw: dict[str, Any]) -> NormalizedCompany:
    """Map + normalize one raw PDL record. Never raises on bad/missing
    fields -- eligibility failures are recorded, not thrown."""
    name = _clean_str(raw.get("name"))
    website_raw = _clean_str(raw.get("website"))
    domain = normalize_domain(raw.get("website"))
    industry_raw = _clean_str(raw.get("industry"))
    industry_key = industry_raw.lower() if industry_raw else None
    segment = INDUSTRY_ALIAS_MAP.get(industry_key) if industry_key else None

    country_raw = _clean_str(raw.get("country"))
    country_key = country_raw.lower() if country_raw else None
    country_normalized = COUNTRY_ALIAS_MAP.get(country_key) if country_key else None

    locality = _clean_str(raw.get("locality"))
    region = _clean_str(raw.get("region"))
    location_parts = [p for p in (locality, region, country_raw) if p]
    location = ", ".join(location_parts) if location_parts else None

    source_id = raw.get("id")
    source_id = str(source_id) if source_id is not None and str(source_id).strip() else None

    normalized = NormalizedCompany(
        company_name=name or "",
        website=website_raw,
        domain=domain,
        raw_industry=industry_raw,
        normalized_industry=segment,
        candidate_segment=segment,
        company_size=_clean_str(raw.get("size")),
        location=location,
        locality=locality,
        region=region,
        country_raw=country_raw,
        country_normalized=country_normalized,
        source_record_id=source_id,
        founded=raw.get("founded"),
        linkedin_url=_clean_str(raw.get("linkedin_url")),
        source_raw_data=dict(raw),
    )

    if not name:
        normalized.eligibility_rejections.append("missing company name")
    if country_normalized != "united states":
        normalized.eligibility_rejections.append("country is not United States")
    if segment is None:
        normalized.eligibility_rejections.append(
            "industry does not match a target segment"
        )

    return normalized


__all__ = ["NormalizedCompany", "normalize_record", "normalize_domain", "MAPPING_VERSION"]
