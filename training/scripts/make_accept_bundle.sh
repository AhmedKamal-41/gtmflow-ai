#!/usr/bin/env bash
# Build the Phase 10 acceptance bundle: the committed training/ package,
# backend/app/ai/prompts.py and the two pinned Phase 8 adapter files. NO data:
# the acceptance inputs are sent as request prompts through the SSH tunnel.
# The adapter files are checked against configs/phase9-eval-v1.json first.
set -euo pipefail
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
OUT="${1:-$REPO/training/runs/gtmflow-phase10-accept-bundle.tar.gz}"
COMMIT="$(git -C "$REPO" rev-parse HEAD)"
if [ -n "$(git -C "$REPO" status --porcelain -- training backend/app/ai/prompts.py)" ]; then
  echo "refused: training/ or prompts.py has uncommitted changes; commit first so the bundle matches $COMMIT" >&2
  exit 2
fi
python3 - "$REPO" <<'PY'
import hashlib, json, sys
from pathlib import Path
repo = Path(sys.argv[1]); cfg_path = repo / "training/configs/phase9-eval-v1.json"
cfg = json.loads(cfg_path.read_text())
adapter = (cfg_path.parent / cfg["adapter"]["dir"]).resolve()
for name, digest in cfg["adapter"]["sha256"].items():
    if hashlib.sha256((adapter / name).read_bytes()).hexdigest() != digest:
        sys.exit(f"refused: adapter {name} does not match the pinned sha256")
print("adapter matches the pinned config")
PY
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git -C "$REPO" archive --format=tar "$COMMIT" training backend/app/ai/prompts.py | tar -x -C "$WORK"
mkdir -p "$WORK/training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter"
cp "$REPO"/training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter/{adapter_config.json,adapter_model.safetensors} \
   "$WORK/training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter/"
echo "$COMMIT" > "$WORK/BUNDLE_COMMIT"
mkdir -p "$(dirname "$OUT")"
tar -czf "$OUT" -C "$WORK" .
sha256sum "$OUT"
echo "bundle commit: $COMMIT (code, prompts and the pinned adapter; no data)"
