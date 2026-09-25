#!/usr/bin/env python3
"""Export inputs-only Phase 9 evaluation files from the frozen held-out datasets.

    python3 training/scripts/make_eval_inputs.py

Reads backend/data/datasets/{validation-v1,test-v1}/{eligible,flagged-uncertain}.jsonl
and writes training/runs/phase9-eval-inputs/<split>-<subset>.jsonl with only
the fields generation needs (example_id, split, subset, task, input_snapshot,
prompt_version). Reference targets, stored gpt-4o-mini predictions and review
data are NOT copied: the GPU host never sees what predictions are scored
against. Output is deterministic (source order, sorted keys), so the pinned
sha256 values in configs/phase9-eval-v1.json can be re-derived.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATASETS = REPO / "backend" / "data" / "datasets"
OUT = REPO / "training" / "runs" / "phase9-eval-inputs"
FIELDS = ("example_id", "split", "task", "input_snapshot", "prompt_version")
SOURCES = {  # (split, subset): (dataset dir, file)
    ("validation", "eligible"): ("validation-v1", "eligible.jsonl"),
    ("validation", "flagged"): ("validation-v1", "flagged-uncertain.jsonl"),
    ("test", "eligible"): ("test-v1", "eligible.jsonl"),
    ("test", "flagged"): ("test-v1", "flagged-uncertain.jsonl"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"note": "inputs only; no references, stored predictions or review data", "files": {}}
    for (split, subset), (dataset, name) in SOURCES.items():
        src = DATASETS / dataset / name
        rows = [json.loads(line) for line in src.read_text().splitlines() if line.strip()]
        if {r["split"] for r in rows} != {split}:
            print(f"refused: {src} is not all-{split}", file=sys.stderr)
            return 1
        out = OUT / f"{split}-{subset}.jsonl"
        with open(out, "w") as f:
            for r in rows:
                f.write(json.dumps({**{k: r[k] for k in FIELDS}, "subset": subset}, sort_keys=True,
                                   ensure_ascii=False) + "\n")
        manifest["files"][out.name] = {
            "sha256": sha256(out), "examples": len(rows),
            "by_task": {t: sum(1 for r in rows if r["task"] == t) for t in sorted({r["task"] for r in rows})},
            "source": f"backend/data/datasets/{dataset}/{name}", "source_sha256": sha256(src),
        }
    (OUT / "inputs-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
