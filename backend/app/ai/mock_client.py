"""Deterministic mock AI client. Same input -> same output, always.

Used by default (USE_MOCK_AI=true) so the lead-enrichment flow can be
demoed and tested with no OpenAI key or network access.

Generation is a pure function of the grounded context dict
(app/ai/grounding.py). Never reads the clock, never calls out, never
injects randomness.

Revision history: "mock-deterministic-v1" (recorded on historical rows)
guessed pain points from keywords anywhere in the lead -- including the
company name and industry -- and pitched GTMFlow itself. v2 states only
what the record contains, lists unknowns, and describes the seller only
from the selected seller revision.
"""

from __future__ import annotations

from typing import Any

from app.ai.client import AIClient


def _facts(ctx: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {fact["field"]: fact for fact in ctx["lead_facts"]}


def _value(facts: dict[str, dict[str, Any]], field: str) -> str | None:
    fact = facts.get(field)
    return fact["value"] if fact else None


def _confidence(ctx: dict[str, Any]) -> str:
    structured = [f for f in ctx["lead_facts"] if f["kind"] == "structured_field"]
    return "medium" if len(structured) >= 5 else "low"


def _record_sentence(facts: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    """What the record says about the company, and which fact ids that used.
    Free-text fields are never quoted into prose."""
    company = _value(facts, "company_name") or "This company"
    used = ["fact-company_name"] if "company_name" in facts else []
    parts = [f"The imported record lists {company}"]
    for field, template in (
        ("industry", "in {}"),
        ("location", "located in {}"),
        ("company_size", "with a size band of {}"),
    ):
        value = _value(facts, field)
        if value:
            parts.append(template.format(value))
            used.append(f"fact-{field}")
    return " ".join(parts) + ".", used


class MockAIClient(AIClient):
    name = "mock"
    # Bumped only if the deterministic generation logic in this file changes
    # shape in a way that matters for reproducing/explaining a past output.
    model_revision = "mock-deterministic-v2-grounded"

    def choose_tools(self, messages: list[dict], tools: list[dict]) -> dict:
        from app.ai.tool_calling import mock_tool_turn

        return mock_tool_turn(messages)

    def generate_company_summary(self, ctx: dict[str, Any]) -> dict[str, Any]:
        facts = _facts(ctx)
        sentence, _used = _record_sentence(facts)
        seller = ctx.get("seller")

        hypotheses: list[str] = []
        seller_relevance = None
        if seller is not None:
            seller_relevance = (
                f"Compared only with {seller['company_name']}'s stated target "
                "customers. The record does not show interest, need or budget."
            )
            industry = _value(facts, "industry")
            if industry:
                hypotheses.append(
                    f"Unconfirmed: the listed industry ({industry}) may fall "
                    "within the seller's stated target customers."
                )

        return {
            "company_summary": sentence,
            "evidence": [
                {"fact_id": fact["id"], "statement": f"Record field {fact['field']}: {fact['value']}"}
                for fact in ctx["lead_facts"]
            ],
            "unknowns": list(ctx["unknowns"]),
            "hypotheses": hypotheses,
            "seller_relevance": seller_relevance,
            "confidence": _confidence(ctx),
        }

    def generate_outreach(self, ctx: dict[str, Any]) -> dict[str, Any]:
        seller = ctx["seller"]
        facts = _facts(ctx)
        company = _value(facts, "company_name") or "your company"
        contact_name = _value(facts, "contact_name")
        first_name = contact_name.split()[0] if contact_name else ""
        sentence, used = _record_sentence(facts)
        if not used:
            # Nothing structured to cite; cite the first available fact.
            used = [ctx["lead_facts"][0]["id"]] if ctx["lead_facts"] else []

        capabilities = seller["capabilities"][:2]
        claims = seller["approved_claims"][:1]

        paragraphs = []
        if seller["profile_kind"] == "demo":
            paragraphs.append(
                "[Demonstration draft generated from a demonstration seller "
                "profile. Not a real offer.]"
            )
        paragraphs.append(f"Hi {first_name}," if first_name else "Hi there,")
        paragraphs.append(
            f"I'm writing from {seller['company_name']} about "
            f"{seller['product_name']}. {seller['value_proposition']}"
        )
        paragraphs.append(
            f"{sentence} I don't know whether this is a priority for your "
            "team right now, so I'd rather ask than assume."
        )
        if capabilities:
            paragraphs.append(
                "What it does: " + "; ".join(c["text"] for c in capabilities) + "."
            )
        if claims:
            paragraphs.append(claims[0]["claim"])
        paragraphs.append("Would a short conversation be useful to find out whether this is relevant?")
        paragraphs.append(f"Best,\n{seller['company_name']}")

        call_note = (
            f"{company}: confirm whether {seller['product_name']} is relevant. "
            "Intent, budget and current tools are unknown."
        )
        if not contact_name:
            call_note += " No contact person is on file."

        return {
            "subject": f"{seller['product_name']} for {company}",
            "email_body": "\n\n".join(paragraphs),
            "lead_facts_used": used,
            "capabilities_used": [c["id"] for c in capabilities],
            "claims_used": [c["id"] for c in claims],
            "unknowns_acknowledged": list(ctx["unknowns"]),
            "call_note": call_note,
            "confidence": _confidence(ctx),
        }
