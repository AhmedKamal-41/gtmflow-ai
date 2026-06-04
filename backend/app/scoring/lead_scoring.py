"""Deterministic, transparent 100-point lead scoring engine.

Pure function: takes any object with the standard lead attributes
(Lead ORM, SimpleNamespace, dataclass, anything that exposes the right
field names) and returns a fully-explainable score dict. No I/O, no LLM.

Score = sum(category scores) + penalties, clamped to [0, 100].
Bands: Hot >= 80, Warm 55-79, Cold 0-54.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------- taxonomies

TARGET_INDUSTRIES: tuple[str, ...] = (
    "property management",
    "housing",
    "multifamily",
    "apartment management",
    "real estate management",
    "healthcare",
    "clinic",
    "medical practice",
    "patient services",
    "hospital",
    "dental",
    "urgent care",
)

TARGET_TITLES: tuple[str, ...] = (
    "ceo",
    "founder",
    "co-founder",
    "vp sales",
    "vp operations",
    "head of operations",
    "revenue operations",
    "revops",
    "property manager",
    "director of leasing",
    "leasing manager",
    "practice manager",
    "operations manager",
    "office manager",
)

PAIN_POINT_KEYWORDS: tuple[str, ...] = (
    "leasing",
    "maintenance",
    "appointment",
    "scheduling",
    "intake",
    "resident",
    "tenant",
    "patient",
    "paperwork",
    "support",
    "call volume",
    "automation",
    "tour",
    "booking",
    "front desk",
    "follow-up",
    "forms",
)

HIGH_QUALITY_SOURCES: frozenset[str] = frozenset(
    {"referral", "webinar", "conference", "inbound"}
)
MEDIUM_QUALITY_SOURCES: frozenset[str] = frozenset({"website", "csv"})

FREE_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "icloud.com",
        "aol.com",
        "live.com",
        "msn.com",
        "proton.me",
        "protonmail.com",
    }
)

DISQUALIFIED_STATUSES: frozenset[str] = frozenset(
    {"do_not_contact", "disqualified", "unsubscribed"}
)

# ---------------------------------------------------------------- bands

HOT_MIN = 80
WARM_MIN = 55


def _band(total_score: int) -> str:
    if total_score >= HOT_MIN:
        return "Hot"
    if total_score >= WARM_MIN:
        return "Warm"
    return "Cold"


# ---------------------------------------------------------------- helpers

def _get(lead: Any, name: str) -> str | None:
    """Safely read a string-like attribute from any lead-like object."""
    value = getattr(lead, name, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _gather_text(lead: Any) -> str:
    """Concatenate every text-bearing field into one lowercase blob.

    Includes cleaned_data JSON values so notes/extras can carry signal.
    """
    parts: list[str] = []
    for name in (
        "company_name",
        "website",
        "industry",
        "contact_name",
        "contact_email",
        "contact_title",
        "company_size",
        "location",
        "source",
    ):
        value = _get(lead, name)
        if value:
            parts.append(value)
    cleaned = getattr(lead, "cleaned_data", None)
    if isinstance(cleaned, dict):
        for v in cleaned.values():
            if isinstance(v, str) and v.strip():
                parts.append(v)
    return " ".join(parts).lower()


# ---------------------------------------------------------------- scorers

def _score_industry(lead: Any) -> tuple[int, list[str]]:
    industry = (_get(lead, "industry") or "").lower()
    full_text = _gather_text(lead)
    matched: list[str] = []
    direct = False
    indirect = False

    for term in TARGET_INDUSTRIES:
        if term in industry:
            matched.append(term)
            direct = True
        elif term in full_text:
            matched.append(term)
            indirect = True

    matched = list(dict.fromkeys(matched))
    if direct:
        return 25, matched
    if indirect:
        return 15, matched
    return 0, matched


def _score_company_size(lead: Any) -> int:
    size = (_get(lead, "company_size") or "").lower()
    if not size:
        return 0

    if "enterprise" in size or "large" in size:
        return 15
    if "medium" in size:
        return 12
    if "small" in size:
        return 5

    if "1000+" in size or "1k+" in size:
        return 15
    if "501-1000" in size or "501 - 1000" in size:
        return 15
    if "201-500" in size or "201 - 500" in size:
        return 14
    if "51-200" in size or "51 - 200" in size:
        return 12
    if "11-50" in size or "11 - 50" in size:
        return 8
    if "1-10" in size or "1 - 10" in size:
        return 5

    numeric = re.fullmatch(r"\s*(\d+)\s*", size)
    if numeric:
        n = int(numeric.group(1))
        if n >= 500:
            return 15
        if n >= 200:
            return 14
        if n >= 50:
            return 12
        if n >= 11:
            return 8
        if n >= 1:
            return 5

    return 0


def _score_persona(lead: Any) -> tuple[int, list[str]]:
    title = (_get(lead, "contact_title") or "").lower()
    if not title:
        return 0, []
    matched = list(dict.fromkeys(t for t in TARGET_TITLES if t in title))
    return (15 if matched else 0), matched


def _score_pain_points(lead: Any) -> tuple[int, list[str]]:
    text = _gather_text(lead)
    if not text:
        return 0, []
    matched = list(dict.fromkeys(kw for kw in PAIN_POINT_KEYWORDS if kw in text))
    n = len(matched)
    if n >= 3:
        return 20, matched
    if n == 2:
        return 14, matched
    if n == 1:
        return 8, matched
    return 0, matched


def _score_data_completeness(lead: Any) -> int:
    score = 0
    for name in (
        "website",
        "industry",
        "contact_name",
        "contact_email",
        "contact_title",
    ):
        if _get(lead, name):
            score += 2
    return min(score, 10)


def _score_source(lead: Any) -> int:
    source = (_get(lead, "source") or "").lower()
    if not source:
        return 2
    if source in HIGH_QUALITY_SOURCES:
        return 10
    if source in MEDIUM_QUALITY_SOURCES:
        return 6
    return 2


def _score_penalties(lead: Any) -> int:
    penalty = 0

    email = (_get(lead, "contact_email") or "").lower()
    if "@" in email:
        domain = email.rsplit("@", 1)[-1].strip()
        if domain in FREE_EMAIL_DOMAINS:
            penalty -= 5

    if not _get(lead, "contact_title"):
        penalty -= 5

    status = (_get(lead, "status") or "").lower()
    if status in DISQUALIFIED_STATUSES:
        penalty -= 10

    return max(penalty, -15)


# ---------------------------------------------------------------- reasoning

def _reasoning(
    band: str,
    industry_matches: list[str],
    title_matches: list[str],
    pain_matches: list[str],
) -> str:
    if not (industry_matches or title_matches or pain_matches):
        return (
            f"{band} fit: no target industry, persona, or pain-point signals "
            "were found on this lead."
        )
    bits: list[str] = []
    if industry_matches:
        bits.append(f"target industry signals ({', '.join(industry_matches[:3])})")
    if title_matches:
        bits.append(f"persona match on title ({', '.join(title_matches[:3])})")
    if pain_matches:
        bits.append(f"pain-point keywords ({', '.join(pain_matches[:3])})")
    return f"{band} fit driven by " + " and ".join(bits) + "."


# ---------------------------------------------------------------- public

def score_lead(lead: Any) -> dict[str, Any]:
    """Score a lead from 0-100 with a transparent per-category breakdown.

    Accepts any object exposing the standard lead attributes. Missing fields
    are tolerated. Returns a dict matching the documented response shape.
    """
    industry_fit, industry_matches = _score_industry(lead)
    persona_fit, title_matches = _score_persona(lead)
    pain_fit, pain_matches = _score_pain_points(lead)
    size_fit = _score_company_size(lead)
    completeness = _score_data_completeness(lead)
    source_q = _score_source(lead)
    penalties = _score_penalties(lead)

    raw_total = (
        industry_fit
        + size_fit
        + persona_fit
        + pain_fit
        + completeness
        + source_q
        + penalties
    )
    total = max(0, min(100, raw_total))
    band = _band(total)

    return {
        "total_score": total,
        "priority": band,
        "score_breakdown": {
            "industry_fit": industry_fit,
            "company_size_fit": size_fit,
            "persona_title_fit": persona_fit,
            "pain_point_keywords": pain_fit,
            "data_completeness": completeness,
            "source_quality": source_q,
            "penalties": penalties,
        },
        "matched_signals": {
            "industry_terms": industry_matches,
            "title_terms": title_matches,
            "pain_point_terms": pain_matches,
        },
        "reasoning": _reasoning(band, industry_matches, title_matches, pain_matches),
    }
