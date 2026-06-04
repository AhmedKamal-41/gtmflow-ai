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

SUMMARY_KEYS = {
    "company_summary",
    "detected_pain_points",
    "fit_reasoning",
    "evidence",
    "inferences",
    "confidence",
}

OUTREACH_KEYS = {
    "subject",
    "email_body",
    "personalization_points",
    "call_note",
    "confidence",
}


def _hot_context() -> dict:
    return {
        "company_name": "Cascade Modular Homes",
        "industry": "Housing",
        "contact_name": "Sarah Chen",
        "contact_title": "VP Operations",
        "contact_email": "sarah@cascade.com",
        "company_size": "240",
        "website": "cascade.com",
        "location": "USA",
        "source": "referral",
        "cleaned_data": {
            "notes": "high tenant maintenance request volume and leasing tour scheduling"
        },
        "score": {"total_score": 92, "priority": "Hot"},
    }


def test_mock_summary_returns_valid_shape() -> None:
    client = MockAIClient()
    out = client.generate_company_summary(_hot_context())
    assert SUMMARY_KEYS.issubset(out.keys())
    assert out["confidence"] in {"low", "medium", "high"}
    assert isinstance(out["detected_pain_points"], list)
    assert isinstance(out["evidence"], list)
    assert isinstance(out["inferences"], list)


def test_mock_outreach_returns_valid_shape() -> None:
    client = MockAIClient()
    out = client.generate_outreach(_hot_context())
    assert OUTREACH_KEYS.issubset(out.keys())
    assert out["confidence"] in {"low", "medium", "high"}
    assert isinstance(out["personalization_points"], list)
    assert out["subject"]
    assert out["email_body"]
    assert out["call_note"]


def test_mock_output_is_deterministic() -> None:
    client = MockAIClient()
    ctx = _hot_context()
    a = client.generate_company_summary(ctx)
    b = client.generate_company_summary(ctx)
    c = client.generate_company_summary(ctx)
    assert a == b == c

    out_a = client.generate_outreach(ctx)
    out_b = client.generate_outreach(ctx)
    assert out_a == out_b


def test_missing_fields_do_not_crash_mock() -> None:
    client = MockAIClient()
    sparse = {"company_name": "Minimal Co"}
    s = client.generate_company_summary(sparse)
    o = client.generate_outreach(sparse)
    assert isinstance(s, dict) and SUMMARY_KEYS.issubset(s.keys())
    assert isinstance(o, dict) and OUTREACH_KEYS.issubset(o.keys())
    # confidence drops to low when the lead is essentially empty
    assert s["confidence"] == "low"
    assert o["confidence"] == "low"


def test_evidence_and_inferences_are_separate() -> None:
    client = MockAIClient()
    out = client.generate_company_summary(_hot_context())

    evidence_blob = " ".join(out["evidence"]).lower()
    # Evidence quotes the actual fields and their values.
    assert "industry:" in evidence_blob or "contact_title:" in evidence_blob

    # Every inference is explicitly labeled "Inference:" so a reader cannot
    # confuse the speculative content for fact.
    for inf in out["inferences"]:
        assert inf.startswith("Inference:")


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
    # evidence separated from inference
    assert "evidence" in rules and "inference" in rules
    # strict JSON only
    assert "strict json" in rules

    # Both prompt builders must inline these system rules.
    sample = {"company_name": "Sample Co"}
    assert SYSTEM_RULES in build_summary_prompt(sample)
    assert SYSTEM_RULES in build_outreach_prompt(sample)
