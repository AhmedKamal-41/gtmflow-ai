"""Phase 7 baseline evaluation: deterministic, reference-based metrics.

Scores a system's outputs against reviewed targets for the same inputs.
Pure functions -- no DB, no network -- so a held-out evaluation can be
rerun exactly. Metrics:

* validator_pass   -- the output passes the versioned v2 validator against
                      its input snapshot (schema, references, contacts,
                      figures).
* rubric lints     -- automated checks derived from the Phase 6 review
                      findings. Outreach: demonstration label present and
                      states it is not a commercial offer; no placeholder or
                      broken signature; no literal escape text; no invented
                      specialization/praise/synergy phrasing; the prospect's
                      own website/LinkedIn not presented as the sender's.
                      Summary: no hypothesis asserting interest or need.
                      These are heuristics, not a truth check.
* exact_match      -- output content hash equals the target's.
* token_f1, rouge_l -- overlap with the target's text fields.

Metric definitions are versioned (METRICS_VERSION); change the version
when a definition changes so results stay comparable.
"""
from __future__ import annotations

import re
from statistics import mean
from typing import Any

from app.ai.grounding import AIOutputValidationError, validate_outreach, validate_summary
from app.core.hashing import content_hash
from app.datasets.dedup import target_text

METRICS_VERSION = "baseline-metrics-v1"

_TOKEN = re.compile(r"[a-z0-9]+")
_PLACEHOLDER = re.compile(r"\[(your|company|name|position|title)[^\]]*\]|my name is from", re.IGNORECASE)
_INVENTED = re.compile(
    r"speciali[sz]|synerg|impressed|key player|making strides|notable player|strong presence|"
    r"making an impact|we excel|actively engaged|growing company",
    re.IGNORECASE,
)
_NEED_HYPOTHESIS = re.compile(r"interested in|may (have|require)|seeking|needs? (related|for)|could be expanding", re.IGNORECASE)
_OWN_WEB = re.compile(r"(check (out )?(our|us)|our (website|profile)|visit us|connect with us)", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def token_f1(pred: str, ref: str) -> float:
    p, r = _tokens(pred), _tokens(ref)
    if not p or not r:
        return 0.0
    common = 0
    counts: dict[str, int] = {}
    for t in r:
        counts[t] = counts.get(t, 0) + 1
    for t in p:
        if counts.get(t, 0) > 0:
            common += 1
            counts[t] -= 1
    if common == 0:
        return 0.0
    precision, recall = common / len(p), common / len(r)
    return 2 * precision * recall / (precision + recall)


def rouge_l(pred: str, ref: str) -> float:
    p, r = _tokens(pred), _tokens(ref)
    if not p or not r:
        return 0.0
    prev = [0] * (len(r) + 1)
    for a in p:
        cur = [0]
        for j, b in enumerate(r, start=1):
            cur.append(prev[j - 1] + 1 if a == b else max(prev[j], cur[j - 1]))
        prev = cur
    lcs = prev[-1]
    if lcs == 0:
        return 0.0
    precision, recall = lcs / len(p), lcs / len(r)
    return 2 * precision * recall / (precision + recall)


def lints(task: str, output: dict[str, Any], ctx: dict[str, Any]) -> dict[str, bool]:
    validate = validate_summary if task == "company_summary" else validate_outreach
    try:
        validate(output, ctx)
        valid = True
    except AIOutputValidationError:
        valid = False
    if task == "company_summary":
        hyps = " ".join(output.get("hypotheses") or [])
        return {"validator_pass": valid, "no_need_hypothesis": not _NEED_HYPOTHESIS.search(hyps)}
    body = f"{output.get('subject', '')}\n{output.get('email_body', '')}\n{output.get('call_note', '')}"
    lead_web = [
        f["value"].lower() for f in ctx.get("lead_facts", [])
        if f["field"] in ("website", "linkedin_url") and f.get("value")
    ]
    offers_lead_web_as_own = bool(_OWN_WEB.search(body)) and any(w in body.lower() for w in lead_web)
    return {
        "validator_pass": valid,
        "demo_label": "demonstration" in body.lower() and "not a commercial offer" in body.lower(),
        "no_placeholder": not _PLACEHOLDER.search(body),
        "no_escape_text": "\\n" not in body,
        "no_invented_phrasing": not _INVENTED.search(body),
        "no_prospect_web_as_own": not offers_lead_web_as_own,
    }


def score(task: str, output: dict[str, Any], target: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    pred, ref = target_text(task, output), target_text(task, target)
    result = lints(task, output, ctx)
    result.update({
        "exact_match": content_hash(output) == content_hash(target),
        "token_f1": round(token_f1(pred, ref), 4),
        "rouge_l": round(rouge_l(pred, ref), 4),
    })
    return result


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    if not scores:
        return {"n": 0}
    keys = sorted({k for s in scores for k in s})
    out: dict[str, Any] = {"n": len(scores), "metrics_version": METRICS_VERSION}
    for k in keys:
        values = [s[k] for s in scores if k in s]
        out[k] = round(mean(float(v) for v in values), 4)
    return out
