"""Phase 4 Parts B/C/D: unit tests for the v2 deterministic company-fit
scorer (app/scoring/fit.py), independent of the API/DB layer.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.scoring import fit


def _lead(**overrides):
    base = dict(
        id="lead-1",
        industry=None,
        company_size=None,
        cleaned_data=None,
        status="new",
        batch=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ------------------------------------------------------------- fit scoring

def test_full_match_scores_100_with_full_coverage():
    lead = _lead(
        industry="Hospital & Health Care",
        cleaned_data={"country": "United States"},
    )
    result = fit.compute_fit(lead)
    assert result.fit_score == 100
    assert result.evidence_coverage_pct == 100.0
    assert result.band == fit.BAND_STRONG


def test_real_estate_industry_alias_matches():
    lead = _lead(industry="Real Estate", cleaned_data={"country": "United States"})
    result = fit.compute_fit(lead)
    assert result.fit_score == 100


def test_hospitality_does_not_match_hospital():
    """Explicit Part C requirement: 'hospitality must not match hospital'."""
    lead = _lead(industry="Hospitality", cleaned_data={"country": "United States"})
    result = fit.compute_fit(lead)
    industry_criterion = next(c for c in result.criteria if c.name == "industry")
    assert industry_criterion.result == fit.RESULT_MISMATCH
    assert industry_criterion.points == 0
    assert result.fit_score == 40  # country still matched


def test_unknown_industry_scores_zero_and_still_counts_as_evidence_gap():
    lead = _lead(industry=None, cleaned_data={"country": "United States"})
    result = fit.compute_fit(lead)
    industry_criterion = next(c for c in result.criteria if c.name == "industry")
    assert industry_criterion.result == fit.RESULT_UNKNOWN
    assert industry_criterion.points == 0
    assert result.fit_score == 40
    # 40/100 active weight interpretable (country only) -> 40% coverage.
    assert result.evidence_coverage_pct == 40.0
    assert result.band == fit.BAND_INSUFFICIENT_EVIDENCE


def test_known_mismatch_counts_as_evidence_not_missing():
    """A mismatch is still interpretable evidence -- coverage must not be
    penalized just because the criterion didn't match."""
    lead = _lead(industry="Retail", cleaned_data={"country": "United States"})
    result = fit.compute_fit(lead)
    assert result.evidence_coverage_pct == 100.0  # both criteria interpretable
    assert result.fit_score == 40  # only country matched


def test_country_never_parsed_from_free_text_location():
    """Only the structured cleaned_data.country field counts -- a
    plausible-looking free-text `location` must NOT be used."""
    lead = _lead(
        industry="Real Estate",
        cleaned_data=None,
        location="Austin, Texas, United States",
    )
    result = fit.compute_fit(lead)
    country_criterion = next(c for c in result.criteria if c.name == "country")
    assert country_criterion.result == fit.RESULT_UNKNOWN
    assert country_criterion.raw_input is None


def test_non_us_country_is_a_mismatch_not_unknown():
    lead = _lead(industry="Real Estate", cleaned_data={"country": "Canada"})
    result = fit.compute_fit(lead)
    country_criterion = next(c for c in result.criteria if c.name == "country")
    assert country_criterion.result == fit.RESULT_MISMATCH
    assert result.fit_score == 60


def test_company_size_criterion_is_always_present_but_inactive():
    lead = _lead(industry="Real Estate", cleaned_data={"country": "United States"}, company_size="1000+")
    result = fit.compute_fit(lead)
    size_criterion = next(c for c in result.criteria if c.name == "company_size")
    assert size_criterion.active is False
    assert size_criterion.weight == 0
    assert size_criterion.points == 0
    assert size_criterion.result == fit.RESULT_NOT_CONFIGURED
    # An inactive criterion must never affect coverage's denominator.
    assert result.evidence_coverage_pct == 100.0


def test_no_evidence_at_all_yields_zero_score_and_insufficient_evidence():
    lead = _lead()
    result = fit.compute_fit(lead)
    assert result.fit_score == 0
    assert result.evidence_coverage_pct == 0.0
    assert result.band == fit.BAND_INSUFFICIENT_EVIDENCE


def test_deterministic_repeat_produces_identical_result():
    lead = _lead(industry="Medical Practice", cleaned_data={"country": "United States"})
    first = fit.compute_fit(lead)
    second = fit.compute_fit(lead)
    assert first.fit_score == second.fit_score
    assert first.evidence_coverage_pct == second.evidence_coverage_pct
    assert first.band == second.band
    assert first.input_fingerprint == second.input_fingerprint
    assert first.criteria_as_dicts() == second.criteria_as_dicts()


def test_fingerprint_changes_when_relevant_input_changes():
    a = fit.compute_fit(_lead(industry="Real Estate", cleaned_data={"country": "United States"}))
    b = fit.compute_fit(_lead(industry="Retail", cleaned_data={"country": "United States"}))
    assert a.input_fingerprint != b.input_fingerprint


def test_fingerprint_unaffected_by_unrelated_fields():
    a = fit.compute_fit(_lead(industry="Real Estate", cleaned_data={"country": "United States"}, status="new"))
    b = fit.compute_fit(_lead(industry="Real Estate", cleaned_data={"country": "United States"}, status="scored"))
    assert a.input_fingerprint == b.input_fingerprint


@pytest.mark.parametrize(
    "fit_score,coverage,expected_band",
    [
        (85, 100.0, fit.BAND_STRONG),
        (80, 100.0, fit.BAND_STRONG),
        (79, 100.0, fit.BAND_PARTIAL),
        (50, 100.0, fit.BAND_PARTIAL),
        (49, 100.0, fit.BAND_WEAK),
        (0, 100.0, fit.BAND_WEAK),
        (100, 79.9, fit.BAND_INSUFFICIENT_EVIDENCE),
        (0, 79.9, fit.BAND_INSUFFICIENT_EVIDENCE),
    ],
)
def test_band_thresholds(fit_score, coverage, expected_band):
    assert fit._band(fit_score, coverage) == expected_band


# --------------------------------------------------------------- readiness

def test_readiness_flags_missing_seller_profile_always():
    result = fit.compute_readiness(
        _lead(), has_contact_email=True, latest_outreach_exists=True,
        latest_outreach_review_decision="approved", eligibility_excluded=False,
    )
    assert fit.GAP_NO_SELLER_PROFILE in result.outbound_email.gaps


def test_readiness_ready_when_no_gaps_except_seller_profile_is_absent():
    """Even a lead with everything else in place is not_ready for outbound
    email today, because no seller profile exists -- this must be visible,
    not silently ignored."""
    result = fit.compute_readiness(
        _lead(), has_contact_email=True, latest_outreach_exists=True,
        latest_outreach_review_decision="approved", eligibility_excluded=False,
    )
    assert result.outbound_email.status == fit.NOT_READY
    assert result.outbound_email.gaps == [fit.GAP_NO_SELLER_PROFILE]


def test_readiness_multiple_gaps_can_coexist():
    result = fit.compute_readiness(
        _lead(), has_contact_email=False, latest_outreach_exists=False,
        latest_outreach_review_decision=None, eligibility_excluded=True,
    )
    gaps = result.outbound_email.gaps
    assert fit.GAP_NO_SELLER_PROFILE in gaps
    assert fit.GAP_MISSING_CONTACT_EMAIL in gaps
    assert fit.GAP_NO_OUTREACH_DRAFT in gaps
    assert fit.GAP_LEAD_EXCLUDED in gaps
    assert fit.GAP_DRAFT_NOT_REVIEWED not in gaps  # draft doesn't exist, distinct gap


def test_readiness_rejected_draft_is_a_distinct_gap_from_missing_draft():
    result = fit.compute_readiness(
        _lead(), has_contact_email=True, latest_outreach_exists=True,
        latest_outreach_review_decision="rejected", eligibility_excluded=False,
    )
    assert fit.GAP_DRAFT_REJECTED in result.outbound_email.gaps
    assert fit.GAP_NO_OUTREACH_DRAFT not in result.outbound_email.gaps


def test_internal_handoff_readiness_independent_of_email_gaps():
    """Missing contact_email must not affect internal Slack handoff
    readiness -- different action, different requirements."""
    result = fit.compute_readiness(
        _lead(), has_contact_email=False, latest_outreach_exists=False,
        latest_outreach_review_decision=None, eligibility_excluded=False,
    )
    assert result.internal_slack_handoff.status == fit.READY
    assert result.internal_slack_handoff.gaps == []


def test_internal_handoff_blocked_when_lead_excluded():
    result = fit.compute_readiness(
        _lead(), has_contact_email=True, latest_outreach_exists=True,
        latest_outreach_review_decision="approved", eligibility_excluded=True,
    )
    assert result.internal_slack_handoff.status == fit.NOT_READY
    assert result.internal_slack_handoff.gaps == [fit.GAP_LEAD_EXCLUDED]


# ------------------------------------------------------------- eligibility

def test_eligibility_excludes_disqualified_statuses():
    for blocked_status in ("do_not_contact", "disqualified", "unsubscribed"):
        lead = _lead(status=blocked_status)
        result = fit.compute_eligibility(lead)
        assert result.excluded is True
        assert any(blocked_status in r for r in result.reasons)


def test_eligibility_excludes_partial_batch():
    batch = SimpleNamespace(id="batch-1", status="partial")
    lead = _lead(status="new", batch=batch)
    result = fit.compute_eligibility(lead)
    assert result.excluded is True
    assert any("partial" in r for r in result.reasons)


def test_eligibility_not_excluded_for_normal_lead():
    batch = SimpleNamespace(id="batch-1", status="uploaded")
    lead = _lead(status="new", batch=batch)
    result = fit.compute_eligibility(lead)
    assert result.excluded is False
    assert result.reasons == []


def test_high_fit_score_never_appears_in_eligibility_computation():
    """compute_eligibility takes no score/fit input at all -- structurally
    impossible for a high score to override a hard exclusion."""
    import inspect

    sig = inspect.signature(fit.compute_eligibility)
    assert list(sig.parameters) == ["lead"]
