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
from app.evaluation import criteria, metrics, phase9
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
        "by_task_and_segment": dict(Counter(f"{r['task']}:{r['segment']}" for r in rows)),
        "by_queue": dict(Counter(r.get("queue") for r in rows)),
        "by_correction_policy": dict(Counter(str(r.get("correction_policy")) for r in rows)),
        "effective_examples": round(sum(r.get("weight", 1.0) for r in rows), 3),
        "without_seller_context": sorted(f"{r.get('queue')}#{r['position']}" for r in rows
                                         if not r.get("seller_profile_content_hash")),
    }


def _as_list(value) -> list:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def cmd_build(args, session) -> int:
    """One or more queues, each with an optional AI export (paired by order;
    'none' means human-only). Queues are combined, then deduplicated and
    checked together."""
    queues = _as_list(args.queue) or [splits.PILOT_QUEUE]
    exports = _as_list(args.ai_export)
    if exports and len(exports) != len(queues):
        print(json.dumps({"error": "give one --ai-export per --queue (use 'none' for human-only)"}), file=sys.stderr)
        return 2
    exports = [None if (x in (None, "none")) else x for x in exports] or [None] * len(queues)
    built = {"examples": [], "excluded": [], "unreviewed_positions": {}, "counts": {"candidates": 0}}
    for queue, export in zip(queues, exports):
        one = pilot.build(session, queue, pilot.load_jsonl(export) if export else [])
        built["examples"] += one["examples"]
        built["excluded"] += [dict(x, queue=queue) for x in one["excluded"]]
        built["unreviewed_positions"][queue] = one["unreviewed_positions"]
        built["counts"]["candidates"] += one["counts"]["candidates"]
    if len(queues) == 1:
        built["unreviewed_positions"] = built["unreviewed_positions"][queues[0]]
    deduped = dedup.apply(built["examples"], cap=args.cap)
    eligible = [e for e in deduped["kept"] if not e["uncertain"]]
    flagged = [e for e in deduped["kept"] if e["uncertain"]]
    problems = checks.run_all(session, {"eligible": eligible, "flagged": flagged})
    os.makedirs(args.out_dir, exist_ok=True)
    hashes = {
        "eligible": _write_jsonl(os.path.join(args.out_dir, EXPORT_FILES["eligible"]), eligible),
        "flagged": _write_jsonl(os.path.join(args.out_dir, EXPORT_FILES["flagged"]), flagged),
    }
    split_set = {e["split"] for e in deduped["kept"]}
    manifest = {
        "dataset_format": pilot.DATASET_FORMAT,
        "queue": queues[0] if len(queues) == 1 else "+".join(queues),
        "queues": queues,
        # Only an all-train dataset may ever be used for training. Held-out
        # datasets are for evaluation only; test is also never used to tune
        # prompts or the review rubric.
        "allowed_use": ("training" if split_set <= {"train"} else
                        "evaluation only -- held out; never train on it" +
                        ("; never tune prompts or rubric on it" if "test" in split_set else "")),
        "inputs": ({"ai_export": exports[0], "ai_export_sha256": _sha(exports[0]) if exports[0] else None,
                    "human_source": "training_annotations (latest per candidate)"} if len(queues) == 1 else
                   {"queues": [{"queue": q, "ai_export": x, "ai_export_sha256": _sha(x) if x else None}
                               for q, x in zip(queues, exports)],
                    "human_source": "training_annotations (latest per candidate)"}),
        "dedup": deduped["report"] | {"near_duplicates_eligible": dedup.near_duplicate_report(eligible)},
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
    held_out = bool(rows) and all(r["split"] in ("validation", "test") for r in rows)
    if held_out and args.system == "source":
        sut = criteria.CRITERIA["system_under_test"]
        wrong = [r["position"] for r in rows if (r["model_revision"], r["prompt_version"], r["output_schema_version"])
                 != (sut["model_revision"], sut["prompt_version"], sut["output_schema_version"])]
        if wrong:
            print(json.dumps({"error": f"predictions not from the frozen system under test: {wrong}"}), file=sys.stderr)
            return 1
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
                            "split": r["split"], "segment": r["segment"], "review_source": r["review_source"],
                            "uncertain": r["uncertain"], **s})
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in per_example:
        groups[f"task={s['task']}"].append(s)
        groups[f"task={s['task']}|review_source={s['review_source']}"].append(s)
        groups[f"task={s['task']}|segment={s['segment']}"].append(s)
    splits_seen = sorted({r["split"] for r in rows})
    numeric = lambda s: {k: v for k, v in s.items()
                         if isinstance(v, (bool, int, float)) and k not in ("position", "uncertain")}
    sources = sorted({r["review_source"] for r in rows})
    result = {
        "system": args.system,
        "system_detail": ("stored generator outputs (gpt-4o-mini, grounded-v2) as recorded"
                          if args.system == "source" else f"MockAIClient {mock.model_revision}"),
        "metrics_version": metrics.METRICS_VERSION,
        "criteria_id": criteria.CRITERIA_ID, "criteria_sha256": criteria.criteria_digest(),
        "prediction": ("original stored predictions (source_output), scored against separate reference targets"
                       if args.system == "source" else "mock outputs generated now from the recorded inputs"),
        "reference_source": sources,
        "reference_label": ("AI-derived reference targets (review_source=ai, human_verified=false); "
                            "not human-verified quality" if sources == ["ai"] else
                            "reference targets from: " + ", ".join(sources)),
        "dataset": args.dataset, "dataset_sha256": _sha(args.dataset), "splits": splits_seen,
        "held_out": held_out,
        "label": (("HELD-OUT evaluation -- AI-evaluated (AI-derived reference targets, not human-verified)"
                   if "ai" in sources else "HELD-OUT evaluation") if held_out
                  else "IN-SAMPLE (training split) -- descriptive only, NOT a held-out evaluation"),
        "overall": metrics.aggregate([numeric(s) for s in per_example]),
        "by_group": {k: metrics.aggregate([numeric(s) for s in v]) for k, v in sorted(groups.items())},
        "per_example": per_example,
    }
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
    print(json.dumps({k: result[k] for k in ("system", "label", "reference_label", "overall", "by_group")}, indent=2))
    return 0


def cmd_evaluate_predictions(args, session) -> int:
    """Phase 9: score generated predictions (or the stored gpt-4o-mini outputs
    with --system source) on a held-out set with the frozen metrics and the
    phase9 report view. Every example is scored; a missing, empty, truncated
    or unparseable generation is a failure, never dropped."""
    rows = pilot.load_jsonl(args.dataset)
    if not rows or not all(r["split"] in ("validation", "test") for r in rows):
        print(json.dumps({"error": "evaluate-predictions is for held-out (validation/test) datasets only"}), file=sys.stderr)
        return 1
    preds: dict[str, dict[str, Any]] = {}
    if args.system == "source":
        preds = {r["example_id"]: {"output": r["source_output"]} for r in rows}
        system_name, pred_sha = "gpt-4o-mini (stored source_output)", None
    else:
        if not args.predictions or not args.system_name:
            print(json.dumps({"error": "--predictions and --system-name are required"}), file=sys.stderr)
            return 1
        wanted = {r["example_id"] for r in rows}
        for rec in pilot.load_jsonl(args.predictions):
            if rec["system"] == args.system_name and rec["example_id"] in wanted:
                if rec["example_id"] in preds:
                    print(json.dumps({"error": f"duplicate prediction {rec['example_id']}"}), file=sys.stderr)
                    return 1
                preds[rec["example_id"]] = rec
        system_name, pred_sha = args.system_name, _sha(args.predictions)
    per_example = []
    for r in rows:
        s = phase9.score_prediction(r, preds.get(r["example_id"]))
        per_example.append({"example_id": r["example_id"], "position": r["position"], "task": r["task"],
                            "split": r["split"], "segment": r["segment"], "uncertain": r["uncertain"], **s})
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in per_example:
        groups[f"task={s['task']}"].append(s)
        groups[f"task={s['task']}|segment={s['segment']}"].append(s)
    sources = sorted({r["review_source"] for r in rows})
    result = {
        "system": system_name, "predictions": args.predictions, "predictions_sha256": pred_sha,
        "report_view_id": phase9.REPORT_VIEW_ID, "report_view_sha256": phase9.report_view_digest(),
        "criteria_id": criteria.CRITERIA_ID, "criteria_sha256": criteria.criteria_digest(),
        "metrics_version": metrics.METRICS_VERSION,
        "dataset": args.dataset, "dataset_sha256": _sha(args.dataset), "splits": sorted({r["split"] for r in rows}),
        "reference_label": ("AI-derived reference targets (review_source=ai, human_verified=false); "
                            "not human-verified quality" if sources == ["ai"] else "references from: " + ", ".join(sources)),
        "label": "HELD-OUT evaluation -- AI-evaluated (AI-derived reference targets, not human-verified)"
                 if "ai" in sources else "HELD-OUT evaluation",
        "overall": phase9.aggregate(per_example),
        "by_group": {k: phase9.aggregate(v) for k, v in sorted(groups.items())},
        "per_example": per_example,
    }
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
    print(json.dumps({k: result[k] for k in ("system", "label", "overall")}, indent=2))
    return 0


def cmd_eval_record(args, session) -> int:
    """Evaluation record for held-out queues: every candidate is accounted
    for -- generation attempts and failures (from the paid-run ledger),
    unresolved candidates, review exclusions, uncertain flags and the
    eligible set actually evaluated -- plus the frozen criteria and result
    hashes. Positions and counts only; no company data."""
    from sqlalchemy import select as _select
    from app.models import AnnotationCandidate
    ledger: dict[int, dict[str, Any]] = {}
    with open(args.ledger) as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                ledger[r["attempt"]] = r
    record: dict[str, Any] = {"criteria_id": criteria.CRITERIA_ID, "criteria_sha256": criteria.criteria_digest(),
                              "ledger": args.ledger, "ledger_sha256": _sha(args.ledger), "queues": {}}
    for queue in args.queue:
        cands = list(session.scalars(_select(AnnotationCandidate).where(AnnotationCandidate.queue == queue)))
        attempts = [r for r in ledger.values() if r["queue"] == queue]
        failures = [{"position": r["position"], "task": r["task"], "pass": r["pass"], "reason_codes": r.get("reason_codes")}
                    for r in attempts if r["outcome"] != "success"]
        ddir = os.path.join(args.datasets_dir, queue)
        with open(os.path.join(ddir, "dataset-manifest.json")) as f:
            manifest = json.load(f)
        flagged = sorted(r["position"] for r in pilot.load_jsonl(os.path.join(ddir, EXPORT_FILES["flagged"])))
        eligible = manifest["counts"]["eligible"]
        results = {}
        for name in sorted(os.listdir(ddir)):
            if name.startswith("eval-") and name.endswith(".json"):
                with open(os.path.join(ddir, name)) as f:
                    res = json.load(f)
                results[name] = {"sha256": _sha(os.path.join(ddir, name)), "label": res["label"],
                                 "system": res["system"], "n": res["overall"]["n"]}
        record["queues"][queue] = {
            "candidates": len(cands),
            "generated": sum(1 for c in cands if c.source_output_id),
            "generation_attempts": len(attempts),
            "generation_failures": failures,
            "unresolved_not_generated": sorted(c.position for c in cands if not c.source_output_id),
            "review_exclusions": manifest["counts"]["excluded"],
            "flagged_uncertain_positions": flagged,
            "eligible_evaluated": eligible["examples"],
            "eligible_by_task": eligible["by_task"],
            "eligible_by_segment": eligible["by_segment"],
            "reference_source": eligible["by_review_source"],
            "human_verified_references": 0 if set(eligible["by_review_source"]) == {"ai"} else None,
            "dataset_files": manifest["files"],
            "results": results,
        }
    with open(args.out, "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)
    print(json.dumps({q: {k: v for k, v in r.items() if k in ("candidates", "generated", "generation_attempts",
                                                                "unresolved_not_generated", "eligible_evaluated")}
                      for q, r in record["queues"].items()}, indent=2))
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
    b.add_argument("--queue", action="append", help="repeatable; default pilot-v1")
    b.add_argument("--ai-export", action="append",
                   help="AI review export per --queue, in order ('none' = human-only); omit entirely for human-only")
    b.add_argument("--out-dir", required=True)
    b.add_argument("--cap", type=int, default=dedup.STRUCTURE_CAP)
    c = sub.add_parser("check")
    c.add_argument("--dir", required=True)
    e = sub.add_parser("evaluate")
    e.add_argument("--dataset", required=True)
    e.add_argument("--system", choices=("source", "mock"), required=True)
    e.add_argument("--out", required=True)
    ep = sub.add_parser("evaluate-predictions")
    ep.add_argument("--dataset", required=True)
    ep.add_argument("--system", choices=("predictions", "source"), default="predictions")
    ep.add_argument("--predictions")
    ep.add_argument("--system-name")
    ep.add_argument("--out", required=True)
    r = sub.add_parser("eval-record")
    r.add_argument("--queue", action="append", required=True)
    r.add_argument("--ledger", required=True)
    r.add_argument("--datasets-dir", required=True)
    r.add_argument("--out", required=True)
    q = sub.add_parser("create-eval-queues")
    q.add_argument("--max-examples", type=int, default=splits.PILOT_MAX_EXAMPLES)
    args = parser.parse_args(argv)
    session = get_sessionmaker()()
    try:
        return {"build": cmd_build, "check": cmd_check, "evaluate": cmd_evaluate,
                "evaluate-predictions": cmd_evaluate_predictions,
                "create-eval-queues": cmd_create_eval_queues, "eval-record": cmd_eval_record}[args.command](args, session)
    except (splits.ManifestError, FileNotFoundError, KeyError) as error:
        session.rollback()
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}), file=sys.stderr)
        return 2
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
