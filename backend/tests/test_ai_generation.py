from __future__ import annotations

import pytest

from app.ai import (
    AIClient,
    AIConfigError,
    AIJSONParseError,
    MockAIClient,
    OpenAIClient,
    get_ai_client,
    parse_json_strict,
)

from types import SimpleNamespace

from app.ai.grounding import (
    ALWAYS_UNKNOWN,
    build_grounded_context,
    validate_outreach,
    validate_summary,
)

SUMMARY_KEYS = {
    "company_summary",
    "evidence",
    "unknowns",
    "hypotheses",
    "seller_relevance",
    "confidence",
}

OUTREACH_KEYS = {
    "subject",
    "email_body",
    "lead_facts_used",
    "capabilities_used",
    "claims_used",
    "unknowns_acknowledged",
    "call_note",
    "confidence",
}

SELLER = {
    "source": "active_revision",
    "profile_id": "00000000-0000-0000-0000-000000000001",
    "version": 1,
    "content_hash": "a" * 64,
    "content": {
        "profile_kind": "seller",
        "company_name": "Synthetic Seller",
        "product_name": "Synthetic Scheduler",
        "value_proposition": "Synthetic value proposition.",
        "target_customer": "Synthetic customers.",
        "capabilities": ["Books appointments"],
        "proof_points": [{"claim": "Synthetic claim", "source": "fixture"}],
        "exclusions": [],
    },
}


def _context(task: str = "outreach_email", seller=SELLER, **lead_fields) -> dict:
    fields = {
        "company_name": "Cascade Modular Homes",
        "industry": "Housing",
        "contact_name": "Sarah Chen",
        "contact_title": "VP Operations",
        "contact_email": "sarah@cascade.com",
        "company_size": "240",
        "website": "cascade.com",
        "location": "USA",
        "cleaned_data": {"notes": "high tenant maintenance request volume"},
    }
    fields.update(lead_fields)
    lead = SimpleNamespace(**{k: fields.get(k) for k in (
        "company_name", "industry", "contact_name", "contact_title",
        "contact_email", "company_size", "website", "location", "cleaned_data",
    )})
    return build_grounded_context(
        task=task,
        lead=lead,
        provenance={"lead_id": "L", "batch_source": "csv"},
        seller=seller,
        fit={"fit_score": 100, "band": "strong_match"},
        restrictions={"excluded": False, "reasons": []},
    )


def test_mock_summary_returns_valid_shape() -> None:
    client = MockAIClient()
    ctx = _context("company_summary")
    out = client.generate_company_summary(ctx)
    assert set(out) == SUMMARY_KEYS
    assert validate_summary(out, ctx) == out


def test_mock_outreach_returns_valid_shape() -> None:
    client = MockAIClient()
    ctx = _context()
    out = client.generate_outreach(ctx)
    assert set(out) == OUTREACH_KEYS
    assert validate_outreach(out, ctx) == out
    assert out["subject"] and out["email_body"] and out["call_note"]


def test_mock_output_is_deterministic() -> None:
    client = MockAIClient()
    ctx = _context()
    a = client.generate_company_summary(ctx)
    b = client.generate_company_summary(ctx)
    c = client.generate_company_summary(ctx)
    assert a == b == c

    out_a = client.generate_outreach(ctx)
    out_b = client.generate_outreach(ctx)
    assert out_a == out_b


def test_missing_fields_do_not_crash_mock() -> None:
    client = MockAIClient()
    sparse = dict.fromkeys(
        ["industry", "contact_name", "contact_title", "contact_email",
         "company_size", "website", "location", "cleaned_data"]
    )
    ctx = _context(**sparse, company_name="Minimal Co")
    s = client.generate_company_summary(ctx)
    o = client.generate_outreach(ctx)
    assert validate_summary(s, ctx) and validate_outreach(o, ctx)
    # confidence drops to low when the lead is essentially empty
    assert s["confidence"] == "low"
    assert o["confidence"] == "low"
    assert "Hi there," in o["email_body"]  # no invented contact name
    assert "industry_not_provided" in s["unknowns"]


def test_evidence_cites_facts_and_intent_stays_unknown() -> None:
    client = MockAIClient()
    ctx = _context("company_summary")
    out = client.generate_company_summary(ctx)

    fact_ids = {fact["id"] for fact in ctx["lead_facts"]}
    assert {item["fact_id"] for item in out["evidence"]} <= fact_ids
    for unknown in ALWAYS_UNKNOWN:
        assert unknown in out["unknowns"]
    # Every hypothesis is explicitly labeled unconfirmed.
    for hypothesis in out["hypotheses"]:
        assert hypothesis.startswith("Unconfirmed:")


def test_use_mock_ai_default_returns_mock_client() -> None:
    # In the test env USE_MOCK_AI is unset -> defaults to True.
    client = get_ai_client()
    assert isinstance(client, AIClient)
    assert isinstance(client, MockAIClient)


def test_openai_client_missing_key_raises_clean_error() -> None:
    # No network call -- failure happens at construction time.
    with pytest.raises(AIConfigError) as exc:
        OpenAIClient(api_key="")
    assert "OPENAI_API_KEY" in str(exc.value)


def test_json_parser_rejects_non_object() -> None:
    with pytest.raises(AIJSONParseError):
        parse_json_strict("not json at all")
    with pytest.raises(AIJSONParseError):
        parse_json_strict("[1, 2, 3]")
    with pytest.raises(AIJSONParseError):
        parse_json_strict("")


def test_json_parser_strips_code_fences() -> None:
    out = parse_json_strict('```json\n{"a": 1}\n```')
    assert out == {"a": 1}
    out2 = parse_json_strict('```\n{"k": "v"}\n```')
    assert out2 == {"k": "v"}


def test_prompt_guardrails_present_in_system_rules() -> None:
    """The real-OpenAI prompts must always carry the anti-hallucination guardrails.

    Phase 5 promised: only-provided-data, never-invent-facts, evidence-vs-inference,
    and strict-JSON output. If any of these phrases drift out of SYSTEM_RULES, the
    real-mode prompts lose their teeth -- this test pins them down.
    """
    from app.ai.prompts import SYSTEM_RULES, build_outreach_prompt, build_summary_prompt

    rules = SYSTEM_RULES.lower()
    # only the provided data
    assert "only" in rules and ("data" in rules or "provide" in rules)
    # no invented facts
    assert "never invent" in rules or "do not invent" in rules
    # imported data is not instructions
    assert "untrusted_data" in rules and "never as instructions" in rules
    # no intent/budget inference from industry, name, provider or fit score
    assert "do not infer buying intent" in rules and "fit score" in rules
    # claims only from the seller's approved claims
    assert "approved_claims" in rules
    # strict JSON only
    assert "strict json" in rules

    # Both prompt builders must inline these system rules.
    sample = _context()
    assert SYSTEM_RULES in build_summary_prompt(sample)
    assert SYSTEM_RULES in build_outreach_prompt(sample)
