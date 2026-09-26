"""Runtime quality checks for generated drafts (Phase 10).

Versioned separately from the frozen evaluation criteria: `heldout-criteria-v1`
and its lints (app/evaluation/metrics.py) are never changed; this module is
`runtime-checks-v1`. The checks are pure functions of the stored output and
the grounded input snapshot it was generated from -- no I/O, no model call --
so they give the same answer for any stored draft, old or new.

A flag never approves, rejects or edits anything. It marks a draft for
review: the approve endpoint requires the reviewer to acknowledge the exact
flags shown (app/api/outreach_review.py).

Where each check comes from:

* `presumed_outreach_activity`, `commercial_opportunity_framing`: the Phase 9
  blind AI review of qwen3-4b-lora-v1 (docs/upgrade/phase9-evaluation-handoff.md
  §12) -- "I would love to learn more about your current outreach strategies"
  and "Exploring Opportunities ..." / "potential collaboration" framing.
* `invented_phrasing`: the frozen `no_invented_phrasing` lint's phrase list,
  but matched only AFTER removing the lead's recorded values (company name,
  website, ...). The frozen lint flagged the recorded name "Allergy &
  Immunology Specialists, LLC" as invented "speciali[sz]" wording (§13).
* `missing_demo_label` (demonstration seller profiles only), `placeholder_text`,
  `literal_escape_text`, `prospect_web_as_own`: the Phase 6/7 review rubric
  (`ai-review-rubric-v1`) and the matching frozen lints.
* `need_or_interest_hypothesis` (summaries): the rubric rule that hypotheses
  inventing interest or need are not acceptable, even when "unconfirmed";
  the base model's most frequent fault in Phase 9.
"""
from __future__ import annotations

import re
from typing import Any

CHECKS_VERSION = "runtime-checks-v1"

_PRESUMED_OUTREACH = re.compile(
    r"your (current |existing )?(outreach|lead[- ]generation|sales) (strateg\w*|efforts?|process\w*|approach\w*|activit\w*|needs?)"
    r"|learn more about your (current |existing )?(outreach|lead|sales)"
    r"|support your (team'?s )?(outreach|lead|sales)",
    re.IGNORECASE,
)
_COMMERCIAL_FRAMING = re.compile(
    r"exploring (new |potential )?opportunit\w*|potential collaboration|opportunit(y|ies) (for us )?to collaborate"
    r"|explore (a )?(potential )?(collaboration|partnership)",
    re.IGNORECASE,
)
# Same phrase list as the frozen no_invented_phrasing lint (heldout-criteria-v1).
_INVENTED = re.compile(
    r"speciali[sz]|synerg|impressed|key player|making strides|notable player|strong presence|"
    r"making an impact|we excel|actively engaged|growing company",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"\[(your|company|name|position|title)[^\]]*\]|my name is from", re.IGNORECASE)
_OWN_WEB = re.compile(r"(check (out )?(our|us)|our (website|profile)|visit us|connect with us)", re.IGNORECASE)
# A hypothesis that only states an unknown ("It is unconfirmed whether the
# company has a current interest in ...") invents nothing.
_STATES_UNKNOWN = re.compile(
    r"\b(unconfirmed|unknown|not known|no (evidence|record)( of)?) whether\b|\bdoes not (show|indicate)\b",
    re.IGNORECASE,
)
_NEED_HYPOTHESIS = re.compile(
    r"interested in|interest in|may (have|require|need|benefit|be looking|be seeking)|seeking"
    r"|\bneeds?\b|challenges?|pain points?|looking for|could be expanding",
    re.IGNORECASE,
)

_NOT_AN_OFFER = re.compile(r"not an? (commercial |real )?offer")

SEVERITY = "review"  # every runtime flag asks for review; none blocks by itself


def _flag(code: str, field: str, match: str | None, source: str) -> dict[str, Any]:
    return {"code": code, "field": field, "match": (match or "")[:120], "source": source, "severity": SEVERITY}


def _recorded_values(ctx: dict[str, Any]) -> list[str]:
    values = [str(f.get("value") or "") for f in ctx.get("lead_facts", [])]
    return sorted((v for v in values if len(v.strip()) >= 3), key=len, reverse=True)


def _without_recorded_values(text: str, ctx: dict[str, Any]) -> str:
    for value in _recorded_values(ctx):
        text = re.sub(re.escape(value), " ", text, flags=re.IGNORECASE)
    return text


def _outreach_fields(output: dict[str, Any]) -> list[tuple[str, str]]:
    return [(name, str(output.get(name) or "")) for name in ("subject", "email_body", "call_note")]


def check_outreach(output: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    fields = _outreach_fields(output)
    for field, text in fields:
        checks = [
            ("presumed_outreach_activity", _PRESUMED_OUTREACH, "phase9-blind-review"),
            ("placeholder_text", _PLACEHOLDER, "ai-review-rubric-v1"),
        ]
        if field != "call_note":  # the call note is the sender's internal note, never sent
            checks.append(("commercial_opportunity_framing", _COMMERCIAL_FRAMING, "phase9-blind-review"))
        for code, pattern, source in checks:
            m = pattern.search(text)
            if m:
                flags.append(_flag(code, field, m.group(0), source))
        m = _INVENTED.search(_without_recorded_values(text, ctx))
        if m:
            flags.append(_flag("invented_phrasing", field, m.group(0), "ai-review-rubric-v1 (recorded values excluded)"))
        if "\\n" in text:
            flags.append(_flag("literal_escape_text", field, "\\n", "ai-review-rubric-v1"))
    body = "\n".join(text for _, text in fields)
    lead_web = [
        str(f.get("value") or "").lower() for f in ctx.get("lead_facts", [])
        if f.get("field") in ("website", "linkedin_url") and f.get("value")
    ]
    own = _OWN_WEB.search(body)
    if own and any(w and w in body.lower() for w in lead_web):
        flags.append(_flag("prospect_web_as_own", "email_body", own.group(0), "ai-review-rubric-v1"))
    seller = ctx.get("seller") or {}
    if seller.get("profile_kind") == "demo":
        lower = body.lower()
        # The rubric asks for a plain statement that this is a demonstration
        # and not an offer; the app's own mock label says "Not a real offer".
        if "demonstration" not in lower or not _NOT_AN_OFFER.search(lower):
            flags.append(_flag("missing_demo_label", "email_body", None, "ai-review-rubric-v1"))
    return flags


def check_summary(output: dict[str, Any], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    flags = []
    for i, hypothesis in enumerate(output.get("hypotheses") or []):
        m = _NEED_HYPOTHESIS.search(str(hypothesis))
        if m and not _STATES_UNKNOWN.search(str(hypothesis)):
            flags.append(_flag("need_or_interest_hypothesis", f"hypotheses[{i}]", m.group(0), "ai-review-rubric-v1"))
    return flags


def check_output(output_type: str, output: dict[str, Any], ctx: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flags for one stored output. Outputs without a grounded input snapshot
    (pre-Phase-5 history) are checked against an empty context."""
    ctx = ctx or {}
    if output_type == "outreach_email":
        return check_outreach(output, ctx)
    if output_type == "company_summary":
        return check_summary(output, ctx)
    return []


def flag_codes(flags: list[dict[str, Any]]) -> list[str]:
    return sorted({f["code"] for f in flags})
