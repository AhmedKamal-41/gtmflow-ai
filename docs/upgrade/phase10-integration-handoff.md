# Phase 10 handoff: model integration and durable background jobs

Date: 2026-09-26. No retraining, paid compute, paid API call, deployment, automatic approval or real Slack send happened in this phase. The project database recovery (Phase 9 handoff §8) is still open and separate.

## 1. Status on the readiness ladder

| Component | Status | Why not higher |
|---|---|---|
| `qwen3-4b-lora-v1` as an `AIClient` (`LocalLoRAClient`) | **Code ready; awaiting real-inference verification.** Tested against mocked and stub servers; a one-time GPU acceptance test is prepared (§8), not run. | The contract's "integrated" rung needs at least one real request served by the evaluated adapter in this environment. That is §8, which needs your approval. |
| The Phase 8 adapter itself | **Evaluated** (Phase 9, AI-evaluated) | Unchanged in this phase. |
| Durable background jobs | **Done and verified:** tests on SQLite and Postgres, including a killed worker process; also run live over HTTP with a real worker process | Not load-tested beyond 300 items and 2 workers. |
| Runtime quality checks (`runtime-checks-v1`) and approval acknowledgement | **Done and verified as code:** tests, regression coverage on the Phase 9 data the rules were tuned on, false-positive check on 100 untouched references, run live over HTTP | Detection of *new* problematic wording is untested (§4). |

## 2. What was built

**Model client** (`backend/app/ai/lora_client.py`):

- **What it does:** `LocalLoRAClient` sends the Phase 9 request to an OpenAI-compatible inference server: the shared system message, the `grounded-v2` prompt builders, temperature 0 and at most 1,024 new tokens. The server applies the model's own chat template, as training and evaluation did.
- **How it is selected:** only with `USE_MOCK_AI=false` **and** `AI_PROVIDER=qwen3-4b-lora-v1`. Mock stays the default, and `openai` stays the default real provider.
- **Checks before any network call:** `LORA_INFERENCE_BASE_URL` must be set and be http(s). Plain http is allowed only to a local or private host, and the URL may not carry credentials, a query or a fragment.
- **First call:** the server must list `LORA_SERVED_MODEL`; otherwise the client refuses with 503 and generates nothing.
- **Failures:** a reply cut off at the token limit is rejected with the new reason code `truncated_output`, never repaired. Provider errors are sanitized. Grounding validation is unchanged: invalid output is never saved.
- **Provenance:**
  - `model_used`: `qwen3-4b-lora-v1`;
  - `model_revision`: `Qwen/Qwen3-4B-Instruct-2507@cdbee75f…`;
  - `adapter_revision`: `phase8-qwen3-4b-lora-v1/epoch3@sha256:a68ff056…`. This is the adapter *configured*; the server cannot prove which file it loaded.
  - These are also recorded on the generation event, with token usage and finish reason.
- **Configuration:** `AI_PROVIDER`, `LORA_INFERENCE_BASE_URL`, `LORA_INFERENCE_API_KEY` (optional; never logged or returned), `LORA_SERVED_MODEL`, `LORA_TIMEOUT_SECONDS`. All are documented in `backend/.env.example`.

**Background jobs** (`backend/app/jobs/`, migration `0010_background_jobs`):

- **Job types:** `fit_score`, `legacy_score`, `generate_summary`, `generate_outreach` and `push_hot`. Each reuses the existing per-lead service, so the same rules apply as on the synchronous endpoints:
  - grounding, validation, provenance and runtime flags;
  - blocked statuses (outreach generation skips blocked leads; push refuses them);
  - incomplete imports;
  - the exact-draft approval gate (push delivers only currently approved drafts).
  - No job approves, rejects or edits anything.
- **Durability:**
  - one row per lead (`background_job_items`);
  - each item's effect, status, progress counts and a **fenced lease renewal** commit in one transaction;
  - a worker that lost its lease cannot commit, and items already committed are never repeated;
  - a killed worker's job is taken over after the lease (60 s) expires, and the takeover is recorded as `job_recovered`;
  - a graceful stop returns the job to the queue at once;
  - a job claimed more than 3 times fails as a crash loop.
- **Retries:**
  - up to 3 attempts per item, for transient provider or network errors only;
  - invalid model output is recorded (`ai_generation_rejected`) and not retried;
  - Slack deliveries are never retried automatically.
- **Other behavior:**
  - cancel stops before the next item;
  - an identical active job is returned instead of queued twice;
  - every state change emits a `WorkflowEvent` (`job_enqueued`, `job_recovered`, `job_released`, `job_completed`, `job_failed`, `job_cancelled`).
- **API:**
  - `POST /api/batches/{id}/jobs` queues a job;
  - `GET /api/jobs?batch_id=` lists them;
  - `GET /api/jobs/{id}` shows progress;
  - `GET /api/jobs/{id}/items` lists the items;
  - `POST /api/jobs/{id}/cancel` cancels.
- **Worker:** `python -m app.jobs.worker [--drain]`.
- **UI:** a "Background jobs" panel on the batch page. It polls only while a job is active and ignores late responses.
- **The synchronous batch endpoints are unchanged.**

**Runtime quality checks** (`backend/app/ai/quality_checks.py`, version `runtime-checks-v1`):

- **What they are:** pure functions of the stored output and its input snapshot. `heldout-criteria-v1` and `app/evaluation/` are unchanged.
- **Checks, and where each comes from:**

  | Check | Source |
  |---|---|
  | `presumed_outreach_activity` ("…your current outreach strategies") | Phase 9 blind review |
  | `commercial_opportunity_framing` ("Exploring Opportunities…", "potential collaboration"; subject and body only, since the call note is internal) | Phase 9 blind review |
  | `invented_phrasing` | The frozen phrase list, matched only after removing the lead's recorded values. This fixes the Phase 9 §13 false positive on "Allergy & Immunology **Specialis**ts". |
  | `missing_demo_label` (demonstration sellers only; "not a commercial/real offer" both accepted) | `ai-review-rubric-v1` |
  | `placeholder_text`, `literal_escape_text`, `prospect_web_as_own` | `ai-review-rubric-v1` |
  | `need_or_interest_hypothesis` (summaries; a hypothesis that only states an unknown is exempt) | `ai-review-rubric-v1` |

- **Where flags appear:** on every `AIOutputRead` (`quality_flags`, `quality_checks_version`), on the review state (`draft_quality_flags`), on the generation event, and in the UI (the output card lists them; approval needs a checkbox).
- **Approving a flagged draft:** requires `acknowledged_quality_flags` equal to the flags shown. Otherwise the endpoint returns 409 and nothing is recorded. The acknowledgement is stored in the `outreach_approved` event. Rejecting and editing are unaffected, and an edited revision is checked again.

## 3. Verification (2026-09-26)

All runs used mock AI, mock Slack, blanked keys and isolated data. At the time, this Codespace's `backend/.env` was in real OpenAI mode, and every command overrode it (it was set to mock afterwards; see §7).

| Check | Command or setup | Result |
|---|---|---|
| Backend suite, SQLite | `DATABASE_URL=sqlite:// pytest -q` (from `backend/`) | **474 passed, 2 skipped** (the 2 Postgres-only tests), exit 0 |
| Backend suite, Postgres | the same with `TEST_DATABASE_URL` pointing at a disposable `postgres:16` container (port 55499, no volume) | **476 passed**, exit 0 |
| New backend tests | `test_lora_client.py` (14), `test_quality_checks.py` (9), `test_background_jobs.py` (17, 2 of them Postgres-only) | all pass |
| Migration `0010` | on disposable Postgres: `alembic upgrade head`, `alembic check`, `downgrade 0009`, `upgrade head` | exit 0; `check`: "No new upgrade operations detected" |
| Frontend | `npx vitest run`, `tsc --noEmit`, `npm run build` | **89 passed** (7 new), exit 0, exit 0 |
| **Killed worker process** (Postgres) | a real worker subprocess on a 300-lead `generate_summary` job, killed with SIGKILL after ≥ 20 items; a second worker with `--drain` after the 3 s lease | Completed: 300 succeeded, **exactly 300 outputs for 300 distinct leads**, attempts 2, one `job_recovered` |
| Takeover during an item (Postgres) | a second worker claims the job while the first is mid-item | The first worker's commit is rolled back by the fence (0 outputs); the second completes (3) |
| Mutation checks | fence removed; retry bound removed; crash-loop limit removed; the frontend approve gate removed | each makes a test fail. Note: the fence mutation first *survived* the SQLite-only suite, which is why the Postgres takeover test was added. |
| **Live mock run** | `uvicorn` against a fresh database `gtmflow_demo` in the disposable container: `POST /api/demo/run`, then HTTP calls and a separate `python -m app.jobs.worker --drain` | Demo: 10 leads, 2 Hot, 2 drafts generated and approved. Jobs: `legacy_score`, `fit_score`, `generate_summary` and `generate_outreach` all completed (3 of 3 each); a duplicate request was returned as deduplicated (HTTP 200). Mock drafts carried no flags. `push_hot`: 2 `mock_success` (approved Hot leads only). |
| **LoRA provider over real HTTP** | the API with `USE_MOCK_AI=false`, `AI_PROVIDER=qwen3-4b-lora-v1`, `LORA_INFERENCE_BASE_URL` pointing at a local **stub** server (fixed reply; no model) | The request arrived as `model=qwen3-4b-lora-v1`, `temperature=0`, `max_tokens=1024`, with the shared system message. The saved output recorded the provider, base revision and adapter hash, and was flagged `commercial_opportunity_framing` and `presumed_outreach_activity`. Approval without acknowledgement: 409; with the exact acknowledgement: 200, recorded in the event. A server that lists only the base model: **503, and no generation request was sent**. |
| Frozen Phase 9 material | `git diff HEAD -- backend/app/evaluation training/configs training/gtmflow_training`; Phase 9 `run/SHA256SUMS`; score file hashes | No diff; SHA256SUMS OK; scores `a3a9a4ae…`, `3c24b547…`, `c3dfbe35…` and the review `7280caa2…` / `95627749…` unchanged |

## 4. What the Phase 9 data does and does not show about the runtime checks

| Data | Role in building the rules | Result | What it establishes |
|---|---|---|---|
| Phase 9 test-v1 eligible outputs with blind-review decisions: LoRA (71) and base (71) | **Tuning data.** The rules were written from these findings, and two adjustments were made after inspecting them: call notes excluded from the framing check, and "unconfirmed whether…" hypotheses exempt. | LoRA: flags coincide with the reviewer's rejections on all 71 (22 rejected flagged, 49 accepted clean). Base: 67 of 70 rejected flagged; 3 missed (missing signatures and sender/recipient confusion have no check); the 1 accepted is clean. | **Regression coverage only.** It shows the rules still encode the documented problems on the examples they were tuned on. It is not evidence of accuracy on anything else. |
| test-v1 references (97) | Also inspected during tuning: one flagged reference led to the "unconfirmed whether…" exemption. | 0 flagged | Regression coverage only. |
| validation-v1 references (100) | Not used for tuning. | 0 flagged | **False positives on these 100 references only:** none of these acceptable texts is flagged. They share the v1 rubric's template wording, so this says little about other acceptable styles. |

**Not established:** whether the checks detect *new* problematic wording. No unseen, independently labeled problematic outputs have been checked. The checks are regular expressions, so a rephrased version of a documented fault will be missed unless it matches a pattern. The GPU acceptance test (§8) produces fresh outputs from the real model, but they are near-reproductions of the Phase 9 test outputs, so they add parity evidence, not detection evidence.

## 5. What is tested versus what remains unverified

**Tested with mocks and stubs:**

- the client's configuration handling, request shape, provenance, error handling and truncation handling;
- selection through `get_ai_client`;
- the whole HTTP path from the API to an OpenAI-compatible endpoint.

**Not verified (needs a real inference server with the adapter):**

- that a real server (vLLM or similar) loads the adapter and serves it under the configured name;
- that its chat template and greedy decoding reproduce the Phase 9 outputs. GPU kernels may differ slightly between Hugging Face `generate` and vLLM;
- real latency, memory and throughput;
- that real outputs pass validation at the Phase 9 rate (197 of 197 parsed and valid there);
- the actual rate of runtime flags on production data.

## 6. Remaining inference setup (to reach "integrated")

1. **Run the prepared acceptance test (§8).** It needs your approval: paid GPU time, and the test inputs sent as prompts to your private pod.
2. **If it passes,** the client's status can move to "integrated" for this environment. Choosing a production serving setup (for example vLLM with `--enable-lora`, or the Phase 10 `serve.py`) and its hosting cost remains a separate decision. No always-on server exists.
3. **If it fails,** nothing changes in the app: mock stays the default, and the evidence is kept for diagnosis.

## 7. Known limitations and open items

- **External side effects:** the fence covers database effects, not Slack delivery. A worker stalled past its lease in the middle of a delivery could deliver again after takeover. This is mitigated by never retrying pushes, and by a lease much longer than the Slack timeout. Push idempotency is Phase 11 (audit D.3).
- **No authentication** on the job endpoints, or anywhere else (audit F.1, Phase 12).
- **No retention or cleanup** of finished jobs and items.
- **Scale:** not load-tested beyond 300 items and 2 workers.
- **The standalone demo's auto-approval** of its synthetic batch is unchanged. It is labeled, mock-only, and its drafts carry no flags.
- **`backend/.env` now has `USE_MOCK_AI=true`** (changed on 2026-09-26; before that it was in real OpenAI mode). Only that line changed, and no credential was displayed. Verified with no environment overrides: a running API reports `configured_provider: mock`, and a real `python -m app.jobs.worker` process generated its output with `mock`. The OpenAI key is still in the file, so setting `USE_MOCK_AI=false` again would re-enable paid calls.
- **Database recovery:** still open (Phase 9 handoff §8), and kept separate.
- **Stop point:** Phase 11 (routing and metrics) has not started.

## 8. Prepared GPU acceptance test (not run; awaiting approval)

**Purpose:** one temporary pod serves the **pinned base model and the Phase 8 adapter**, and the app in this Codespace uses it through its real `LocalLoRAClient`. This is the missing "real request" for the integrated rung. It reuses the verified Phase 8/9 work:

- the RunPod launcher, with self-deletion proven twice on 2026-09-26;
- the hash-locked GPU environment, the tests with the GPU hidden, and packaging with `SHA256SUMS`;
- the Phase 9 model loading and greedy decoding code.

**Why not vLLM:** it is not in the verified environment, and adding it would bring a different torch build. `training/gtmflow_training/serve.py` serves the adapter with the exact Phase 9 stack.

**Prepared and verified locally ($0):**

| Item | Evidence |
|---|---|
| `serve.py`: OpenAI-compatible; binds 127.0.0.1; one-time bearer token; greedy only; at most 1,024 tokens; adapter sha256 verified before loading; no prompt or output text in its logs; stops on a stop file or deadline; refuses without `--confirm-paid-compute` or a GPU | CPU smoke with the tiny random model through the app's `LocalLoRAClient`: 401 without the token, 400 for temperature 0.7, a truncated reply rejected as `truncated_output`, stop file honored. Refusals exit 2. Unit test of the request rules. |
| `pod_serve_job.sh`: the Phase 9 pod job with generation replaced by serving | bash syntax check; allowlist equal to the launcher's (test) |
| `launch.py --profile phase10-accept` | 75 min hard lifetime, $0.60/hr cap, 20 min post-job grace, its own state file. On `serving` it opens an SSH tunnel (127.0.0.1:18000 → the pod's 127.0.0.1:8000), runs the driver, then creates the stop file. Tests show the acceptance runs exactly once and one stop is requested (mutation-checked). |
| Bundle `training/runs/gtmflow-phase10-accept-bundle.tar.gz` | sha256 **`4bde2e6a19d594b2bd034a071e95581d9a1bbdca9fcb51b6e062eaff4c54c0b7`**, commit `45bae42`, 39 members. **No data file of any kind:** code, `prompts.py`, and the two pinned adapter files. The launcher's check passed. From a fresh extraction: 22 tests passed, 1 skipped (git-only). |
| `backend/scripts/phase10_acceptance.py` (the driver) | **Dry run** against `phase10_replay_stub.py` (stored Phase 9 outputs replayed; no model) with a disposable Postgres: all required criteria passed, labeled "DRY RUN (not real inference)". **Failure path** against the tiny random model: exit 1; A1, A2 and A3 failed as they should; C1 and C2 passed. |

**Test cases** (fixed and deterministic; the inputs are sent as prompts, never stored on the pod):

- **A. 20 test-v1 eligible cases through `LocalLoRAClient`:**
  - 10 outreach: the shared Phase 9 case `39fd8237`, 5 the blind reviewer rejected and 4 it accepted (first by example id);
  - 10 summaries: 5 healthcare and 5 real estate.
- **B. The app path**, on a fresh disposable Postgres (`gtmflow_accept`, local port 55498):
  - upload a 3-lead CSV and activate the demonstration seller;
  - `POST generate-summary` and `generate-outreach` for one lead;
  - a `generate_outreach` background job run by a real `python -m app.jobs.worker` process.
- **C. Negatives:** an unserved model name; a wrong token.

**Expected checks:**

| Criterion | Required | Pass condition |
|---|---|---|
| A1 | yes | 20 of 20 complete: no provider error, no truncation |
| A2 | yes | 20 of 20 pass the app's grounding validator |
| A3 | yes | 20 of 20 get the same Phase 9 report categories (valid structure, factual support, missing-info handling, automated writing) as the stored Phase 9 prediction |
| A4 | no (measured) | outputs identical to the stored Phase 9 prediction. High but not necessarily 20 of 20: Phase 9 decoded left-padded batches of 8, and a single request can differ numerically in bf16. |
| B1 | yes | both API calls return 200 with `model_used=qwen3-4b-lora-v1`, the pinned `adapter_revision`, and runtime flags present |
| B2 | yes | the job completes: 2 succeeded, 1 skipped (the lead generated in B1) |
| B3 | yes | 0 review rows and 0 push rows; every saved output carries the pinned adapter revision |
| C1, C2 | yes | an unserved model is refused before any generation; a wrong token is refused |
| D | no (measured) | latency p50/p95 and completion tokens |
| Copy-back | yes | archive and file hashes match; the adapter hashes the server loaded equal the pins; the config sha matches the repository |

**Exact commands** (from `/workspaces/gtmflow-ai`; steps 1 and 2 are free):

```bash
# 1. Fresh disposable database for the app side (local only; removed afterwards)
docker run -d --rm --name gtmflow-accept-pg -e POSTGRES_PASSWORD=acceptonly -e POSTGRES_DB=gtmflow_accept \
  -p 127.0.0.1:55498:5432 postgres:16
# 2. Free check: API access, no pods, balance, SSH key, bundle
python3 training/scripts/runpod/launch.py check --profile phase10-accept \
  --bundle-sha256 4bde2e6a19d594b2bd034a071e95581d9a1bbdca9fcb51b6e062eaff4c54c0b7
# 3. PAID: one pod, one run
python3 training/scripts/runpod/launch.py run --profile phase10-accept \
  --bundle-sha256 4bde2e6a19d594b2bd034a071e95581d9a1bbdca9fcb51b6e062eaff4c54c0b7 --confirm-paid-compute
# 4. Afterwards
docker stop gtmflow-accept-pg
```

The results land in `training/runs/phase10-accept-v1/`: `acceptance/acceptance-result.json` and `driver.log`, and `run/` (the server manifest, request log and pod evidence). The launcher's exit code reflects copy-back verification and confirmed removal; the acceptance verdict is `state-phase10-accept-v1.json` → `acceptance.passed`.

**Estimated runtime.** These estimates come from Phase 9 measurements and are not measured for this setup:

| Stage | Estimate |
|---|---|
| Pod start, safety checks, transfer (122 MB) | 1–6 min (Phase 9: SSH in 62 s) |
| Environment install and tests | 1–3 min (Phase 9: under 1 min) |
| Model download (8 GB) and load | 2–8 min |
| 24 generation requests, one at a time (20 in A, 4 in B), about 290 tokens each | 7–22 min. Phase 9 batched decoding ran at about 0.19 s per step for a batch of 8. A single request should be faster per step; the range assumes 0.06–0.19 s. |
| Stop, packaging, copy-back, self-deletion | 2–4 min |
| **Expected pod lifetime** | **about 15–40 min** |

**Cost.** L4 Secure Cloud at $0.49/hr (measured on 2026-09-26; the launcher refuses anything above $0.60/hr):

| | USD |
|---|---|
| Expected (15–40 min) | **$0.12–0.33** |
| Hard ceiling: 75 min pod lifetime × $0.60/hr + 50 GB container disk (~$0.01) | **$0.76** |
| **Proposed authorization** | **$0.80** |

The last recorded RunPod balance is $8.6924. The Phase 9 authorization does not carry over.

**Shutdown safeguards (proven or verified):**

| Safeguard | Value | Evidence |
|---|---|---|
| Pod deletes itself with its own key (runs `t`) | — | **Proven twice on 2026-09-26:** proof pod `dda4ezvch2s6a1` (0 launcher deletes) and the Phase 9 run pod `o6erbl8n4z2uhq` |
| Hard lifetime from boot, armed by the start command | 75 min | the same mechanism enforced the Phase 9 run |
| Claim check: removed if the job never starts | 30 min | same |
| Post-job grace: removed after the job ends | 20 min | same |
| Serving deadline (the server stops by itself) | hard limit − 10 min | `serve.py --deadline-unix`, set by the launcher |
| Workspace watchdog (API removal) | hard limit + 2 min | same as Phase 9 |
| Orchestrator: removal after copy-back or on any error, confirmed through the API | immediate | Phase 9: removal confirmed |
| Price cap; one gtmflow pod at a time; unfinished-run refusal | $0.60/hr | Phase 9 and the new tests |
| Pre-transfer check: watchdog armed, pod id, CLI config, GraphQL self-read, terminate probe | before any transfer | Phase 9 |
| Nothing leaves the pod except over the SSH tunnel; the server listens on 127.0.0.1 only | — | `serve.py` bind address; the tunnel uses the launcher's SSH key |

**Data and safety notes:**

- **What reaches the pod:** the bundle holds no data. The 20 test inputs (PDL-derived company records, CC-BY-4.0, the same inputs whose transfer you approved for Phase 9) reach the pod only inside request prompts through the SSH tunnel. The pod never writes prompts or outputs to disk.
- **What the test runs in real mode:** only the driver's own process and its worker use real LoRA mode. They get their configuration from the driver and point at the disposable `gtmflow_accept` database; the driver refuses any database that isn't local and named `…_accept`. `backend/.env` stays `USE_MOCK_AI=true`.
- **No real outreach:** no approvals, rejections or pushes are made, `SLACK_WEBHOOK_URL` is empty, and B3 checks it.
- **No automatic retries:** a failed run is reported and nothing is relaunched.

**Your decisions before launch:**

1. Authorize **$0.80** of RunPod spending for one run.
2. Approve sending the 20 test inputs, as prompts, to the private pod.

Model integration stays **"awaiting real-inference verification"** until this test passes.

