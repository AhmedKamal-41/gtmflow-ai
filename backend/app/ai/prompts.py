"""Prompt templates used by the real OpenAI client.

The mock client does NOT use these -- it composes its responses
deterministically from the lead context. Only real-mode generations
go through these prompts.
"""

from __future__ import annotations

import json
from typing import Any

SYSTEM_RULES = (
    "You are a careful B2B sales analyst. "
    "Use ONLY the lead data the user provides. "
    "Never invent facts, statistics, customer names, tool stacks, or product details. "
    "Separate evidence (facts present in the data) from inferences (hypotheses). "
    "Never claim this company uses any specific tool unless explicitly provided. "
    "Never pretend an email was sent. "
    "Output STRICT JSON only -- no preamble, no commentary, no markdown fences."
)


SUMMARY_SCHEMA = (
    '{\n'
    '  "company_summary": "string",\n'
    '  "detected_pain_points": ["string"],\n'
    '  "fit_reasoning": "string",\n'
    '  "evidence": ["string"],\n'
    '  "inferences": ["string"],\n'
    '  "confidence": "low|medium|high"\n'
    '}'
)


OUTREACH_SCHEMA = (
    '{\n'
    '  "subject": "string",\n'
    '  "email_body": "string",\n'
    '  "personalization_points": ["string"],\n'
    '  "call_note": "string",\n'
    '  "confidence": "low|medium|high"\n'
    '}'
)


def _dump(ctx: dict[str, Any]) -> str:
    return json.dumps(ctx, indent=2, default=str, sort_keys=True)


def build_summary_prompt(ctx: dict[str, Any]) -> str:
    return (
        f"{SYSTEM_RULES}\n\n"
        f"LEAD DATA:\n{_dump(ctx)}\n\n"
        "Produce a structured analyst summary as JSON matching this exact shape:\n"
        f"{SUMMARY_SCHEMA}\n"
    )


def build_outreach_prompt(ctx: dict[str, Any]) -> str:
    return (
        f"{SYSTEM_RULES}\n\n"
        "Write concise B2B sales outreach grounded in the lead's industry, "
        "persona, score, and detected pain points. No fake metrics. "
        "No fake customer names. No claim this prospect uses any specific tool.\n\n"
        f"LEAD DATA:\n{_dump(ctx)}\n\n"
        "Produce JSON matching this exact shape:\n"
        f"{OUTREACH_SCHEMA}\n"
    )
