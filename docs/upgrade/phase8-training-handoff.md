# Phase 8 handoff: LoRA training (preparation)

Date: 2026-09-25. **Status: code ready. Not trained.** No paid compute has been used. The work stopped before any training run.

Per the readiness ladder: the runner is **code ready**, verified with tests and CPU smoke runs on a tiny random model. Once you have reviewed the approach, it moves to **needs compute**. It becomes **trained** only after a real run on the pinned data produces an adapter, with the run's command, machine, wall time and artifact location recorded.

## 1. Starting point

- **Phase 7 is closed** (handoff §28) under your revised scope: 419 training, 74 validation and 71 test eligible examples, AI-reviewed references and human validation deferred. All closure integrity checks passed.
- **The existing Phase 8 plan** (`decisions.md`, "Dependencies later phases will need") called for a LoRA/PEFT stack and a base-model choice, with no GPU run before explicit approval. **No base model had been documented.** It is chosen and recorded here.
- **Hardware in this Codespace:** 2 vCPU (AMD EPYC 7763), 7.8 GB RAM, **no GPU**, about 13 GB of free disk.
  - That covers setup, tests and CPU smoke runs only.
  - The real run needs a rented GPU (§5).

## 2. What was built (`training/`)

| Item | Where |
|---|---|
| Runner: `plan` (token statistics, no weights), `smoke` (tiny random model on CPU), `train` (real run; refuses without `--confirm-paid-compute`, CUDA and a bf16 GPU with at least 20 GiB) | `training/gtmflow_training/train.py` |
| Data loading with guards and production-identical formatting | `training/gtmflow_training/data.py` |
| Example-weighted loss | `training/gtmflow_training/weights.py` |
| Pinned run configuration | `training/configs/phase8-qwen3-4b-lora-v1.json` |
| Pinned dependencies | `training/requirements-lock-cpu.txt` (the exact local environment); `training/requirements-gpu.txt` (identical except `torch==2.14.0+cu126`) |
| Run bundle: committed code plus the train and validation datasets only (no test data); records its commit | `training/scripts/make_bundle.sh` |
| Tests (8) | `training/tests/test_training.py` |
| CI (CPU only; never trains the real model) | `.github/workflows/training.yml` |
| How to reproduce and launch | `training/README.md` |

**Backend change.** The real client's system message moved into a shared constant, `app.ai.prompts.JSON_SYSTEM_MESSAGE`. The text is identical, so behavior is unchanged. Training uses the same constant and prompt builders.

- Training loads `prompts.py` by file path.
- A test asserts that no backend settings, `.env` or API keys are loaded.

## 3. Training design

**Base model:** `Qwen/Qwen3-4B-Instruct-2507`, revision `cdbee75f17c01a7cc42f958dc650907174af0554`.

- License: Apache-2.0; ungated (verified on the Hugging Face API).
- A non-thinking instruct model, so it answers in JSON directly.
- About 4.0B parameters, bf16.

**Method:** bf16 LoRA.

- r = 16, alpha = 32, dropout 0.05, on q, k, v, o, gate, up and down.
- That is about 33M trainable parameters, and an adapter of about 130 MB.
- No quantization.
- Gradient checkpointing is on.
- Logits are computed only at the answer positions, which keeps memory flat for the 152k-token vocabulary.

**Hyperparameters:**

- 3 epochs, LR 1e-4, 5% warmup then cosine;
- 1 example per micro-batch with gradient accumulation 8, which gives 53 steps per epoch;
- AdamW (betas 0.9 / 0.999, no weight decay);
- gradient clipping at 1.0.

**Data roles:**

- **Fitting:** `train-combined-v1` (train split only, 419 examples).
- **Tuning:** `validation-v1` (74 examples), used for per-epoch loss, best-epoch adapter selection and early stopping (patience 0).
- **Final evaluation:** `test-v1` is **never loaded**. The data guard refuses any test split, the config may not name one, and the bundle does not contain it. It is reserved for Phase 9.

**Guards:**

- every dataset file must match the sha256 pinned in the config;
- the training set must be an all-train dataset with `allowed_use: training`;
- the validation set must be all-validation;
- uncertain-flagged examples are refused;
- the prompt version must be `grounded-v2`;
- every training weight must lie in (0, 1].

**Formatting:** the backend's `grounded-v2` prompt builders plus the shared system message.

- The target is the reviewed JSON with sorted keys.
- Loss is on the answer tokens only, including the end-of-turn token.
- Nothing is truncated.
- Verified on a real example: the answer tokens decode back exactly to the reviewed target.

**Example weights are actually applied.**

- The objective is Σ wᵢ·lᵢ / Σ wᵢ. Each example's loss is scaled by wᵢ / mean(w).
- Unit tests check the objective equality and the gradient scaling.
- The smoke run's manifest records the weighting: normalizer 0.578 over 6 examples, 3 of them below weight 1. Its per-step losses scale accordingly (for example 20.55 vs 3.20).
- Validation loss is unweighted.
- The 397.9 total weight is descriptive only.

## 4. Reproducibility

- **Pinned:**
  - the model revision;
  - the dataset hashes: training `a69ea054…` and manifest `2912ca48…`; validation `8306c8d0…` and manifest `23b96bb3…`;
  - the dependency versions: torch 2.14.0, transformers 5.17.0, peft 0.21.0, accelerate 1.15.0, safetensors 0.8.0, tokenizers 0.23.2, huggingface-hub 1.33.0, Python 3.12;
  - the seed: `20260925`, used for Python and torch, the per-epoch shuffle and the tiny-model initialization.
- **Deterministic algorithms** are requested. Two identical CPU smoke runs produced byte-identical logs, and so did the checkpointing and non-checkpointing variants. GPU kernels may still differ slightly across GPU types; the GPU is recorded.
- **Each run writes** `run-manifest.json`:
  - the config and its sha256, and the data hashes;
  - library versions;
  - the git commit, or the bundle commit when run from a bundle;
  - hardware, command, per-step and per-epoch metrics, and wall time;
  - `log.jsonl` and `best_adapter/`.

## 5. Recommended training run (awaiting your approval)

| | |
|---|---|
| Run | `phase8-qwen3-4b-lora-v1` (config above) |
| Hardware | 1× **NVIDIA L4 24 GB** (bf16), 8+ vCPU, 32+ GB RAM, 40+ GB disk. Estimated peak GPU memory: about 12–16 GB. |
| Workload (measured tokens) | 881,605 training tokens per epoch (max 2,285 per example); 156,829 validation tokens per evaluation; at most 159 optimizer steps |
| **Expected duration** (estimate, not measured) | About 25–40 minutes of GPU compute, and about 35–55 minutes wall-clock including setup (dependency install, 8 GB model download, tests and plan). Basis: about 7×10¹⁶ FLOPs at 25–45% of the L4's 121 dense bf16 TFLOPS. |
| **Estimated cost** | RunPod Secure Cloud L4 at **$0.49/hr** (verified 2026-09-25) gives **about $0.30–$0.50**. Disk cost is negligible. |
| Suggested approval ceiling | **$3.00.** The launch command's `timeout 4h` bounds compute at about $2. |
| Alternative | 1× A100 80 GB at $1.59/hr: about 15–25 minutes, about $0.40–$0.70 |

**Exact launch command.** Run it on the GPU machine, from the extracted bundle's `training/`, after setup:

```bash
timeout 4h .venv/bin/python -m gtmflow_training.train train --config configs/phase8-qwen3-4b-lora-v1.json --confirm-paid-compute
```

`training/README.md` gives the full sequence: build the bundle, copy it, install `requirements-gpu.txt`, run `plan`, run the tests, run `train`, copy the run back, then terminate the machine.

**Bundle verified locally:** `make_bundle.sh` at commit `a4578f2…` produced 364 KB. Extracted into a fresh directory, `plan`, `smoke` and the 8 tests all passed (exit 0), and the manifest recorded the bundle commit. The bundle must be rebuilt at launch time from the then-current commit; the script refuses uncommitted `training/` changes.

## 6. Verification run (2026-09-25; all local and free)

See `phase-status.md` for the final gate exit codes.

| Check | Result | Exit code |
|---|---|---|
| Training tests (`pytest`, Python 3.12, CPU) | 8 passed | 0 |
| `plan` on the real pinned data with the real pinned tokenizer (offline) | 419 / 74 examples, weight sum 397.906, 53 steps per epoch, no example over 4,096 tokens | 0 |
| `smoke` (tiny random Qwen3 model, LoRA, gradient checkpointing, weighting, validation selection) | Validation loss 11.942 → 11.841 → 11.826; best-epoch adapter saved; about 20 s | 0 |
| `smoke` run twice with the same seed | Byte-identical logs | 0 / 0 |
| `train` without confirmation, and without a GPU | Refused | 2 / 2 |
| Bundle build, then plan, smoke and tests from the extracted bundle | Passed | 0 |
| Backend suite after the system-message refactor (SQLite) | 430 passed | 0 |

The smoke runs used the real data format but a **random 4.9M-parameter model**. They prove the pipeline, not model quality, and are not training results.

## 7. What is ready, and the remaining blockers

**Ready:** the pinned configuration, runner, guards, weighting, reproducibility records, dependency pins, bundle, tests, CI workflow and launch instructions.

**Blockers before training:**

1. **Your approval of the concrete training budget** (suggested ceiling $3.00) and of this approach (model, method, hyperparameters). Contract rule 7 requires sign-off before any paid GPU job. Approval of the approach also moves the status from code ready to needs compute.
2. **A GPU machine.** I cannot create cloud accounts or machines. You would create one (for example a RunPod L4 Secure Cloud pod), or give me access to one.
3. **Data transfer confirmation.** The bundle carries the PDL-derived training and validation records (CC-BY-4.0 source data) to the GPU provider. The test data is not included.

**Not in scope (Phase 9):** evaluating the trained adapter on the test set, and comparing it with the gpt-4o-mini baseline under `heldout-criteria-v1`.
