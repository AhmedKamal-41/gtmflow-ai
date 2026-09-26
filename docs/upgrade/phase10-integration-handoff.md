# Phase 10 handoff: model integration and durable background jobs

Date: 2026-09-26. No retraining, paid compute, paid API call, deployment, automatic approval or real Slack send happened in this phase. The project database recovery (Phase 9 handoff §8) is still open and separate.

## 1. Status on the readiness ladder

| Component | Status | Why not higher |
|---|---|---|
| `qwen3-4b-lora-v1` as an `AIClient` (`LocalLoRAClient`) | **Code ready; tested against mocked and stub servers. Not integrated.** | The contract's "integrated" rung needs at least one real request served by the evaluated adapter in this environment. No inference server with the adapter has been run (it needs a GPU, see §6). |
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

All runs used mock AI, mock Slack, blanked keys and isolated data. This Codespace's `backend/.env` is set to real OpenAI mode; every command overrode it.

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

Each step needs your approval where it costs money:

1. **A GPU host.** A local GPU, or a rented one (for example an L4, as in Phases 8 and 9). This Codespace has no GPU.
2. **Serve the adapter:**
   ```bash
   vllm serve Qwen/Qwen3-4B-Instruct-2507 --revision cdbee75f17c01a7cc42f958dc650907174af0554 \
     --dtype bfloat16 --enable-lora --max-lora-rank 16 \
     --lora-modules qwen3-4b-lora-v1=/path/to/phase8-qwen3-4b-lora-v1/train/best_adapter
   ```
   Check the adapter files against the pinned sha256 first (`training/configs/phase9-eval-v1.json`). The vLLM version and flags above are a suggestion; they have not been run.
3. **Configure the API and the worker:** `USE_MOCK_AI=false`, `AI_PROVIDER=qwen3-4b-lora-v1`, `LORA_INFERENCE_BASE_URL=https://…/v1` (or a private-network http URL), and optionally `LORA_INFERENCE_API_KEY`. With no key set, the client sends a placeholder bearer token, which vLLM ignores.
4. **Acceptance run** (one request, then a small batch job):
   - generate for a few test-v1 inputs;
   - compare against the stored Phase 9 predictions (exact or near-exact match expected under greedy decoding);
   - confirm the outputs pass validation;
   - record latency.
   - Only then may the status move to "integrated".
5. **Decide hosting and cost** before any always-on server exists.

## 7. Known limitations and open items

- **External side effects:** the fence covers database effects, not Slack delivery. A worker stalled past its lease in the middle of a delivery could deliver again after takeover. This is mitigated by never retrying pushes, and by a lease much longer than the Slack timeout. Push idempotency is Phase 11 (audit D.3).
- **No authentication** on the job endpoints, or anywhere else (audit F.1, Phase 12).
- **No retention or cleanup** of finished jobs and items.
- **Scale:** not load-tested beyond 300 items and 2 workers.
- **The standalone demo's auto-approval** of its synthetic batch is unchanged. It is labeled, mock-only, and its drafts carry no flags.
- **This Codespace's `backend/.env` is in real OpenAI mode** (`USE_MOCK_AI=false` with a key). Starting the API or a worker here without overrides would make paid calls. It was not changed.
- **Database recovery:** still open (Phase 9 handoff §8), and kept separate.
- **Stop point:** Phase 11 (routing and metrics) has not started.
