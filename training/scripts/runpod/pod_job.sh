#!/usr/bin/env bash
# Phase 8 job, executed ON the RunPod pod (started detached by launch.py:
# setsid nohup, so a dropped SSH session does not stop it).
#
#   pod_job.sh <bundle_sha256> <train_deadline_unix> <post_job_grace_seconds>
#
# Steps: verify bundle checksum and contents -> GPU/driver check -> pinned
# environment (hash-locked) and version check -> plan -> tests -> GPU
# preflight with the real model (gated) -> training (stops before the
# deadline) -> package artifacts. Status goes to /workspace/job/status.
# Whatever happens, the EXIT trap arms a self-termination after the grace
# period (the orchestrator normally copies artifacts back and terminates
# the pod first); the pod's start command also enforces a hard lifetime.
set -uo pipefail

BUNDLE_SHA="$1"
TRAIN_DEADLINE="$2"
GRACE="${3:-3600}"
JOB=${GTMFLOW_JOB_DIR:-/workspace/job}          # overridable for local tests only
ROOT=${GTMFLOW_ROOT_DIR:-/workspace/gtmflow}
UPLOAD=${GTMFLOW_UPLOAD_DIR:-/workspace/upload}
RUN_NAME=phase8-qwen3-4b-lora-v1
RUN_DIR="$ROOT/training/runs/$RUN_NAME/train"
CONFIG=configs/$RUN_NAME.json
mkdir -p "$JOB"
exec >>"$JOB/job.log" 2>&1
# SSH sessions do not inherit the container environment; take the pod id and
# the pod-scoped API key (never printed) from the container's main process,
# so self-termination works from this job too.
while IFS= read -r -d '' kv; do case "$kv" in RUNPOD_POD_ID=*|RUNPOD_API_KEY=*) export "$kv";; esac; done </proc/1/environ
[ -n "${RUNPOD_POD_ID:-}" ] || { echo "ERROR: RUNPOD_POD_ID unavailable"; }
STEP=start

status() { echo "$1" >"$JOB/status"; echo "$(date -u +%FT%TZ) STATUS $1"; }
self_terminate() {
  runpodctl remove pod "$RUNPOD_POD_ID" \
    || curl -fsS -X DELETE -H "Authorization: Bearer ${RUNPOD_API_KEY:-}" "https://rest.runpod.io/v1/pods/$RUNPOD_POD_ID"
}
on_exit() {
  local rc=$?
  echo "$rc" >"$JOB/exit_code"
  grep -q '^done' "$JOB/status" 2>/dev/null || status "failed:$STEP:rc=$rc"
  [ -n "${SMI_PID:-}" ] && kill "$SMI_PID" 2>/dev/null
  echo "$(date -u +%FT%TZ) job ended; self-termination armed in ${GRACE}s"
  (sleep "$GRACE"; while :; do echo "$(date -u +%FT%TZ) grace over; terminating"; self_terminate; sleep 60; done) \
    >>"$JOB/grace.log" 2>&1 </dev/null &
  disown
}
trap on_exit EXIT
fail() { echo "ERROR: $*"; exit 1; }
now() { date +%s; }

status running:verify
STEP=verify
BUNDLE=$UPLOAD/gtmflow-phase8-bundle.tar.gz
echo "$BUNDLE_SHA  $BUNDLE" | sha256sum -c - || fail "bundle checksum mismatch"
# Allowlist: code, prompts, and the train/validation eligible datasets only.
tar -tzf "$BUNDLE" >"$JOB/bundle-members.txt" || fail "cannot list bundle"
python3 -c '
import re, sys
allowed = re.compile(r"^(BUNDLE_COMMIT|training/.*|backend/|backend/app/|backend/app/ai/|backend/app/ai/prompts\.py|backend/data/|backend/data/datasets/|backend/data/datasets/(train-combined-v1|validation-v1)/(eligible\.jsonl|dataset-manifest\.json)?)$")
bad = [n for n in (l.strip().removeprefix("./") for l in sys.stdin) if n and not allowed.match(n)]
sys.exit("unexpected bundle members: " + ", ".join(bad[:10]) if bad else 0)' <"$JOB/bundle-members.txt" || fail "bundle contents outside the allowlist"
grep -Eiq '(test-v1|\.env($|/)|\.db$|\.sqlite|flagged)' "$JOB/bundle-members.txt" && fail "bundle contains test data, secrets or a database"
rm -rf "$ROOT" && mkdir -p "$ROOT" && tar -xzf "$BUNDLE" -C "$ROOT" || fail "extract failed"
echo "bundle commit: $(cat "$ROOT/BUNDLE_COMMIT")"

status running:gpu
STEP=gpu
nvidia-smi | tee "$JOB/nvidia-smi.txt" || fail "nvidia-smi failed"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv | tee -a "$JOB/nvidia-smi.txt"
DRIVER_CUDA=$(nvidia-smi | grep -oE 'CUDA Version: [0-9]+\.[0-9]+' | awk '{print $3}')
python3 -c "import sys; v=tuple(map(int,'$DRIVER_CUDA'.split('.'))); sys.exit(0 if v>=(12,6) else 'driver CUDA %s < 12.6' % '$DRIVER_CUDA')" \
  || fail "driver too old for the pinned CUDA 12.6 build"
# Device-level memory samples (includes the CUDA context), every 5 s.
nvidia-smi --query-gpu=timestamp,memory.used,memory.total,utilization.gpu --format=csv -l 5 >"$JOB/gpu-memory.csv" 2>&1 &
SMI_PID=$!

status running:environment
STEP=environment
cd "$ROOT/training"
unset HF_HUB_ENABLE_HF_TRANSFER  # image default; the pinned env does not include hf_transfer
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
.venv/bin/python -c "import torch; assert torch.cuda.is_available() and torch.cuda.is_bf16_supported(); print('torch', torch.__version__, 'cuda', torch.version.cuda, torch.cuda.get_device_name(0))" \
  || fail "torch cannot use the GPU"

status running:plan
STEP=plan
.venv/bin/python -m gtmflow_training.train plan --config "$CONFIG" || fail "plan"

status running:tests
STEP=tests
.venv/bin/python -m pytest -q || fail "tests"

status running:preflight
STEP=preflight
.venv/bin/python -m gtmflow_training.train preflight --config "$CONFIG" --confirm-paid-compute \
  --out "runs/$RUN_NAME/preflight" || fail "preflight"
.venv/bin/python - "$TRAIN_DEADLINE" <<'PY' || fail "preflight gate"
import json, sys, time
p = json.load(open("runs/phase8-qwen3-4b-lora-v1/preflight/preflight.json"))["preflight"]
mem = p["gpu_memory"]
available_min = (float(sys.argv[1]) - time.time()) / 60
ok_mem = mem["peak_reserved_gib"] <= 0.90 * mem["total_gib"]
ok_time = p["projected_training_minutes"] * 1.3 <= available_min
print(json.dumps({"preflight": p, "available_minutes": round(available_min, 1),
                  "memory_ok": ok_mem, "time_ok": ok_time}))
raise SystemExit(0 if ok_mem and ok_time else 1)
PY

status running:train
STEP=train
.venv/bin/python -m gtmflow_training.train train --config "$CONFIG" --confirm-paid-compute \
  --deadline-unix "$TRAIN_DEADLINE" --deadline-reserve-seconds 600 || fail "train"

status running:package
STEP=package
kill "$SMI_PID" 2>/dev/null; SMI_PID=
mkdir -p "$RUN_DIR/pod"
cp "$JOB"/{job.log,nvidia-smi.txt,gpu-memory.csv,pip-freeze.txt} "$RUN_DIR/pod/"
cp /watchdog.log "$RUN_DIR/pod/" 2>/dev/null
cp -r "runs/$RUN_NAME/preflight" "$RUN_DIR/pod/preflight"
cp "runs/$RUN_NAME/plan/plan.json" "$RUN_DIR/pod/plan.json"
cp requirements-gpu-lock.txt "$RUN_DIR/pod/"
cp "$ROOT/BUNDLE_COMMIT" "$RUN_DIR/pod/"
echo "$BUNDLE_SHA" >"$RUN_DIR/pod/bundle.sha256"
(cd "$RUN_DIR" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS)
tar -czf /workspace/phase8-run-v1.tar.gz -C "$RUN_DIR" . || fail "package"
sha256sum /workspace/phase8-run-v1.tar.gz | awk '{print $1}' >/workspace/phase8-run-v1.tar.gz.sha256
STEP=done
status "done"
