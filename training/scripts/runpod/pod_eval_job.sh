#!/usr/bin/env bash
# Phase 9 generation job, executed ON the RunPod pod (started detached by
# launch.py --profile phase9-eval: setsid nohup, so a dropped SSH session
# does not stop it).
#
#   pod_eval_job.sh <bundle_sha256> <generation_deadline_unix> <post_job_grace_seconds>
#
# Steps: verify bundle checksum and allowlist (code, prompts, inputs-only
# held-out files, the two pinned adapter files; no datasets, references,
# reviews or secrets) -> GPU/driver check -> pinned environment (hash-locked,
# versions checked) -> generate plan (re-verifies inputs and adapter hashes)
# -> tests with the GPU hidden -> generate run (validation first, gate, then
# test; passes that cannot finish before the deadline are recorded as not
# generated) -> package with SHA256SUMS. A validation-gate stop is a
# completed job: its evidence is packaged and copied back like any other.
# The EXIT trap arms self-termination after the grace period; the pod's
# start command enforces the hard lifetime.
set -uo pipefail

BUNDLE_SHA="$1"
GEN_DEADLINE="$2"
GRACE="${3:-1800}"
JOB=${GTMFLOW_JOB_DIR:-/workspace/job}          # overridable for local tests only
ROOT=${GTMFLOW_ROOT_DIR:-/workspace/gtmflow}
UPLOAD=${GTMFLOW_UPLOAD_DIR:-/workspace/upload}
RUN_NAME=phase9-eval-v1
RUN_DIR="$ROOT/training/runs/$RUN_NAME/run"
CONFIG=configs/$RUN_NAME.json
ARCHIVE=/workspace/$RUN_NAME.tar.gz
mkdir -p "$JOB"
exec >>"$JOB/job.log" 2>&1
# Same pod environment as the boot watchdog: pod id, pod-scoped key (never
# printed), CLI config, and `t` (self-termination).
. /gtmflow-pod-env.sh || echo "ERROR: /gtmflow-pod-env.sh missing"
STEP=start

status() { echo "$1" >"$JOB/status"; echo "$(date -u +%FT%TZ) STATUS $1"; }
on_exit() {
  local rc=$?
  echo "$rc" >"$JOB/exit_code"
  grep -q '^done' "$JOB/status" 2>/dev/null || status "failed:$STEP:rc=$rc"
  [ -n "${SMI_PID:-}" ] && kill "$SMI_PID" 2>/dev/null
  echo "$(date -u +%FT%TZ) job ended; self-termination armed in ${GRACE}s"
  (sleep "$GRACE"; while :; do echo "$(date -u +%FT%TZ) grace over; terminating"; t; sleep 60; done) \
    >>"$JOB/grace.log" 2>&1 </dev/null &
  disown
}
trap on_exit EXIT
fail() { echo "ERROR: $*"; exit 1; }

status running:verify
STEP=verify
BUNDLE=$UPLOAD/gtmflow-phase9-bundle.tar.gz
echo "$BUNDLE_SHA  $BUNDLE" | sha256sum -c - || fail "bundle checksum mismatch"
tar -tzf "$BUNDLE" >"$JOB/bundle-members.txt" || fail "cannot list bundle"
# Must equal launch.py PHASE9_ALLOWED / PHASE9_FORBIDDEN (a test checks this).
python3 - "$JOB/bundle-members.txt" <<'PY' || fail "bundle contents outside the allowlist"
import re, sys
PHASE9_ALLOWED = r"^(BUNDLE_COMMIT|backend/|backend/app/|backend/app/ai/|backend/app/ai/prompts\.py|training/(?!runs/).*|training/runs/|training/runs/phase9-eval-inputs/((validation|test)-(eligible|flagged)\.jsonl|inputs-manifest\.json)?|training/runs/phase8-qwen3-4b-lora-v1/|training/runs/phase8-qwen3-4b-lora-v1/train/|training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter/(adapter_config\.json|adapter_model\.safetensors)?)$"
PHASE9_FORBIDDEN = r"(\.env($|/)|\.db$|\.sqlite|backend/data/|ai_reviews|/eligible\.jsonl$|flagged-uncertain)"
names = [l.strip().removeprefix("./") for l in open(sys.argv[1])]
bad = [n for n in names if n and (not re.match(PHASE9_ALLOWED, n) or re.search(PHASE9_FORBIDDEN, n, re.I))]
sys.exit("unexpected bundle members: " + ", ".join(bad[:10]) if bad else 0)
PY
rm -rf "$ROOT" && mkdir -p "$ROOT" && tar -xzf "$BUNDLE" -C "$ROOT" || fail "extract failed"
echo "bundle commit: $(cat "$ROOT/BUNDLE_COMMIT")"

status running:gpu
STEP=gpu
nvidia-smi | tee "$JOB/nvidia-smi.txt" || fail "nvidia-smi failed"
DRIVER_CUDA=$(nvidia-smi | grep -oE 'CUDA Version: [0-9]+\.[0-9]+' | awk '{print $3}')
python3 -c "import sys; v=tuple(map(int,'$DRIVER_CUDA'.split('.'))); sys.exit(0 if v>=(12,6) else 'driver CUDA %s < 12.6' % '$DRIVER_CUDA')" \
  || fail "driver too old for the pinned CUDA 12.6 build"
nvidia-smi --query-gpu=timestamp,memory.used,memory.total,utilization.gpu --format=csv -l 5 >"$JOB/gpu-memory.csv" 2>&1 &
SMI_PID=$!

status running:environment
STEP=environment
cd "$ROOT/training"
unset HF_HUB_ENABLE_HF_TRANSFER
export HF_HUB_DISABLE_TELEMETRY=1
command -v uv >/dev/null || pip install uv==0.9.* || fail "uv unavailable"
uv venv --python 3.12 .venv || fail "venv"
VIRTUAL_ENV=.venv uv pip install --require-hashes -r requirements-gpu-lock.txt \
  --index https://download.pytorch.org/whl/cu126 --index-strategy unsafe-best-match || fail "pinned install failed"
VIRTUAL_ENV=.venv uv pip freeze >"$JOB/pip-freeze.txt"
JOB="$JOB" .venv/bin/python - <<'PY' || fail "installed versions differ from the lock"
import os, re
lock = dict(re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", open("requirements-gpu-lock.txt").read(), re.M))
got = dict(re.findall(r"^([A-Za-z0-9_.-]+)==(\S+)", open(os.environ["JOB"] + "/pip-freeze.txt").read(), re.M))
norm = lambda d: {k.lower().replace("_", "-"): v for k, v in d.items()}
lock, got = norm(lock), norm(got)
diff = {k: (v, got.get(k)) for k, v in lock.items() if got.get(k) != v}
print("pinned packages verified:", len(lock), "mismatches:", diff)
raise SystemExit(1 if diff else 0)
PY

status running:plan
STEP=plan
.venv/bin/python -m gtmflow_training.generate plan --config "$CONFIG" || fail "plan"

status running:tests
STEP=tests
# The GPU is hidden from the test suite: no test can start real GPU work.
CUDA_VISIBLE_DEVICES="" .venv/bin/python -m pytest -q || fail "tests"

status running:generate
STEP=generate
.venv/bin/python -m gtmflow_training.generate run --config "$CONFIG" --confirm-paid-compute \
  --deadline-unix "$GEN_DEADLINE" --deadline-reserve-seconds 300
GEN_RC=$?
[ "$GEN_RC" -eq 0 ] || [ "$GEN_RC" -eq 4 ] || fail "generate exited $GEN_RC"
[ "$GEN_RC" -eq 4 ] && echo "validation gate failed: no test generation (packaged as evidence)"

status running:package
STEP=package
kill "$SMI_PID" 2>/dev/null; SMI_PID=
mkdir -p "$RUN_DIR/pod"
cp "$JOB"/{job.log,nvidia-smi.txt,gpu-memory.csv,pip-freeze.txt} "$RUN_DIR/pod/"
cp /watchdog.log "$RUN_DIR/pod/" 2>/dev/null
cp "runs/$RUN_NAME/plan/plan.json" "$RUN_DIR/pod/plan.json"
cp requirements-gpu-lock.txt "$ROOT/BUNDLE_COMMIT" "$RUN_DIR/pod/"
echo "$BUNDLE_SHA" >"$RUN_DIR/pod/bundle.sha256"
echo "$GEN_RC" >"$RUN_DIR/pod/generate_exit_code"
(cd "$RUN_DIR" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS)
tar -czf "$ARCHIVE" -C "$RUN_DIR" . || fail "package"
sha256sum "$ARCHIVE" | awk '{print $1}' >"$ARCHIVE.sha256"
STEP=done
status "done"
