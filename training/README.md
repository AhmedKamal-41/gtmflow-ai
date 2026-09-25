# GTMFlow Phase 8: LoRA fine-tuning of the grounded generator

Status: **code ready. Not trained.** No paid compute has been used. The real run needs your approval (see "Recommended run").

## What it trains

**Base model:** `Qwen/Qwen3-4B-Instruct-2507`, pinned at revision `cdbee75f17c01a7cc42f958dc650907174af0554`.

- License: Apache-2.0; ungated.
- It is a non-thinking instruct model, so it answers directly in JSON.
- Architecture: 36 layers, hidden size 2,560, about 4.0B parameters, bf16.

**Method:** a bf16 LoRA adapter.

- r = 16, alpha = 32, dropout 0.05.
- Applied to the q, k, v, o, gate, up and down projections.
- No quantization, which keeps the numerics simple and reproducible.

**Task:** the production grounded generator (`grounded-v2`). Each training example is formatted with the backend's own prompt builders and system message (`backend/app/ai/prompts.py`, loaded by file path so no backend settings or keys are loaded). The model therefore sees exactly the input the real client sends.

- **Target:** the reviewed JSON, with sorted keys.
- **Loss:** on the answer tokens only, including the end-of-turn token.
- **Length:** no truncation; an over-length example is an error.

## Data (pinned by sha256; the runner verifies before loading)

| Role | Dataset | Examples | Tokens | Use |
|---|---|---|---|---|
| Fitting | `backend/data/datasets/train-combined-v1/eligible.jsonl` (`a69ea054…`) | 419 (weight sum 397.906) | 881,605 per epoch; max 2,285 per example | Training, **example-weighted** |
| Tuning | `backend/data/datasets/validation-v1/eligible.jsonl` (`8306c8d0…`) | 74 | 156,829 | Per-epoch validation loss, best-checkpoint selection, early stopping |
| Final evaluation | `test-v1` | 71 | — | **Never loaded here.** The runner refuses any test split; the bundle does not contain it. Reserved for Phase 9. |

**Example weights** (`structure-cap-v1`, from Phase 7) are applied in the loss. The objective is

    Σ wᵢ·lᵢ / Σ wᵢ

where lᵢ is each example's mean answer-token cross-entropy. Each micro-batch holds one example scaled by wᵢ / mean(w); gradient accumulation averages them. The unit tests and the smoke run verify this. Validation loss is unweighted.

Uncertain-flagged examples are refused, and so are datasets from another prompt version.

## Reproducibility

- **Pinned:**
  - the model revision;
  - the dataset hashes, in `configs/phase8-qwen3-4b-lora-v1.json`;
  - the dependencies: `requirements-lock-cpu.txt` is the exact local environment, and `requirements-gpu.txt` is identical except for `torch==2.14.0+cu126`;
  - the seed: `20260925`, applied to Python and torch, the per-epoch data order and the tiny-model initialization.
- **Deterministic algorithms** are requested. CPU smoke runs reproduce byte-identically. GPU kernels may still differ slightly between GPU types; the run manifest records the GPU.
- **Every run writes** `run-manifest.json`:
  - config and data hashes, library versions, git commit and dirty flag, hardware and command;
  - per-step and per-epoch metrics and wall time;
  - `log.jsonl` (the step log) and `best_adapter/`.

## Commands (run from `training/`)

```bash
# Local environment (Python 3.12; uv is used because python3.12-venv lacks ensurepip here)
uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install -r requirements-lock-cpu.txt --extra-index-url https://download.pytorch.org/whl/cpu

.venv/bin/python -m pytest -q                                                     # tests (CPU, free)
.venv/bin/python -m gtmflow_training.train plan  --config configs/phase8-qwen3-4b-lora-v1.json   # token counts and steps; no weights
.venv/bin/python -m gtmflow_training.train smoke --config configs/phase8-qwen3-4b-lora-v1.json   # tiny random model on CPU, full pipeline
```

`train` refuses to start unless all of these hold:

- `--confirm-paid-compute` is passed;
- a CUDA GPU is present;
- the GPU is bf16-capable with at least 20 GiB of memory.

## Recommended run (needs your approval: paid compute)

**Hardware:** 1× NVIDIA **L4 24 GB** (bf16), 8+ vCPU, 32+ GB RAM, 40+ GB disk.

- Estimated peak GPU memory is about 12–16 GB: 8 GB of bf16 weights, plus checkpointed activations for sequences of about 2.3k tokens, plus answer-position-only logits.

**Schedule:**

- 3 epochs × 53 optimizer steps (419 examples, gradient accumulation 8), so at most 159 steps;
- LR 1e-4, 5% warmup then cosine;
- validation after every epoch;
- the best epoch's adapter is kept, with early stopping after one non-improving epoch.

**Expected duration:** about 25–40 minutes of GPU compute, and about 35–55 minutes wall-clock including setup.

- This is an estimate, not a measurement. The workload is about 7×10¹⁶ FLOPs, from 2.8M training tokens including 3 epochs, frozen-base backward and checkpoint recompute, plus 4 validation passes.
- The L4's peak is 121 dense bf16 TFLOPS; the estimate assumes 25–45% utilization.
- Setup covers the dependency install, the 8 GB model download, tests and plan.

**Estimated cost:** at RunPod Secure Cloud's L4 price of **$0.49/hr** (verified 2026-09-25; Community is $0.44/hr), about **$0.30–$0.50**. Disk cost for about an hour is negligible.

- Suggested approval ceiling: **$3.00**. The launch command below also hard-stops after 4 hours, which bounds compute at about $2 even if throughput is far below the estimate.
- Alternative: one A100 80 GB at $1.59/hr, about 15–25 minutes, about $0.40–$0.70.

**Spending safeguard.** A shell `timeout` does not stop RunPod billing, so the pod removes itself. `scripts/runpod/launch.py` sets up five layers:

1. **Hard lifetime.** Armed by the pod's container start command at boot, independent of SSH. The pod removes itself 3.25 h after first boot, and the clock survives container restarts.
2. **Claim check.** If the job has not started 30 min after boot, the pod removes itself.
3. **Post-job grace.** When the job ends, whether it succeeded or failed, the pod removes itself 60 min later unless the orchestrator already has.
4. **Workspace watchdog.** A detached process in this workspace removes the pod through the API at the hard limit plus 2 min.
5. **Orchestrator.** It removes the pod as soon as the artifacts are verified, or on any error, and then confirms through the API that the pod is gone.

The price is capped at $0.80/hr, so the worst case is 3.25 h × $0.80 + disk ≈ $2.62 (under $3). No volume disk or network volume is created. Before any data is sent, the orchestrator checks over SSH that the pod-side watchdog is armed and that the pod's own API key works; otherwise it removes the pod.

**Steps** (from the repository root):

```bash
training/scripts/make_bundle.sh                                  # after committing; prints the bundle sha256
python3 training/scripts/runpod/launch.py check --bundle-sha256 <sha>                          # free: API key, balance, bundle allowlist
python3 training/scripts/runpod/launch.py run   --bundle-sha256 <sha> --confirm-paid-compute   # the paid run
python3 training/scripts/runpod/launch.py resume      # re-attach if this workspace disconnected mid-run
python3 training/scripts/runpod/launch.py terminate   # emergency stop
```

**What `run` does:**

1. Creates one Secure Cloud **NVIDIA L4** pod:
   - image `runpod/base:1.3.2-ubuntu2204`;
   - 50 GB container disk, no volume disk;
   - SSH on 22/tcp with a local key (only the public half is sent).
2. Verifies the safeguards and uploads the bundle and `pod_job.sh` (sha256 checked on both ends).
3. Starts the job detached. On the pod, `scripts/runpod/pod_job.sh`:
   - verifies the bundle checksum and allowlist (no test data, secrets or database);
   - checks that the driver supports CUDA 12.6 or later;
   - installs `requirements-gpu-lock.txt` with `--require-hashes` and checks every installed version against the lock;
   - runs `plan`, then the tests;
   - runs `preflight`, a real-model memory and throughput check, gated on peak reserved memory ≤ 90% of the GPU and a projected time that fits;
   - runs `train` with a deadline 40 min before the hard limit;
   - packages the run with `SHA256SUMS`.
4. Copies the archive back to `training/runs/phase8-qwen3-4b-lora-v1/`.
5. Verifies the copy:
   - the archive checksum and every file's hash;
   - the adapter and tokenizer hashes recorded in the manifest;
   - that the config matches the repository.
6. Removes the pod, confirms the removal, and writes `runpod-report.json` with the runtime, price and estimated cost.

The API key is read from `~/.config/gtmflow/runpod_api_key` (mode 600) or `$RUNPOD_API_KEY`. It is never printed.

After the run, the status can move to "trained". Evaluating the adapter on the test set is Phase 9 work.
