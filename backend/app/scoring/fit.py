"""Deterministic lead ranking against the seller's target customers.

One scorer ranks every lead in the app (lists, filters, dashboards, batch
jobs, the assistant and delivery eligibility). It compares the facts on a
lead record with the structured `target_preferences` of a seller-profile
revision -- who the seller wants to sell to -- and never calls a model.

## Criteria and default weights

| Criterion      | Weight | Lead fields read                                         |
|----------------|--------|----------------------------------------------------------|
| industry       | 35     | `industry`                                               |
| company_size   | 25     | `company_size` (a count or a range such as "51-200")     |
| location       | 20     | `location`, and `cleaned_data` locality/region/country   |
| contact_role   | 15     | `contact_title`                                          |
| keywords       | 5      | free-text `cleaned_data` values (notes, descriptions...) |

Only criteria the profile configures take part. Their weights are rescaled
to 100, so a profile that sets only industries and sizes ranks on those two
alone (35 and 25 become 58.3 and 41.7 of 100).

## Per-criterion results

* `match` -- full weight. `partial` -- half weight (company size only: the
  recorded range overlaps a target range without lying inside it).
* `mismatch` -- the lead has a value and it is outside the targets: 0.
* `unknown` -- the lead has no usable value: 0, and reported as missing
  evidence, never as a mismatch.
* `not_configured` -- the profile states no preference: weight 0, shown
  so its absence is visible.

## Score, coverage and bands

    fit_score = round(100 * earned weight / configured weight)      0..100
    coverage  = 100 * weight with a known result / configured weight

Unknown criteria earn nothing, so a high score always rests on evidence.
Bands: coverage below COVERAGE_MIN_PCT (50) -> `insufficient_evidence`
("needs data") whatever the score; otherwise score >= 75 -> strong (Hot),
>= 45 -> partial (Warm), else weak (Cold).

Text is compared as whole words after normalization (case, punctuation,
"&"/"and", simple plurals and a few abbreviations), so "hospital" does not
match "hospitality" and "clinic" matches "Clinics". It is not semantic:
"Austin, TX" is not recognized as being in "United States".

A match describes recorded company attributes only. It is not evidence of
interest, need, budget or intent to buy. Contact completeness and
permission to contact (blocked statuses) are separate from fit: see
`compute_readiness` and `compute_eligibility` below.

## Versioning

`targeting_hash` covers the target preferences, the weights, the thresholds
and the scorer/normalization versions. Stored scores are current only while
their hash equals the active profile's, so changing preferences (or this
file's rules) makes earlier scores outdated without deleting them.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

SCORER_VERSION = "fit-scorer-v2"
NORMALIZATION_VERSION = "fit-norm-v2"

CRITERION_INDUSTRY = "industry"
CRITERION_SIZE = "company_size"
CRITERION_LOCATION = "location"
CRITERION_ROLE = "contact_role"
CRITERION_KEYWORDS = "keywords"

# Default weights (sum 100), in display order.
WEIGHTS: dict[str, int] = {
    CRITERION_INDUSTRY: 35,
    CRITERION_SIZE: 25,
    CRITERION_LOCATION: 20,
    CRITERION_ROLE: 15,
    CRITERION_KEYWORDS: 5,
}
LABELS: dict[str, str] = {
    CRITERION_INDUSTRY: "Industry",
    CRITERION_SIZE: "Company size",
    CRITERION_LOCATION: "Location",
    CRITERION_ROLE: "Contact role",
    CRITERION_KEYWORDS: "Keywords",
}
# target_preferences key for each criterion.
PREFERENCE_KEYS: dict[str, str] = {
    CRITERION_INDUSTRY: "industries",
    CRITERION_SIZE: "company_sizes",
    CRITERION_LOCATION: "locations",
    CRITERION_ROLE: "contact_roles",
    CRITERION_KEYWORDS: "keywords",
}

MAX_FIT_SCORE = 100
HOT_MIN = 75
WARM_MIN = 45
COVERAGE_MIN_PCT = 50.0
PARTIAL_CREDIT = 0.5

BAND_STRONG = "strong_match"
BAND_PARTIAL = "partial_match"
BAND_WEAK = "weak_match"
BAND_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
BANDS = (BAND_STRONG, BAND_PARTIAL, BAND_WEAK, BAND_INSUFFICIENT_EVIDENCE)

PRIORITY_HOT = "Hot"
PRIORITY_WARM = "Warm"
PRIORITY_COLD = "Cold"
PRIORITY_BY_BAND: dict[str, str | None] = {
    BAND_STRONG: PRIORITY_HOT,
    BAND_PARTIAL: PRIORITY_WARM,
    BAND_WEAK: PRIORITY_COLD,
    BAND_INSUFFICIENT_EVIDENCE: None,
}
BAND_BY_PRIORITY: dict[str, str] = {p: b for b, p in PRIORITY_BY_BAND.items() if p}

RESULT_MATCH = "match"
RESULT_PARTIAL = "partial"
RESULT_MISMATCH = "mismatch"
RESULT_UNKNOWN = "unknown"
RESULT_NOT_CONFIGURED = "not_configured"
_CREDIT = {RESULT_MATCH: 1.0, RESULT_PARTIAL: PARTIAL_CREDIT, RESULT_MISMATCH: 0.0}

SOURCE_ACTIVE = "active_revision"
SOURCE_BUILTIN_DEMO = "builtin_demo"

# cleaned_data keys that describe where a company is, read for location.
LOCATION_KEYS = ("locality", "region", "country")
# cleaned_data keys that are not a description of the company, so never
# searched for keywords (our own import labels, places, dates, URLs).
NON_DESCRIPTIVE_KEYS = frozenset({
    "candidate_segment", "segment_is_candidate_classification_only",
    "locality", "region", "country", "founded", "linkedin_url",
})


class RankingSetupRequired(ValueError):
    """The seller profile states no target customers, so nothing can be ranked."""


def priority_for_band(band: str | None) -> str | None:
    return PRIORITY_BY_BAND.get(band) if band else None


# ------------------------------------------------------------ normalization

_STOPWORDS = frozenset({"and", "of", "the", "for", "in", "a", "an", "at", "to"})
# Token-sequence rewrites applied to both sides (after singularization), so
# common spellings and abbreviations compare equal as whole words.
_REWRITES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = tuple(sorted((
    (("health", "care"), ("healthcare",)),
    (("e", "commerce"), ("ecommerce",)),
    (("united", "state", "america"), ("us",)),
    (("united", "state"), ("us",)),
    (("u", "s", "a"), ("us",)),
    (("u", "s"), ("us",)),
    (("usa",), ("us",)),
    (("united", "kingdom"), ("uk",)),
    (("great", "britain"), ("uk",)),
    (("vice", "president"), ("vp",)),
    (("chief", "executive", "officer"), ("ceo",)),
    (("chief", "operating", "officer"), ("coo",)),
    (("chief", "financial", "officer"), ("cfo",)),
    (("chief", "technology", "officer"), ("cto",)),
    (("chief", "marketing", "officer"), ("cmo",)),
    (("chief", "revenue", "officer"), ("cro",)),
    (("sr",), ("senior",)),
    (("mgr",), ("manager",)),
    (("dir",), ("director",)),
    (("ops",), ("operation",)),
), key=lambda pair: -len(pair[0])))


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize_tokens(text: Any) -> tuple[str, ...]:
    if text is None:
        return ()
    lowered = str(text).lower().replace("&", " and ")
    words = [_singular(w) for w in re.split(r"[^a-z0-9+]+", lowered) if w and w not in _STOPWORDS]
    out: list[str] = []
    i = 0
    while i < len(words):
        for old, new in _REWRITES:
            if tuple(words[i:i + len(old)]) == old:
                out.extend(new)
                i += len(old)
                break
        else:
            out.append(words[i])
            i += 1
    return tuple(out)


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    n = len(needle)
    return n > 0 and any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def phrase_in_text(phrase: Any, text: Any) -> bool:
    """Whether `phrase` occurs in `text` as whole normalized words."""
    return _contains(normalize_tokens(text), normalize_tokens(phrase))


def _matching_phrases(text_tokens: tuple[str, ...], phrases: list[str]) -> list[str]:
    return [p for p in phrases if _contains(text_tokens, normalize_tokens(p))]


def _display(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


# ------------------------------------------------------------ company size

_RANGE = re.compile(r"^(\d+)\s*(?:-|–|—|to)\s*(\d+)$")
_OPEN = re.compile(r"^(\d+)\s*\+$")
_EXACT = re.compile(r"^(\d+)$")


def parse_company_size(raw: Any) -> tuple[int, int | None] | None:
    """(low, high) employees; high None means open-ended. None when the value
    is not a number or a numeric range ("small", "enterprise", "")."""
    text = _display(raw)
    if text is None:
        return None
    text = re.sub(r"(?i)\b(employees?|staff|people)\b", "", text).replace(",", "").strip()
    if m := _RANGE.match(text):
        low, high = sorted((int(m.group(1)), int(m.group(2))))
        return low, high
    if m := _OPEN.match(text):
        return int(m.group(1)), None
    if m := _EXACT.match(text):
        return int(m.group(1)), int(m.group(1))
    return None


def _merge_ranges(ranges: list[dict[str, Any]]) -> list[tuple[int, float]]:
    spans = sorted(
        (int(r["min_employees"]), math.inf if r.get("max_employees") is None else int(r["max_employees"]))
        for r in ranges
    )
    merged: list[tuple[int, float]] = []
    for low, high in spans:
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def format_size_range(low: int, high: int | float | None) -> str:
    if high is None or high == math.inf:
        return f"{low:,}+"
    return f"{low:,}" if low == high else f"{low:,}–{int(high):,}"


# ------------------------------------------------------------ profile

def normalize_targeting(preferences: dict[str, Any] | None) -> dict[str, Any]:
    """Canonical target preferences: trimmed, de-duplicated (by normalized
    words), in a stable order, so the hash only changes when the meaning
    could. Unknown keys are ignored."""
    preferences = preferences or {}

    def phrases(key: str) -> list[str]:
        seen: dict[tuple[str, ...], str] = {}
        for value in preferences.get(key) or []:
            text = _display(value)
            tokens = normalize_tokens(text)
            if text and tokens and tokens not in seen:
                seen[tokens] = text
        return sorted(seen.values(), key=lambda v: (normalize_tokens(v), v))

    sizes = []
    for r in preferences.get("company_sizes") or []:
        low, high = r.get("min_employees"), r.get("max_employees")
        if isinstance(low, int) and low >= 1 and (high is None or (isinstance(high, int) and high >= low)):
            sizes.append({"min_employees": low, "max_employees": high})
    sizes = sorted({(s["min_employees"], s["max_employees"] or 0): s for s in sizes}.values(),
                   key=lambda s: (s["min_employees"], s["max_employees"] or math.inf))
    return {
        "industries": phrases("industries"),
        "locations": phrases("locations"),
        "company_sizes": sizes,
        "contact_roles": phrases("contact_roles"),
        "keywords": phrases("keywords"),
    }


def configured_criteria(targeting: dict[str, Any]) -> list[str]:
    return [name for name, key in PREFERENCE_KEYS.items() if targeting.get(key)]


def _canonical(targeting: dict[str, Any]) -> dict[str, Any]:
    """Targets as the normalized words matching compares, so only a change
    that could alter a result changes the hash (not case or spacing)."""
    out: dict[str, Any] = {"company_sizes": targeting["company_sizes"]}
    for key in ("industries", "locations", "contact_roles", "keywords"):
        out[key] = sorted({" ".join(normalize_tokens(v)) for v in targeting[key]})
    return out


def targeting_hash(targeting: dict[str, Any]) -> str:
    payload = {
        "scorer_version": SCORER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "weights": WEIGHTS,
        "thresholds": {"hot": HOT_MIN, "warm": WARM_MIN, "coverage": COVERAGE_MIN_PCT,
                       "partial_credit": PARTIAL_CREDIT},
        "targeting": _canonical(normalize_targeting(targeting)),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RankingProfile:
    """The target preferences of one seller-profile revision, as ranking
    uses them. `profile_id` is the revision's UUID as text, or
    "builtin-demo" for the labeled demonstration profile."""

    source: str
    profile_id: str
    seller_profile_id: UUID | None
    version: str
    profile_kind: str
    targeting: dict[str, Any]
    targeting_hash: str

    @property
    def configured(self) -> list[str]:
        return configured_criteria(self.targeting)

    @property
    def is_configured(self) -> bool:
        return bool(self.configured)

    def describe(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "profile_id": self.profile_id,
            "seller_profile_id": str(self.seller_profile_id) if self.seller_profile_id else None,
            "version": self.version,
            "profile_kind": self.profile_kind,
            "targeting": self.targeting,
            "targeting_hash": self.targeting_hash,
            "configured_criteria": self.configured,
        }


def ranking_profile_from_content(
    content: dict[str, Any], *, source: str, profile_id: str,
    seller_profile_id: UUID | None, version: str,
) -> RankingProfile:
    targeting = normalize_targeting(content.get("target_preferences"))
    return RankingProfile(
        source=source,
        profile_id=profile_id,
        seller_profile_id=seller_profile_id,
        version=version,
        profile_kind=str(content.get("profile_kind") or "seller"),
        targeting=targeting,
        targeting_hash=targeting_hash(targeting),
    )


# ------------------------------------------------------------ criteria

@dataclass
class Criterion:
    name: str
    weight: int
    active: bool
    source_field: str
    raw_input: Any
    normalized_input: str | None
    result: str
    points: float
    explanation: str
    targets: list[str] = field(default_factory=list)
    matched_targets: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LABELS[self.name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "weight": self.weight,
            "active": self.active,
            "source_field": self.source_field,
            "raw_input": self.raw_input,
            "normalized_input": self.normalized_input,
            "result": self.result,
            "points": self.points,
            "explanation": self.explanation,
            "targets": self.targets,
            "matched_targets": self.matched_targets,
        }


def _criterion(name: str, source_field: str, raw: Any, targets: list[str], result: str,
               explanation: str, matched: list[str] | None = None,
               normalized: str | None = None) -> Criterion:
    active = result != RESULT_NOT_CONFIGURED
    return Criterion(
        name=name, weight=WEIGHTS[name] if active else 0, active=active,
        source_field=source_field, raw_input=raw,
        normalized_input=normalized if normalized is not None else (" ".join(normalize_tokens(raw)) or None),
        result=result, points=0.0, explanation=explanation, targets=targets,
        matched_targets=matched or [],
    )


def _not_configured(name: str, source_field: str, raw: Any) -> Criterion:
    return _criterion(name, source_field, raw, [], RESULT_NOT_CONFIGURED,
                      f"The seller profile states no {LABELS[name].lower()} preference, so it does not count.")


def _phrase_criterion(name: str, source_field: str, raw: Any, targets: list[str], noun: str) -> Criterion:
    if not targets:
        return _not_configured(name, source_field, raw)
    shown = _display(raw)
    if shown is None or not normalize_tokens(shown):
        return _criterion(name, source_field, raw, targets, RESULT_UNKNOWN,
                          f"The lead has no {noun} on record, so this is missing information, not a mismatch.")
    matched = _matching_phrases(normalize_tokens(shown), targets)
    if matched:
        return _criterion(name, source_field, raw, targets, RESULT_MATCH,
                          f"{noun.capitalize()} '{shown}' matches the target {', '.join(matched)}.", matched)
    return _criterion(name, source_field, raw, targets, RESULT_MISMATCH,
                      f"{noun.capitalize()} '{shown}' does not match any target ({', '.join(targets)}).")


def _score_industry(lead: Any, targeting: dict[str, Any]) -> Criterion:
    return _phrase_criterion(CRITERION_INDUSTRY, "lead.industry", getattr(lead, "industry", None),
                             targeting["industries"], "industry")


def _score_role(lead: Any, targeting: dict[str, Any]) -> Criterion:
    return _phrase_criterion(CRITERION_ROLE, "lead.contact_title", getattr(lead, "contact_title", None),
                             targeting["contact_roles"], "contact title")


def _location_text(lead: Any) -> str | None:
    parts = [_display(getattr(lead, "location", None))]
    cleaned = getattr(lead, "cleaned_data", None)
    if isinstance(cleaned, dict):
        parts += [_display(cleaned.get(key)) if isinstance(cleaned.get(key), str) else None
                  for key in LOCATION_KEYS]
    seen: list[str] = []
    for part in parts:
        if part and part.lower() not in (s.lower() for s in seen):
            seen.append(part)
    return ", ".join(seen) or None


def _score_location(lead: Any, targeting: dict[str, Any]) -> Criterion:
    criterion = _phrase_criterion(CRITERION_LOCATION, "lead.location + cleaned_data.locality/region/country",
                                  _location_text(lead), targeting["locations"], "location")
    if criterion.result == RESULT_MISMATCH:
        criterion.explanation += " Places are matched by name only; a city or state is not resolved to its country."
    return criterion


def _score_size(lead: Any, targeting: dict[str, Any]) -> Criterion:
    raw = getattr(lead, "company_size", None)
    ranges = targeting["company_sizes"]
    if not ranges:
        return _not_configured(CRITERION_SIZE, "lead.company_size", raw)
    merged = _merge_ranges(ranges)
    targets = [format_size_range(r["min_employees"], r["max_employees"]) for r in ranges]
    shown = _display(raw)
    parsed = parse_company_size(raw)
    if parsed is None:
        why = ("The lead has no company size on record" if shown is None
               else f"Recorded size '{shown}' is not a number or numeric range")
        return _criterion(CRITERION_SIZE, "lead.company_size", raw, targets, RESULT_UNKNOWN,
                          f"{why}, so this is missing information, not a mismatch.", normalized="")
    low, high = parsed
    high_value = math.inf if high is None else high
    normalized = format_size_range(low, high)
    inside = [format_size_range(a, b) for a, b in merged if a <= low and high_value <= b]
    if inside:
        return _criterion(CRITERION_SIZE, "lead.company_size", raw, targets, RESULT_MATCH,
                          f"{normalized} employees is within the target {inside[0]}.", inside, normalized)
    overlap = [format_size_range(a, b) for a, b in merged if a <= high_value and low <= b]
    if overlap:
        return _criterion(CRITERION_SIZE, "lead.company_size", raw, targets, RESULT_PARTIAL,
                          f"{normalized} employees only partly overlaps the target {overlap[0]} (half credit).",
                          overlap, normalized)
    return _criterion(CRITERION_SIZE, "lead.company_size", raw, targets, RESULT_MISMATCH,
                      f"{normalized} employees is outside every target size ({', '.join(targets)}).",
                      normalized=normalized)


def _description_text(lead: Any) -> str | None:
    cleaned = getattr(lead, "cleaned_data", None)
    if not isinstance(cleaned, dict):
        return None
    parts = [_display(cleaned[k]) for k in sorted(cleaned)
             if k not in NON_DESCRIPTIVE_KEYS and isinstance(cleaned[k], str)]
    return " | ".join(p for p in parts if p) or None


def _score_keywords(lead: Any, targeting: dict[str, Any]) -> Criterion:
    criterion = _phrase_criterion(CRITERION_KEYWORDS, "lead.cleaned_data (free text)",
                                  _description_text(lead), targeting["keywords"], "description")
    if criterion.result == RESULT_UNKNOWN:
        criterion.explanation = ("The lead has no notes or description to search, so this is missing "
                                 "information, not a mismatch.")
    elif criterion.result == RESULT_MISMATCH:
        criterion.explanation = ("None of the keywords (" + ", ".join(criterion.targets)
                                 + ") appear in the lead's notes or description.")
    elif criterion.result == RESULT_MATCH:
        criterion.explanation = ("The lead's notes or description mention "
                                 + ", ".join(criterion.matched_targets) + ".")
    return criterion


# ------------------------------------------------------------ result

@dataclass
class FitResult:
    scorer_version: str
    profile_id: str
    profile_version: str
    normalization_version: str
    seller_profile_id: UUID | None
    targeting_hash: str
    input_fingerprint: str
    fit_score: int
    max_fit_score: int
    evidence_coverage_pct: float
    band: str
    criteria: list[Criterion]
    computed_at: datetime
    computation_ms: float

    @property
    def priority(self) -> str | None:
        return priority_for_band(self.band)

    def criteria_as_dicts(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.criteria]


def summarize_criteria(criteria: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Labels grouped by outcome, for a short "why" line."""
    out: dict[str, list[str]] = {"matched": [], "partial": [], "mismatched": [], "missing": [],
                                 "not_configured": []}
    key = {RESULT_MATCH: "matched", RESULT_PARTIAL: "partial", RESULT_MISMATCH: "mismatched",
           RESULT_UNKNOWN: "missing", RESULT_NOT_CONFIGURED: "not_configured"}
    for c in criteria:
        if c.get("result") in key:
            out[key[c["result"]]].append(c.get("label") or LABELS.get(c.get("name", ""), c.get("name", "")))
    return out


def explain(criteria: list[dict[str, Any]], band: str) -> str:
    """One sentence for Slack and lists: what matched, what did not, what is missing."""
    s = summarize_criteria(criteria)
    bits = []
    if s["matched"]:
        bits.append("matches " + ", ".join(s["matched"]).lower())
    if s["partial"]:
        bits.append("partly matches " + ", ".join(s["partial"]).lower())
    if s["mismatched"]:
        bits.append("outside the target " + ", ".join(s["mismatched"]).lower())
    if s["missing"]:
        bits.append("no data for " + ", ".join(s["missing"]).lower())
    lead_in = ("Not enough information to rank confidently" if band == BAND_INSUFFICIENT_EVIDENCE
               else "Target-customer fit")
    return f"{lead_in}: " + ("; ".join(bits) if bits else "nothing to compare") + "."


def _input_fingerprint(profile: RankingProfile, criteria: list[Criterion]) -> str:
    """sha256 over the inputs this scorer read plus the targeting hash (which
    already covers every version and rule), so identical inputs under the
    identical targeting give the identical fingerprint whenever it runs."""
    payload = {
        "scorer_version": SCORER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "targeting_hash": profile.targeting_hash,
        "inputs": {c.source_field: c.raw_input for c in criteria if c.active},
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _band(fit_score: int, coverage_pct: float) -> str:
    if coverage_pct < COVERAGE_MIN_PCT:
        return BAND_INSUFFICIENT_EVIDENCE
    if fit_score >= HOT_MIN:
        return BAND_STRONG
    if fit_score >= WARM_MIN:
        return BAND_PARTIAL
    return BAND_WEAK


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def compute_fit(lead: Any, profile: RankingProfile) -> FitResult:
    """Pure and deterministic. Accepts anything exposing the standard Lead
    attributes. Raises RankingSetupRequired when the profile states no
    target customers -- there is no fallback rubric."""
    if not profile.is_configured:
        raise RankingSetupRequired(
            "The seller profile has no target customers. Add industries, locations, "
            "company sizes, contact roles or keywords before ranking leads."
        )
    start = time.perf_counter()
    t = profile.targeting
    criteria = [
        _score_industry(lead, t),
        _score_size(lead, t),
        _score_location(lead, t),
        _score_role(lead, t),
        _score_keywords(lead, t),
    ]
    active = [c for c in criteria if c.active]
    total = sum(c.weight for c in active)
    earned = 0.0
    known = 0
    for c in active:
        credit = _CREDIT.get(c.result)
        if credit is None:
            continue
        known += c.weight
        c.points = round(c.weight * credit, 2)
        earned += c.weight * credit
    fit_score = _round_half_up(100.0 * earned / total)
    coverage_pct = round(100.0 * known / total, 1)
    band = _band(fit_score, coverage_pct)
    return FitResult(
        scorer_version=SCORER_VERSION,
        profile_id=profile.profile_id,
        profile_version=profile.version,
        normalization_version=NORMALIZATION_VERSION,
        seller_profile_id=profile.seller_profile_id,
        targeting_hash=profile.targeting_hash,
        input_fingerprint=_input_fingerprint(profile, criteria),
        fit_score=fit_score,
        max_fit_score=MAX_FIT_SCORE,
        evidence_coverage_pct=coverage_pct,
        band=band,
        criteria=criteria,
        computed_at=datetime.now(timezone.utc),
        computation_ms=round((time.perf_counter() - start) * 1000, 3),
    )


# --------------------------------------------------------------- readiness

GAP_NO_SELLER_PROFILE = "no_seller_profile_configured"
GAP_MISSING_CONTACT_EMAIL = "missing_contact_email"
GAP_NO_OUTREACH_DRAFT = "no_outreach_draft"
GAP_DRAFT_NOT_REVIEWED = "draft_not_reviewed"
GAP_DRAFT_REJECTED = "draft_rejected"
GAP_LEAD_EXCLUDED = "lead_excluded_from_routing"
# Phase 5: seller-profile state is read live, like every other gap.
GAP_SELLER_PROFILE_DEMO = "seller_profile_is_demonstration"
GAP_DRAFT_NOT_FROM_ACTIVE_PROFILE = "draft_not_from_active_seller_profile"
# Phase 6: an approval only authorizes the exact content and inputs it saw.
GAP_DRAFT_INPUTS_CHANGED = "draft_inputs_changed_since_generation"
GAP_APPROVAL_WITHOUT_CONTENT_IDENTITY = "approval_does_not_identify_content"

SELLER_STATE_NONE = "none"
SELLER_STATE_DEMO = "demo"
SELLER_STATE_SELLER = "seller"

READY = "ready"
NOT_READY = "not_ready"

_GAP_EXPLANATIONS: dict[str, str] = {
    GAP_NO_SELLER_PROFILE: (
        "No seller profile revision is active. Outreach generation needs an "
        "explicitly activated, reviewed revision."
    ),
    GAP_SELLER_PROFILE_DEMO: (
        "The active seller profile is a labeled demonstration, not a real "
        "offer, so outbound email cannot be ready."
    ),
    GAP_DRAFT_NOT_FROM_ACTIVE_PROFILE: (
        "The current outreach draft was not generated from the active seller "
        "profile revision (it predates grounded generation or used another "
        "revision). Generate a new draft."
    ),
    GAP_MISSING_CONTACT_EMAIL: "Lead has no contact_email on file.",
    GAP_NO_OUTREACH_DRAFT: "No outreach draft has been generated for this lead yet.",
    GAP_DRAFT_NOT_REVIEWED: "The current outreach draft has not been approved or rejected yet.",
    GAP_DRAFT_REJECTED: "The current outreach draft was rejected and has not been replaced.",
    GAP_LEAD_EXCLUDED: "This lead is currently excluded from routing (see eligibility reasons).",
    GAP_DRAFT_INPUTS_CHANGED: (
        "Company facts, fit, restrictions or seller content changed after this "
        "draft was generated. Generate a new draft and review it."
    ),
    GAP_APPROVAL_WITHOUT_CONTENT_IDENTITY: (
        "The approval on record predates exact-content review and does not "
        "identify the content it approved. Review the draft again."
    ),
}


@dataclass
class ActionReadiness:
    status: str
    gaps: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "gaps": self.gaps,
            "gap_explanations": {g: _GAP_EXPLANATIONS[g] for g in self.gaps},
        }


@dataclass
class ReadinessResult:
    outbound_email: ActionReadiness
    internal_slack_handoff: ActionReadiness

    def to_dict(self) -> dict[str, Any]:
        return {
            "outbound_email": self.outbound_email.to_dict(),
            "internal_slack_handoff": self.internal_slack_handoff.to_dict(),
        }


def compute_readiness(
    lead: Any,
    *,
    has_contact_email: bool,
    latest_outreach_exists: bool,
    latest_outreach_review_decision: str | None,
    eligibility_excluded: bool,
    seller_profile_state: str = SELLER_STATE_NONE,
    latest_outreach_uses_active_profile: bool = False,
    outreach_review_blockers: list[str] | None = None,
    email_review_blockers: list[str] | None = None,
) -> ReadinessResult:
    """Deterministic status + ordered gap list for two distinct actions
    (Part C.3/E): outbound email send readiness, and the internal Slack
    handoff readiness. These are different actions with different
    requirements -- missing contact_email blocks outbound email but says
    nothing about whether the company was researched internally.

    `latest_outreach_review_decision` is None when no review exists yet
    (draft generated but never approved/rejected), "approved", or
    "rejected" -- the caller resolves this from AIOutputReview so this
    function stays pure/testable.

    `seller_profile_state` is the live activation state ("none", "demo" or
    "seller"); `latest_outreach_uses_active_profile` says whether the
    current draft was generated from exactly the active revision. Both
    default to the most restrictive value.

    `outreach_review_blockers` (Phase 6) is the ordered list of reasons the
    current draft's review can't authorize delivery right now, from the
    shared review state (app/services/draft_review.py) that Slack delivery
    also enforces. When given, it replaces the draft/review checks above
    for both actions, so readiness and delivery can never disagree.
    `email_review_blockers` (defaults to the same list) can add stricter
    reasons for outbound email only -- e.g. a built-in demonstration draft
    may be handed off internally in the demo but is never an email to send.
    """
    email_gaps: list[str] = []
    if seller_profile_state == SELLER_STATE_NONE:
        email_gaps.append(GAP_NO_SELLER_PROFILE)
    elif seller_profile_state == SELLER_STATE_DEMO:
        email_gaps.append(GAP_SELLER_PROFILE_DEMO)
    if not has_contact_email:
        email_gaps.append(GAP_MISSING_CONTACT_EMAIL)
    if not latest_outreach_exists:
        email_gaps.append(GAP_NO_OUTREACH_DRAFT)
    elif (
        seller_profile_state != SELLER_STATE_NONE
        and not latest_outreach_uses_active_profile
    ):
        email_gaps.append(GAP_DRAFT_NOT_FROM_ACTIVE_PROFILE)
    elif latest_outreach_review_decision == "rejected":
        email_gaps.append(GAP_DRAFT_REJECTED)
    elif latest_outreach_review_decision is None:
        email_gaps.append(GAP_DRAFT_NOT_REVIEWED)
    if eligibility_excluded:
        email_gaps.append(GAP_LEAD_EXCLUDED)

    handoff_gaps: list[str] = []
    if eligibility_excluded:
        handoff_gaps.append(GAP_LEAD_EXCLUDED)

    if outreach_review_blockers is not None:
        # Same order as above: seller state, contact, draft/review, exclusion.
        email_gaps = [
            g for g in email_gaps
            if g in (GAP_NO_SELLER_PROFILE, GAP_SELLER_PROFILE_DEMO, GAP_MISSING_CONTACT_EMAIL)
        ]
        email_gaps += list(
            email_review_blockers
            if email_review_blockers is not None
            else outreach_review_blockers
        )
        if eligibility_excluded:
            email_gaps.append(GAP_LEAD_EXCLUDED)
        handoff_gaps = list(outreach_review_blockers) + handoff_gaps

    return ReadinessResult(
        outbound_email=ActionReadiness(
            NOT_READY if email_gaps else READY, email_gaps
        ),
        internal_slack_handoff=ActionReadiness(
            NOT_READY if handoff_gaps else READY, handoff_gaps
        ),
    )


# -------------------------------------------------------------- eligibility

@dataclass
class EligibilityResult:
    excluded: bool
    reasons: list[str]
    checked_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "excluded": self.excluded,
            "reasons": self.reasons,
            "checked_at": self.checked_at.isoformat(),
        }


def compute_eligibility(lead: Any) -> EligibilityResult:
    """Current hard exclusions only -- mirrors exactly what
    app/services/integration_push.py and app/api/push.py actually enforce
    today (DISQUALIFIED_STATUSES, INCOMPLETE_BATCH_STATUSES), recomputed fresh from
    live lead/batch state every call. Never accepts a `force` parameter:
    per Part C.4, no score and no force flag can override a hard exclusion,
    so this function doesn't expose a way to ask it to.
    """
    from app.models.lead_batch import INCOMPLETE_BATCH_STATUSES
    from app.models.lead import DISQUALIFIED_STATUSES

    reasons: list[str] = []
    status_value = getattr(lead, "status", None)
    if status_value in DISQUALIFIED_STATUSES:
        reasons.append(f"lead status '{status_value}' blocks outreach delivery")

    batch = getattr(lead, "batch", None)
    batch_status = getattr(batch, "status", None) if batch is not None else None
    if batch_status in INCOMPLETE_BATCH_STATUSES:
        reasons.append(
            f"batch {batch.id} is only partially imported (status='{batch_status}')"
        )

    return EligibilityResult(
        excluded=bool(reasons),
        reasons=reasons,
        checked_at=datetime.now(timezone.utc),
    )


__all__ = [
    "SCORER_VERSION",
    "NORMALIZATION_VERSION",
    "WEIGHTS",
    "HOT_MIN",
    "WARM_MIN",
    "COVERAGE_MIN_PCT",
    "BANDS",
    "RankingProfile",
    "RankingSetupRequired",
    "ranking_profile_from_content",
    "targeting_hash",
    "Criterion",
    "FitResult",
    "compute_fit",
    "explain",
    "summarize_criteria",
    "ReadinessResult",
    "ActionReadiness",
    "compute_readiness",
    "EligibilityResult",
    "compute_eligibility",
]
