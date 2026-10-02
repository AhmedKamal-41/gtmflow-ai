"""Frozen held-out evaluation criteria (Phase 7).

Frozen on 2026-09-24, BEFORE the validation-v1 / test-v1 candidates were
generated or reviewed. Nothing here may change in response to held-out
results. A change needs a new CRITERIA_ID, and results under different ids
are not comparable. `evaluate` records the id and digest in every result
file and refuses a held-out dataset whose predictions were not produced by
the frozen system.
"""
from __future__ import annotations

import hashlib
import json

from app.evaluation.metrics import METRICS_VERSION

CRITERIA_ID = "heldout-criteria-v1"

CRITERIA = {
    "criteria_id": CRITERIA_ID,
    "frozen_on": "2026-09-24",
    "metrics_version": METRICS_VERSION,
    # The system under evaluation: the stored ORIGINAL predictions of the
    # grounded-v2 prompt on gpt-4o-mini. Never the corrected targets.
    "system_under_test": {
        "prediction_field": "source_output",
        "model_revision": "gpt-4o-mini",
        "prompt_version": "grounded-v2",
        "output_schema_version": "v2",
    },
    "comparison_systems": ["mock (MockAIClient, deterministic, free)"],
    # References are separate corrected targets from review. On these queues
    # the review is AI-derived (review_source=ai, human_verified=false).
    "reference": {
        "field": "target",
        "review_rubric": "ai-review-rubric-v1",
        "label": "AI-derived reference targets, not human-verified",
    },
    "datasets": {
        "validation": "validation-v1 eligible.jsonl (uncertain-flagged examples reported separately)",
        "test": "test-v1 eligible.jsonl (uncertain-flagged examples reported separately)",
    },
    "metrics": {
        "validator_pass": "passes the v2 validator against the recorded input snapshot",
        "rubric_lints": "summary: no_need_hypothesis; outreach: demo_label, no_placeholder, "
                        "no_escape_text, no_invented_phrasing, no_prospect_web_as_own",
        "exact_match": "prediction content hash == reference hash; for the system under test this "
                       "equals the AI reviewer's acceptance rate",
        "token_f1": "token-overlap F1 of text fields against the reference",
        "rouge_l": "LCS F1 of text fields against the reference",
    },
    "breakdowns": ["task", "task x segment", "review_source"],
    "rules": [
        "Validation and test examples are never used for training.",
        "Test examples are never used for prompt or rubric tuning; the prompt (grounded-v2) and "
        "review rubric (ai-review-rubric-v1) are fixed before test review.",
        "Company-group assignments (company-groups-v1) are not changed.",
        "Uncertain-flagged examples are excluded from the headline numbers and reported separately.",
        "Reference-overlap metrics are anchored on the reviewed predictions (accepted = exact match); "
        "report them with that caveat.",
    ],
}


def criteria_digest() -> str:
    return hashlib.sha256(json.dumps(CRITERIA, sort_keys=True).encode()).hexdigest()
