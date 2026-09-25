#!/usr/bin/env bash
# Build the Phase 9 generation bundle:
#   - the committed training/ package and backend/app/ai/prompts.py at HEAD;
#   - the inputs-only held-out files (training/runs/phase9-eval-inputs/,
#     from make_eval_inputs.py) -- no references, stored predictions or reviews;
#   - the two pinned files of the Phase 8 epoch-3 adapter.
# Every data file is checked against the sha256 pinned in
# configs/phase9-eval-v1.json before it is packed.
set -euo pipefail
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
OUT="${1:-$REPO/training/runs/gtmflow-phase9-bundle.tar.gz}"
COMMIT="$(git -C "$REPO" rev-parse HEAD)"
if [ -n "$(git -C "$REPO" status --porcelain -- training backend/app/ai/prompts.py)" ]; then
  echo "refused: training/ or prompts.py has uncommitted changes; commit first so the bundle matches $COMMIT" >&2
  exit 2
fi
python3 - "$REPO" <<'PY'
import hashlib, json, sys
from pathlib import Path
repo = Path(sys.argv[1]); cfg_path = repo / "training/configs/phase9-eval-v1.json"
cfg = json.loads(cfg_path.read_text()); base = cfg_path.parent
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
allowed = {"example_id", "split", "subset", "task", "input_snapshot", "prompt_version"}
for name, spec in cfg["inputs"].items():
    p = (base / spec["path"]).resolve()
    if sha(p) != spec["sha256"]:
        sys.exit(f"refused: {p.name} does not match the pinned sha256")
    for line in p.read_text().splitlines():
        extra = set(json.loads(line)) - allowed
        if extra:
            sys.exit(f"refused: {p.name} carries non-input fields {sorted(extra)}")
adapter = (base / cfg["adapter"]["dir"]).resolve()
for name, digest in cfg["adapter"]["sha256"].items():
    if sha(adapter / name) != digest:
        sys.exit(f"refused: adapter {name} does not match the pinned sha256")
print("inputs and adapter match the pinned config")
PY
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git -C "$REPO" archive --format=tar "$COMMIT" training backend/app/ai/prompts.py | tar -x -C "$WORK"
mkdir -p "$WORK/training/runs/phase9-eval-inputs" "$WORK/training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter"
cp "$REPO"/training/runs/phase9-eval-inputs/{validation,test}-{eligible,flagged}.jsonl \
   "$REPO/training/runs/phase9-eval-inputs/inputs-manifest.json" "$WORK/training/runs/phase9-eval-inputs/"
cp "$REPO"/training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter/{adapter_config.json,adapter_model.safetensors} \
   "$WORK/training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter/"
echo "$COMMIT" > "$WORK/BUNDLE_COMMIT"
mkdir -p "$(dirname "$OUT")"
tar -czf "$OUT" -C "$WORK" .
sha256sum "$OUT"
echo "bundle commit: $COMMIT (inputs only; no references, stored predictions, reviews or datasets)"
