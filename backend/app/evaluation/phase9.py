"""Phase 9 held-out comparison: scoring generated predictions with the frozen
Phase 7 criteria, and the blind AI review protocol.

Frozen on 2026-09-25, BEFORE any Phase 9 prediction exists. It does not
change `heldout-criteria-v1`: every frozen metric (validator, rubric lints,
exact match, token F1, ROUGE-L) is computed by the unchanged
`metrics.score`. This module only adds

* a failure-inclusive wrapper: every held-out example is scored, and a
  missing, empty, truncated or unparseable generation counts as a failure in
  every metric (it is never dropped from a denominator);
* a report view that groups the frozen checks into the four reported
  categories, using the validator's own reason codes; word-overlap metrics
  are reported separately from them;
* the blind AI review protocol for the writing-acceptability judgment.

Parsing is production's `parse_json_strict` (tolerates whitespace and code
fences, nothing else).
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from statistics import mean
from typing import Any

from app.ai.grounding import AIOutputValidationError, validate_outreach, validate_summary
from app.ai.json_parser import AIJSONParseError, parse_json_strict
from app.evaluation import criteria, metrics

REPORT_VIEW_ID = "phase9-report-v1"

STRUCTURE_CODES = {"schema_invalid"}
GROUNDING_CODES = {"unknown_fact_reference", "unknown_capability_reference", "unapproved_claim_reference",
                   "seller_relevance_without_seller", "unsupplied_email_address", "unsupported_figure"}

REPORT_VIEW = {
    "report_view_id": REPORT_VIEW_ID,
    "frozen_on": "2026-09-25 (before any Phase 9 generation)",
    "criteria_id": criteria.CRITERIA_ID,
    "metrics_version": metrics.METRICS_VERSION,
    "parse": "app.ai.json_parser.parse_json_strict (production parser)",
    "denominator": "every example of the held-out set; missing, empty, truncated and unparseable generations "
                   "count as failures in every category and every metric",
    "categories": {
        "valid_structure": "parsed as a JSON object and no validator schema_invalid",
        "factual_support": "valid structure, no validator grounding code (" + ", ".join(sorted(GROUNDING_CODES))
                           + "); outreach also passes the frozen lints no_invented_phrasing and "
                             "no_prospect_web_as_own",
        "missing_info_handling": "valid structure; summary passes no_need_hypothesis, outreach passes "
                                 "no_placeholder",
        "writing_acceptability_automated": "outreach only: valid structure and demo_label and no_escape_text "
                                           "(summaries have no automated writing check; see the AI review)",
    },
    "word_overlap": "exact_match, token_f1, rouge_l against the AI-derived references; reported separately; "
                    "anchored on gpt-4o-mini's reviewed outputs, so they favour its wording",
    "ai_review": {
        "protocol": "phase9-blind-review-v1",
        "rubric": "ai-review-rubric-v1 (the Phase 6/7 standard)",
        "blind": "outputs of both Qwen systems are pooled, shuffled with a fixed seed and given opaque ids; "
                 "the reviewer sees the input snapshot and the output only; the system key is sealed until "
                 "all decisions are recorded",
        "scope": "test-v1 eligible; outputs without valid structure are not reviewed and count as not acceptable",
        "labels": "factual_support (supported / partially_supported / unsupported), missing_info_handling "
                  "(good / acceptable / poor), writing_quality 1-5, acceptable_as_is (bool), issues",
        "reviewer": "AI (Claude, in session); results are AI-evaluated, not human-verified",
    },
}


def report_view_digest() -> str:
    return hashlib.sha256(json.dumps(REPORT_VIEW, sort_keys=True).encode()).hexdigest()


def _validator_codes(task: str, output: dict[str, Any], ctx: dict[str, Any]) -> list[str]:
    validate = validate_summary if task == "company_summary" else validate_outreach
    try:
        validate(output, ctx)
        return []
    except AIOutputValidationError as error:
        return sorted(set(error.reason_codes))


def score_prediction(row: dict[str, Any], prediction: dict[str, Any] | None) -> dict[str, Any]:
    """Score one held-out example. `prediction` is a generation record
    ({"text", "finish", ...}) or, for stored outputs, {"output": dict}; None =
    not generated."""
    task, ctx = row["task"], row["input_snapshot"]
    result: dict[str, Any] = {"generation": "ok", "parse_ok": False, "validator_codes": []}
    output = None
    if prediction is None:
        result["generation"] = "not_generated"
    elif "output" in prediction:
        output = prediction["output"]
    else:
        text = prediction.get("text") or ""
        if not text.strip():
            result["generation"] = "empty"
        elif prediction.get("finish") == "length":
            result["generation"] = "truncated"
        try:
            output = parse_json_strict(text)
        except AIJSONParseError:
            output = None
    frozen = {"validator_pass": False, "exact_match": False, "token_f1": 0.0, "rouge_l": 0.0}
    if isinstance(output, dict):
        result["parse_ok"] = True
        result["validator_codes"] = _validator_codes(task, output, ctx)
        try:
            frozen = metrics.score(task, output, row["target"], ctx)
        except (AttributeError, TypeError, KeyError):
            # Malformed field types the lints cannot read: failure, never dropped.
            frozen = {**frozen, "validator_pass": False}
    codes = set(result["validator_codes"])
    valid = result["parse_ok"] and not (codes & STRUCTURE_CODES)
    grounded = valid and not (codes & GROUNDING_CODES)
    if task == "company_summary":
        factual = grounded
        missing = valid and bool(frozen.get("no_need_hypothesis"))
        writing = None
    else:
        factual = grounded and bool(frozen.get("no_invented_phrasing")) and bool(frozen.get("no_prospect_web_as_own"))
        missing = valid and bool(frozen.get("no_placeholder"))
        writing = valid and bool(frozen.get("demo_label")) and bool(frozen.get("no_escape_text"))
    result.update({
        "valid_structure": valid, "factual_support": factual, "missing_info_handling": missing,
        "writing_acceptability_automated": writing,
        "frozen_metrics": frozen,
    })
    return result


def _rate(values: list[Any]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return round(mean(vals), 4) if vals else None


def aggregate(scored: list[dict[str, Any]]) -> dict[str, Any]:
    if not scored:
        return {"n": 0}
    out: dict[str, Any] = {"n": len(scored)}
    out["generation"] = dict(Counter(s["generation"] for s in scored))
    out["parse_ok"] = _rate([s["parse_ok"] for s in scored])
    for key in ("valid_structure", "factual_support", "missing_info_handling", "writing_acceptability_automated"):
        out[key] = _rate([s[key] for s in scored])
    frozen_keys = sorted({k for s in scored for k in s["frozen_metrics"]})
    out["frozen_metrics"] = {k: _rate([s["frozen_metrics"].get(k, False) for s in scored]) for k in frozen_keys}
    out["validator_code_counts"] = dict(Counter(c for s in scored for c in s["validator_codes"]))
    return out


# --------------------------------------------------------------- blind AI review

def build_review_packet(rows: list[dict[str, Any]], predictions: dict[str, dict[str, dict[str, Any]]],
                        seed: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pool both systems' structurally valid outputs, shuffle with `seed`,
    assign opaque ids. Returns (packet for the reviewer, sealed key)."""
    items, key, not_reviewable = [], {}, []
    rng = random.Random(seed)
    for system, preds in sorted(predictions.items()):
        for row in rows:
            scored = score_prediction(row, preds.get(row["example_id"]))
            if not scored["valid_structure"]:
                not_reviewable.append({"system": system, "example_id": row["example_id"],
                                       "reason": scored["generation"] if scored["generation"] != "ok" else "invalid_structure"})
                continue
            items.append((system, row, parse_json_strict(preds[row["example_id"]]["text"])))
    rng.shuffle(items)
    packet = []
    for i, (system, row, output) in enumerate(items, start=1):
        review_id = f"r{i:03d}-" + hashlib.sha256(f"{seed}:{system}:{row['example_id']}".encode()).hexdigest()[:8]
        key[review_id] = {"system": system, "example_id": row["example_id"]}
        packet.append({"review_id": review_id, "task": row["task"], "input_snapshot": row["input_snapshot"],
                       "output": output})
    return packet, {"seed": seed, "protocol": REPORT_VIEW["ai_review"]["protocol"], "key": key,
                    "not_reviewable": not_reviewable}


REVIEW_LABELS = {
    "factual_support": ("supported", "partially_supported", "unsupported"),
    "missing_info_handling": ("good", "acceptable", "poor"),
}


def validate_decisions(decisions: list[dict[str, Any]], packet_ids: set[str]) -> list[str]:
    problems = []
    seen = Counter(d.get("review_id") for d in decisions)
    problems += [f"duplicate decision {rid}" for rid, n in seen.items() if n > 1]
    problems += [f"missing decision {rid}" for rid in sorted(packet_ids - set(seen))]
    problems += [f"unknown review id {rid}" for rid in sorted(set(seen) - packet_ids)]
    for d in decisions:
        for field, allowed in REVIEW_LABELS.items():
            if d.get(field) not in allowed:
                problems.append(f"{d.get('review_id')}: {field}={d.get(field)!r}")
        if d.get("writing_quality") not in (1, 2, 3, 4, 5):
            problems.append(f"{d.get('review_id')}: writing_quality={d.get('writing_quality')!r}")
        if not isinstance(d.get("acceptable_as_is"), bool):
            problems.append(f"{d.get('review_id')}: acceptable_as_is must be true/false")
        if d.get("acceptable_as_is") and d.get("factual_support") != "supported":
            problems.append(f"{d.get('review_id')}: acceptable_as_is requires factual_support=supported")
    return problems


def aggregate_review(decisions: list[dict[str, Any]], sealed: dict[str, Any], n_by_system: dict[str, int]) -> dict[str, Any]:
    """Unblind and aggregate. Not-reviewable outputs count as not acceptable;
    the denominator is every held-out example of the system."""
    by_system: dict[str, list[dict[str, Any]]] = {s: [] for s in n_by_system}
    for d in decisions:
        entry = sealed["key"][d["review_id"]]
        by_system[entry["system"]].append(d)
    out = {}
    for system, n in n_by_system.items():
        ds = by_system[system]
        not_rev = sum(1 for x in sealed["not_reviewable"] if x["system"] == system)
        out[system] = {
            "n_examples": n, "reviewed": len(ds), "not_reviewable": not_rev,
            "acceptable_as_is_rate": round(sum(d["acceptable_as_is"] for d in ds) / n, 4) if n else None,
            "factual_support": {k: sum(d["factual_support"] == k for d in ds) for k in REVIEW_LABELS["factual_support"]},
            "missing_info_handling": {k: sum(d["missing_info_handling"] == k for d in ds)
                                      for k in REVIEW_LABELS["missing_info_handling"]},
            "writing_quality_mean_reviewed": round(mean(d["writing_quality"] for d in ds), 2) if ds else None,
            "issues": dict(Counter(i for d in ds for i in d.get("issues", []))),
        }
    return out
