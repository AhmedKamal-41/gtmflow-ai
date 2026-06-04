"""Deterministic mock AI client. Same input -> same output, always.

Used by default (USE_MOCK_AI=true) so the lead-enrichment flow can be
demoed and tested with no OpenAI key or network access.

Generation is a pure function of the lead context dict. Never reads the
clock, never calls out, never injects randomness.
"""

from __future__ import annotations

from typing import Any

from app.ai.client import AIClient

PAIN_KEYWORDS: tuple[str, ...] = (
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

HOUSING_INDUSTRY_TERMS = (
    "housing",
    "property",
    "multifamily",
    "apartment",
    "real estate",
)

HEALTHCARE_INDUSTRY_TERMS = (
    "healthcare",
    "clinic",
    "medical",
    "patient",
    "hospital",
    "dental",
    "urgent care",
)


def _walk_strings(value: Any) -> list[str]:
    """Yield every string nested anywhere inside the value."""
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_walk_strings(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_walk_strings(v))
    return out


def _detect_pain_terms(ctx: dict[str, Any]) -> list[str]:
    haystack = " ".join(_walk_strings(ctx)).lower()
    matched = [kw for kw in PAIN_KEYWORDS if kw in haystack]
    return list(dict.fromkeys(matched))


def _build_evidence(ctx: dict[str, Any]) -> list[str]:
    """Lead facts only -- evidence must never be invented."""
    evidence: list[str] = []
    for field in (
        "industry",
        "contact_title",
        "contact_name",
        "company_size",
        "source",
        "website",
        "location",
    ):
        value = ctx.get(field)
        if value:
            evidence.append(f"{field}: {value}")
    cleaned = ctx.get("cleaned_data")
    if isinstance(cleaned, dict):
        for k, v in cleaned.items():
            if v:
                evidence.append(f"cleaned_data.{k}: {v}")
    score = ctx.get("score")
    if isinstance(score, dict) and "total_score" in score and "priority" in score:
        evidence.append(
            f"deterministic_score: {score['total_score']}/100 ({score['priority']})"
        )
    return evidence


def _build_inferences(ctx: dict[str, Any]) -> list[str]:
    """Speculative hypotheses, clearly distinct from evidence."""
    inferences: list[str] = []
    industry = (ctx.get("industry") or "").lower()
    if any(t in industry for t in HOUSING_INDUSTRY_TERMS):
        inferences.append(
            "Inference: may benefit from automated tenant-facing maintenance "
            "and leasing-scheduling workflows."
        )
    if any(t in industry for t in HEALTHCARE_INDUSTRY_TERMS):
        inferences.append(
            "Inference: may benefit from patient appointment scheduling and "
            "intake-form automation."
        )
    title = (ctx.get("contact_title") or "").lower()
    if "ops" in title or "operations" in title:
        inferences.append(
            "Inference: operations-focused persona is usually receptive to "
            "workflow-automation pitches."
        )
    if not inferences:
        inferences.append(
            "Inference: lead data is sparse -- recommend enrichment before "
            "an outbound touch."
        )
    return inferences


def _confidence_level(ctx: dict[str, Any]) -> str:
    filled = sum(
        1
        for field in (
            "industry",
            "contact_title",
            "contact_email",
            "company_size",
            "website",
        )
        if ctx.get(field)
    )
    has_score = isinstance(ctx.get("score"), dict)
    if filled >= 4 and has_score:
        return "high"
    if filled >= 3:
        return "medium"
    return "low"


def _focus_pain(ctx: dict[str, Any], pains: list[str]) -> str:
    if pains:
        return pains[0]
    industry = (ctx.get("industry") or "").lower()
    if any(t in industry for t in HOUSING_INDUSTRY_TERMS):
        return "leasing and maintenance"
    if any(t in industry for t in HEALTHCARE_INDUSTRY_TERMS):
        return "patient intake and scheduling"
    return "operations workflow"


class MockAIClient(AIClient):
    name = "mock"

    def generate_company_summary(self, ctx: dict[str, Any]) -> dict[str, Any]:
        company = ctx.get("company_name") or "this company"
        industry = ctx.get("industry") or "an unspecified industry"
        title = ctx.get("contact_title")
        size = ctx.get("company_size")
        location = ctx.get("location")

        summary_parts = [f"{company} operates in {industry}."]
        if title:
            summary_parts.append(f"Primary contact holds the title {title}.")
        if size:
            summary_parts.append(f"Approximate size: {size}.")
        if location:
            summary_parts.append(f"Based in {location}.")

        pains = _detect_pain_terms(ctx)
        if pains:
            summary_parts.append(
                "Public-facing details surface workflow themes: "
                + ", ".join(pains[:3])
                + "."
            )

        company_summary = " ".join(summary_parts)

        score = ctx.get("score")
        if isinstance(score, dict):
            fit_reasoning = (
                f"{score['priority']} fit (score {score['total_score']}/100). "
                "The deterministic scorer flagged industry, persona, and "
                "pain-point signals; this summary mirrors those signals."
            )
        else:
            fit_reasoning = (
                "Fit not yet scored. Run POST /api/leads/{id}/score to anchor "
                "this reasoning to the deterministic 100-point model."
            )

        return {
            "company_summary": company_summary,
            "detected_pain_points": pains,
            "fit_reasoning": fit_reasoning,
            "evidence": _build_evidence(ctx),
            "inferences": _build_inferences(ctx),
            "confidence": _confidence_level(ctx),
        }

    def generate_outreach(self, ctx: dict[str, Any]) -> dict[str, Any]:
        company = ctx.get("company_name") or "your team"
        contact_name = ctx.get("contact_name") or ""
        title = ctx.get("contact_title") or "your team"
        industry = ctx.get("industry") or "your industry"

        latest_summary = ctx.get("latest_summary")
        if isinstance(latest_summary, dict) and isinstance(
            latest_summary.get("detected_pain_points"), list
        ):
            pains = list(latest_summary["detected_pain_points"])
        else:
            pains = _detect_pain_terms(ctx)

        focus = _focus_pain(ctx, pains)
        subject = f"Quick idea for {company}'s {focus} workflow"

        first_name = contact_name.split()[0] if contact_name else ""
        greeting = f"Hi {first_name}," if first_name else "Hi there,"

        if pains:
            pain_line = (
                f"In {industry}, teams often hit friction around "
                f"{', '.join(pains[:3])}."
            )
        else:
            pain_line = (
                f"In {industry}, operations teams often hit friction in "
                "workflow handoffs."
            )

        pitch = (
            "GTMFlow turns lead lists into prioritized outreach with a "
            f"full audit trail, so {title} can focus on the work the model "
            "flags as Hot."
        )
        ask = "Worth a 15-minute call this week to compare notes?"

        email_body = "\n\n".join(
            [
                greeting,
                pain_line,
                pitch,
                ask,
                "Best,\nGTMFlow team",
            ]
        )

        personalization_points: list[str] = []
        if title and title != "your team":
            personalization_points.append(f"Role-specific framing: {title}")
        if ctx.get("industry"):
            personalization_points.append(f"Industry framing: {industry}")
        if pains:
            personalization_points.append(
                "Pain-point hook: " + ", ".join(pains[:2])
            )
        score = ctx.get("score")
        if isinstance(score, dict):
            personalization_points.append(
                f"Deterministic score anchor: {score['total_score']}/100 "
                f"({score['priority']})"
            )
        if not personalization_points:
            personalization_points.append(
                "Generic framing -- lead data is sparse; recommend enrichment "
                "before sending."
            )

        call_note_parts = [f"{company} ({industry})."]
        if isinstance(score, dict):
            call_note_parts.append(
                f"Score {score['total_score']}/100 ({score['priority']})."
            )
        call_note_parts.append(f"Open with the {focus} workflow angle.")
        if pains:
            call_note_parts.append(
                "Pain hooks: " + ", ".join(pains[:3]) + "."
            )
        call_note = " ".join(call_note_parts)

        return {
            "subject": subject,
            "email_body": email_body,
            "personalization_points": personalization_points,
            "call_note": call_note,
            "confidence": _confidence_level(ctx),
        }
