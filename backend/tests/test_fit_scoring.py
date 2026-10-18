"""Unit tests for lead ranking against a seller profile's target customers
(app/scoring/fit.py), independent of the API/DB layer: per-criterion
results, the weighted 0-100 score, coverage and bands, whole-word matching,
missing data versus mismatch, determinism and versioning. Readiness and
eligibility (contact completeness and permission, kept apart from fit)
are at the end."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.scoring import fit


def _lead(**overrides):
    base = dict(
        id="lead-1",
        industry=None,
        company_size=None,
        location=None,
        contact_title=None,
        cleaned_data=None,
        status="new",
        batch=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _profile(**preferences):
    return fit.ranking_profile_from_content(
        {"profile_kind": "seller", "target_preferences": preferences},
        source=fit.SOURCE_ACTIVE, profile_id="profile-1", seller_profile_id=None, version="1",
    )


ALL = dict(
    industries=["Healthcare"],
    company_sizes=[{"min_employees": 11, "max_employees": 50}],
    locations=["United States"],
    contact_roles=["Practice Manager"],
    keywords=["scheduling"],
)


def _criterion(result, name):
    return next(c for c in result.criteria if c.name == name)


# ------------------------------------------------------------- scoring

def test_full_match_scores_100_hot_with_full_coverage():
    lead = _lead(industry="Healthcare", company_size="11-50", location="Austin, Texas, USA",
                 contact_title="Practice Manager", cleaned_data={"notes": "Patient scheduling backlog"})
    result = fit.compute_fit(lead, _profile(**ALL))
    assert result.fit_score == 100 and result.evidence_coverage_pct == 100.0
    assert result.band == fit.BAND_STRONG and result.priority == "Hot"
    assert all(c.result == fit.RESULT_MATCH for c in result.criteria)


def test_default_weights_sum_to_100_and_thresholds_are_documented():
    assert sum(fit.WEIGHTS.values()) == 100
    assert fit.WEIGHTS == {"industry": 35, "company_size": 25, "location": 20, "contact_role": 15, "keywords": 5}
    assert (fit.HOT_MIN, fit.WARM_MIN, fit.COVERAGE_MIN_PCT) == (75, 45, 50.0)


def test_only_configured_criteria_count_and_weights_rescale():
    profile = _profile(industries=["Healthcare"], company_sizes=[{"min_employees": 11, "max_employees": 50}])
    lead = _lead(industry="Healthcare", company_size="5000", location="Canada", contact_title="CEO")
    result = fit.compute_fit(lead, profile)
    # industry 35 earned of 35 + 25 configured -> 58.3 -> 58
    assert result.fit_score == 58
    assert _criterion(result, "location").result == fit.RESULT_NOT_CONFIGURED
    assert _criterion(result, "location").weight == 0
    assert _criterion(result, "contact_role").result == fit.RESULT_NOT_CONFIGURED


def test_small_company_scores_well_when_the_seller_targets_small_companies():
    small = _lead(company_size="8")
    large = _lead(company_size="10001+")
    profile = _profile(company_sizes=[{"min_employees": 1, "max_employees": 10}])
    assert fit.compute_fit(small, profile).fit_score == 100
    assert fit.compute_fit(large, profile).fit_score == 0
    enterprise = _profile(company_sizes=[{"min_employees": 1001, "max_employees": None}])
    assert fit.compute_fit(small, enterprise).fit_score == 0
    assert fit.compute_fit(large, enterprise).fit_score == 100


@pytest.mark.parametrize("raw,expected", [
    ("51-200", (51, 200)), ("1,001 - 5,000", (1001, 5000)), ("10001+", (10001, None)),
    ("240", (240, 240)), ("1000+ employees", (1000, None)), ("small", None), ("", None), (None, None),
])
def test_company_size_parsing(raw, expected):
    assert fit.parse_company_size(raw) == expected


def test_company_size_partial_overlap_gets_half_credit_and_adjacent_targets_merge():
    profile = _profile(company_sizes=[{"min_employees": 1, "max_employees": 100}])
    result = fit.compute_fit(_lead(company_size="51-200"), profile)
    assert _criterion(result, "company_size").result == fit.RESULT_PARTIAL
    assert result.fit_score == 50
    merged = _profile(company_sizes=[{"min_employees": 11, "max_employees": 50},
                                     {"min_employees": 51, "max_employees": 200}])
    assert _criterion(fit.compute_fit(_lead(company_size="11-200"), merged), "company_size").result == fit.RESULT_MATCH


def test_hospitality_does_not_match_hospital():
    profile = _profile(industries=["Hospital"])
    result = fit.compute_fit(_lead(industry="Hospitality"), profile)
    assert _criterion(result, "industry").result == fit.RESULT_MISMATCH
    assert _criterion(fit.compute_fit(_lead(industry="Hospitals & Clinics"), profile), "industry").result == fit.RESULT_MATCH


@pytest.mark.parametrize("target,value,expected", [
    ("Health Care", "Hospital & Health Care", True),
    ("Healthcare", "Hospital & Health Care", True),
    ("Clinic", "Clinics", True),
    ("Real Estate", "Commercial Real Estate", True),
    ("Estate", "Real Estate", True),
    ("Software", "Computer Software", True),
    ("Soft", "Software", False),
    ("Care", "Healthcare", False),
    ("VP Operations", "Vice President of Operations", True),
    ("United States", "USA", True),
    ("United States", "U.S.", True),
    ("UK", "London, United Kingdom", True),
    ("Georgia", "Atlanta, Georgia, US", True),
    ("Indiana", "Indianapolis", False),
])
def test_whole_word_matching(target, value, expected):
    assert fit.phrase_in_text(target, value) is expected


def test_missing_values_are_unknown_not_mismatch():
    profile = _profile(**ALL)
    result = fit.compute_fit(_lead(industry="Healthcare"), profile)
    by = {c.name: c.result for c in result.criteria}
    assert by == {"industry": "match", "company_size": "unknown", "location": "unknown",
                  "contact_role": "unknown", "keywords": "unknown"}
    assert "missing information, not a mismatch" in _criterion(result, "company_size").explanation
    summary = fit.summarize_criteria(result.criteria_as_dicts())
    assert summary["missing"] == ["Company size", "Location", "Contact role", "Keywords"]
    assert summary["mismatched"] == []


def test_little_evidence_is_never_presented_as_a_confident_match():
    # Only the industry is known and it matches: 35 of 100 earned, coverage
    # 35% -> "needs data", not Hot, even though nothing contradicts a match.
    result = fit.compute_fit(_lead(industry="Healthcare"), _profile(**ALL))
    assert result.fit_score == 35 and result.evidence_coverage_pct == 35.0
    assert result.band == fit.BAND_INSUFFICIENT_EVIDENCE and result.priority is None
    assert fit.explain(result.criteria_as_dicts(), result.band).startswith("Not enough information")


def test_known_mismatch_counts_as_evidence():
    result = fit.compute_fit(_lead(industry="Retail", company_size="30", location="USA",
                                   contact_title="Buyer", cleaned_data={"notes": "store staffing"}),
                             _profile(**ALL))
    assert _criterion(result, "industry").result == fit.RESULT_MISMATCH
    assert result.evidence_coverage_pct == 100.0
    assert result.fit_score == 45 and result.priority == "Warm"  # size 25 + location 20


def test_unparseable_size_is_unknown():
    result = fit.compute_fit(_lead(company_size="medium"), _profile(company_sizes=[{"min_employees": 1, "max_employees": 50}]))
    assert _criterion(result, "company_size").result == fit.RESULT_UNKNOWN
    assert result.band == fit.BAND_INSUFFICIENT_EVIDENCE


def test_location_reads_structured_fields_and_says_it_does_not_resolve_places():
    profile = _profile(locations=["United States"])
    pdl = _lead(cleaned_data={"locality": "Austin", "region": "Texas", "country": "united states"})
    assert fit.compute_fit(pdl, profile).fit_score == 100
    city_only = fit.compute_fit(_lead(location="Austin, TX"), profile)
    assert _criterion(city_only, "location").result == fit.RESULT_MISMATCH
    assert "not resolved" in _criterion(city_only, "location").explanation


def test_keywords_search_free_text_but_not_import_labels():
    profile = _profile(keywords=["healthcare"])
    labels_only = _lead(cleaned_data={"candidate_segment": "healthcare", "country": "united states"})
    assert _criterion(fit.compute_fit(labels_only, profile), "keywords").result == fit.RESULT_UNKNOWN
    notes = _lead(cleaned_data={"notes": "Regional healthcare network"})
    assert _criterion(fit.compute_fit(notes, profile), "keywords").result == fit.RESULT_MATCH


def test_no_target_preferences_means_setup_required_not_a_fallback():
    with pytest.raises(fit.RankingSetupRequired):
        fit.compute_fit(_lead(industry="Hospital & Health Care"), _profile())
    assert _profile().is_configured is False


def test_old_demo_criteria_play_no_part():
    # The retired rubric gave 100 to these exact values. A profile that
    # targets other customers must not.
    old_demo_lead = _lead(industry="Hospital & Health Care", cleaned_data={"country": "united states"})
    profile = _profile(industries=["Software"], locations=["Germany"])
    result = fit.compute_fit(old_demo_lead, profile)
    assert result.fit_score == 0 and result.priority == "Cold"
    assert not hasattr(fit, "INDUSTRY_MATCH_VALUES") and not hasattr(fit, "PROFILE_ID")


def test_deterministic_repeat_produces_identical_result():
    lead = _lead(industry="Healthcare", company_size="20", location="USA")
    a, b = fit.compute_fit(lead, _profile(**ALL)), fit.compute_fit(lead, _profile(**ALL))
    assert (a.fit_score, a.band, a.input_fingerprint, a.criteria_as_dicts()) == \
        (b.fit_score, b.band, b.input_fingerprint, b.criteria_as_dicts())


def test_fingerprint_tracks_inputs_and_targeting_but_not_unrelated_fields():
    profile = _profile(industries=["Healthcare"])
    base = fit.compute_fit(_lead(industry="Healthcare"), profile).input_fingerprint
    assert fit.compute_fit(_lead(industry="Retail"), profile).input_fingerprint != base
    # company_size is not configured -> not read -> does not change it
    assert fit.compute_fit(_lead(industry="Healthcare", company_size="5"), profile).input_fingerprint == base
    other = _profile(industries=["Healthcare", "Dental"])
    assert fit.compute_fit(_lead(industry="Healthcare"), other).input_fingerprint != base


def test_targeting_hash_ignores_order_case_and_duplicates_but_not_meaning():
    a = _profile(industries=["Healthcare", "Dental"])
    b = _profile(industries=["dental", "Healthcare", "healthcare "])
    assert a.targeting_hash == b.targeting_hash
    assert _profile(industries=["Healthcare"]).targeting_hash != a.targeting_hash


@pytest.mark.parametrize(
    "fit_score,coverage,expected_band",
    [
        (100, 100.0, fit.BAND_STRONG),
        (75, 100.0, fit.BAND_STRONG),
        (74, 100.0, fit.BAND_PARTIAL),
        (45, 100.0, fit.BAND_PARTIAL),
        (44, 100.0, fit.BAND_WEAK),
        (0, 100.0, fit.BAND_WEAK),
        (49, 50.0, fit.BAND_PARTIAL),
        (49, 49.9, fit.BAND_INSUFFICIENT_EVIDENCE),
        (0, 0.0, fit.BAND_INSUFFICIENT_EVIDENCE),
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
