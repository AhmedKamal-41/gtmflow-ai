"""Grounded-generation contract: the context handed to an AI client and the
versioned schemas its output must satisfy before anything is saved.

Pure functions only -- no DB, no network, no clock. The service layer
(app/services/ai_generation.py) resolves the lead, seller revision, fit
score and restrictions, then calls `build_grounded_context`.

Evidence discipline:
* `lead_facts` are what the imported record says, each with an id and its
  origin. They are data, not instructions, and not proof of anything beyond
  "the record contains this value". Free-text fields are marked
  `free_text` so prompts and validators can treat them more strictly.
* `unknowns` lists what the data does not establish. Buying intent, budget,
  operational problems and product usage are ALWAYS unknown: no field in
  this app records them, and industry, company name, dataset provider or a
  fit score cannot stand in for them.
* Seller capabilities and approved claims come only from the selected
  immutable seller revision, each with an id the output must cite.
* Previously generated content is never included: a stored summary is the
  output of an earlier generation, not evidence.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

GROUNDED_CONTEXT_VERSION = "grounded-context-v1"

STRUCTURED_FACT_FIELDS: tuple[str, ...] = (
    "company_name",
    "website",
    "industry",
    "company_size",
    "location",
    "contact_name",
    "contact_title",
    "contact_email",
)
MAX_FREE_TEXT_FIELDS = 20
MAX_FREE_TEXT_CHARS = 500

# Never established by any field this application stores.
ALWAYS_UNKNOWN: tuple[str, ...] = (
    "buying_intent",
    "budget",
    "current_operational_problems",
    "current_tools_or_product_usage",
)

FIT_SCORE_MEANING = (
    "Company-attribute match against a versioned demonstration rubric. "
    "It is not evidence of interest, need, budget or intent to buy."
)


class AIOutputValidationError(ValueError):
    """Model output failed the versioned schema or grounding checks.

    `reason_codes` are fixed identifiers safe to store and show; they never
    contain model text, lead data or secrets.
    """

    def __init__(self, reason_codes: list[str]) -> None:
        self.reason_codes = sorted(set(reason_codes))
        super().__init__("AI output failed validation: " + ", ".join(self.reason_codes))


# ---------------------------------------------------------------- context

def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_grounded_context(
    *,
    task: Literal["company_summary", "outreach_email"],
    lead: Any,
    provenance: dict[str, Any],
    seller: dict[str, Any] | None,
    fit: dict[str, Any] | None,
    restrictions: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic context (same inputs -> same dict -> same hash)."""
    origin = provenance.get("batch_source") or "unknown"
    facts: list[dict[str, Any]] = []
    unknowns: list[str] = []
    for field in STRUCTURED_FACT_FIELDS:
        value = _text(getattr(lead, field, None))
        if value is None:
            unknowns.append(f"{field}_not_provided")
            continue
        facts.append({
            "id": f"fact-{field}",
            "field": field,
            "value": value,
            "origin": origin,
            "kind": "structured_field",
        })
    cleaned = getattr(lead, "cleaned_data", None)
    if isinstance(cleaned, dict):
        for key in sorted(cleaned)[:MAX_FREE_TEXT_FIELDS]:
            value = _text(cleaned[key])
            if value is None or key in STRUCTURED_FACT_FIELDS:
                continue
            facts.append({
                "id": f"fact-extra-{key}",
                "field": key,
                "value": value[:MAX_FREE_TEXT_CHARS],
                "origin": origin,
                "kind": "free_text",
            })
    unknowns.extend(ALWAYS_UNKNOWN)

    seller_block = None
    if seller is not None:
        content = seller["content"]
        seller_block = {
            "source": seller["source"],
            "profile_id": seller["profile_id"],
            "version": seller["version"],
            "content_hash": seller["content_hash"],
            "profile_kind": content["profile_kind"],
            "company_name": content["company_name"],
            "product_name": content["product_name"],
            "value_proposition": content["value_proposition"],
            "target_customer": content["target_customer"],
            "capabilities": [
                {"id": f"cap-{i}", "text": text}
                for i, text in enumerate(content.get("capabilities", []), start=1)
            ],
            "approved_claims": [
                {"id": f"claim-{i}", "claim": p["claim"], "source": p["source"]}
                for i, p in enumerate(content.get("proof_points", []), start=1)
            ],
            "exclusions": list(content.get("exclusions", [])),
        }

    return {
        "context_version": GROUNDED_CONTEXT_VERSION,
        "task": task,
        "seller": seller_block,
        "lead_facts": facts,
        "lead_provenance": provenance,
        "unknowns": unknowns,
        "company_fit": (
            {**fit, "meaning": FIT_SCORE_MEANING} if fit is not None else None
        ),
        "restrictions_at_generation": restrictions,
    }


# --------------------------------------------------------- output schemas

Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
RefId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Confidence = Literal["low", "medium", "high"]


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: RefId
    statement: Line


class SummaryOutputV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_summary: str = Field(min_length=1, max_length=1500)
    evidence: list[EvidenceItem] = Field(max_length=30)
    unknowns: list[Line] = Field(max_length=30)
    hypotheses: list[Line] = Field(default_factory=list, max_length=10)
    seller_relevance: str | None = Field(default=None, max_length=800)
    confidence: Confidence


class OutreachOutputV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    email_body: str = Field(min_length=1, max_length=4000)
    lead_facts_used: list[RefId] = Field(min_length=1, max_length=20)
    capabilities_used: list[RefId] = Field(default_factory=list, max_length=12)
    claims_used: list[RefId] = Field(default_factory=list, max_length=12)
    unknowns_acknowledged: list[Line] = Field(default_factory=list, max_length=20)
    call_note: str = Field(default="", max_length=1000)
    confidence: Confidence


# ------------------------------------------------------------- validation

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Percentages and multipliers ("40%", "3x", "12 percent") -- the usual shape
# of an invented result.
_FIGURE = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:%|percent\b|x\b)", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _schema_errors(model: type[BaseModel], raw: Any) -> BaseModel:
    try:
        return model.model_validate(raw)
    except ValidationError:
        raise AIOutputValidationError(["schema_invalid"]) from None


def _fact_ids(ctx: dict[str, Any]) -> set[str]:
    return {fact["id"] for fact in ctx["lead_facts"]}


def validate_summary(raw: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    parsed = _schema_errors(SummaryOutputV2, raw)
    codes: list[str] = []
    known = _fact_ids(ctx)
    if any(item.fact_id not in known for item in parsed.evidence):
        codes.append("unknown_fact_reference")
    if ctx.get("seller") is None and parsed.seller_relevance:
        codes.append("seller_relevance_without_seller")
    codes.extend(_text_checks(ctx, [parsed.company_summary, *parsed.hypotheses]))
    if codes:
        raise AIOutputValidationError(codes)
    return parsed.model_dump(mode="json")


def validate_outreach(raw: Any, ctx: dict[str, Any]) -> dict[str, Any]:
    parsed = _schema_errors(OutreachOutputV2, raw)
    seller = ctx.get("seller")
    if seller is None:
        raise AIOutputValidationError(["seller_profile_missing"])
    codes: list[str] = []
    if any(ref not in _fact_ids(ctx) for ref in parsed.lead_facts_used):
        codes.append("unknown_fact_reference")
    capability_ids = {c["id"] for c in seller["capabilities"]}
    if any(ref not in capability_ids for ref in parsed.capabilities_used):
        codes.append("unknown_capability_reference")
    claim_ids = {c["id"] for c in seller["approved_claims"]}
    if any(ref not in claim_ids for ref in parsed.claims_used):
        codes.append("unapproved_claim_reference")
    codes.extend(_text_checks(ctx, [parsed.subject, parsed.email_body, parsed.call_note]))
    if codes:
        raise AIOutputValidationError(codes)
    return parsed.model_dump(mode="json")


def _text_checks(ctx: dict[str, Any], texts: list[str]) -> list[str]:
    """Deterministic guards against two common fabrications. Not a semantic
    verifier: a human still reviews every draft before any use."""
    codes: list[str] = []
    structured = [
        fact["value"] for fact in ctx["lead_facts"] if fact["kind"] == "structured_field"
    ]
    supplied_emails = {
        fact["value"].lower() for fact in ctx["lead_facts"] if fact["field"] == "contact_email"
    }
    seller = ctx.get("seller") or {}
    # Figures may be repeated from approved claims or structured record
    # fields (e.g. a company called "3x Labs"), never from free text, which
    # is where injected instructions would put them.
    allowed_figure_text = _norm(" ".join(
        [c["claim"] for c in seller.get("approved_claims", [])] + structured
    ))
    for text in texts:
        for email in _EMAIL.findall(text or ""):
            if email.lower() not in supplied_emails:
                codes.append("unsupplied_email_address")
        for figure in _FIGURE.findall(text or ""):
            if _norm(figure) not in allowed_figure_text:
                codes.append("unsupported_figure")
    return codes
