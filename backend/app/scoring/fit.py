"""Phase 4 (Parts B/C/D): the v2 deterministic company-fit scorer.

This is a NEW, independent scoring engine, deliberately kept separate from
the legacy v1 `score_lead` in app/scoring/lead_scoring.py (which is
unchanged and remains authoritative for every existing consumer -- API
responses, push gating, Slack payloads, metrics). Nothing here touches
`Lead.status`, `LeadBatch.status`, or any review/approval state.

## The demonstration profile: demo-us-sectors-v1

The seller's actual product and ideal-customer-profile are not yet defined
(that's Phase 5's `docs/upgrade` scope). This profile is an explicit,
labeled STAND-IN so the scoring *mechanism* (weights, coverage, bands,
versioning, storage, API/UI plumbing) can be built and verified against
the real imported cohort now, without waiting on and without pretending to
already have a real ICP. Its two active criteria are exact-match filters on
the fields already used as PDL import eligibility gates (see
app/pdl/config.py's INDUSTRY_ALIAS_MAP / COUNTRY_ALIAS_MAP) -- which is
exactly why every one of the 5,000 already-imported leads scores 100 under
it: they already passed these same two filters at import time. That is
EXPECTED, not a bug, and is not a claim that every healthcare business is a
clinic or every real-estate business is a property manager -- see
docs/upgrade/phase4-scoring-handoff.md.

Changing any weight, threshold, or mapping below is, by definition, a new
profile version -- bump PROFILE_VERSION (and NORMALIZATION_VERSION if a
mapping changes) rather than mutating this one in place.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCORER_VERSION = "fit-scorer-v1"
PROFILE_ID = "demo-us-sectors-v1"
PROFILE_VERSION = "1"
NORMALIZATION_VERSION = "fit-norm-v1"

# Exact (lowercased, whitespace-collapsed) source `industry` value -> match.
# Deliberately the same three strings PDL import eligibility already
# requires (app/pdl/config.py's INDUSTRY_ALIAS_MAP) -- independently
# declared and independently versioned here, since scoring and import
# eligibility are different concerns that happen to currently agree. A
# future profile version is free to diverge from the import filter without
# needing to touch import eligibility, and vice versa.
INDUSTRY_MATCH_VALUES: frozenset[str] = frozenset(
    {"hospital & health care", "medical practice", "real estate"}
)
INDUSTRY_WEIGHT = 60

# Exact (lowercased, whitespace-collapsed) structured country value -> match.
COUNTRY_MATCH_VALUES: frozenset[str] = frozenset({"united states"})
COUNTRY_WEIGHT = 40

# Company-size preference is not configured (no seller profile exists yet)
# -- present in every response as an explicit, inactive, zero-weight
# criterion rather than silently omitted, so its absence is visible instead
# of ambiguous with "not evaluated."
SIZE_WEIGHT = 0

# Criteria this profile explicitly assigns zero fit points to. Not
# per-lead-computed (nothing to compute -- weight is always 0), but
# declared here so the profile definition (exposed via GET
# /api/scoring/fit-profile) is a complete, honest account of what is and
# isn't part of the rubric.
ZERO_WEIGHT_CRITERIA: tuple[str, ...] = (
    "website",
    "contact_details",
    "founded_year",
    "company_name_keywords",
    "dataset_provider",
)

MAX_FIT_SCORE = INDUSTRY_WEIGHT + COUNTRY_WEIGHT + SIZE_WEIGHT  # 100
ACTIVE_CRITERION_WEIGHT_TOTAL = INDUSTRY_WEIGHT + COUNTRY_WEIGHT  # 100; size excluded (inactive)

COVERAGE_THRESHOLD_PCT = 80.0
BAND_STRONG_MIN = 80
BAND_PARTIAL_MIN = 50

BAND_STRONG = "strong_match"
BAND_PARTIAL = "partial_match"
BAND_WEAK = "weak_match"
BAND_INSUFFICIENT_EVIDENCE = "insufficient_evidence"

RESULT_MATCH = "match"
RESULT_MISMATCH = "mismatch"
RESULT_UNKNOWN = "unknown"
RESULT_NOT_CONFIGURED = "not_configured"


def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip().lower()
    return text or None


@dataclass
class Criterion:
    name: str
    weight: int
    active: bool
    source_field: str
    raw_input: Any
    normalized_input: str | None
    result: str
    points: int
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "weight": self.weight,
            "active": self.active,
            "source_field": self.source_field,
            "raw_input": self.raw_input,
            "normalized_input": self.normalized_input,
            "result": self.result,
            "points": self.points,
            "explanation": self.explanation,
        }


def _score_industry(lead: Any) -> Criterion:
    raw = getattr(lead, "industry", None)
    normalized = _normalize(raw)
    if normalized is None:
        return Criterion(
            "industry", INDUSTRY_WEIGHT, True, "lead.industry", raw, normalized,
            RESULT_UNKNOWN, 0,
            "No industry value on this lead -- unknown, not scored as a mismatch.",
        )
    if normalized in INDUSTRY_MATCH_VALUES:
        return Criterion(
            "industry", INDUSTRY_WEIGHT, True, "lead.industry", raw, normalized,
            RESULT_MATCH, INDUSTRY_WEIGHT,
            f"Industry '{raw}' exactly matches a target segment in profile "
            f"{PROFILE_ID} (hospital & health care / medical practice / real estate).",
        )
    return Criterion(
        "industry", INDUSTRY_WEIGHT, True, "lead.industry", raw, normalized,
        RESULT_MISMATCH, 0,
        f"Industry '{raw}' does not exactly match any target segment in "
        f"profile {PROFILE_ID}.",
    )


def _structured_country(lead: Any) -> Any:
    """Structured geography only (Part C): the PDL importer stores the
    source record's raw `country` field in `Lead.cleaned_data["country"]`
    (app/pdl/importer.py's `_lead_cleaned_data`) -- distinct from
    `Lead.location`, which is a free-text concatenation of
    locality/region/country and is never parsed here. A CSV-uploaded or
    demo lead has no structured country field at all, so this returns None
    for those, and the criterion is correctly reported unknown rather than
    guessed from free text.
    """
    cleaned = getattr(lead, "cleaned_data", None)
    if isinstance(cleaned, dict):
        return cleaned.get("country")
    return None


def _score_country(lead: Any) -> Criterion:
    raw = _structured_country(lead)
    normalized = _normalize(raw)
    if normalized is None:
        return Criterion(
            "country", COUNTRY_WEIGHT, True, "lead.cleaned_data.country", raw,
            normalized, RESULT_UNKNOWN, 0,
            "No structured country field on this lead (only available on "
            "PDL-imported leads) -- unknown, not scored as a mismatch. "
            "Ambiguous free-text location is never parsed for this criterion.",
        )
    if normalized in COUNTRY_MATCH_VALUES:
        return Criterion(
            "country", COUNTRY_WEIGHT, True, "lead.cleaned_data.country", raw,
            normalized, RESULT_MATCH, COUNTRY_WEIGHT,
            f"Structured country '{raw}' matches the supported normalization "
            "for United States.",
        )
    return Criterion(
        "country", COUNTRY_WEIGHT, True, "lead.cleaned_data.country", raw,
        normalized, RESULT_MISMATCH, 0,
        f"Structured country '{raw}' does not normalize to United States.",
    )


def _score_size(lead: Any) -> Criterion:
    raw = getattr(lead, "company_size", None)
    return Criterion(
        "company_size", SIZE_WEIGHT, False, "lead.company_size", raw, _normalize(raw),
        RESULT_NOT_CONFIGURED, 0,
        f"Company-size preference is not configured in profile {PROFILE_ID} "
        "-- inactive until an actual size preference is set (Phase 5).",
    )


@dataclass
class FitResult:
    scorer_version: str
    profile_id: str
    profile_version: str
    normalization_version: str
    input_fingerprint: str
    fit_score: int
    max_fit_score: int
    evidence_coverage_pct: float
    band: str
    criteria: list[Criterion]
    computed_at: datetime
    computation_ms: float

    def criteria_as_dicts(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.criteria]


def _input_fingerprint(lead: Any, criteria: list[Criterion]) -> str:
    """sha256 over exactly the raw inputs this scorer read plus every
    version identifier -- identical inputs under identical versions always
    produce the identical fingerprint (and therefore, given a pure
    function, the identical result), independent of *when* scoring ran
    (Part D.2). Deliberately excludes anything not actually consumed by a
    criterion (e.g. contact_email), so an unrelated field changing on the
    lead doesn't change the fingerprint.
    """
    payload = {
        "scorer_version": SCORER_VERSION,
        "profile_id": PROFILE_ID,
        "profile_version": PROFILE_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "inputs": {c.source_field: c.raw_input for c in criteria},
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _band(fit_score: int, coverage_pct: float) -> str:
    if coverage_pct < COVERAGE_THRESHOLD_PCT:
        return BAND_INSUFFICIENT_EVIDENCE
    if fit_score >= BAND_STRONG_MIN:
        return BAND_STRONG
    if fit_score >= BAND_PARTIAL_MIN:
        return BAND_PARTIAL
    return BAND_WEAK


def compute_fit(lead: Any) -> FitResult:
    """Pure, deterministic. Accepts anything exposing `industry`,
    `cleaned_data`, `company_size` (standard Lead attributes)."""
    start = time.perf_counter()

    industry = _score_industry(lead)
    country = _score_country(lead)
    size = _score_size(lead)
    criteria = [industry, country, size]

    fit_score = sum(c.points for c in criteria)

    # Coverage = share of ACTIVE criterion weight with an interpretable
    # (non-unknown) input, including known mismatches -- a mismatch is
    # still evidence; only "unknown" withholds it. Inactive criteria
    # (size, weight 0) never enter the denominator -- Part B: "missing
    # website doesn't reduce coverage when website isn't an active
    # criterion" applies identically to any inactive criterion.
    active = [c for c in criteria if c.active]
    interpretable_weight = sum(
        c.weight for c in active if c.result in (RESULT_MATCH, RESULT_MISMATCH)
    )
    active_weight = sum(c.weight for c in active) or 1  # guard: never zero here (100)
    coverage_pct = round(100.0 * interpretable_weight / active_weight, 1)

    band = _band(fit_score, coverage_pct)
    fingerprint = _input_fingerprint(lead, criteria)
    computation_ms = round((time.perf_counter() - start) * 1000, 3)

    return FitResult(
        scorer_version=SCORER_VERSION,
        profile_id=PROFILE_ID,
        profile_version=PROFILE_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        input_fingerprint=fingerprint,
        fit_score=fit_score,
        max_fit_score=MAX_FIT_SCORE,
        evidence_coverage_pct=coverage_pct,
        band=band,
        criteria=criteria,
        computed_at=datetime.now(timezone.utc),
        computation_ms=computation_ms,
    )


# --------------------------------------------------------------- readiness

GAP_NO_SELLER_PROFILE = "no_seller_profile_configured"
GAP_MISSING_CONTACT_EMAIL = "missing_contact_email"
GAP_NO_OUTREACH_DRAFT = "no_outreach_draft"
GAP_DRAFT_NOT_REVIEWED = "draft_not_reviewed"
GAP_DRAFT_REJECTED = "draft_rejected"
GAP_LEAD_EXCLUDED = "lead_excluded_from_routing"

READY = "ready"
NOT_READY = "not_ready"

_GAP_EXPLANATIONS: dict[str, str] = {
    GAP_NO_SELLER_PROFILE: (
        "No versioned seller product/ICP profile is configured yet (Phase 5 "
        "scope) -- outreach content today comes from GTMFlow's hardcoded "
        "demo pitch, not a configured seller context."
    ),
    GAP_MISSING_CONTACT_EMAIL: "Lead has no contact_email on file.",
    GAP_NO_OUTREACH_DRAFT: "No outreach draft has been generated for this lead yet.",
    GAP_DRAFT_NOT_REVIEWED: "The current outreach draft has not been approved or rejected yet.",
    GAP_DRAFT_REJECTED: "The current outreach draft was rejected and has not been replaced.",
    GAP_LEAD_EXCLUDED: "This lead is currently excluded from routing (see eligibility reasons).",
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
    """
    email_gaps: list[str] = [GAP_NO_SELLER_PROFILE]
    if not has_contact_email:
        email_gaps.append(GAP_MISSING_CONTACT_EMAIL)
    if not latest_outreach_exists:
        email_gaps.append(GAP_NO_OUTREACH_DRAFT)
    elif latest_outreach_review_decision == "rejected":
        email_gaps.append(GAP_DRAFT_REJECTED)
    elif latest_outreach_review_decision is None:
        email_gaps.append(GAP_DRAFT_NOT_REVIEWED)
    if eligibility_excluded:
        email_gaps.append(GAP_LEAD_EXCLUDED)

    handoff_gaps: list[str] = []
    if eligibility_excluded:
        handoff_gaps.append(GAP_LEAD_EXCLUDED)

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
    from app.scoring.lead_scoring import DISQUALIFIED_STATUSES

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
    "PROFILE_ID",
    "PROFILE_VERSION",
    "NORMALIZATION_VERSION",
    "INDUSTRY_MATCH_VALUES",
    "INDUSTRY_WEIGHT",
    "COUNTRY_MATCH_VALUES",
    "COUNTRY_WEIGHT",
    "SIZE_WEIGHT",
    "ZERO_WEIGHT_CRITERIA",
    "MAX_FIT_SCORE",
    "COVERAGE_THRESHOLD_PCT",
    "BAND_STRONG_MIN",
    "BAND_PARTIAL_MIN",
    "BAND_STRONG",
    "BAND_PARTIAL",
    "BAND_WEAK",
    "BAND_INSUFFICIENT_EVIDENCE",
    "Criterion",
    "FitResult",
    "compute_fit",
    "ReadinessResult",
    "ActionReadiness",
    "compute_readiness",
    "EligibilityResult",
    "compute_eligibility",
]
