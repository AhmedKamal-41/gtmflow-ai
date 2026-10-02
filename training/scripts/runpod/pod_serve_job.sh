#!/usr/bin/env bash
# Phase 10 acceptance job, executed ON the RunPod pod (started detached by
# launch.py --profile phase10-accept).
#
#   pod_serve_job.sh <bundle_sha256> <serve_deadline_unix> <post_job_grace_seconds>
#
# Steps (same as the verified Phase 9 job up to the environment):
# verify bundle checksum and allowlist (code, prompts, the two pinned adapter
# files; NO data of any kind) -> GPU/driver check -> pinned environment
# (hash-locked, versions checked) -> tests with the GPU hidden -> serve.py on
# 127.0.0.1:8000 (verifies the adapter hashes before loading; bearer token from
# /workspace/job/serve_token, written by the launcher, never printed) ->
# status "serving" -> the workspace runs the acceptance test through an SSH
# tunnel and then creates /workspace/job/stop -> server stops (or at the
# deadline) -> package logs with SHA256SUMS. Prompts and outputs are never
# written to disk here; the request log holds sizes and timings only.
# The EXIT trap arms self-termination after the grace period; the pod's start
# command enforces the hard lifetime.
set -uo pipefail

BUNDLE_SHA="$1"
SERVE_DEADLINE="$2"
GRACE="${3:-1200}"
JOB=${GTMFLOW_JOB_DIR:-/workspace/job}
ROOT=${GTMFLOW_ROOT_DIR:-/workspace/gtmflow}
UPLOAD=${GTMFLOW_UPLOAD_DIR:-/workspace/upload}
RUN_NAME=phase10-accept-v1
RUN_DIR="$ROOT/training/runs/$RUN_NAME/run"
CONFIG=configs/phase9-eval-v1.json
ARCHIVE=/workspace/$RUN_NAME.tar.gz
mkdir -p "$JOB"
exec >>"$JOB/job.log" 2>&1
. /gtmflow-pod-env.sh || echo "ERROR: /gtmflow-pod-env.sh missing"
STEP=start

status() { echo "$1" >"$JOB/status"; echo "$(date -u +%FT%TZ) STATUS $1"; }
on_exit() {
  local rc=$?
  echo "$rc" >"$JOB/exit_code"
  [ -n "${SERVE_PID:-}" ] && kill "$SERVE_PID" 2>/dev/null
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
BUNDLE=$UPLOAD/gtmflow-phase10-accept-bundle.tar.gz
echo "$BUNDLE_SHA  $BUNDLE" | sha256sum -c - || fail "bundle checksum mismatch"
tar -tzf "$BUNDLE" >"$JOB/bundle-members.txt" || fail "cannot list bundle"
# Must equal launch.py PHASE10_ALLOWED / PHASE10_FORBIDDEN (a test checks this).
python3 - "$JOB/bundle-members.txt" <<'PY' || fail "bundle contents outside the allowlist"
import re, sys
PHASE10_ALLOWED = r"^(BUNDLE_COMMIT|backend/|backend/app/|backend/app/ai/|backend/app/ai/prompts\.py|training/(?!runs/).*|training/runs/|training/runs/phase8-qwen3-4b-lora-v1/|training/runs/phase8-qwen3-4b-lora-v1/train/|training/runs/phase8-qwen3-4b-lora-v1/train/best_adapter/(adapter_config\.json|adapter_model\.safetensors)?)$"
PHASE10_FORBIDDEN = r"(\.env($|/)|\.db$|\.sqlite|backend/data/|\.jsonl$|ai_reviews|datasets|phase9-eval-inputs)"
names = [l.strip().removeprefix("./") for l in open(sys.argv[1])]
bad = [n for n in names if n and (not re.match(PHASE10_ALLOWED, n) or re.search(PHASE10_FORBIDDEN, n, re.I))]
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

status running:tests
STEP=tests
CUDA_VISIBLE_DEVICES="" .venv/bin/python -m pytest -q || fail "tests"

status running:load
STEP=load
[ -s "$JOB/serve_token" ] || fail "serve token missing"
mkdir -p "$RUN_DIR/serve"
GTMFLOW_SERVE_TOKEN="$(cat "$JOB/serve_token")" .venv/bin/python -m gtmflow_training.serve \
  --config "$CONFIG" --out "$RUN_DIR/serve" --port 8000 --confirm-paid-compute \
  --stop-file "$JOB/stop" --deadline-unix "$SERVE_DEADLINE" >"$JOB/serve.log" 2>&1 &
SERVE_PID=$!
for _ in $(seq 1 240); do   # model download + load: up to 20 min
  curl -sf http://127.0.0.1:8000/healthz >/dev/null && break
  kill -0 "$SERVE_PID" 2>/dev/null || fail "server exited while loading (see serve.log)"
  sleep 5
done
curl -sf http://127.0.0.1:8000/healthz >/dev/null || fail "server not ready after 20 min"
grep '"type": "serving"' "$JOB/serve.log" | tail -n 1

status serving
STEP=serving
wait "$SERVE_PID"
SERVE_RC=$?
SERVE_PID=
[ "$SERVE_RC" -eq 0 ] || fail "server exited $SERVE_RC"

status running:package
STEP=package
kill "$SMI_PID" 2>/dev/null; SMI_PID=
mkdir -p "$RUN_DIR/pod"
cp "$JOB"/{job.log,serve.log,nvidia-smi.txt,gpu-memory.csv,pip-freeze.txt} "$RUN_DIR/pod/"
cp /watchdog.log "$RUN_DIR/pod/" 2>/dev/null
cp requirements-gpu-lock.txt "$ROOT/BUNDLE_COMMIT" "$RUN_DIR/pod/"
echo "$BUNDLE_SHA" >"$RUN_DIR/pod/bundle.sha256"
(cd runs/phase8-qwen3-4b-lora-v1/train/best_adapter && sha256sum adapter_config.json adapter_model.safetensors) >"$RUN_DIR/pod/adapter.sha256"
(cd "$RUN_DIR" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS)
tar -czf "$ARCHIVE" -C "$RUN_DIR" . || fail "package"
sha256sum "$ARCHIVE" | awk '{print $1}' >"$ARCHIVE.sha256"
STEP=done
status "done"
