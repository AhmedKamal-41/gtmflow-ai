"""Phase 6 mixed human/AI review pilot: AI-review tooling.

AI reviews are NOT human annotations. They are never written through the
annotation API (which records the unauthenticated operator label and UI
timing as a human review). Instead:

  dump   -- print the exact stored input facts and output for a batch of
            candidates that have no human annotation, for the AI reviewer.
  build  -- read the AI reviewer's decision files, verify each decision is
            pinned to the output id and content hash that was reviewed,
            merge corrected fields over the original output, run every
            corrected target through the same versioned validator against
            the recorded input snapshot, and write a separate JSONL export.

Read-only against the database. Usage (from backend/):

    DATABASE_URL=... python scripts/phase6_ai_review.py dump --from 7 --count 10 [--queue validation-v1]
    DATABASE_URL=... python scripts/phase6_ai_review.py build \\
        --decisions data/ai_reviews/pilot-v1/decisions --out data/ai_reviews/pilot-v1/ai-review-export.jsonl
"""
from __future__ import annotations

import argparse
import copy
import glob
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select

from app.ai.grounding import AIOutputValidationError, describe_details, validate_outreach, validate_summary
from app.core.database import get_sessionmaker
from app.core.hashing import content_hash
from app.datasets import correction_policy
from app.models import AIOutput, AnnotationCandidate, Lead
from app.services.annotation import latest_annotations

QUEUE = "pilot-v1"
EXPORT_FORMAT = "ai-review-export-v1"
RUBRIC_VERSION = "ai-review-rubric-v1"
REVIEWER = {
    "reviewer_type": "ai",
    "reviewer_model": "claude-opus-5-5",
    "reviewer_context": "Claude Code session; read the stored input snapshot and output; no model calls",
    "human_verified": False,
    "human_review_time_ms": None,
}


def _candidates(session, queue=QUEUE):
    rows = list(session.scalars(
        select(AnnotationCandidate).where(AnnotationCandidate.queue == queue).order_by(AnnotationCandidate.position)
    ))
    return rows, latest_annotations(session, [c.id for c in rows])


def dump(args) -> int:
    session = get_sessionmaker()()
    rows, human = _candidates(session, args.queue)
    shown = 0
    for c in rows:
        if c.position < args.start or shown >= args.count:
            continue
        if c.id in human:
            print(f"#{c.position}: human-reviewed ({human[c.id].decision}); not dumped")
            continue
        if c.source_output_id is None:
            print(f"#{c.position}: not generated; not dumped")
            continue
        o = session.get(AIOutput, c.source_output_id)
        snap = o.input_snapshot
        seller = snap.get("seller") or {}
        print(f"===== #{c.position} {c.task} | output {o.id} | content_hash {content_hash(o.content)} | "
              f"input_hash {o.input_hash} | seller v{seller.get('version')} {seller.get('profile_kind')}")
        print("FACTS: " + " | ".join(f"{f['id']}={f['value']}" for f in snap["lead_facts"]))
        print("UNKNOWN: " + ", ".join(snap["unknowns"]))
        body = o.content
        if c.task == "company_summary":
            print("SUMMARY: " + body["company_summary"])
            for e in body["evidence"]:
                print(f"  EV {e['fact_id']}: {e['statement']}")
            print("HYPOTHESES: " + json.dumps(body["hypotheses"]))
            print("SELLER_RELEVANCE: " + json.dumps(body["seller_relevance"]))
            print(f"UNKNOWNS_OUT: {body['unknowns']} | CONFIDENCE: {body['confidence']}")
        else:
            print("SUBJECT: " + body["subject"])
            print("BODY: " + body["email_body"].replace("\n", " / "))
            print(f"FACTS_USED: {body['lead_facts_used']} | CAPS: {body['capabilities_used']} | CLAIMS: {body['claims_used']}")
            print("CALL_NOTE: " + body["call_note"])
            print(f"UNKNOWNS_ACK: {body['unknowns_acknowledged']} | CONFIDENCE: {body['confidence']}")
        shown += 1
    return 0


def build(args) -> int:
    session = get_sessionmaker()()
    rows, human = _candidates(session, args.queue)
    by_position = {c.position: c for c in rows}
    decisions = []
    for path in sorted(glob.glob(f"{args.decisions}/batch-*.json")):
        batch = json.load(open(path))
        for d in batch["reviews"]:
            d["_batch_file"] = path.rsplit("/", 1)[-1]
            d["_rubric"] = batch.get("rubric", RUBRIC_VERSION)
            decisions.append(d)
    seen, errors, out = set(), [], []
    for d in decisions:
        pos = d["position"]
        if pos in seen:
            errors.append(f"#{pos}: reviewed twice")
            continue
        seen.add(pos)
        c = by_position.get(pos)
        if c is None or c.source_output_id is None:
            errors.append(f"#{pos}: no candidate/output")
            continue
        if d["_rubric"] not in correction_policy.KNOWN:
            errors.append(f"#{pos}: unknown rubric {d['_rubric']}")
            continue
        if d["_rubric"] in correction_policy.TRAIN_ONLY and c.split != "train":
            errors.append(f"#{pos}: {d['_rubric']} is for training queues only (this is {c.split})")
            continue
        o = session.get(AIOutput, c.source_output_id)
        if str(o.id) != d["source_output_id"] or content_hash(o.content) != d["source_content_hash"]:
            errors.append(f"#{pos}: decision is not pinned to the stored output/content hash")
            continue
        target = None
        if d["decision"] == "corrected":
            target = copy.deepcopy(o.content)
            target.update(d["corrected_fields"])
            validate = validate_summary if c.task == "company_summary" else validate_outreach
            try:
                target = validate(target, o.input_snapshot)
            except AIOutputValidationError as e:
                errors.append(f"#{pos}: corrected target fails validation: {describe_details(e.details)}")
                continue
            if content_hash(target) == d["source_content_hash"]:
                errors.append(f"#{pos}: 'corrected' target is identical to the original")
                continue
        elif d["decision"] == "accepted":
            target = o.content
        elif d["decision"] != "skipped":
            errors.append(f"#{pos}: unknown decision {d['decision']}")
            continue
        policy_problems, kept_ratio = [], None
        if target is not None and d["_rubric"] == correction_policy.RUBRIC_V2_TRAIN:
            policy_problems = correction_policy.check_target(c.task, target, o.input_snapshot)
            kept_ratio = correction_policy.preservation(c.task, o.content, target)
        if policy_problems:
            errors.append(f"#{pos}: target fails {d['_rubric']}: {policy_problems}")
            continue
        lead = session.get(Lead, c.lead_id)
        human_row = human.get(c.id)
        out.append({
            "export_format": EXPORT_FORMAT,
            "rubric_version": d["_rubric"],
            "preservation_rouge_l": kept_ratio,
            "review_source": "ai",
            **REVIEWER,
            "reviewed_at": d["reviewed_at"],
            "decision_batch": d["_batch_file"],
            "queue": c.queue, "candidate_id": str(c.id), "position": pos, "task": c.task,
            "split": c.split, "manifest_version": c.manifest_version, "company_group_key": c.group_key,
            "lead_id": str(lead.id), "company_name": lead.company_name,
            "company_identity_id": str(lead.company_identity_id) if lead.company_identity_id else None,
            "source_record_id": lead.source_record_id,
            "source_output_id": str(o.id), "source_content_hash": d["source_content_hash"],
            "input_hash": o.input_hash, "input_snapshot": o.input_snapshot, "source_output": o.content,
            "seller_profile_id": str(o.seller_profile_id) if o.seller_profile_id else None,
            "seller_profile_version": o.seller_profile_version,
            "seller_profile_content_hash": o.seller_profile_content_hash,
            "seller_profile_kind": o.seller_profile_kind,
            "prompt_version": o.prompt_version, "output_schema_version": o.output_schema_version,
            "model_used": o.model_used, "model_revision": o.model_revision, "is_mock": o.model_used == "mock",
            "decision": d["decision"],
            "target": target,
            "target_content_hash": content_hash(target) if target is not None else None,
            "assessment": {k: d.get(k) for k in ("factual_support", "writing_quality", "missing_info_handling")},
            "reason": d["reason"], "issues": d.get("issues", []),
            "uncertain": bool(d.get("uncertain")), "uncertainty_reason": d.get("uncertainty_reason"),
            "human_review_exists": human_row is not None,
            "human_decision": human_row.decision if human_row else None,
        })
    uncovered = [c.position for c in rows if c.id not in human and c.position not in seen]
    print(json.dumps({
        "decisions": len(decisions), "exported": len(out), "errors": errors,
        "unreviewed_without_human_or_ai": uncovered,
        "by_decision": dict(Counter(r["decision"] for r in out)),
        "by_task_decision": dict(Counter(f"{r['task']}:{r['decision']}" for r in out)),
        "uncertain": [r["position"] for r in out if r["uncertain"]],
        "by_rubric": dict(Counter(r["rubric_version"] for r in out)),
    }, indent=2))
    if errors:
        print("NOT WRITTEN: fix the errors above.", file=sys.stderr)
        return 1
    text = "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in out)
    with open(args.out, "w") as f:
        f.write(text)
    print(f"wrote {args.out} sha256={hashlib.sha256(text.encode()).hexdigest()} built_at={datetime.now(timezone.utc).isoformat()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--from", dest="start", type=int, required=True)
    d.add_argument("--count", type=int, default=10)
    d.add_argument("--queue", default=QUEUE)
    b = sub.add_parser("build")
    b.add_argument("--decisions", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--queue", default=QUEUE)
    args = parser.parse_args()
    return dump(args) if args.cmd == "dump" else build(args)


if __name__ == "__main__":
    sys.exit(main())
