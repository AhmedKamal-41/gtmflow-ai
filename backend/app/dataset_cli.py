"""Phase 7 command line: build, check and evaluate the reviewed dataset,
and create held-out annotation queues.

    DATABASE_URL=... python -m app.dataset_cli build --queue pilot-v1 \\
        --ai-export data/ai_reviews/pilot-v1/ai-review-export.jsonl --out-dir data/datasets/pilot-v1
    DATABASE_URL=... python -m app.dataset_cli check --dir data/datasets/pilot-v1
    DATABASE_URL=... python -m app.dataset_cli evaluate --dataset data/datasets/pilot-v1/eligible.jsonl \\
        --system source|mock --out data/datasets/pilot-v1/eval-<system>.json
    DATABASE_URL=... python -m app.dataset_cli create-eval-queues

Read-only except `create-eval-queues`, which adds queue rows. No model is
called: `--system source` scores the stored generator outputs, and
`--system mock` runs the free deterministic mock client.
Exit codes: 0 ok, 1 checks failed, 2 usage/state error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any

from app.ai.mock_client import MockAIClient
from app.core.database import get_sessionmaker
from app.datasets import checks, dedup, pilot
from app.evaluation import metrics
from app.services import splits

EXPORT_FILES = {
    "eligible": "eligible.jsonl",
    "flagged": "flagged-uncertain.jsonl",
}


def _write_jsonl(path: str, rows: list[dict[str, Any]]) -> str:
    text = "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows)
    with open(path, "w") as f:
        f.write(text)
    return hashlib.sha256(text.encode()).hexdigest()


def _sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "examples": len(rows),
        "unique_companies": len({r["company_group_key"] for r in rows}),
        "by_task": dict(Counter(r["task"] for r in rows)),
        "by_segment": dict(Counter(r["segment"] for r in rows)),
        "by_split": dict(Counter(r["split"] for r in rows)),
        "by_review_source": dict(Counter(r["review_source"] for r in rows)),
        "by_decision": dict(Counter(f"{r['review_source']}:{r['review_decision']}" for r in rows)),
        "by_task_and_source": dict(Counter(f"{r['task']}:{r['review_source']}" for r in rows)),
        "effective_examples": round(sum(r.get("weight", 1.0) for r in rows), 3),
        "without_seller_context": sorted(r["position"] for r in rows if not r.get("seller_profile_content_hash")),
    }


def cmd_build(args, session) -> int:
    ai_rows = pilot.load_jsonl(args.ai_export) if args.ai_export else []
    built = pilot.build(session, args.queue, ai_rows)
    deduped = dedup.apply(built["examples"], cap=args.cap)
    eligible = [e for e in deduped["kept"] if not e["uncertain"]]
    flagged = [e for e in deduped["kept"] if e["uncertain"]]
    problems = checks.run_all(session, {"eligible": eligible, "flagged": flagged})
    os.makedirs(args.out_dir, exist_ok=True)
    hashes = {
        "eligible": _write_jsonl(os.path.join(args.out_dir, EXPORT_FILES["eligible"]), eligible),
        "flagged": _write_jsonl(os.path.join(args.out_dir, EXPORT_FILES["flagged"]), flagged),
    }
    manifest = {
        "dataset_format": pilot.DATASET_FORMAT,
        "queue": args.queue,
        "inputs": {"ai_export": args.ai_export, "ai_export_sha256": _sha(args.ai_export) if args.ai_export else None,
                   "human_source": "training_annotations (latest per candidate)"},
        "dedup": deduped["report"],
        "files": {EXPORT_FILES[k]: v for k, v in hashes.items()},
        "counts": {
            "candidates": built["counts"]["candidates"],
            "excluded": built["excluded"],
            "exact_duplicates_dropped": deduped["dropped"],
            "unreviewed_positions": built["unreviewed_positions"],
            "eligible": _counts(eligible),
            "flagged_uncertain": _counts(flagged),
        },
        "checks": problems,
    }
    with open(os.path.join(args.out_dir, "dataset-manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True, default=str)
    print(json.dumps({k: manifest[k] for k in ("files", "checks")} | {
        "eligible": manifest["counts"]["eligible"], "flagged_uncertain": manifest["counts"]["flagged_uncertain"],
        "excluded": manifest["counts"]["excluded"], "dedup": deduped["report"],
    }, indent=2, default=str))
    return 1 if any(problems.values()) else 0


def cmd_check(args, session) -> int:
    with open(os.path.join(args.dir, "dataset-manifest.json")) as f:
        manifest = json.load(f)
    sets, problems = {}, {"files": []}
    for key, name in EXPORT_FILES.items():
        path = os.path.join(args.dir, name)
        if _sha(path) != manifest["files"][name]:
            problems["files"].append(f"{name}: sha256 differs from the manifest")
        sets[key] = pilot.load_jsonl(path)
    problems |= checks.run_all(session, sets)
    overlap = {e["example_id"] for e in sets["eligible"]} & {e["example_id"] for e in sets["flagged"]}
    if overlap:
        problems["files"].append(f"{len(overlap)} examples in both eligible and flagged exports")
    print(json.dumps({"problems": problems, "eligible": len(sets["eligible"]), "flagged": len(sets["flagged"])}, indent=2))
    return 1 if any(problems.values()) else 0


def cmd_evaluate(args, session) -> int:
    rows = pilot.load_jsonl(args.dataset)
    mock = MockAIClient()
    per_example = []
    for r in rows:
        ctx = r["input_snapshot"]
        if args.system == "source":
            output = r["source_output"]
        elif r["task"] == "company_summary":
            output = mock.generate_company_summary(ctx)
        else:
            output = mock.generate_outreach(ctx)
        s = metrics.score(r["task"], output, r["target"], ctx)
        per_example.append({"example_id": r["example_id"], "position": r["position"], "task": r["task"],
                            "split": r["split"], "review_source": r["review_source"], **s})
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in per_example:
        groups[f"task={s['task']}"].append(s)
        groups[f"task={s['task']}|review_source={s['review_source']}"].append(s)
    splits_seen = sorted({r["split"] for r in rows})
    numeric = lambda s: {k: v for k, v in s.items() if isinstance(v, (bool, int, float)) and k != "position"}
    result = {
        "system": args.system,
        "system_detail": ("stored generator outputs (gpt-4o-mini, grounded-v2) as recorded"
                          if args.system == "source" else f"MockAIClient {mock.model_revision}"),
        "metrics_version": metrics.METRICS_VERSION,
        "dataset": args.dataset, "dataset_sha256": _sha(args.dataset), "splits": splits_seen,
        "held_out": all(s in ("validation", "test") for s in splits_seen),
        "label": ("HELD-OUT evaluation" if all(s in ("validation", "test") for s in splits_seen)
                  else "IN-SAMPLE (training split) -- descriptive only, NOT a held-out evaluation"),
        "overall": metrics.aggregate([numeric(s) for s in per_example]),
        "by_group": {k: metrics.aggregate([numeric(s) for s in v]) for k, v in sorted(groups.items())},
        "per_example": per_example,
    }
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
    print(json.dumps({k: result[k] for k in ("system", "label", "overall", "by_group")}, indent=2))
    return 0


def cmd_create_eval_queues(args, session) -> int:
    created = {}
    for split, (queue, seed) in splits.EVAL_QUEUES.items():
        rows = splits.create_queue(session, queue=queue, split=split, seed=seed, max_examples=args.max_examples)
        created[queue] = {"split": split, "examples": len(rows),
                          "unique_companies": len({r.lead_id for r in rows})}
    session.commit()
    print(json.dumps(created, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.dataset_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--queue", default=splits.PILOT_QUEUE)
    b.add_argument("--ai-export", help="AI review export (optional; omit for human-only targets)")
    b.add_argument("--out-dir", required=True)
    b.add_argument("--cap", type=int, default=dedup.STRUCTURE_CAP)
    c = sub.add_parser("check")
    c.add_argument("--dir", required=True)
    e = sub.add_parser("evaluate")
    e.add_argument("--dataset", required=True)
    e.add_argument("--system", choices=("source", "mock"), required=True)
    e.add_argument("--out", required=True)
    q = sub.add_parser("create-eval-queues")
    q.add_argument("--max-examples", type=int, default=splits.PILOT_MAX_EXAMPLES)
    args = parser.parse_args(argv)
    session = get_sessionmaker()()
    try:
        return {"build": cmd_build, "check": cmd_check, "evaluate": cmd_evaluate,
                "create-eval-queues": cmd_create_eval_queues}[args.command](args, session)
    except (splits.ManifestError, FileNotFoundError, KeyError) as error:
        session.rollback()
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}), file=sys.stderr)
        return 2
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
