#!/usr/bin/env bash
# Build the self-contained Phase 8 run bundle (no git push needed):
#   - the committed training/ package and backend/app/ai/prompts.py at HEAD
#     (git archive: only committed files, so the bundle matches a commit);
#   - the pinned TRAIN and VALIDATION datasets only. The test dataset is
#     deliberately NOT included: it is reserved for the final evaluation.
# The runner re-verifies every dataset file against the sha256 pinned in the
# config, so a corrupted or substituted file refuses to train.
set -euo pipefail
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
OUT="${1:-$REPO/training/runs/gtmflow-phase8-bundle.tar.gz}"
COMMIT="$(git -C "$REPO" rev-parse HEAD)"
if [ -n "$(git -C "$REPO" status --porcelain -- training backend/app/ai/prompts.py)" ]; then
  echo "refused: training/ or prompts.py has uncommitted changes; commit first so the bundle matches $COMMIT" >&2
  exit 2
fi
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git -C "$REPO" archive --format=tar "$COMMIT" training backend/app/ai/prompts.py | tar -x -C "$WORK"
mkdir -p "$WORK/backend/data/datasets"
for d in train-combined-v1 validation-v1; do
  cp -r "$REPO/backend/data/datasets/$d" "$WORK/backend/data/datasets/$d"
  rm -f "$WORK/backend/data/datasets/$d"/eval-*.json
done
echo "$COMMIT" > "$WORK/BUNDLE_COMMIT"
mkdir -p "$(dirname "$OUT")"
tar -czf "$OUT" -C "$WORK" .
sha256sum "$OUT"
echo "bundle commit: $COMMIT (test dataset not included)"
