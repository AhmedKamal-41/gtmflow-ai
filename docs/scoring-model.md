# Scoring model

A deterministic, explainable 100-point fit score. No LLM in the loop, every score is reproducible from the lead fields alone.

## Why deterministic

GTM teams need to defend a "this is Hot" claim with specifics. A heuristic model with named categories does that. An LLM would add unpredictability and zero auditability. Phase 5 layers AI on top of the score, but the score itself is the source of truth.

## Categories and caps

| Category | Max | How it earns points |
|---|---|---|
| Industry fit | 25 | Lead's `industry` directly matches a target term → 25. A target term shows up elsewhere in the lead's text (incl. `cleaned_data`) but not the `industry` field → 15. Otherwise 0. |
| Company size fit | 15 | Banded: 1–10 → 5; 11–50 → 8; 51–200 → 12; 201–500 → 14; 500+ → 15. Verbal labels small/medium/large/enterprise map equivalently. Plain integers also bucketed. Missing → 0. |
| Persona / title fit | 15 | `contact_title` contains any target title (case-insensitive substring) → 15. Otherwise 0. |
| Pain-point keywords | 20 | 1 match → 8; 2 → 14; 3+ → 20. Searched across every text field and every value in `cleaned_data`. |
| Data completeness | 10 | +2 each for website, industry, contact_name, contact_email, contact_title; capped at 10. |
| Source quality | 10 | `referral` / `webinar` / `conference` / `inbound` → 10. `website` / `csv` → 6. Missing/unknown → 2. |
| Penalties | -15 | Free-email domain –5, missing `contact_title` –5, `status` in {do_not_contact, disqualified, unsubscribed} –10. Floor at –15. |

**Total** = clamp(sum, 0, 100). Theoretical max with all positives = 95. The clamp at 100 is defensive.

## Bands

- **Hot** ≥ 80, call this week
- **Warm** 55–79, nurture
- **Cold** 0–54, deprioritize

Boundaries are unit-tested at the exact edges (54, 55, 79, 80, 100).

## Target taxonomies

These are constants in `backend/app/scoring/lead_scoring.py`; surface them in the UI as part of the matched-signals panel.

**Industries**
> property management, housing, multifamily, apartment management, real estate management, healthcare, clinic, medical practice, patient services, hospital, dental, urgent care

**Titles**
> ceo, founder, co-founder, vp sales, vp operations, head of operations, revenue operations, revops, property manager, director of leasing, leasing manager, practice manager, operations manager, office manager

**Pain-point keywords**
> leasing, maintenance, appointment, scheduling, intake, resident, tenant, patient, paperwork, support, call volume, automation, tour, booking, front desk, follow-up, forms

## Output shape

```json
{
  "total_score": 94,
  "priority": "Hot",
  "score_breakdown": {
    "industry_fit": 25,
    "company_size_fit": 14,
    "persona_title_fit": 15,
    "pain_point_keywords": 20,
    "data_completeness": 10,
    "source_quality": 10,
    "penalties": 0
  },
  "matched_signals": {
    "industry_terms": ["property management"],
    "title_terms": ["vp operations"],
    "pain_point_terms": ["maintenance", "scheduling", "tenant"]
  },
  "reasoning": "Hot fit driven by target industry signals (property management) and persona match on title (vp operations) and pain-point keywords (maintenance, scheduling, tenant)."
}
```

The frontend renders the breakdown as a per-category progress bar with the matched signals as pills, so a non-technical reader can see *why* a lead landed where it did.

## Persistence

Scores live in `LeadScore`. The matched-signals lists are folded into the `score_breakdown` JSON column to avoid adding a separate matched-signals column; the API splits them back out at read time. Re-scoring upserts in place, no historical scores are kept (intentional MVP scope).

## Limitations

- Target taxonomies are global constants; no per-tenant tuning yet.
- Pain-point matching is plain substring, `"call volume"` won't catch `"phone load"`.
- The 5-min-per-lead time-saved estimate in the metrics dashboard is a portfolio number, not a measured value.

## Testing

`backend/tests/test_lead_scoring.py` covers:

- Hot housing + Hot healthcare leads
- Cold retail lead
- Missing fields don't crash
- Missing website/email lowers completeness
- Target title raises persona
- Pain keywords raise score
- 0–100 clamp at both edges
- Band boundaries (`_band(0)`, `_band(54)`, `_band(55)`, `_band(79)`, `_band(80)`, `_band(100)`)
- Determinism (same lead → same score 3× in a row)
- Malformed `cleaned_data` (list/string/int) is ignored safely
