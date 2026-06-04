from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.scoring.lead_scoring import _band, score_lead


def _lead(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "company_name": "Test Co",
        "website": None,
        "industry": None,
        "contact_name": None,
        "contact_email": None,
        "contact_title": None,
        "company_size": None,
        "location": None,
        "source": None,
        "status": None,
        "cleaned_data": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_hot_property_management_lead() -> None:
    lead = _lead(
        company_name="Cascade Property Mgmt",
        industry="property management",
        contact_title="Director of Leasing",
        contact_name="Sarah Chen",
        contact_email="sarah@cascade.com",
        website="cascade.com",
        company_size="201-500",
        source="referral",
        cleaned_data={"notes": "needs better tenant maintenance scheduling"},
    )
    result = score_lead(lead)
    assert result["priority"] == "Hot"
    assert result["total_score"] >= 80
    assert "property management" in result["matched_signals"]["industry_terms"]
    assert "director of leasing" in result["matched_signals"]["title_terms"]
    assert {"tenant", "maintenance", "scheduling"}.issubset(
        set(result["matched_signals"]["pain_point_terms"])
    )


def test_hot_healthcare_lead() -> None:
    lead = _lead(
        company_name="Northbridge Clinics",
        industry="clinic",
        contact_title="Practice Manager",
        contact_name="Anika Rao",
        contact_email="anika@northbridge.com",
        website="northbridge.com",
        company_size="1000+",
        source="webinar",
        cleaned_data={
            "notes": "high patient call volume, appointment scheduling pain"
        },
    )
    result = score_lead(lead)
    assert result["priority"] == "Hot"
    assert result["total_score"] >= 80
    assert "clinic" in result["matched_signals"]["industry_terms"]
    assert "practice manager" in result["matched_signals"]["title_terms"]


def test_irrelevant_retail_lead_scores_cold() -> None:
    lead = _lead(
        company_name="Vault Outfitters",
        industry="Retail",
        contact_title="COO",
        contact_name="Jamie Russo",
        contact_email="hello@vault.com",
        company_size="11-50",
        source="csv",
    )
    result = score_lead(lead)
    assert result["priority"] == "Cold"
    assert result["score_breakdown"]["industry_fit"] == 0
    assert result["score_breakdown"]["persona_title_fit"] == 0


def test_missing_fields_do_not_crash() -> None:
    lead = _lead(company_name="Minimal Co")
    result = score_lead(lead)
    assert isinstance(result["total_score"], int)
    assert 0 <= result["total_score"] <= 100
    assert result["priority"] in {"Hot", "Warm", "Cold"}
    assert "score_breakdown" in result
    assert "matched_signals" in result


def test_missing_website_or_email_lowers_data_completeness() -> None:
    full = _lead(
        company_name="A",
        website="a.com",
        industry="X",
        contact_name="Y",
        contact_email="y@a.com",
        contact_title="Property Manager",
    )
    missing_web_and_email = _lead(
        company_name="A",
        industry="X",
        contact_name="Y",
        contact_title="Property Manager",
    )
    full_dc = score_lead(full)["score_breakdown"]["data_completeness"]
    partial_dc = score_lead(missing_web_and_email)["score_breakdown"]["data_completeness"]
    assert full_dc == 10
    assert partial_dc == 6
    assert full_dc > partial_dc


def test_target_title_increases_persona_score() -> None:
    with_target = _lead(company_name="A", contact_title="Property Manager")
    without = _lead(company_name="A", contact_title="Software Engineer")
    with_score = score_lead(with_target)["score_breakdown"]["persona_title_fit"]
    without_score = score_lead(without)["score_breakdown"]["persona_title_fit"]
    assert with_score == 15
    assert without_score == 0


def test_pain_point_keywords_increase_score() -> None:
    with_pain = _lead(
        company_name="A",
        cleaned_data={"notes": "tenant maintenance scheduling"},
    )
    without_pain = _lead(company_name="A")
    assert score_lead(with_pain)["score_breakdown"]["pain_point_keywords"] == 20
    assert score_lead(without_pain)["score_breakdown"]["pain_point_keywords"] == 0


def test_total_score_clamped_between_0_and_100() -> None:
    # Maxed positives.
    maxed = _lead(
        company_name="Top",
        industry="property management",
        contact_title="Property Manager",
        contact_name="Ops Lead",
        contact_email="ops@top.com",
        website="top.com",
        company_size="1000+",
        location="USA",
        source="referral",
        cleaned_data={
            "notes": "leasing maintenance tenant scheduling appointment patient"
        },
    )
    r = score_lead(maxed)
    assert 0 <= r["total_score"] <= 100

    # Maximum penalties + no positives -> clamped to 0.
    minimal = _lead(company_name="X", status="disqualified")
    r2 = score_lead(minimal)
    assert r2["total_score"] >= 0


def test_priority_band_boundaries_are_correct() -> None:
    assert _band(0) == "Cold"
    assert _band(54) == "Cold"
    assert _band(55) == "Warm"
    assert _band(79) == "Warm"
    assert _band(80) == "Hot"
    assert _band(100) == "Hot"


def test_non_dict_cleaned_data_is_ignored_safely() -> None:
    """cleaned_data is JSON in the DB -- if anything weird sneaks in, scoring
    must not raise. The scorer's _gather_text guards with isinstance(..., dict).
    """
    list_data = _lead(company_name="A", cleaned_data=["not", "a", "dict"])
    str_data = _lead(company_name="B", cleaned_data="not a dict")
    int_data = _lead(company_name="C", cleaned_data=42)

    for lead in (list_data, str_data, int_data):
        result = score_lead(lead)
        assert isinstance(result["total_score"], int)
        assert 0 <= result["total_score"] <= 100
        assert result["priority"] in {"Hot", "Warm", "Cold"}
        # No pain-point keywords are drawn from a malformed cleaned_data blob.
        assert result["score_breakdown"]["pain_point_keywords"] == 0


def test_scoring_is_deterministic() -> None:
    lead = _lead(
        company_name="Cascade",
        industry="property management",
        contact_title="Property Manager",
        contact_email="ops@cascade.com",
        contact_name="Sam Lead",
        website="cascade.com",
        company_size="201-500",
        source="referral",
        cleaned_data={"notes": "leasing scheduling"},
    )
    first = score_lead(lead)
    second = score_lead(lead)
    third = score_lead(lead)
    assert first == second == third
