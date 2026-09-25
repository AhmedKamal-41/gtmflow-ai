"""Phase 7: exact-duplicate removal and structure weighting.

Policy `structure-cap-v1` (documented in docs/upgrade/decisions.md):

1. Exact duplicates are dropped, keeping the first by queue position:
   the same target content hash, or the same (task, input_hash).
2. Repeated *structures* are measured with a fact-masked signature: the
   target's text fields are tokenized, every token that occurs in that
   example's own input facts (company name, industry, location, ...) is
   removed, and the remaining token sequence is hashed. Two targets that
   differ only in company-specific slots share a signature.
3. No example is duplicated, rewritten or invented. Each keeps
   `weight = min(1, CAP / group_size)`, so a structure group contributes at
   most CAP effective examples. Example counts are unchanged; the effective
   count (sum of weights) is reported separately.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any

POLICY = "structure-cap-v1"
STRUCTURE_CAP = 5

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def target_text(task: str, target: dict[str, Any]) -> str:
    if task == "company_summary":
        parts = [target.get("company_summary") or "", *(target.get("hypotheses") or []),
                 target.get("seller_relevance") or ""]
        parts += [e.get("statement", "") for e in target.get("evidence") or []]
    else:
        parts = [target.get("subject") or "", target.get("email_body") or "", target.get("call_note") or ""]
    return "\n".join(parts)


def structure_signature(example: dict[str, Any]) -> str:
    fact_tokens = {
        tok for fact in example["input_snapshot"]["lead_facts"] for tok in _tokens(str(fact["value"]))
    }
    kept = [t for t in _tokens(target_text(example["task"], example["target"])) if t not in fact_tokens]
    return hashlib.sha256(" ".join(kept).encode()).hexdigest()[:16]


def _masked_tokens(example: dict[str, Any]) -> set[str]:
    fact_tokens = {
        tok for fact in example["input_snapshot"]["lead_facts"] for tok in _tokens(str(fact["value"]))
    }
    return {t for t in _tokens(target_text(example["task"], example["target"])) if t not in fact_tokens}


def near_duplicate_report(examples: list[dict[str, Any]], thresholds: tuple[float, ...] = (0.8, 0.9)) -> dict[str, Any]:
    """Looser than the exact structure signature: Jaccard similarity of the
    fact-masked token SETS of every pair of targets within a task (and within
    each correction policy). Reported only; weights are unchanged."""
    by_group: dict[str, list[set[str]]] = defaultdict(list)
    for e in examples:
        masked = _masked_tokens(e)
        by_group[e["task"]].append(masked)
        by_group[f"{e['task']}|{e.get('correction_policy')}"].append(masked)
    out: dict[str, Any] = {}
    for key, sets in sorted(by_group.items()):
        pairs = 0
        above = {t: 0 for t in thresholds}
        in_pair = {t: set() for t in thresholds}
        max_sim = []
        for i in range(len(sets)):
            best = 0.0
            for j in range(len(sets)):
                if i == j:
                    continue
                union = sets[i] | sets[j]
                sim = len(sets[i] & sets[j]) / len(union) if union else 1.0
                best = max(best, sim)
                if j > i:
                    pairs += 1
                    for t in thresholds:
                        if sim >= t:
                            above[t] += 1
                            in_pair[t].update((i, j))
            max_sim.append(best)
        max_sim.sort()
        out[key] = {
            "examples": len(sets), "pairs": pairs,
            "pairs_at_or_above": {str(t): above[t] for t in thresholds},
            "examples_with_a_near_duplicate": {str(t): len(in_pair[t]) for t in thresholds},
            "median_nearest_similarity": round(max_sim[len(max_sim) // 2], 4) if max_sim else None,
        }
    return out


def apply(examples: list[dict[str, Any]], cap: int = STRUCTURE_CAP) -> dict[str, Any]:
    """Drop exact duplicates, annotate structure groups and weights.
    Returns {"kept": [...], "dropped": [...], "report": {...}}."""
    kept, dropped = [], []
    seen_target, seen_input = {}, {}
    for e in sorted(examples, key=lambda x: (x.get("queue", ""), x["position"])):
        key_input = (e["task"], e["input_hash"])
        ref = f"{e.get('queue', '')}#{e['position']}"
        if e["target_content_hash"] in seen_target:
            dropped.append({"queue": e.get("queue"), "position": e["position"], "reason": "exact duplicate target",
                            "duplicate_of": seen_target[e["target_content_hash"]]})
            continue
        if key_input in seen_input:
            dropped.append({"queue": e.get("queue"), "position": e["position"], "reason": "duplicate (task, input)",
                            "duplicate_of": seen_input[key_input]})
            continue
        seen_target[e["target_content_hash"]] = ref
        seen_input[key_input] = ref
        kept.append(e)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in kept:
        e["structure_signature"] = structure_signature(e)
        groups[(e["task"], e["structure_signature"])].append(e)
    for members in groups.values():
        for e in members:
            e["structure_group_size"] = len(members)
            e["weight"] = round(min(1.0, cap / len(members)), 6)

    sizes = Counter()
    for (task, _sig), members in groups.items():
        if len(members) > 1:
            sizes[task] += len(members)
    return {
        "kept": kept,
        "dropped": dropped,
        "report": {
            "policy": POLICY,
            "structure_cap": cap,
            "exact_duplicates_dropped": len(dropped),
            "structure_groups": {
                f"{task}:{sig}": len(members)
                for (task, sig), members in sorted(groups.items(), key=lambda kv: -len(kv[1]))
                if len(members) > 1
            },
            "examples_in_repeated_structures": dict(sizes),
            "effective_examples": round(sum(e["weight"] for e in kept), 3),
        },
    }
