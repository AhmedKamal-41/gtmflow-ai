# Phase 8 handoff: LoRA training (preparation)

Date: 2026-09-25. **Status: trained (v1 adapter). Not evaluated.** One real training run completed on 2026-09-25 (§10). Evaluation on the test set is Phase 9 and has not started.

> **Update 2026-09-26:** the v1 adapter has been evaluated in Phase 9 (AI-evaluated: automated metrics and blind AI review) and is the recommended system; see `phase9-evaluation-handoff.md` §11–§14. Pod self-deletion with the pod-scoped key is now proven. The project moved to a new Codespace. The run records below keep their original `/workspaces/gtmflow-phase5` paths, and the artifacts now live at the same relative paths under `/workspaces/gtmflow-ai`.

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

## 8. Run authorization and launch preparation (2026-09-25)

**Authorized by you:**

- one run of `phase8-qwen3-4b-lora-v1` with the pinned config;
- **at most $3.00 total RunPod usage**, covering setup, GPU preflight, training and storage;
- transfer of the train/validation bundle to your private RunPod instance. No test data, application secrets or database may be included.

Also required: no additional experiments, no automatic model changes, and a stop before Phase 9 and before application integration.

**Prepared and verified before renting a GPU** (all local, $0; commit `ef60df3`):

| Item | Evidence |
|---|---|
| **Spending safeguard.** A shell `timeout` does not stop billing, so the pod removes itself. There are five layers: (1) a hard lifetime of 3.25 h from first boot, armed in the container start command so it works without any SSH session and survives restarts; (2) removal if the job has not started 30 min after boot; (3) removal 60 min after the job ends, whether it succeeded or failed; (4) a workspace-side watchdog that removes the pod through the API; (5) the orchestrator removes the pod after verified copy-back or on any error, then confirms through the API. | Local simulation with a stubbed `runpodctl`: the claim check fired at 3 s; after a simulated restart the lifetime still counted from first boot and fired on time; a failed job recorded `failed:verify` and removed the pod after its grace period. |
| **Budget arithmetic.** The price is capped at $0.80/hr (L4 Secure is $0.49/hr). The worst case is 3.25 h × $0.80 + 50 GB container disk (about $0.02) ≈ **$2.62**, under $3. There is no volume disk and no network volume. At the actual L4 price the worst case is about $1.62. | `launch.py` constants: `MAX_COST_PER_HR`, `HARD_LIMIT_SECONDS` |
| **Safety check before transfer.** The orchestrator checks over SSH that the watchdog is armed and that the pod's own API key reaches the RunPod API. If either check fails, it removes the pod before sending any data. | `launch.py verify_safeguards` |
| **Bundle** `30c77a9a…73c0` (commit `ef60df3`, 402,670 bytes, 33 members). The only data files are the train and validation eligible sets and their manifests. | The allowlist is checked in the workspace and again on the pod. A tampered bundle containing a `test-v1` file was refused on both sides, before extraction. The bundle was re-verified from an extracted copy: plan exit 0, 10 tests passed. |
| **Pinned GPU environment.** `requirements-gpu-lock.txt` is the full resolution for Linux x86_64 with Python 3.12 and CUDA 12.6: 62 packages, including the `nvidia-*` libraries and triton, all hashed. The pod installs with `--require-hashes` and compares `pip freeze` against the lock. It refuses a driver older than CUDA 12.6. | `uv pip compile` exit 0 |
| **GPU preflight** (`train preflight`). Loads the real pinned model and adds LoRA. Runs forward and backward on the longest training example (2,285 tokens) and a forward pass on the longest validation example. Records peak allocated and reserved GPU memory and a projected run time. There is no optimizer step and no adapter is saved. **Gate:** peak reserved memory ≤ 90% of the GPU, and 1.3 × the projected time fits before the training deadline. | CPU stand-in test: 10 tests passed |
| **Run records** added to the manifest: per-epoch training loss (weighted objective and unweighted mean), per-epoch seconds, peak GPU memory, epochs completed, tokenizer files with hashes and the chat-template hash, a copy of the config, and adapter file hashes. The pod also records `nvidia-smi` memory samples every 5 s, `pip freeze`, the job log and the watchdog log. | smoke and tests |
| **Deadline-aware stop.** Training does not start an epoch that cannot finish, plus a 10-minute reserve, before the deadline. The deadline is 40 min before the hard limit, which leaves time for copy-back. The best adapter so far is kept. | test: the deadline stops the run after one epoch and keeps the adapter |
| **Copy-back verification.** The orchestrator checks the archive sha256 against the pod's value, checks every file against the pod-side `SHA256SUMS`, checks the adapter and tokenizer hashes against the manifest, and checks that the config sha matches the repository config. | On a packaged local smoke run: clean copy verified (10 files, 28 adapter tensors). A single flipped adapter byte and a wrong archive sha were both caught. |

**Remaining blocker: RunPod access.** This workspace has no RunPod API key. `launch.py` needs one to create the pod, confirm its removal and read the balance.

- **Your one-time setup:**
  1. Create a RunPod account and buy credit.
  2. Create an API key with read/write permission.
  3. Store the key in `~/.config/gtmflow/runpod_api_key` (mode 600) from a separate terminal, never in chat.
- **The credit purchase is a prepaid deposit, not the run's cost.** The minimum purchase is reportedly $10; confirm it on RunPod's Billing page. RunPod credit is non-refundable. The run itself is expected to use about $0.30–$0.50 and cannot exceed about $2.62 under the safeguards.
- **What the script creates:**

  | Setting | Value |
  |---|---|
  | Cloud and GPU | Secure Cloud, on-demand (not spot), 1× NVIDIA L4 |
  | Image | `runpod/base:1.3.2-ubuntu2204` |
  | Resources | ≥8 vCPU, ≥30 GB RAM, 50 GB container disk, 0 GB volume disk |
  | Ports | 22/tcp with a public IP |
  | SSH | the public key of the workspace-local `~/.ssh/gtmflow_runpod_ed25519`, passed as `PUBLIC_KEY`; the private key never leaves the workspace |
  | Start command | the watchdog above, then `/start.sh` |

- **Launch, once the key is in place:**
  ```bash
  python3 training/scripts/runpod/launch.py check --bundle-sha256 30c77a9a49e751b1c5349e23e27f8491b2c6a059bbf09f2d2aa05ea2550b73c0
  python3 training/scripts/runpod/launch.py run --bundle-sha256 30c77a9a49e751b1c5349e23e27f8491b2c6a059bbf09f2d2aa05ea2550b73c0 --confirm-paid-compute
  ```

**Known residual risks:**

- Removing a pod from inside itself relies on RunPod's pod-scoped key. The pre-transfer check proves the key works for reads; whether it can remove the pod can only be proven by removing one. The workspace-side watchdog and the orchestrator cover this.
- If this Codespace sleeps during the run, the pod still stops itself: 60 min after the job ends, or at the hard limit. `launch.py resume` can re-attach and copy the artifacts back within that window.
- An L4 may not be available in Secure Cloud. A failed pod creation is not charged. Switching to an RTX 4090 needs your OK: its $0.74/hr fits under the price cap, but `--allow-rtx4090` is off by default.

## 9. Launch attempts (2026-09-25): stopped at the pre-transfer safeguard check

- **Access checks (free):** passed. The launcher reads `RUNPOD_API_KEY` from the git-ignored `backend/.env`. Only that line is parsed; the launcher refuses to read a tracked file and scrubs the key from logs; a test covers this. Initial balance: $10.00, no pods.
- **Cloudflare block:** the first API call was rejected with Cloudflare error 1010, which blocks the default Python-urllib User-Agent. Fixed with an explicit User-Agent.
- **Attempt 1** (pod `9ag8gyyd6ns8j2`, L4 Secure, $0.49/hr): the watchdog was armed at boot. The check over SSH could not see `RUNPOD_POD_ID`, because SSH sessions do not inherit the container environment. The launcher removed the pod before any data was transferred and confirmed the removal. Fix: the check and `pod_job.sh` load `RUNPOD_POD_ID` and the pod-scoped key from `/proc/1/environ`, without printing them.
- **Attempt 2** (pod `9al239dpb8kj7l`): the pod id matched, the pod-scoped key was present, `runpodctl` was installed and the watchdog was armed. Both self-access probes failed:
  - `runpodctl get pod`: legacy syntax;
  - a REST GET via curl with its default User-Agent.

  The pod was removed before transfer and the removal was confirmed.
  Fix (commit `4e02b94`, bundle `2ba83a36…`): probe and self-terminate with the current `runpodctl pod get` / `pod delete` syntax, then fall back to the legacy syntax, then to REST with an explicit User-Agent. The probe records each method's first error line, redacted on the pod. Tested locally with a stubbed CLI. **Not yet verified on a real pod.**
- **Spend so far:** RunPod balance $10.00 → $9.9888, which is **$0.0112** for both attempts. No pods remain, no volumes were created, and no data left the workspace. No training has run.
- **Blocked:** adding a paid one-pod diagnostic mode was denied by the Claude Code permission classifier ("Real-World Transactions"). Further paid attempts wait for your decision.
- **Attempt 3** (authorized single retry; pod `jizdlmae149op7`, L4 Secure, $0.49/hr, bundle `2ba83a36…`, created 03:00:30Z, removal confirmed 03:07:14Z): SSH was up after about 6 minutes; the watchdog was armed, the pod id matched and the pod-scoped key was present. All three self-access probes failed. Exact errors, with credentials hidden:
  - `runpodctl pod get`: `Error: unknown command "pod" for "runpodctl"`. The installed runpodctl is the older v1 CLI.
  - `runpodctl get pod`: `Runpod config file not found, please run 'runpodctl config' to create it Error: Unauthorized`.
  - REST `GET /v1/pods/{id}` using the pod key, with an explicit User-Agent: `curl: (22) The requested URL returned error: 403`.

  Following the pre-transfer rule, the launcher removed the pod before any transfer and confirmed the removal. **No further paid retry**, as you instructed.
- **Spend after 3 attempts:** balance $10.00 → **$9.9546**, which is **$0.0454** so far. The launcher's per-pod estimates add up to about $0.072; the balance may settle slightly lower. No pods, no volumes, no launcher or watchdog processes remain. No data left the workspace, and no training has run.
- **Open decision (yours).** The pod-scoped key did not authenticate for self-management through runpodctl v1 (no config file) or REST (403). Options, each needing one more paid attempt of about $0.06:
  - (a) inside the pod, run `runpodctl config --apiKey "$RUNPOD_API_KEY"` (never printed) before probing with the v1 `get pod` / `remove pod`;
  - (b) probe the GraphQL API with the pod key;
  - (c) give the pod a separate, restricted RunPod key used only for self-removal. That puts a credential on the GPU host, so it needs your explicit approval;
  - (d) accept only workspace-side removal: the workspace watchdog plus the launcher. That does not cover this Codespace sleeping.

## 10. Authorized run (attempt 4): training completed (2026-09-25)

**Launcher fix before the run** (commit `37e395e`, bundle `e8629ce6…`). RunPod pods ship the **v1** `runpodctl` (confirmed on the pod: `runpodctl 1.14.15-dac76ad`). Checked against its source and the official release binary, run locally against a mock API:

- It reads `RUNPOD_API_KEY` from the environment first, then `~/.runpod/config.toml` (`apikey`/`apiurl`).
- `get pod` queries the account-wide `myself { pods }`. The pod-scoped key is not allowed to do that, which was the cause of the "Unauthorized" error in attempt 3.
- `remove pod` sends the GraphQL `podTerminate` mutation.
- `runpodctl config` was deliberately **not** used: besides saving the file, it registers an SSH key on the account.

What the fixed launcher does:

- **Shared environment.** At boot, the container start command writes `/gtmflow-pod-env.sh`, which holds no secret: it reads the pod id and the pod-scoped key from the container's main process, writes the CLI config (mode 600), and defines the self-termination function `t` (`runpodctl remove pod`, else GraphQL `podTerminate`). The boot watchdog, the SSH checks and `pod_job.sh` all source the same file. Your account key never leaves the Codespace.
- **Pre-transfer check.** Requires a GraphQL read of the pod's own record **and** a `podTerminate` authorization probe on a nonexistent pod id, which must return `POD_NOT_FOUND`. For comparison: an authorized account key returns `POD_NOT_FOUND`; an invalid key returns HTTP 401. A read alone is refused (tested locally in both a positive and a negative case). The check reports delete permission as *inferred, not proven*.

**Run** (pod `kphhuk2ckurhv9`, 1× NVIDIA L4 24 GB, Secure Cloud, $0.49/hr, created 03:21:22Z):

- **Pre-transfer check: passed.** The watchdog was armed ("hard limit in 11700s"), the pod id matched, the CLI config matched the pod key with mode 600, and the GraphQL read of the pod's own record returned `RUNNING`. The CLI and GraphQL terminate probes both returned `pod not found to terminate` / `POD_NOT_FOUND`.
- **Pod setup:** bundle sha verified on the pod; driver CUDA ≥ 12.6; 59 hash-locked packages installed with zero mismatches (torch 2.14.0+cu126, transformers 5.17.0, peft 0.21.0, Python 3.12.13); `plan` passed.
- **Deviation: training was started by the test suite.** Two refusal tests called `train`/`preflight --confirm-paid-compute` and expected "no CUDA". On the GPU pod they instead ran the real pinned training, and afterwards the real preflight. Config, data, seed, example weights, validation-based selection and the output path were exactly as planned. Consequences:
  - (a) the GPU preflight gate did not run *before* training; it ran afterwards (see the memory row below);
  - (b) the training-deadline stop was not active (the pod hard limit still applied; training ended 2 h before it);
  - (c) the job ended as `failed:tests` (3 failed, 8 passed: the two tests above, plus the launcher test, which needs a git checkout);
  - (d) the orchestrator therefore took its failed-run path: it saved the evidence archive, skipped the end-of-run self-deletion proof, and removed the pod with the account key.

  The tests are fixed (commits `530882e` and the next commit): they force "no CUDA", and the git-dependent test skips outside a checkout.
- **Cleanup:** removal confirmed 04:27:50Z. No pods and no network volumes remain (checked through the API afterwards); current spend $0/hr.

**Results** (`run-manifest.json`):

| | |
|---|---|
| Steps / epochs | 159 of 159 steps; **3 of 3 epochs**; no early stop |
| Example weights | Applied: normalizer 0.9497; 25 examples below weight 1; weight sum 397.906 |
| Validation loss before training | 1.2628 (token-weighted 1.2016), 74 examples |
| Epoch 1 | training 0.3956 weighted / 0.4252 unweighted; **validation 0.2638**; 1,241 s |
| Epoch 2 | training 0.2073 / 0.2073; **validation 0.1392**; 1,243 s |
| Epoch 3 | training 0.1806 / 0.1775; **validation 0.1292** (token-weighted 0.1289); 1,243 s |
| **Selected checkpoint** | **Epoch 3** (best validation loss 0.1292); LR decayed to 0 |
| Trainable parameters | 33,030,144 of 4,055,498,240 |
| Peak GPU memory | torch: 9.27 GiB allocated, 12.49 GiB reserved of 22.03 GiB; `nvidia-smi` device peak 13,087 MiB of 23,034 MiB; mean GPU utilization 98% |
| Preflight (measured after training) | 3.08 s for the longest (2,285-token) training example; projected 63.1 min, actual 62.1 min |
| Training wall time | 3,805 s (03:22:33Z → 04:25:58Z) |
| Pod lifetime | 1 h 06 m 28 s (03:21:22Z → 04:27:50Z) |
| Provenance | bundle commit `37e395e` (clean); test data "not loaded (reserved for final evaluation)" |

These are **training and validation losses only**. They are not a quality evaluation. The held-out comparison under `heldout-criteria-v1` on test-v1 is Phase 9.

**Artifacts** (workspace, git-ignored, 138 MB): `training/runs/phase8-qwen3-4b-lora-v1/train/`

- `best_adapter/` (`adapter_model.safetensors`: 132 MB, 504 tensors; `adapter_config.json`)
- `config.json`
- `tokenizer/`, with its record in the manifest
- `run-manifest.json` and `log.jsonl` (159 step lines + epoch records)
- `pod/`: job log, `gpu-memory.csv`, `nvidia-smi.txt`, `pip-freeze.txt`, the lock, preflight, plan, watchdog log, RunPod report
- `SHA256SUMS` (21 files) and `verification.json`

The original evidence archive is `failed-run.tar.gz` (sha256 `c3b11898…`). **Verification:** adapter, tokenizer and config hashes all match the values recorded in the manifest at training time; the config also matches the repository config; the data hashes match the pinned config.

**Spending** (cumulative against the $3 cap; settled RunPod balance $10.00 → **$9.3869**):

| | USD |
|---|---|
| Attempts 1–3 (refused before transfer) | 0.0647 |
| Attempt 4 (this run) | 0.5484 |
| **Total** | **$0.6131** |

**Remaining for Phase 8:**

- Self-deletion with the pod key has still **not** been proven end to end, because the failed-run path skipped that step.
- The adapter is untested for generation quality.
- Evaluation and application integration belong to Phase 9 and later, and have not started.
