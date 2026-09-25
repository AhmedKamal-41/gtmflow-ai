"""Versioned correction policies for AI-reviewed targets.

`ai-review-rubric-v1` (Phase 6 pilot, frozen with the held-out criteria):
corrected outreach is rewritten into one demonstration template. Used for
pilot-v1 AI reviews and for the validation-v1 / test-v1 references. Never
changed.

`ai-review-rubric-v2-train` (Phase 7 training expansion, 2026-09-25):
minimal-edit corrections for NEW TRAINING examples only. Designed from
training evidence only (pilot-v1 outputs, the pilot AI-review findings, and
the human corrections of pilot #4 and #6); see
docs/upgrade/phase7-correction-policy-v2.md. Rules:

  1. Keep every original sentence that is supported by the record or the
     seller revision, well formed, and free of prohibited content -- verbatim.
  2. Make the smallest necessary change: delete a prohibited clause or
     sentence; rewrite only that sentence if the deletion breaks it; restore
     a recorded value the output altered; fix mechanical faults (literal
     '\\n', placeholders, broken signatures).
  3. Structure, length, greeting and call to action may vary; they are not
     normalized to a template.
  4. Outreach must state that it is a (portfolio) demonstration and not a
     commercial offer; the wording may vary.
  5. Never add company facts, needs, interest, goals, challenges, results,
     praise, specialization, fit or a contact person.

`check_target` is the automatic gate the review build applies to every v2
target: the v2 validator (run by the build) plus the rubric lints must pass.
`preservation` measures how much of the original prediction a target keeps.
"""
from __future__ import annotations

from typing import Any

from app.evaluation.metrics import lints, rouge_l
from app.datasets.dedup import target_text

RUBRIC_V1 = "ai-review-rubric-v1"
RUBRIC_V2_TRAIN = "ai-review-rubric-v2-train"
KNOWN = (RUBRIC_V1, RUBRIC_V2_TRAIN)
TRAIN_ONLY = {RUBRIC_V2_TRAIN}


def check_target(task: str, target: dict[str, Any], ctx: dict[str, Any]) -> list[str]:
    """Problems that make a v2 target unacceptable (empty = passes)."""
    failed = [name for name, ok in lints(task, target, ctx).items() if not ok]
    return [f"lint failed: {name}" for name in failed]


def preservation(task: str, source: dict[str, Any], target: dict[str, Any]) -> float:
    """ROUGE-L F1 between the original prediction's and the target's text
    fields: 1.0 = unchanged; a template rewrite scores low."""
    return round(rouge_l(target_text(task, target), target_text(task, source)), 4)
