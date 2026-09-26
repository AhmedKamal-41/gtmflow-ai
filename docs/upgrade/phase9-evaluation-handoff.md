# Phase 9 handoff: held-out comparison (launch preparation)

Date: 2026-09-26. **Status: code ready; needs compute.** Nothing has been generated. No GPU has been rented for Phase 9, and no paid API call has been made.

Per the readiness ladder, the Phase 8 adapter is **trained, not evaluated**. It becomes **evaluated** only after this run's predictions are copied back, verified and scored.

## 1. Starting point

- **Phase 8 adapter:** `phase8-qwen3-4b-lora-v1`, epoch 3, selected on validation loss 0.129. It lives in `training/runs/phase8-qwen3-4b-lora-v1/train/`, and all 21 files match its `SHA256SUMS`. The two adapter files match the sha256 values pinned in `training/configs/phase9-eval-v1.json`.
- **Frozen held-out data:** `validation-v1` has 74 eligible and 26 flagged-uncertain examples; `test-v1` has 71 and 26. The criteria are `heldout-criteria-v1`, AI-evaluated.
- **Phase 9 code:** committed in `d694089`. It covers the base-vs-LoRA generation runner, failure-inclusive scoring (`phase9-report-v1`), the blind review protocol and the `phase9-eval` launcher profile.
- **Workspace:** this is a new Codespace, migrated from the one that held `/workspaces/gtmflow-phase5`. §9 lists the migration checks. The project's PostgreSQL database was **not** migrated (§8). Phase 9 does not need it: generation reads only the inputs-only files, and scoring reads only the dataset JSONL files.

## 2. What was done in this session (all local, $0)

| Item | Result |
|---|---|
| **Launcher fix** (commit `e68d25b`) | Before this fix, every profile shared one `training/runs/runpod/state.json`, and `run` only refused when that state was unfinished. A Phase 9 launch would therefore have **overwritten the Phase 8 run record**. Now each profile has its own state file: Phase 8 keeps `state.json`, and Phase 9 uses `state-phase9-eval-v1.json`. A new run is refused while *any* profile has an unfinished run. `resume` and `terminate` accept `--profile`, or pick the one unfinished run. A new test covers this; it fails on the previous launcher. |
| **SSH key** | `~/.ssh/gtmflow_runpod_ed25519` was created (ed25519, mode 600, fingerprint `SHA256:I5KB993m…`). The key from the old Codespace was not migrated. Only the public half is sent, as the pod's `PUBLIC_KEY`. |
| **Install instructions** | `training/README.md`: the `uv` install of the CPU lock now needs `--index-strategy unsafe-best-match`. Every package is an exact pin, and plain pip and CI already merge indexes this way. |
| **Tokenizer cache** | `training/.cache/hf` (tokenizer and config files only, no weights). Its chat-template hash and vocabulary size match the Phase 8 run manifest. |
| **Bundle rebuilt** from `e68d25b` | See §3. The previous bundle (commit `d694089`, sha `6ffda7e7…`) is kept as `training/runs/gtmflow-phase9-bundle-d694089.tar.gz`. |
| **Scoring path** | `dataset_cli evaluate-predictions --system source` on `test-v1` eligible ran with `DATABASE_URL=sqlite://` (exit 0): 71 of 71 scored, no Postgres needed. |

## 3. Bundle

| | |
|---|---|
| File | `training/runs/gtmflow-phase9-bundle.tar.gz` (122,229,722 bytes, 42 members) |
| sha256 | `67f44dee173ba4e9d7e786e5e453f0b1a0670cae5f8e30e99ec39c14c5c4cc86` |
| Commit | `e68d25b9dac58def41b926566a1bc135adb694c3` |
| Contents | Committed `training/` code, `backend/app/ai/prompts.py`, the four inputs-only held-out files with their manifest, and the two pinned adapter files. **No** reference targets, stored gpt-4o-mini outputs, reviews, datasets, database files or `.env`. |

**Verification (exit 0 for all).** The launcher's own `check_bundle` passed with the phase9 allowlist and deny patterns. From a fresh extraction of the bundle:

- `generate plan` re-verified all input and adapter hashes;
- the test suite gave 19 passed and 1 skipped (the git-only test, as designed);
- `generate smoke` ran the tiny random model on CPU.

The pod checks the same allowlist again before extracting.

## 4. What the run does

The pod job is `training/scripts/runpod/pod_eval_job.sh`:

1. Verify the bundle sha256 and allowlist.
2. Check the GPU: driver CUDA must be ≥ 12.6.
3. Install the hash-locked environment and compare `pip freeze` with the lock.
4. Run `plan`, which re-verifies input and adapter hashes.
5. Run the tests **with the GPU hidden**. In Phase 8, a test started the real run on the GPU; that cannot happen here.
6. Run `generate run`:
   - Both systems use one base model load. `qwen3-4b-base` runs with the adapter disabled and `qwen3-4b-lora-v1` with it enabled.
   - Decoding is greedy, in batches of 8, capped at 1,024 new tokens, with identical batches for both systems.
   - The passes run in order: validation-eligible, validation-flagged, test-eligible, test-flagged.
   - **Validation gate before any test input:** the run stops if the LoRA system's truncation rate or either system's empty-output rate exceeds 10%. Nothing is tuned, and the stop is copied back as evidence.
   - A pass that cannot finish before the generation deadline is recorded as not generated, never cut short.
7. Package everything with `SHA256SUMS`.

## 5. Launch commands (not run)

From `/workspaces/gtmflow-ai`:

```bash
# 0. Keep this Codespace up for the whole run: set the idle timeout to 120 min or more
#    (github.com/settings/codespaces), or stay active. If it stops, the pod still stops itself (§7).

# 1. Free: account reachable, no gtmflow pods, balance, SSH key present, bundle checked.
python3 training/scripts/runpod/launch.py check --profile phase9-eval \
  --bundle-sha256 67f44dee173ba4e9d7e786e5e453f0b1a0670cae5f8e30e99ec39c14c5c4cc86

# 2. Recommended, paid (about $0.02-0.05, at most $0.20): prove that a pod removes itself
#    with its own key. No data is sent.
python3 training/scripts/runpod/launch.py prove-self-delete --confirm-paid-compute

# 3. Paid: the evaluation run.
python3 training/scripts/runpod/launch.py run --profile phase9-eval \
  --bundle-sha256 67f44dee173ba4e9d7e786e5e453f0b1a0670cae5f8e30e99ec39c14c5c4cc86 \
  --confirm-paid-compute

# If the orchestrator is interrupted: re-attach, or remove the pod.
python3 training/scripts/runpod/launch.py resume --profile phase9-eval
python3 training/scripts/runpod/launch.py terminate --profile phase9-eval
```

**Why step 2.**

- Pod-side self-deletion has never been proven end to end. Phase 8's failed-run path skipped that proof (Phase 8 handoff §10). The pre-transfer check only *infers* delete permission.
- The pod-side path is what bounds cost if this Codespace stops mid-run. With the proof done, the worst case no longer depends on this Codespace.
- If the proof fails, do not launch; decide first (Phase 8 handoff §9, options a–d).

**After copy-back (workspace, free):**

```bash
cd backend
for s in qwen3-4b-base qwen3-4b-lora-v1; do for sub in eligible flagged-uncertain; do
  DATABASE_URL=sqlite:// .venv/bin/python -m app.dataset_cli evaluate-predictions \
    --dataset data/datasets/test-v1/$sub.jsonl \
    --predictions ../training/runs/phase9-eval-v1/run/predictions/$s.jsonl --system-name $s \
    --out ../training/runs/phase9-eval-v1/scores/test-$sub-$s.json
done; done
```

Then score the gpt-4o-mini baseline the same way (`--system source`). After that comes the blind AI review. `app/evaluation/phase9.py` provides the packet, key and aggregation functions, and they are tested, but **no CLI drives them yet**. That remains free, workspace-only work to finish before the review, and it needs no pod.

## 6. Estimated runtime and cost

**Measured inputs:**

- `plan` counts 27 batches per system: 10 + 4 + 9 + 4.
- Prompts are at most 1,906 tokens.
- With the real tokenizer, the reference outputs (reviewed target or gpt-4o-mini output, whichever is longer) need **9,547 decode steps per system**, summed over each batch's longest example. The longest single output is 445 tokens.
- The worst case, where every batch runs to 1,024 tokens, is **27,648 steps per system**.

**Assumed, not measured:** an L4 decode step for this 4B bf16 model at batch 8 with about 2.3k-token context takes 45–80 ms. The floor comes from the weight and KV-cache reads (about 36 ms at 300 GB/s); the rest is Python and PEFT overhead. The first real pass logs the actual rate, and the deadline logic uses it.

| Stage | Estimate |
|---|---|
| Pod start, SSH, safety checks, transfer (122 MB) | 1–8 min. Phase 8 attempt 4 reached SSH in 31 s; attempt 3 took about 6 min. |
| Environment install, 8 GB model download, plan, tests | 2–8 min |
| Generation, both systems, expected: LoRA near reference lengths, base up to 2× longer | 19k–29k steps × 45–80 ms, plus about 2 min prefill ≈ **16–41 min** |
| Generation, worst case: every batch hits 1,024 tokens | 55k steps × 80 ms ≈ 76 min. Still inside the generation deadline (below); otherwise the remaining passes are recorded as skipped. |
| Package, copy-back, verification, self-deletion, accounting | 3–6 min |
| **Expected pod lifetime** | **about 25–60 min** |

**Cost.** RunPod L4 Secure Cloud was $0.49/hr when checked on 2026-09-25; it has not been re-checked, since that needs an API call. The launcher reads the actual price and refuses a pod above the $0.60/hr profile cap.

| | USD |
|---|---|
| Evaluation run, expected (25–60 min at $0.49/hr) | **$0.20–0.50** |
| Evaluation run, hard ceiling: 2 h pod lifetime × $0.60/hr + 50 GB container disk (~$0.01) | **$1.21** |
| Self-deletion proof (step 2), expected / ceiling (10 min window + 5 min backstop at the $0.80/hr default-profile cap) | $0.02–0.05 / $0.20 |
| **Proposed total authorization for Phase 9** | **$1.50** (worst case $1.41) |

The last recorded RunPod balance is $9.3869, after Phase 8's $0.6131. The Phase 8 authorization ($3.00) covered only the Phase 8 run and does not carry over.

## 7. Shutdown safeguards (the `phase9-eval` profile)

A shell `timeout` does not stop RunPod billing, so the pod removes itself.

| # | Safeguard | Phase 9 value |
|---|---|---|
| 1 | Hard pod lifetime, armed by the container start command at boot and independent of SSH or this Codespace | **2 h** after boot |
| 2 | Claim check: the pod removes itself if the job has not started | 30 min after boot |
| 3 | Post-job grace: the pod removes itself after the job ends, success or failure | 30 min |
| 4 | Workspace watchdog (detached process): removes the pod through the API | hard limit + 2 min |
| 5 | Orchestrator: removes the pod after verified copy-back or on any error, then confirms through the API | immediate |
| — | Generation deadline: passes that cannot finish are skipped and recorded, never cut short | 20 min before the hard limit, with a 5 min reserve |
| — | Price cap: a pod above it is removed before any transfer | $0.60/hr |
| — | Pre-transfer check: watchdog armed, pod id matches, the pod key can read its own record, and the terminate probe returns `POD_NOT_FOUND`; otherwise the pod is removed before any data is sent | — |
| — | One pod at a time: refused if any `gtmflow-` pod exists or any profile has an unfinished run; refused if `runs/phase9-eval-v1/run` already exists | — |
| — | Copy-back verification: archive sha, every file against `SHA256SUMS`, prediction hashes against the manifest, config, input and adapter hashes against the pinned config | — |
| — | After a verified run: the pod deletes itself with its own key, confirmed through the API (this proves safeguard 1's path) | — |

**Residual risk.** Safeguards 1–3 depend on the pod-scoped key being allowed to terminate its own pod, which is unproven (step 2 proves it). If that path fails *and* this Codespace stops, the pod keeps billing until this Codespace is back and `launch.py terminate --profile phase9-eval` runs. At $0.49/hr that is about $12 per day. Hence step 0 and step 2.

## 8. Unresolved recovery task: the project PostgreSQL database

**Status: open. Not needed for Phase 9. Do not delete the old Codespace.**

- The working database was the Docker container `gtmflow-dev-postgres` (named volume `gtmflow-dev-postgres-data`, `localhost:55433`, database `gtmflow`) in the **old** Codespace, the one with `/workspaces/gtmflow-phase5`. The migration backup did not include it, and this Codespace has no database, container or volume.
- **Newest dump available here:** `backend/.backups/gtmflow-pre-train-v2-20260925T004458Z.dump`, copied from the backup with 8 older dumps; all are git-ignored and byte-identical to the backup copies. It predates the Phase 7 train-v2 writes: **520 `train-v2` candidate rows, 448 `ai_outputs` and 25 `ai_generation_rejected` events** (Phase 7 handoff §26). A restore of it therefore gives 299 outputs, not the 747 recorded at Phase 7 closure (§27).
- **What survives without it:** the content of those 448 outputs and their AI reviews is in `backend/data/datasets/train-combined-v1/` and `backend/data/ai_reviews/train-v2/`. The generation ledger `train-v2.jsonl` matches its recorded sha256 `3d47d3ab…`.
- **Recovery steps (you, when convenient):**
  1. In the **old** Codespace: `docker start gtmflow-dev-postgres`, then `docker exec gtmflow-dev-postgres pg_dump -U postgres -Fc gtmflow > /workspaces/gtmflow-post-phase8-<UTC>.dump`, then `sha256sum` it.
  2. Copy the dump here, for example with `gh codespace cp -e -c <old-codespace> 'remote:/workspaces/gtmflow-post-phase8-<UTC>.dump' backend/.backups/`, and compare the sha256.
  3. Here: start a new `postgres:16` container named `gtmflow-dev-postgres` on port 55433 with a named volume, `pg_restore` the dump into `gtmflow`, then run `alembic current` (expect `0009`) and `scripts/phase4_db_snapshot.py`.
  4. Check the recorded counts: `ai_outputs` 747, `training_annotations` 7, `annotation_candidates` for queue `train-v2` 520, and `ai_output_reviews` and `integration_pushes` both 0. The Phase 7 fingerprints (`c4345157…`, `90ad0bfb…`, `a08739e2…`, `afe44529…`) were computed ad hoc and no committed script reproduces them, so counts plus the snapshot are the verifiable check.
- **Deadline risk:** GitHub deletes a stopped Codespace after its retention period (30 days by default). Check the old Codespace's retention at github.com/codespaces, and finish step 1 before then.
- If the old Codespace is no longer available, the fallback is the 00:44Z dump plus the dataset files above. That leaves the database without the 448 train-v2 outputs, and it would need a decision on whether to re-insert them.

## 9. Migration checks reused (2026-09-25, not repeated)

- The backup tarball checksum passed. The old phase5 tree is byte-identical to `/workspaces/gtmflow-ai` (excluding caches).
- Every commit on every branch of the old repository is in this repository and is an ancestor of HEAD. There were no stashes.
- No code, config or script references `/workspaces/gtmflow-phase5`. Only dated records do (Phase 6 handoff lines 29, 309–312 and 413; `training/runs/runpod/events.jsonl`), and they are left unchanged.
- Five uncommitted files in the old `main` checkout differ from this repository. They are superseded drafts from 2026-09-22/23 and stay preserved in the backup.
- Environments: backend Python 3.12 (435 tests passed on SQLite); training Python 3.12 (`pip freeze` identical to the lock; 20 tests after this session's addition); frontend `npm ci` (typecheck, build, 82 tests). One earlier frontend run failed while all three suites were running at once on 2 vCPUs; two isolated reruns passed. Which test failed was not captured.

## 10. Open decisions (yours)

1. **Authorize the Phase 9 budget.** The proposal is a $1.50 total ceiling covering step 2 and step 3.
2. **Data transfer:** send the inputs-only held-out files and the adapter to your private RunPod pod. No references, reviews, database or secrets are included.
3. Whether to run the self-deletion proof first (recommended).
4. The database recovery in §8.
