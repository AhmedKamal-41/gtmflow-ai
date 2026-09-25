#!/usr/bin/env python3
"""Phase 8 RunPod orchestrator (runs in the project workspace; stdlib only).

    python3 training/scripts/runpod/launch.py check
    python3 training/scripts/runpod/launch.py run --bundle-sha256 <sha> --confirm-paid-compute
    python3 training/scripts/runpod/launch.py resume          # re-attach after a local disconnect
    python3 training/scripts/runpod/launch.py terminate       # emergency: remove this run's pod now

The RunPod API key is read from $RUNPOD_API_KEY, else the RUNPOD_API_KEY line of
backend/.env (only that line is parsed; the file must be git-ignored), else
~/.config/gtmflow/runpod_api_key. It is never printed or logged (event records are
scrubbed), and .env is never transferred (the bundle allowlist refuses it). The SSH key is ~/.ssh/gtmflow_runpod_ed25519 (local;
only the public half is sent, as the pod's PUBLIC_KEY).

Spending safeguards (a shell timeout does not stop RunPod billing, so the pod
removes itself):
  1. Pod-side hard lifetime, armed by the container start command at boot and
     independent of any SSH session: the pod terminates itself
     HARD_LIMIT_SECONDS after first boot (survives container restarts).
  2. Pod-side claim check: if the job is not started within CLAIM_LIMIT_SECONDS
     of boot (setup never happened), the pod terminates itself.
  3. Pod-side post-job grace: when the job ends (success or failure), the pod
     terminates itself GRACE_SECONDS later if nothing else has.
  4. Workspace-side watchdog (detached process) removes the pod at the hard
     limit (+2 min) via the API, in case the pod-side mechanism fails.
  5. This orchestrator terminates the pod as soon as artifacts are verified, or
     on any error, and confirms removal via the API.
  Before any data is transferred, the orchestrator verifies over SSH that the
  pod-side watchdog is armed and that the pod can reach the RunPod API with its
  pod-scoped key; otherwise it terminates the pod and stops.

Worst case: HARD_LIMIT_SECONDS x MAX_COST_PER_HR + disk = 3.25 h x $0.80 + ~$0.02 = $2.62 (< $3).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
TRAINING = REPO / "training"
RUN_NAME = "phase8-qwen3-4b-lora-v1"
STATE_DIR = TRAINING / "runs" / "runpod"
STATE = STATE_DIR / "state.json"
EVENTS = STATE_DIR / "events.jsonl"
KNOWN_HOSTS = STATE_DIR / "known_hosts"
BUNDLE = TRAINING / "runs" / "gtmflow-phase8-bundle.tar.gz"
POD_JOB = Path(__file__).resolve().parent / "pod_job.sh"
LOCAL_RUN = TRAINING / "runs" / RUN_NAME / "train"
SSH_KEY = Path.home() / ".ssh" / "gtmflow_runpod_ed25519"
KEY_FILE = Path.home() / ".config" / "gtmflow" / "runpod_api_key"
ENV_FILE = REPO / "backend" / ".env"
_KEY: str | None = None
REST = "https://rest.runpod.io/v1"
GRAPHQL = "https://api.runpod.io/graphql"
USER_AGENT = "gtmflow-phase8-launcher/1.0 (+python-urllib)"  # the default urllib agent is blocked (Cloudflare 1010)

POD_NAME_PREFIX = "gtmflow-phase8-"
IMAGE = "runpod/base:1.3.2-ubuntu2204"  # 656 MB; has /start.sh (sshd from PUBLIC_KEY) and uv
GPU_TYPES = ["NVIDIA L4"]
FALLBACK_GPU_TYPES = ["NVIDIA GeForce RTX 4090"]  # only with --allow-rtx4090 (24 GB, bf16)
MAX_COST_PER_HR = 0.80
HARD_LIMIT_SECONDS = int(3.25 * 3600)
CLAIM_LIMIT_SECONDS = 30 * 60
GRACE_SECONDS = 60 * 60
COPY_BACK_RESERVE_SECONDS = 40 * 60  # training must end this long before the hard limit
BUDGET_USD = 3.00
CONTAINER_DISK_GB = 50
DISK_USD_PER_GB_MONTH = 0.10

# Shared pod-side environment, written at boot by the container start command
# and sourced by every pod-side context (the boot watchdog, SSH-launched checks
# and pod_job.sh), so all of them use the same CLI configuration and the same
# self-termination function. It holds no secret itself: it takes the pod id and
# the RunPod-provided pod-scoped key from the container's main process.
# runpodctl on RunPod pods is the v1 CLI (checked on a pod: no `pod` subcommand);
# v1.14.15 source: RUNPOD_API_KEY env first, then ~/.runpod/config.toml
# (apikey/apiurl); `remove pod` = GraphQL podTerminate. `runpodctl config` is NOT
# used: besides saving the file it registers an SSH key on the account.
POD_ENV_SH = r'''# gtmflow pod environment (generated at boot)
while IFS= read -r -d '' kv; do case "$kv" in RUNPOD_POD_ID=*|RUNPOD_API_KEY=*) export "$kv";; esac; done </proc/1/environ
export HOME=/root RUNPOD_API_URL="https://api.runpod.io/graphql"
if [ ! -s /root/.runpod/config.toml ]; then
  mkdir -p /root/.runpod && (umask 077; printf 'apikey = "%s"\napiurl = "%s"\n' "${RUNPOD_API_KEY:-}" "$RUNPOD_API_URL" >/root/.runpod/config.toml)
fi
chmod 700 /root/.runpod; chmod 600 /root/.runpod/config.toml
gql_terminate() {  # same GraphQL mutation as `runpodctl remove pod` (v1)
  curl -sS -m 30 -A gtmflow-pod/1.0 -H "Content-Type: application/json" -H "Authorization: Bearer ${RUNPOD_API_KEY:-}" \
    -d '{"query":"mutation t($p: String!) { podTerminate(input: {podId: $p}) }","variables":{"p":"'"$1"'"}}' "$RUNPOD_API_URL"
}
t() { runpodctl remove pod "$RUNPOD_POD_ID" || gql_terminate "$RUNPOD_POD_ID"; }
'''

# Container command: write and source the shared environment, arm the hard
# lifetime and the claim check at boot, then hand over to /start.sh (sshd etc.).
WATCHDOG_CMD = r'''W=/watchdog.log
cat >/gtmflow-pod-env.sh <<'GTMFLOW_ENV'
''' + POD_ENV_SH + r'''GTMFLOW_ENV
. /gtmflow-pod-env.sh
[ -f /watchdog.boot ] || date +%s >/watchdog.boot
BOOT=$(cat /watchdog.boot); NOW=$(date +%s)
HARD=$(( BOOT + ${GTMFLOW_HARD_LIMIT_SECONDS:-11700} - NOW )); CLAIM=$(( BOOT + ${GTMFLOW_CLAIM_LIMIT_SECONDS:-1800} - NOW ))
echo "$(date -u +%FT%TZ) armed: hard limit in ${HARD}s, claim check in ${CLAIM}s" >>$W
( sleep $(( HARD > 0 ? HARD : 0 )); while :; do echo "$(date -u +%FT%TZ) hard limit reached; terminating" >>$W; t >>$W 2>&1; sleep 60; done ) &
( sleep $(( CLAIM > 0 ? CLAIM : 0 )); if [ ! -e /workspace/job/claimed ]; then while :; do echo "$(date -u +%FT%TZ) job never started; terminating" >>$W; t >>$W 2>&1; sleep 60; done; fi ) &
exec /start.sh'''


# ------------------------------------------------------------------ utils

def now() -> float:
    return time.time()


def iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or now(), timezone.utc).isoformat(timespec="seconds")


def scrub(text: str) -> str:
    return text.replace(_KEY, "<redacted>") if _KEY else text


def event(kind: str, **fields) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    line = scrub(json.dumps({"at": iso(), "event": kind, **fields}, default=str))
    with open(EVENTS, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _key_from_env_file(path: Path) -> str:
    """Value of RUNPOD_API_KEY in a dotenv file; other lines are not kept."""
    if not path.exists():
        return ""
    ignored = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=REPO).returncode == 0
    if not ignored:
        sys.exit(f"refused: {path} is not git-ignored; not reading a credential from it")
    for line in path.read_text().splitlines():
        m = re.match(r"\s*(?:export\s+)?RUNPOD_API_KEY\s*=\s*(.*?)\s*$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def api_key() -> str:
    global _KEY
    if _KEY is None:
        key = (os.environ.get("RUNPOD_API_KEY") or _key_from_env_file(ENV_FILE)
               or (KEY_FILE.read_text().strip() if KEY_FILE.exists() else ""))
        if not key:
            sys.exit(f"no RunPod API key: set RUNPOD_API_KEY in {ENV_FILE} (git-ignored) or the environment")
        _KEY = key
    return _KEY


class ApiError(RuntimeError):
    pass


def rest(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    req = urllib.request.Request(REST + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as err:
        text = scrub(err.read().decode(errors="replace")[:500])
        return err.code, {"error": text}
    except (urllib.error.URLError, TimeoutError) as err:
        raise ApiError(f"{method} {path}: {err}") from None


def balance() -> dict | None:
    query = {"query": "query { myself { clientBalance currentSpendPerHr } }"}
    req = urllib.request.Request(GRAPHQL, data=json.dumps(query).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read()).get("data", {}).get("myself")
    except Exception as err:  # noqa: BLE001 -- informational only
        return {"unavailable": type(err).__name__}


def get_pod(pod_id: str) -> dict | None:
    status, body = rest("GET", f"/pods/{pod_id}")
    if status == 404 or (isinstance(body, dict) and "not found" in json.dumps(body).lower()):
        return None
    if status != 200:
        raise ApiError(f"GET pod {pod_id}: HTTP {status} {body}")
    return body


def list_pods() -> list[dict]:
    status, body = rest("GET", "/pods")
    if status != 200:
        raise ApiError(f"GET pods: HTTP {status} {body}")
    return body if isinstance(body, list) else body.get("pods", [])


def terminate(pod_id: str, reason: str) -> bool:
    """Remove the pod and confirm it no longer exists. Returns True when gone."""
    event("terminate_requested", pod=pod_id, reason=reason)
    for attempt in range(20):
        try:
            if get_pod(pod_id) is None:
                event("pod_removed_confirmed", pod=pod_id)
                return True
            status, body = rest("DELETE", f"/pods/{pod_id}")
            event("delete_sent", pod=pod_id, http=status)
        except ApiError as err:
            event("delete_error", pod=pod_id, error=str(err))
        time.sleep(15)
    event("pod_removal_unconfirmed", pod=pod_id)
    return False


def check_bundle(expected_sha: str | None) -> dict:
    if not BUNDLE.exists():
        sys.exit(f"bundle missing: run training/scripts/make_bundle.sh ({BUNDLE})")
    digest = sha256(BUNDLE)
    if expected_sha and digest != expected_sha:
        sys.exit(f"bundle sha256 {digest} != expected {expected_sha}")
    allowed = re.compile(r"^(BUNDLE_COMMIT|training/.*|backend/|backend/app/|backend/app/ai/|backend/app/ai/prompts\.py|"
                         r"backend/data/|backend/data/datasets/|backend/data/datasets/(train-combined-v1|validation-v1)/"
                         r"(eligible\.jsonl|dataset-manifest\.json)?)$")
    forbidden = re.compile(r"(test-v1|\.env($|/)|\.db$|\.sqlite|flagged)", re.I)
    with tarfile.open(BUNDLE) as tar:
        members = {m.name.removeprefix("./").rstrip("/") + ("/" if m.isdir() else ""): m for m in tar.getmembers()}
        names = [n for n in members if n not in ("", "./", "/")]
        bad = [n for n in names if not allowed.match(n) or forbidden.search(n)]
        if bad:
            sys.exit(f"bundle holds files outside the allowlist: {bad[:10]}")
        commit = tar.extractfile(members["BUNDLE_COMMIT"]).read().decode().strip()
    datasets = sorted(n for n in names if n.endswith(".jsonl"))
    return {"sha256": digest, "bytes": BUNDLE.stat().st_size, "commit": commit, "members": len(names),
            "datasets": datasets}


# -------------------------------------------------------------------- ssh

def ssh_base(state: dict) -> list[str]:
    return ["-i", str(SSH_KEY), "-o", "IdentitiesOnly=yes", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
            "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4", "-o", "BatchMode=yes"]


def ssh(state: dict, command: str, timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["ssh", *ssh_base(state), "-p", str(state["ssh_port"]), f"root@{state['ssh_host']}", command]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"ssh `{command[:80]}` exit {proc.returncode}: {proc.stderr.strip()[-400:]}")
    return proc


def scp(state: dict, src: str, dst: str, timeout: int = 1800) -> None:
    cmd = ["scp", *ssh_base(state), "-P", str(state["ssh_port"]), src, dst]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"scp exit {proc.returncode}: {proc.stderr.strip()[-400:]}")


def remote(state: dict, path: str) -> str:
    return f"root@{state['ssh_host']}:{path}"


# ---------------------------------------------------------------- phases

def create_pod(allow_4090: bool) -> dict:
    pub = SSH_KEY.with_suffix(".pub").read_text().strip()
    body = {
        "name": POD_NAME_PREFIX + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "imageName": IMAGE,
        "cloudType": "SECURE",
        "computeType": "GPU",
        "gpuTypeIds": GPU_TYPES + (FALLBACK_GPU_TYPES if allow_4090 else []),
        "gpuCount": 1,
        "interruptible": False,
        "containerDiskInGb": CONTAINER_DISK_GB,
        "volumeInGb": 0,
        "ports": ["22/tcp"],
        "supportPublicIp": True,
        "minVCPUPerGPU": 8,
        "minRAMPerGPU": 30,
        "allowedCudaVersions": ["12.6", "12.7", "12.8", "12.9", "13.0"],
        "env": {"PUBLIC_KEY": pub,
                "GTMFLOW_HARD_LIMIT_SECONDS": str(HARD_LIMIT_SECONDS),
                "GTMFLOW_CLAIM_LIMIT_SECONDS": str(CLAIM_LIMIT_SECONDS)},
        "dockerStartCmd": ["bash", "-c", WATCHDOG_CMD],
    }
    status, pod = rest("POST", "/pods", body)
    if status == 400 and "allowedCudaVersions" in json.dumps(pod):
        # Filter not accepted: create without it; pod_job.sh still refuses drivers below CUDA 12.6.
        event("create_retry_without_cuda_filter", detail=str(pod)[:300])
        body.pop("allowedCudaVersions")
        status, pod = rest("POST", "/pods", body)
    if status not in (200, 201) and (body["minVCPUPerGPU"], body["minRAMPerGPU"]) != (4, 23):
        # No machine with 8 vCPU / 30 GB: RunPod's L4 offers start at 4 vCPU / 23 GB RAM,
        # which still holds the 8 GB bf16 checkpoint during loading. Failed creates are free.
        event("create_retry_smaller_host", http=status, detail=str(pod)[:300])
        body.update(minVCPUPerGPU=4, minRAMPerGPU=23)
        status, pod = rest("POST", "/pods", body)
    if status not in (200, 201):
        raise ApiError(f"create pod: HTTP {status} {pod}")
    return pod


def start_local_watchdog(pod_id: str, at: float) -> int:
    proc = subprocess.Popen([sys.executable, __file__, "watchdog", "--pod", pod_id, "--at", str(at)],
                            stdin=subprocess.DEVNULL, stdout=open(STATE_DIR / "watchdog.log", "a"),
                            stderr=subprocess.STDOUT, start_new_session=True)
    return proc.pid


def wait_for_ssh(state: dict) -> None:
    deadline = now() + 20 * 60
    while now() < deadline:
        pod = get_pod(state["pod_id"])
        if pod is None:
            raise RuntimeError("pod disappeared while starting")
        ip, ports = pod.get("publicIp"), pod.get("portMappings") or {}
        if ip and ports.get("22"):
            state.update(ssh_host=ip, ssh_port=ports["22"], cost_per_hr=pod.get("costPerHr"),
                         gpu=(pod.get("gpu") or {}).get("displayName") or pod.get("gpu"),
                         machine=pod.get("machine"))
            save_state(state)
            try:
                ssh(state, "true", timeout=40)
                event("ssh_ready", host=ip, port=ports["22"])
                return
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
        time.sleep(15)
    raise RuntimeError("SSH did not become available within 20 minutes")


PROBE_POD_ID = "gtmflowprobe0nonexistent"  # never a real pod id

SAFEGUARD_PROBE = r"""
. /gtmflow-pod-env.sh || echo ENV_MISSING
K="${RUNPOD_API_KEY:-}"; P="${RUNPOD_POD_ID:-}"
red() { local x="$1"; [ -n "$K" ] && x="${x//$K/<redacted>}"; printf '%s' "$x" | tr '\n' ' ' | cut -c1-300; }
cat /watchdog.log
echo "POD=$P"; [ -n "$K" ] && echo POD_KEY=present || echo POD_KEY=absent
echo "CLI_VERSION=$(runpodctl version 2>&1 | tail -n 1)"
cfg=$(sed -n 's/^apikey = "\(.*\)"$/\1/p' /root/.runpod/config.toml 2>/dev/null)
[ -n "$K" ] && [ "$cfg" = "$K" ] && echo CFG=match || echo CFG=mismatch
echo "CFG_MODE=$(stat -c %a /root/.runpod/config.toml 2>/dev/null)"
r=$(curl -sS -m 30 -A gtmflow-pod/1.0 -H "Content-Type: application/json" -H "Authorization: Bearer $K" -w ' HTTP=%{http_code}' \
    -d '{"query":"query q($p: String!) { pod(input: {podId: $p}) { id desiredStatus } }","variables":{"p":"'"$P"'"}}' "$RUNPOD_API_URL" 2>&1)
echo "READ: $(red "$r")"
c=$(runpodctl remove pod PROBE_ID 2>&1); echo "CLI_TERMINATE_PROBE: $(red "$c")"
g=$(gql_terminate PROBE_ID 2>&1); echo "GQL_TERMINATE_PROBE: $(red "$g")"
""".replace("PROBE_ID", PROBE_POD_ID)


def verify_safeguards(state: dict) -> dict:
    """Refuse to transfer data unless (1) the boot watchdog is armed, (2) the
    pod-scoped key is present and is what the CLI is configured with, (3) the
    key can read this pod through GraphQL, and (4) the key passes the
    authorization of the podTerminate mutation that the watchdog uses (probed on
    a nonexistent pod id: POD_NOT_FOUND = authorized and looked up; 401/other =
    not). (4) is evidence, not proof, that the pod may delete itself; proof is
    the end-of-run self-deletion (prove_self_delete)."""
    out = ssh(state, SAFEGUARD_PROBE, timeout=180).stdout
    line = lambda tag: next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith(tag + ":")), "")
    read = line("READ")
    cli_probe, gql_probe = line("CLI_TERMINATE_PROBE"), line("GQL_TERMINATE_PROBE")
    result = {
        "watchdog_armed": "armed: hard limit" in out,
        "pod_id_matches": f"POD={state['pod_id']}" in out,
        "pod_key": "present" if "POD_KEY=present" in out else "absent",
        "cli_version": next((l.split("=", 1)[1] for l in out.splitlines() if l.startswith("CLI_VERSION=")), None),
        "cli_config_matches_pod_key": "CFG=match" in out,
        "cli_config_mode": next((l.split("=", 1)[1] for l in out.splitlines() if l.startswith("CFG_MODE=")), None),
        "graphql_read_own_pod": bool(re.search(r'"id"\s*:\s*"' + re.escape(state["pod_id"]) + '"', read)) and "HTTP=200" in read,
        "cli_terminate_authorized": "pod not found to terminate" in cli_probe,
        "graphql_terminate_authorized": "POD_NOT_FOUND" in gql_probe,
        "responses": {"read": read, "cli_terminate_probe": cli_probe, "graphql_terminate_probe": gql_probe},
        "watchdog_log": [l for l in out.splitlines() if "armed" in l],
        "delete_permission": "inferred, not proven (proof = end-of-run self-deletion)",
    }
    event("safeguards_checked", **result)
    required = ("watchdog_armed", "pod_id_matches", "cli_config_matches_pod_key", "graphql_read_own_pod",
                "cli_terminate_authorized")
    if result["pod_key"] != "present" or result["cli_config_mode"] != "600" or not all(result[k] for k in required):
        raise RuntimeError(f"pod-side safeguards not verified: {result}")
    return result


def prove_self_delete(state: dict) -> bool:
    """After verified copy-back: the pod deletes itself with its own key via the
    same path as the watchdog (`t`); confirmed through the account API."""
    ssh(state, "setsid nohup bash -c '. /gtmflow-pod-env.sh; sleep 3; t >/tmp/self-delete.log 2>&1' "
               ">/dev/null 2>&1 </dev/null & echo sent", timeout=60, check=False)
    deadline = now() + 180
    while now() < deadline:
        time.sleep(10)
        try:
            if get_pod(state["pod_id"]) is None:
                event("self_delete_proven", pod=state["pod_id"])
                return True
        except ApiError:
            pass
    event("self_delete_not_observed", pod=state["pod_id"])
    return False


def upload_and_start(state: dict) -> None:
    ssh(state, "mkdir -p /workspace/upload /workspace/job && touch /workspace/job/claimed")
    scp(state, str(BUNDLE), remote(state, "/workspace/upload/gtmflow-phase8-bundle.tar.gz"))
    scp(state, str(POD_JOB), remote(state, "/workspace/upload/pod_job.sh"))
    remote_sha = ssh(state, "sha256sum /workspace/upload/gtmflow-phase8-bundle.tar.gz").stdout.split()[0]
    if remote_sha != state["bundle"]["sha256"]:
        raise RuntimeError(f"bundle checksum on pod {remote_sha} != local {state['bundle']['sha256']}")
    event("bundle_transferred", sha256=remote_sha)
    train_deadline = state["hard_limit_at"] - COPY_BACK_RESERVE_SECONDS
    state["train_deadline_unix"] = train_deadline
    save_state(state)
    args = " ".join(shlex.quote(a) for a in (state["bundle"]["sha256"], str(int(train_deadline)), str(GRACE_SECONDS)))
    ssh(state, f"setsid nohup bash /workspace/upload/pod_job.sh {args} >/dev/null 2>&1 < /dev/null & echo started")
    event("job_started", train_deadline=iso(train_deadline))


def poll(state: dict) -> str:
    failures, last_line = 0, None
    while True:
        if now() > state["hard_limit_at"] + 300:
            return "hard_limit_passed"
        try:
            out = ssh(state, "cat /workspace/job/status 2>/dev/null; echo ---; "
                             "grep -E 'STATUS|\"type\": \"(step|epoch|preflight|early_stop|deadline_stop|baseline_validation)\"|ERROR' "
                             "/workspace/job/job.log | tail -n 1", timeout=90, check=False)
            if out.returncode != 0:
                raise RuntimeError(out.stderr.strip()[-200:])
            failures = 0
            status, _, tail = out.stdout.partition("---")
            status, tail = status.strip(), tail.strip()
            if tail and tail != last_line:
                print(f"[{iso()}] {status} | {tail[:300]}", flush=True)
                last_line = tail
            if status == "done" or status.startswith("failed"):
                event("job_finished", status=status)
                return status
        except (RuntimeError, subprocess.TimeoutExpired) as err:
            failures += 1
            event("poll_error", consecutive=failures, error=str(err)[:200])
            if get_pod(state["pod_id"]) is None:
                return "pod_gone"
        time.sleep(60)


def fetch_and_verify(state: dict, status: str) -> dict:
    local_dir = TRAINING / "runs" / RUN_NAME
    local_dir.mkdir(parents=True, exist_ok=True)
    if status != "done":
        # Keep the evidence of a failed run: job log, status, whatever the run wrote.
        ssh(state, "cd /workspace && tar -czf /workspace/failed-run.tar.gz job "
                   f"$(test -d gtmflow/training/runs/{RUN_NAME} && echo gtmflow/training/runs/{RUN_NAME}) "
                   "/watchdog.log 2>/dev/null; true", timeout=600, check=False)
        scp(state, remote(state, "/workspace/failed-run.tar.gz"), str(local_dir / "failed-run.tar.gz"))
        event("failed_run_evidence_saved", path=str(local_dir / "failed-run.tar.gz"))
        return {"verified": False, "status": status}
    remote_sha = ssh(state, "cat /workspace/phase8-run-v1.tar.gz.sha256").stdout.strip()
    archive = local_dir / "phase8-run-v1.tar.gz"
    scp(state, remote(state, "/workspace/phase8-run-v1.tar.gz"), str(archive))
    result = verify_archive(archive, remote_sha, LOCAL_RUN)
    result["status"] = status
    event("artifacts_verified", **result)
    if not result["verified"]:
        raise RuntimeError(f"artifact verification failed: {result}")
    return result


def final_accounting(state: dict) -> dict:
    ended = state.get("terminated_at") or now()
    hours = (ended - state["created_at"]) / 3600
    rate = float(state.get("cost_per_hr") or 0)
    disk = CONTAINER_DISK_GB * DISK_USD_PER_GB_MONTH / 730 * hours
    ours = [p for p in list_pods() if str(p.get("name", "")).startswith(POD_NAME_PREFIX)]
    report = {"pod_id": state["pod_id"], "gpu": state.get("gpu"), "cost_per_hr": rate,
              "created_at": iso(state["created_at"]), "terminated_at": iso(ended),
              "billed_hours_estimate": round(hours, 3), "estimated_usd": round(rate * hours + disk, 3),
              "balance_before": state.get("balance_before"), "balance_after": balance(),
              "remaining_gtmflow_pods": [p.get("id") for p in ours],
              "network_volumes_created": 0, "volume_disk_gb": 0}
    (TRAINING / "runs" / RUN_NAME / "runpod-report.json").write_text(json.dumps(report, indent=2))
    event("accounting", **report)
    return report


def verify_archive(archive: Path, expected_sha: str, dest: Path) -> dict:
    """Checksum the copied archive, extract it, and check every file against
    the pod-side SHA256SUMS, the adapter/tokenizer hashes recorded in the run
    manifest, and the config against the repository's pinned config."""
    local_sha = sha256(archive)
    if local_sha != expected_sha:
        raise RuntimeError(f"archive checksum mismatch: local {local_sha} expected {expected_sha}")
    if dest.exists():
        raise RuntimeError(f"{dest} already exists; refusing to overwrite")
    dest.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(dest, filter="data")
    sums = (dest / "SHA256SUMS").read_text().splitlines()
    mismatches = [name for digest, name in (line.split(maxsplit=1) for line in sums)
                  if sha256(dest / name.removeprefix("./")) != digest]
    manifest = json.loads((dest / "run-manifest.json").read_text())
    adapter = dest / "best_adapter"
    weights = adapter / "adapter_model.safetensors"
    recorded = manifest.get("artifact_sha256") or {}
    adapter_ok = weights.exists() and "adapter_model.safetensors" in recorded and all(
        sha256(adapter / n) == d for n, d in recorded.items())
    raw = weights.read_bytes() if weights.exists() else b"\0" * 8
    header = json.loads(raw[8:8 + int.from_bytes(raw[:8], "little")] or b"{}")
    tok = manifest.get("tokenizer") or {}
    tok_ok = bool(tok.get("saved_files_sha256")) and all(
        sha256(dest / "tokenizer" / n) == d for n, d in tok["saved_files_sha256"].items())
    config_ok = sha256(dest / "config.json") == manifest["config_sha256"] == sha256(
        TRAINING / "configs" / f"{RUN_NAME}.json")
    return {"verified": not mismatches and adapter_ok and tok_ok and config_ok, "archive_sha256": local_sha,
            "files_checked": len(sums), "mismatches": mismatches, "adapter_ok": adapter_ok,
            "adapter_tensors": len([k for k in header if k != "__metadata__"]),
            "tokenizer_ok": tok_ok, "config_matches_repo": config_ok, "local_path": str(dest)}


# -------------------------------------------------------------- commands

def cmd_check(args) -> int:
    bundle = check_bundle(args.bundle_sha256)
    print(json.dumps({"bundle": bundle}, indent=2))
    pods = list_pods()
    print(json.dumps({"api": "ok", "existing_pods": [{k: p.get(k) for k in ("id", "name", "desiredStatus", "costPerHr")}
                                                     for p in pods], "balance": balance(),
                      "ssh_key": SSH_KEY.exists(), "state": load_state().get("pod_id")}, indent=2))
    return 0


def cmd_run(args) -> int:
    if not args.confirm_paid_compute:
        print("refused: needs --confirm-paid-compute", file=sys.stderr)
        return 2
    if load_state().get("pod_id") and not load_state().get("finished"):
        print(f"refused: an unfinished run exists in {STATE}; use resume or terminate", file=sys.stderr)
        return 2
    if LOCAL_RUN.exists():
        print(f"refused: {LOCAL_RUN} already exists (one run only)", file=sys.stderr)
        return 2
    bundle = check_bundle(args.bundle_sha256)
    if any(str(p.get("name", "")).startswith(POD_NAME_PREFIX) for p in list_pods()):
        print("refused: a gtmflow-phase8 pod already exists", file=sys.stderr)
        return 2
    state = {"bundle": bundle, "balance_before": balance(), "started_at": now()}
    save_state(state)
    event("creating_pod", gpu_types=GPU_TYPES + (FALLBACK_GPU_TYPES if args.allow_rtx4090 else []),
          hard_limit_hours=HARD_LIMIT_SECONDS / 3600, max_cost_per_hr=MAX_COST_PER_HR)
    pod = create_pod(args.allow_rtx4090)
    state.update(pod_id=pod["id"], created_at=now(), cost_per_hr=pod.get("costPerHr"))
    state["hard_limit_at"] = state["created_at"] + HARD_LIMIT_SECONDS
    state["local_watchdog_pid"] = start_local_watchdog(pod["id"], state["hard_limit_at"] + 120)
    save_state(state)
    event("pod_created", pod=pod["id"], cost_per_hr=pod.get("costPerHr"), hard_limit=iso(state["hard_limit_at"]))
    return drive(state, fresh=True)


def drive(state: dict, fresh: bool) -> int:
    pod_id, outcome = state["pod_id"], {"verified": False}
    try:
        if fresh:
            rate = float(state.get("cost_per_hr") or 0)
            if not 0 < rate <= MAX_COST_PER_HR:
                raise RuntimeError(f"pod price ${rate}/hr outside the allowed (0, {MAX_COST_PER_HR}]")
            wait_for_ssh(state)
            if float(state.get("cost_per_hr") or 0) > MAX_COST_PER_HR:
                raise RuntimeError(f"pod price ${state['cost_per_hr']}/hr above cap")
            state["safeguards"] = verify_safeguards(state)
            save_state(state)
            upload_and_start(state)
        status = poll(state)
        state["job_status"] = status
        save_state(state)
        if status not in ("pod_gone", "hard_limit_passed"):
            outcome = fetch_and_verify(state, status)
        if outcome.get("verified"):
            state["self_delete_proven"] = prove_self_delete(state)
            save_state(state)
    except Exception as err:  # noqa: BLE001 -- always terminate below
        event("error", error=f"{type(err).__name__}: {err}"[:600])
        outcome = {"verified": False, "error": str(err)[:600]}
    finally:
        gone = terminate(pod_id, "run finished" if outcome.get("verified") else "run ended without verified artifacts")
        state.update(terminated_at=now(), removal_confirmed=gone, finished=True, outcome=outcome)
        save_state(state)
        try:
            os.kill(state.get("local_watchdog_pid", 0), 15)
        except (OSError, TypeError):
            pass
        final_accounting(state)
    return 0 if outcome.get("verified") and state["removal_confirmed"] else 1


def cmd_resume(args) -> int:
    state = load_state()
    if not state.get("pod_id") or state.get("finished"):
        print("nothing to resume", file=sys.stderr)
        return 2
    if get_pod(state["pod_id"]) is None:
        state.update(terminated_at=state.get("terminated_at") or now(), removal_confirmed=True, finished=True)
        save_state(state)
        final_accounting(state)
        print("pod no longer exists (self-terminated); see events.jsonl", file=sys.stderr)
        return 1
    return drive(state, fresh=False)


def cmd_terminate(args) -> int:
    state = load_state()
    pod_id = args.pod or state.get("pod_id")
    if not pod_id:
        print("no pod id", file=sys.stderr)
        return 2
    gone = terminate(pod_id, "manual")
    if state.get("pod_id") == pod_id:
        state.update(terminated_at=state.get("terminated_at") or now(), removal_confirmed=gone, finished=True)
        save_state(state)
        final_accounting(state)
    return 0 if gone else 1


def cmd_watchdog(args) -> int:
    while now() < args.at:
        time.sleep(min(60, max(1, args.at - now())))
        try:
            if get_pod(args.pod) is None:
                return 0
        except ApiError:
            pass
    return 0 if terminate(args.pod, "workspace watchdog: hard limit") else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--bundle-sha256")
    r = sub.add_parser("run")
    r.add_argument("--bundle-sha256", required=True)
    r.add_argument("--confirm-paid-compute", action="store_true")
    r.add_argument("--allow-rtx4090", action="store_true")
    sub.add_parser("resume")
    t = sub.add_parser("terminate")
    t.add_argument("--pod")
    w = sub.add_parser("watchdog")
    w.add_argument("--pod", required=True)
    w.add_argument("--at", type=float, required=True)
    args = parser.parse_args()
    return {"check": cmd_check, "run": cmd_run, "resume": cmd_resume,
            "terminate": cmd_terminate, "watchdog": cmd_watchdog}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
