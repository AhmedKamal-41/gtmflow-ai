"""Phase 7: build the reviewed training dataset for an annotation queue.

Combines two review sources without mixing their provenance:

* human -- each candidate's latest `training_annotations` row
  (app/services/annotation.export_rows: accepted or corrected only);
* ai    -- the separate AI-review export (`ai-review-export-v1`), whose rows
  are re-verified against the database before use.

Rules:
* One example per candidate: its latest valid accepted/corrected target.
  A human review always takes precedence over an AI review of the same
  candidate. Skipped candidates are excluded (and counted).
* Every target is re-validated with the same v2 validator against the
  candidate's recorded input snapshot; nothing is repaired.
* Examples whose source record was flagged uncertain are kept out of the
  eligible set and written to a separate flagged export -- not dropped, not
  treated as confirmed errors.
* Exact duplicates are dropped (and counted); repeated target *structures*
  are down-weighted, never duplicated or rewritten (see dedup.py).

Read-only against the database.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.hashing import content_hash
from app.models import AIOutput, AnnotationCandidate, Lead
from app.services.annotation import export_rows, latest_annotations

DATASET_FORMAT = "gtmflow-sft-v1"


def load_jsonl(path: str) -> list[dict[str, Any]]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _segment(lead: Lead) -> str | None:
    return (lead.cleaned_data or {}).get("candidate_segment") if isinstance(lead.cleaned_data, dict) else None


def _base(candidate: AnnotationCandidate, lead: Lead, source: AIOutput) -> dict[str, Any]:
    return {
        "dataset_format": DATASET_FORMAT,
        "example_id": str(candidate.id),
        "queue": candidate.queue,
        "position": candidate.position,
        "task": candidate.task,
        "split": candidate.split,
        "manifest_version": candidate.manifest_version,
        "company_group_key": candidate.group_key,
        "lead_id": str(lead.id),
        "company_identity_id": str(lead.company_identity_id) if lead.company_identity_id else None,
        "source_record_id": lead.source_record_id,
        "company_name": lead.company_name,
        "segment": _segment(lead),
        "input_snapshot": source.input_snapshot,
        "input_hash": source.input_hash,
        "source_output_id": str(source.id),
        "source_content_hash": content_hash(source.content),
        "source_output": source.content,
        "seller_profile_id": str(source.seller_profile_id) if source.seller_profile_id else None,
        "seller_profile_version": source.seller_profile_version,
        "seller_profile_content_hash": source.seller_profile_content_hash,
        "seller_profile_kind": source.seller_profile_kind,
        "prompt_version": source.prompt_version,
        "output_schema_version": source.output_schema_version,
        "model_used": source.model_used,
        "model_revision": source.model_revision,
        "is_mock": source.model_used == "mock",
    }


def build(session: Session, queue: str, ai_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return {"examples": [...], "excluded": [...], "counts": {...}}.
    `examples` includes uncertain ones (flag set); callers split them out."""
    candidates = {
        c.id: c for c in session.scalars(select(AnnotationCandidate).where(AnnotationCandidate.queue == queue))
    }
    by_position = {c.position: c for c in candidates.values()}
    human_latest = latest_annotations(session, list(candidates))
    human_rows = {row["candidate_id"]: row for row in export_rows(session, queue)}

    examples: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for cid, candidate in sorted(candidates.items(), key=lambda kv: kv[1].position):
        latest = human_latest.get(cid)
        if latest is None:
            continue
        if latest.decision == "skipped":
            excluded.append({"position": candidate.position, "review_source": "human",
                             "reason": "skipped by the human reviewer"})
            continue
        row = human_rows.get(str(cid))
        if row is None:  # latest annotation was for a stale output
            excluded.append({"position": candidate.position, "review_source": "human",
                             "reason": "latest human annotation does not target the candidate's current output"})
            continue
        lead = session.get(Lead, candidate.lead_id)
        source = session.get(AIOutput, candidate.source_output_id)
        example = _base(candidate, lead, source)
        example.update({
            "review_source": "human",
            "reviewer": row["reviewer_label"],
            "reviewer_authenticated": False,
            "human_verified": True,
            "review_id": row["example_id"],
            "review_decision": row["decision"],
            "review_mode": row["review_mode"],
            "reviewed_at": row["reviewed_at"],
            "assessment": row["assessment"],
            "review_note": row["notes"],
            "correction_policy": None,  # human judgment, no AI rubric
            "review_timing": row["timing"],
            "target": row["target"],
            "target_content_hash": row["target_content_hash"],
            "target_output_id": row["target_output_id"],
            "target_origin": row["target_origin"],
            "uncertain": False,
            "uncertainty_reason": None,
        })
        examples.append(example)

    human_done = {e["position"] for e in examples} | {x["position"] for x in excluded}
    for row in ai_rows:
        position = row["position"]
        candidate = by_position.get(position)
        reason = None
        if candidate is None or str(candidate.id) != row["candidate_id"]:
            reason = "AI row does not match a candidate in this queue"
        elif position in human_done:
            reason = "superseded by a human review of the same candidate"
        else:
            source = session.get(AIOutput, candidate.source_output_id)
            if (source is None or str(source.id) != row["source_output_id"]
                    or content_hash(source.content) != row["source_content_hash"]
                    or source.input_hash != row["input_hash"]):
                reason = "AI row is not pinned to the candidate's stored output/input"
        if reason:
            excluded.append({"position": position, "review_source": "ai", "reason": reason})
            continue
        if row["decision"] == "skipped":
            excluded.append({"position": position, "review_source": "ai", "reason": "skipped by the AI reviewer"})
            continue
        lead = session.get(Lead, candidate.lead_id)
        example = _base(candidate, lead, source)
        example.update({
            "review_source": "ai",
            "reviewer": row["reviewer_model"],
            "reviewer_authenticated": False,
            "human_verified": False,
            "review_id": f"{row['decision_batch']}#{position}",
            "review_decision": row["decision"],
            "review_mode": "ai_review",
            "reviewed_at": row["reviewed_at"],
            "assessment": row["assessment"],
            "review_note": row["reason"],
            "correction_policy": row.get("rubric_version"),
            "review_timing": None,
            "target": row["target"],
            "target_content_hash": row["target_content_hash"],
            "target_output_id": None,  # AI corrections are not stored as outputs
            "target_origin": "ai_corrected" if row["decision"] == "corrected" else source.origin,
            "uncertain": bool(row["uncertain"]),
            "uncertainty_reason": row["uncertainty_reason"],
        })
        examples.append(example)

    examples.sort(key=lambda e: e["position"])
    reviewed = {e["position"] for e in examples} | {x["position"] for x in excluded}
    return {
        "examples": examples,
        "excluded": excluded,
        "unreviewed_positions": sorted(p for p in by_position if p not in reviewed),
        "counts": {
            "candidates": len(candidates),
            "examples": len(examples),
            "excluded": len(excluded),
            "by_source": dict(Counter(e["review_source"] for e in examples)),
        },
    }
