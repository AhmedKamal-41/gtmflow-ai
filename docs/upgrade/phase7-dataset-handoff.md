# Phase 7 handoff: dataset splits and baseline evaluation

Date: 2026-09-24 (two checkpoints the same day). **Phase 7 is in progress, not complete.** The work stopped before Phase 8.

**Second checkpoint (§12–§19):**

- The held-out queues are generated: 197 of 200. The run made 207 paid attempts at a measured $0.0954, within the 220-attempt and $0.25 caps.
- All 197 generated candidates are AI-reviewed, with a frozen rubric, and built into held-out datasets.
- The gpt-4o-mini baseline is evaluated against the frozen criteria `heldout-criteria-v1`.

**Still open:**

- The training set has 73 eligible examples against the 400 target. §18 has the expansion plan; no spend has been made on it.
- Every held-out reference target is AI-derived. None is human-verified.
- 3 test candidates failed generation twice and stay unresolved.

**First checkpoint:** the dataset tooling, checks and held-out queues were built and verified. §1–§11 are kept as written then; §9 is superseded by §19.

## 1. Scope change accepted

On 2026-09-24 you accepted the mixed human/AI pilot in place of 100 human reviews:

- **Human reviews (#1–#6):** 6 in total. 3 accepted, 2 corrected, 1 skipped.
- **AI reviews (#7–#100):** 94 in total. 30 accepted, 64 corrected, 0 skipped; 26 of them are flagged uncertain.

The original requirement of 100 human-reviewed examples was **not met**. The acceptance changes the scope; it does not make the requirement met.

**Limitation carried into every later phase:** most training targets are AI-reviewed. Only 5 of the 73 eligible examples are human-verified. Every export row says which kind it is (`review_source`, `human_verified`), and reports never add the two together without naming the source.

## 2. What was built

| Component | Where | What it does |
|---|---|---|
| Combined builder | `backend/app/datasets/pilot.py` | Merges the latest human decisions (`training_annotations`) with the AI export (`ai-review-export-v1`) into one format, `gtmflow-sft-v1`, with provenance. |
| Dedup and weighting | `backend/app/datasets/dedup.py` | Policy `structure-cap-v1` (§5). |
| Checks | `backend/app/datasets/checks.py` | Checks schema, provenance, duplicates and company-group leakage against the database. |
| Evaluation metrics | `backend/app/evaluation/metrics.py` | `baseline-metrics-v1`: validator pass, rubric lints, exact match, token F1 and ROUGE-L. |
| Held-out queues | `backend/app/services/splits.py` (`create_queue`, `EVAL_QUEUES`) | Draws each queue only from its own frozen split, with its own seed. It refuses to rebuild an existing queue. |
| CLI | `backend/app/dataset_cli.py` | `build`, `check`, `evaluate` and `create-eval-queues`. Exit codes: 0 ok, 1 checks failed, 2 usage/state error. |
| Queue listing | `GET /api/annotation/queues` | Lists every queue with its split, candidate count and generated count. |
| Workbench queue selector | `frontend/src/app/annotation/page.tsx` | Switches between `pilot-v1`, `validation-v1` and `test-v1`. Held-out queues are labeled "held out, never used for training". Switching clears the selected candidate, and a late summary for a queue you already left is discarded. |

### How the combined export is built

1. **Human reviews take precedence.** For each candidate, the latest human decision wins. An AI row for a human-reviewed candidate is excluded with the reason "superseded by a human review".
2. **Skipped and stale human decisions are excluded,** with the reason recorded.
3. **Every AI row is re-verified against the database.** Its candidate id, source output id, source content hash and input hash must match the stored output, or the row is excluded.
4. **Originals are preserved.** Each row keeps:
   - the original output and its content hash;
   - the target and its hash;
   - source record, lead and company-identity ids;
   - the input snapshot and input hash;
   - seller profile id, version, hash and kind;
   - prompt, output-schema, model and model revision;
   - reviewer, decision and time; for human rows, the note and UI timing.
5. **AI rows are labeled as AI.** They carry `human_verified: false`, the reviewer model, the decision batch and position, and no human timing.

## 3. Real run (`pilot-v1`)

Commands, run from `backend/` with `DATABASE_URL` from `.env` and the AI key and webhook blanked for the process:

```bash
python -m app.dataset_cli create-eval-queues            # exit 0
python -m app.dataset_cli build --queue pilot-v1 \
  --ai-export data/ai_reviews/pilot-v1/ai-review-export.jsonl \
  --out-dir data/datasets/pilot-v1                     # exit 0
python -m app.dataset_cli check --dir data/datasets/pilot-v1   # exit 0, no problems
python -m app.dataset_cli evaluate --dataset data/datasets/pilot-v1/eligible.jsonl \
  --system source --out data/datasets/pilot-v1/eval-source.json   # exit 0
python -m app.dataset_cli evaluate ... --system mock ...          # exit 0
```

**Reproducibility:** a second build into another directory produced a byte-identical manifest.

**Output files** (git-ignored under `backend/data/datasets/`, because they embed PDL input snapshots):

| File | sha256 |
|---|---|
| `eligible.jsonl` | `c583a4b6bc4cafc6261fc5fc0854b49da6eaf40e8d967d254f0dac70b8f3bc08` |
| `flagged-uncertain.jsonl` | `7c5fe18815cbd38aa6ff8adf2cd186f9b8fb9c44c60e49a63456c76a905fd270` |
| `dataset-manifest.json` | `ba370c2b84e003e92876949e0c9e45c685829f5b6b735d5b7ed3823780c5cfbf` |

**Input:** the AI export sha256 is `788038718cab4effa429c65865d95237996f50b36e31eb16ea293b53dc23815e` (94 rows).

### Counts (actual)

The 100 pilot candidates break down as:

- 73 eligible;
- 26 flagged uncertain;
- 1 excluded (#2, skipped by the human reviewer);
- 0 exact duplicates.

**Eligible (`eligible.jsonl`): 73 examples from 38 companies, all in the train split.**

| | Human | AI | Total |
|---|---|---|---|
| company_summary, healthcare | 2 | 15 | 17 |
| company_summary, real estate | 1 | 18 | 19 |
| outreach_email, healthcare | 1 | 16 | 17 |
| outreach_email, real estate | 1 | 19 | 20 |
| **Total** | **5** (3 accepted, 2 corrected) | **68** (20 accepted, 48 corrected) | **73** |

Effective examples after weighting: **51.9** (summaries 36.0, outreach 15.9).

One eligible example has no seller context: #1, the human-accepted summary. It was generated before any seller profile was active, so its input has no seller block. Its provenance is consistently empty, and the check accepts it for that reason.

**Flagged uncertain (`flagged-uncertain.jsonl`): 26 examples from 14 companies, all AI-reviewed and all in train.**

- By task: 14 summaries and 12 outreach.
- By segment: 15 healthcare and 11 real estate.
- By decision: 10 accepted and 16 corrected.
- Effective examples: 20.1.

**Validation and test:** 0 examples. The queues exist (§6), but nothing in them has been generated or reviewed.

## 4. Uncertain cases (26, kept separate)

Each flag questions the **source record**, not the output. In every case the output follows the record, and the AI review judged the output against it. Nothing was repaired: no company name, industry, website or location was changed.

**Effect on eligibility:** all 26 stay out of `eligible.jsonl` until a human resolves them. For each case:

- **Record confirmed plausible:** the example moves into the eligible set.
- **Record is wrong:** the example is dropped. Its output is not rewritten, because training on corrected facts that are absent from the input would teach the model to invent facts.

An uncertainty is **not** treated as a confirmed error.

| Positions | Company (as recorded) | Concern |
|---|---|---|
| 11 | National Development Foundation Inc | The name suggests a nonprofit; the record says real estate. |
| 17, 18 | Webb Sanders Funeral Home | A funeral home is recorded under hospital & health care. |
| 21 | Playdate LMSW Social Work PLLC | A social-work practice is recorded as a medical practice. |
| 23, 24 | Ultimate Capital | A .co.uk website with a Pennsylvania location; the record may mix two companies. |
| 33, 34 | Saint Francis Community and Residential Services | A residential-services organization is recorded as a medical practice. |
| 41, 42 | Jeremy Ryan Butler | A person's name; the record may be an individual's profile. |
| 47, 48 | Newport News Redevelopment & Housing | A public housing authority is recorded as a real estate company. |
| 65, 66 | Binita Amin | A person's name. |
| 71, 72 | Equity Solutions and Investments | The website and LinkedIn slug don't match the name; the sources may be mixed. |
| 77, 78 | Laurenwood Nursing & Rehabilation | Several issues: a name typo in the source, a website that doesn't match, and a nursing center recorded as a medical practice. |
| 85, 86 | Herbert E Todd | A person's name. |
| 91, 92 | Piedmont Securities LLC | A securities firm is recorded as real estate. |
| 95, 96 | Clark Capital Group, LLC | The website doesn't match the name. |
| 97, 98 | Statewide Properties LLC | A property company is recorded under hospital & health care. |

Each row in `flagged-uncertain.jsonl` has the full reason in its `uncertainty_reason` field.

## 5. Duplicates and repeated structures

**Exact duplicates:** 0. The build checked for:

- the same target content hash;
- the same (task, input hash).

**Repeated structures.** A structure signature hashes a target's text after removing every token that occurs in that example's own input facts. Two targets that differ only in company name, industry or place therefore share a signature. Across all 99 examples, the repeated structures are:

| Group | Examples | Weight each |
|---|---|---|
| Outreach, the "demonstration" template from the AI corrections | 32 | 0.156 |
| Outreach | 4 | 1.0 (group size is within the cap) |
| Outreach (×3 groups) | 2 each | 1.0 |

42 outreach examples fall into repeated structures. Summaries have none.

**Policy `structure-cap-v1` (K = 5):**

- No example is dropped, copied, paraphrased or invented.
- Each example gets `weight = min(1, 5 / group size)`, so one structure contributes at most 5 effective examples.
- Example counts stay as reported; the effective count is reported beside them: 72.0 overall, 51.9 eligible.

Training in Phase 8 must use the weights, or sample one example per structure group. It must not treat the 32 templated emails as 32 independent writing examples.

## 6. Splits and leakage

**Frozen assignments are unchanged.** `company-groups-v1` still verifies:

- 5,000 leads;
- digest `8fdf98be8821311a85582bd85dd790476d8621608e46620a3d476a1ec48f5a3a`;
- `verify-manifest` exit 0.

**Pilot examples stay in train.** They were not re-split into validation or test.

**New held-out queues,** each drawn only from its own split with its own seed:

| Queue | Split | Seed | Examples | Companies | Healthcare / real estate | Generated | Reviewed |
|---|---|---|---|---|---|---|---|
| `validation-v1` | validation | `gtmflow-phase7-validation-2026-09-24` | 100 (50 summary + 50 outreach) | 50 | 50 / 50 | 0 | 0 |
| `test-v1` | test | `gtmflow-phase7-test-2026-09-24` | 100 (50 + 50) | 50 | 50 / 50 | 0 | 0 |

**Leakage check results** (database queries plus `checks.check_leakage`):

- 0 queue rows whose split differs from the frozen assignment;
- 0 groups shared between `pilot-v1` and either held-out queue;
- 0 groups that span two splits.

### Data still missing

The experiment targets are 400 train, 100 validation and 100 test examples.

| Split | Target | Have | Missing |
|---|---|---|---|
| Train | 400 | 73 eligible (5 human), plus 26 pending the uncertain spot-check | about 301–327 examples |
| Validation | 100 | 0 | 100: generate, then review |
| Test | 100 | 0 | 100: generate, then review (human review recommended) |

The outreach training examples are also low in diversity: 15.9 effective out of 37. Adding a few varied, human-written outreach examples would help more than more copies of the template.

## 7. In-sample descriptive results (not a baseline)

These runs scored the 73 eligible **training** examples. Each result file is labeled "IN-SAMPLE (training split) -- descriptive only, NOT a held-out evaluation". They check that the harness works. They are **not** a baseline result.

| System | Task | n | Validator pass | Exact match | Token F1 | ROUGE-L |
|---|---|---|---|---|---|---|
| gpt-4o-mini outputs (as stored) | summary | 36 | 1.00 | 0.64 | 0.92 | 0.92 |
| gpt-4o-mini outputs (as stored) | outreach | 37 | 1.00 | 0.00 | 0.36 | 0.21 |
| Mock (deterministic) | summary | 36 | 1.00 | 0.00 | 0.54 | 0.46 |
| Mock (deterministic) | outreach | 37 | 1.00 | 0.00 | 0.50 | 0.30 |

For stored gpt-4o-mini outreach, the rubric lints passed at these rates:

| Lint | Pass rate |
|---|---|
| Demonstration label | 0.00 |
| No invented phrasing | 0.35 |
| No placeholder | 0.32 |
| No escape text | 0.92 |
| No prospect website presented as the sender's | 0.92 |

The mock passes every lint.

**Why these numbers are biased:**

- **The targets come from the stored outputs.** They are those outputs, accepted or corrected, so stored outputs match accepted targets exactly.
- **The metrics favor the mock on outreach.** The lints encode the review rubric, and the AI corrections follow a template close to the mock's.
- **Held-out targets will have the same anchoring bias** if they are made by reviewing the baseline's own outputs. §9 describes how to reduce it.

## 8. Tests and gates (all run 2026-09-24)

| Gate | Result | Exit code |
|---|---|---|
| Backend, SQLite (`pytest -q`) | 414 passed. 389 at the Phase 6 checkpoint, plus the safety test and the Phase 7 tests (`tests/test_phase7_dataset.py`, 12 tests). | 0 |
| Backend, disposable Postgres (`TEST_DATABASE_URL=…/gtmflow_restore_verify`) | 414 passed. | 0 |
| Frontend `npm test` | 82 passed, including 3 new queue-selector tests. | 0 |
| Frontend `npm run typecheck` | Clean. | 0 |
| Frontend `npm run build` (isolated copy, so the running dev server was not touched) | Built. | 0 |
| `dataset_cli build` / `check` / `evaluate` ×2 / `create-eval-queues` on the real database | As in §3. | 0 |

**The Phase 7 tests cover:**

- human precedence, including a latest correction overriding an earlier acceptance;
- skip exclusion;
- AI rows superseded by a human review;
- AI rows not pinned to the stored output;
- preservation of the original output and versions;
- checks that catch tampered targets, false human verification, provenance drift, partial seller provenance, duplicates, a split mismatch, and a group shared with a held-out queue;
- structure weighting without count changes, and exact-duplicate removal;
- a reproducible build, and tamper detection by `check`;
- in-sample versus held-out labeling;
- held-out queues drawing only from their own split, disjoint from each other and deterministic;
- the queue listing endpoint;
- the metric values and lints.

## 9. Remaining work at the first checkpoint (superseded by §19)

Phase 7's goal is "dataset splits and baseline evaluation". The baseline needs reviewed held-out data, so these steps remain.

1. **Paid generation, not started.** Generate `validation-v1` and `test-v1` with gpt-4o-mini: 200 calls. Use the workbench's queue selector and explicit-provider Generate, or the same small-batch procedure used for the pilot.
   - Rules: stop on the first authentication or billing error, and allow at most one retry per candidate.
   - Cost: about 1,740 prompt and 300 completion tokens per call (measured on the pilot). At $0.15 per 1M input and $0.60 per 1M output tokens, that is about $0.00044 per call, or about **$0.09 for 200 calls. Cap: $0.10**, which allows about 10% retries.
2. **Review.** Human review is recommended for `test-v1`, so the final comparison rests on human targets. `validation-v1` could use the AI procedure, labeled as such.
   - To reduce anchoring, review with the rubric and correct freely.
   - Optionally, write some references without looking at the baseline.
3. **Resolve the 26 uncertain training examples** (§4). This step is optional and needs no paid calls.
4. **Build and evaluate the held-out sets:**
   - `dataset_cli build --queue validation-v1 [--ai-export …] --out-dir data/datasets/validation-v1`, then the same for `test-v1` without an AI export;
   - then `check`, and `evaluate --system source` (the gpt-4o-mini baseline) and `--system mock`. The results are then labeled "HELD-OUT evaluation".
5. **Optional:** add a small set of varied, human-written outreach training examples (§5).

No paid call, training run, outreach approval or Slack send happened in Phase 7.

## 10. Incident: two unintended paid calls from the local test suite

During this phase, running `pytest` from `backend/` loaded the developer `backend/.env` file, which holds the real key with `USE_MOCK_AI=false`.

- **Effect:** one test called `generate(..., provider="openai")` and made **2 real gpt-4o-mini calls** over two runs. The calls used synthetic fixture data in an in-memory SQLite database, cost about $0.0008, and wrote nothing to the real database.
- **Why it matters:** this broke the instruction not to start new paid API runs.
- **Fix (commit `9fda368`):**
  - `tests/conftest.py` forces the mock provider, an empty key and an empty webhook before settings load, and refuses to run otherwise;
  - `test_test_suite_never_uses_real_provider_or_webhook` guards against a regression.
- **Since the fix:** every test run in this phase used the mock provider.

## 11. Database changes in this phase

- **Backup:** `backend/.backups/gtmflow-pre-phase7-20260924T045845Z.dump` (`pg_dump -Fc`, exit 0), taken before any write.
- **Only write:** 200 new `annotation_candidates` rows (`validation-v1`, `test-v1`) with no outputs.
- **Unchanged,** by table fingerprints taken before and after:
  - `training_annotations` (7 rows, including your notes);
  - `ai_outputs` (102);
  - the `pilot-v1` candidates;
  - `company_split_assignments`.
- **Not touched:** `ai_output_reviews` (0 rows) and `integration_pushes` (0 rows).
- **No migration:** Alembic head is still `0009_review_annotation`.

---

# Second checkpoint: held-out generation, review and baseline (2026-09-24)

## 12. Authorization and what was done under it

**Your authorization:** generation for the existing `validation-v1` and `test-v1` queues only. That covered 200 candidates, at most 220 paid attempts and a $0.25 cap, whichever came first, with at most one retry per candidate. This allowance was used for nothing else. No training-expansion generation, training run, outreach approval or message send happened.

**Order of work.** Each step was committed or backed up before the paid call:

1. Test safeguard strengthened and verified.
2. Evaluation criteria frozen and hash-pinned.
3. Budgeted runner written and tested with fake clients. These three steps went into commit `76c604d`.
4. Pricing verified.
5. Database backed up: `backend/.backups/gtmflow-pre-heldout-gen-20260924T052319Z.dump` (`pg_dump -Fc`, exit 0).
6. Preflight run.
7. Paid run.
8. AI review.
9. Dataset builds, checks and evaluation.

## 13. Test safeguard (no live calls used to verify it)

- **Layer 1 (existing):** `tests/conftest.py` forces `USE_MOCK_AI=true`, an empty key and an empty webhook before settings load. It refuses to start otherwise.
- **Layer 2 (new): a network guard.** During tests, `socket.getaddrinfo`, `connect` and `connect_ex` refuse any non-loopback host and record the attempt. Local Postgres and the in-process TestClient are unaffected.
  - `test_tests_cannot_reach_external_hosts` checks `api.openai.com`, `hooks.slack.com` and a public IP address.
  - `test_real_client_with_a_key_is_still_blocked` builds a real `OpenAIClient` with a fake key and confirms the OpenAI SDK's DNS lookup is refused locally. Nothing leaves the machine and no usage is recorded.
- **Hostile-environment run.** The full suite ran with `USE_MOCK_AI=false`, a fake `OPENAI_API_KEY` and a fake Slack webhook exported, and the real `backend/.env` present: 416 passed, exit 0. Final gates are in §17.

## 14. Paid generation run (actual)

**Pricing check.** On 2026-09-24, developers.openai.com/api/docs/pricing listed gpt-4o-mini (standard tier) at $0.15 per 1M input tokens, $0.075 per 1M cached input and $0.60 per 1M output. The cached discount was ignored, which slightly overstates cost.

**Budget enforcement** (`app/services/budgeted_generation.py`, `scripts/phase7_generate_heldout.py`):

- Every attempt is written to a durable ledger, `backend/data/generation_ledgers/heldout-v1.jsonl` (git-ignored). It is recorded as in-flight before the call and completed after it.
- An attempt starts only if two conditions hold:
  - attempts so far < 220;
  - spent + $0.01103 worst-case reserve ≤ $0.25. The reserve assumes an 8,000-token prompt and gpt-4o-mini's maximum 16,384 output tokens.
- Cost comes from provider-reported usage. An attempt with unknown usage is charged the worst case.
- Each candidate gets at most 2 attempts, and retries run only after every first attempt.
- The run stops on any provider or configuration error, or after 3 consecutive failures.
- Successful outputs are committed immediately and never replaced.

**Preflight:** the provider was openai/gpt-4o-mini with a key present (the key was never printed). The active seller is demo v1, the same revision `04588eb8…` the pilot used. 200 candidates were pending.

**Result:**

| | Count / value |
|---|---|
| Paid attempts | **207** of the 220 allowed. Pass 1: 200. Pass 2 (retries): 7. |
| Successful outputs | **197** of 200 candidates. Pass 1: 193. Retries: 4. |
| Failed attempts (billed, nothing saved, each audited as `ai_generation_rejected`) | **10**, all company summaries. Reasons: `schema_invalid` at `seller_relevance` ×7; `unknown_fact_reference` ×3 (invented ids such as `fact-candidate_segment` and `fact-locality`). |
| Unresolved after one retry | **test-v1 #3, #41, #85**, all company summaries. They have no output and are reported, not retried again. |
| Tokens (provider-reported) | 360,276 prompt + 68,921 completion. Per attempt: ≤ 1,828 prompt and ≤ 554 completion. |
| **Cost** | **$0.0954** (input $0.0540 + output $0.0414), including the failed attempts. The cap of $0.25 was never approached. |
| Model actually served | `gpt-4o-mini-2024-07-18` on all 207 responses. |
| Stop reason | None: the run completed. The script exits 1 because 3 candidates are unresolved. |

**First-attempt validity by task:**

- **Outreach:** 100 of 100 valid.
- **Summaries:** 93 of 100 valid on the first attempt, 97 of 100 after one retry.

The 3 unresolved summaries are missing from test-v1. The test results are therefore conditional on generation succeeding, a mild survivorship effect.

## 15. AI review of the held-out candidates

**Reviewer:** `claude-opus-5-5`, in this Claude Code session, using `review_source=ai` and `human_verified=false` with no human timing.

**Method:** the reviewer read each candidate's exact stored input snapshot, including the seller revision, and its stored output (`scripts/phase6_ai_review.py dump --queue …`).

**Rubric:** `ai-review-rubric-v1`, unchanged from the pilot and frozen in `heldout-criteria-v1`. Outreach corrections use the same demonstration format as your #4 and #6 corrections.

**Pinning:** each decision records the output id and content hash that were read. `build` refuses any mismatch and runs every corrected target through the v2 validator against the recorded input. Both builds had 0 errors.

**Storage:** the reviews were **not** written to `training_annotations` or through the human annotation API.

- The decisions are committed: `backend/data/ai_reviews/{validation-v1,test-v1}/decisions/*.json`, written with `data/ai_reviews/heldout_helpers.py`.
- The exports are git-ignored:
  - validation `b0ef907f…`, 100 rows;
  - test `2cdf82de…`, 97 rows.

**Original predictions are preserved.** Every row keeps `source_output`, the gpt-4o-mini prediction. Corrected reference targets are separate objects with their own hashes.

| Queue | Reviewed | Summaries: accepted / corrected | Outreach: accepted / corrected | Flagged uncertain |
|---|---|---|---|---|
| validation-v1 | 100 | 23 / 27 | 0 / 50 | 26 |
| test-v1 | 97 (3 not generated) | 27 / 20 | 0 / 50 | 26 |

**Most frequent issues:**

| Issue | validation | test |
|---|---|---|
| Invented need | 39 | 39 |
| Missing demonstration label | 38 | 38 |
| Placeholder signature | 37 | 40 |
| Invented specialization | 33 | 27 |
| Invented fit | 28 | 17 |
| Broken signature ("My name is from GTMFlow") | 14 | 15 |
| Incomplete label | 12 | 12 |
| Asserted relevance | 9 | 9 |

Other issues:

- validation: invented-need hypotheses 19;
- test: invented-need hypotheses 6, misattributed website 4, literal `\n` escape text 3.

**Uncertain cases.** Each flag questions the source record, not the output, and nothing was repaired. Where an output had changed a recorded value, the recorded value was restored: validation #23 silently respelled the website, and test #64 invented a capitalization. Flagged examples are excluded from the headline numbers and evaluated separately.

| Queue | Positions | Concern |
|---|---|---|
| validation | 11, 12 | The LinkedIn slug doesn't match the name or location. |
| validation | 13, 14; 17, 18 | Person's name. |
| validation | 19, 20 | Realtors association recorded as a company. |
| validation | 23, 24 | The recorded website looks misspelled (kept as recorded). |
| validation | 47, 48 | "Our Parish Time" recorded as real estate. |
| validation | 49, 50 | University institute with a garbled location ("Maryland, Illinois"). |
| validation | 55, 56 | Finance-sounding name and website recorded as real estate. |
| validation | 63, 64 | Singapore "Pte Ltd" suffix with a North Dakota location. |
| validation | 65, 66 | "Industries" recorded under health care. |
| validation | 77, 78 | Social worker recorded as a medical practice. |
| validation | 83, 84 | The name is a tagline and the website is a person's name. |
| validation | 95, 96 | "Round Table" group recorded as a company. |
| test | 4; 5, 6; 17, 18; 53, 54; 73, 74 | Person's name; #73–74 also has an unusual locality. |
| test | 9, 10 | Publishing company recorded under health care. |
| test | 25, 26 | Title company recorded under health care. |
| test | 29, 30 | .nl website with a Texas location. |
| test | 35, 36 | The website doesn't match the name. |
| test | 42 | Properties company recorded under health care. |
| test | 63, 64 | The website doesn't match the name, and the location is the country only. |
| test | 65, 66 | Public health department recorded as a company. |
| test | 71, 72 | College housing corporation recorded as a company. |
| test | 99, 100 | The website is a person's name. |

## 16. Held-out datasets and baseline results

**Builds** (`dataset_cli build --queue {validation-v1,test-v1} --ai-export …`, exit 0):

- `check` passed with exit 0 and no problems of any kind (files, schema, provenance, duplicates, leakage).
- A rebuild into another directory produced a byte-identical manifest.
- A combined check across the pilot, validation and test datasets found:
  - no leakage;
  - no duplicates;
  - 0 shared company groups for each pair (pilot–validation, pilot–test, validation–test).
- Each manifest records its `allowed_use`:
  - validation: "evaluation only — held out; never train on it";
  - test: the same, plus "never tune prompts or rubric on it".

| Dataset | Examples | Companies | Tasks (summary / outreach) | Segments (healthcare / real estate) | Review source | Decisions |
|---|---|---|---|---|---|---|
| validation-v1 eligible | **74** | 37 | 37 / 37 | 40 / 34 | AI 74 | 20 accepted, 54 corrected |
| validation-v1 flagged | 26 | 13 | 13 / 13 | 10 / 16 | AI 26 | 3 / 23 |
| test-v1 eligible | **71** | 36 | 35 / 36 | 31 / 40 | AI 71 | 22 accepted, 49 corrected |
| test-v1 flagged | 26 | 14 | 12 / 14 | 17 / 9 | AI 26 | 5 / 21 |

Both held-out sets have 0 exact duplicates. Structure weights are recorded but not used for evaluation, which is unweighted.

**Evaluation.** `evaluate --system source` scores the **original stored gpt-4o-mini predictions** against the separate reference targets. It never scores corrected targets against themselves. The system-under-test check passed: every prediction came from gpt-4o-mini, `grounded-v2`, schema `v2`. `--system mock` is the free deterministic comparison.

Results are labeled "HELD-OUT evaluation", with the reference label "AI-derived reference targets (review_source=ai, human_verified=false); not human-verified quality", criteria `heldout-criteria-v1` (sha256 `1b5d3779a94d…`). All 16 result files exited 0.

**Eligible examples (headline):**

| Split | System | Task | n | Validator pass | Exact match (= AI reviewer acceptance) | Token F1 | ROUGE-L |
|---|---|---|---|---|---|---|---|
| validation | gpt-4o-mini | summary | 37 | 1.00 | 0.54 | 0.93 | 0.93 |
| validation | gpt-4o-mini | outreach | 37 | 1.00 | 0.00 | 0.35 | 0.20 |
| validation | mock | summary | 37 | 1.00 | 0.00 | 0.52 | 0.44 |
| validation | mock | outreach | 37 | 1.00 | 0.00 | 0.51 | 0.30 |
| **test** | gpt-4o-mini | summary | 35 | 1.00 | **0.63** | 0.95 | 0.94 |
| **test** | gpt-4o-mini | outreach | 36 | 1.00 | **0.00** | 0.34 | 0.20 |
| test | mock | summary | 35 | 1.00 | 0.00 | 0.52 | 0.43 |
| test | mock | outreach | 36 | 1.00 | 0.00 | 0.51 | 0.30 |

**Rubric lints, gpt-4o-mini outreach, pass rates:**

| Lint | validation | test |
|---|---|---|
| Demonstration label | 0.00 | 0.00 |
| No placeholder | 0.19 | 0.25 |
| No invented phrasing | 0.24 | 0.28 |
| No escape text | 0.97 | 0.92 |
| No prospect website presented as the sender's | 1.00 | 0.92 |

The mock passes every lint except "no invented phrasing" on test (0.97).

**No-need-hypothesis lint, gpt-4o-mini summaries:** 0.76 on validation, 0.91 on test.

**By segment, gpt-4o-mini, test:**

| Segment | Summary exact match | Summary token F1 | Outreach token F1 |
|---|---|---|---|
| Healthcare (n = 15 / 16) | 0.73 | 0.95 | 0.35 |
| Real estate (n = 20 / 20) | 0.55 | 0.94 | 0.33 |

Validation by segment:

| Segment | Summary exact match |
|---|---|
| Healthcare (n = 20) | 0.55 |
| Real estate (n = 17) | 0.53 |

The flagged-uncertain sets are reported separately in `eval-*-flagged-uncertain.json`. For example, gpt-4o-mini summary exact match there is 0.23 on validation and 0.42 on test.

**How to read these numbers:**

1. **gpt-4o-mini as prompted (`grounded-v2`) produces acceptable summaries about 54–63% of the time.** No outreach draft was acceptable under the demonstration rubric: 0 of 73. The main causes are:
   - a missing or incomplete demonstration / not-a-commercial-offer label (100%);
   - placeholders and broken signatures;
   - invented needs, specialization and fit.
2. **Validator pass is 1.00 by construction.** Invalid outputs are never stored. End-to-end validity is 100% for outreach, 93% for summaries on the first attempt and 97% after one retry (§14).
3. **Reference-overlap metrics are anchored.** Accepted outputs *are* their references, so they get exact match, and the AI corrections share one template. The mock's higher outreach token F1 reflects that template, not writing quality. The lints and the acceptance rate are the more meaningful numbers.
4. **Everything above is judged by an AI reviewer.** None of it is human-verified quality.

Result files are git-ignored under `backend/data/datasets/{validation-v1,test-v1}/`.

**File hashes:**

| File | sha256 |
|---|---|
| validation eligible | `8306c8d0…` |
| validation flagged | `c269e541…` |
| test eligible | `9b3f89e2…` |
| test flagged | `bf3e5a3c…` |

**Separation rules followed:**

- Validation and test examples are in no training file.
- The prompt (`grounded-v2`) and the rubric were not changed after the test outputs were seen, and nothing was tuned on them.
- The `company-groups-v1` manifest still verifies (digest `8fdf98be…`, exit 0).

## 17. Tests and gates (second checkpoint, all run 2026-09-24)

| Gate | Result | Exit code |
|---|---|---|
| Backend, SQLite | 424 passed (414 before, plus 2 network-guard tests, 7 budget tests and 1 criteria-pin test) | 0 |
| Backend, hostile environment (`USE_MOCK_AI=false`, fake key, fake webhook exported; real `.env` present) | 424 passed | 0 |
| Backend, disposable Postgres (`TEST_DATABASE_URL`) | 424 passed | 0 |
| Frontend `npm test` | 82 passed (no frontend change in this checkpoint) | 0 |
| Frontend `npm run typecheck` | Clean | 0 |
| Frontend `npm run build` (isolated copy) | Built | 0 |
| Generation preflight | No problems | 0 |
| Paid generation `execute` | Completed; 3 candidates unresolved (§14) | 1 (by design, while any candidate is unresolved) |
| AI review `build`, validation and test | 0 errors | 0 / 0 |
| `dataset_cli build`, validation and test (plus rebuilds) | Rebuilds byte-identical | 0 |
| `dataset_cli check`, validation, test and pilot | No problems | 0 |
| `dataset_cli evaluate` (16 runs) | Held-out labels as described in §16 | 0 |
| `annotation_cli verify-manifest` | Digest matches | 0 |

**Database changes:**

- **Additions only:** 197 `ai_outputs` rows (purpose `annotation`, gpt-4o-mini, `grounded-v2`, seller demo v1), 197 candidate links, and 10 `ai_generation_rejected` audit events.
- **Unchanged,** by fingerprints taken before and after:
  - `training_annotations` (7 rows, including your notes);
  - the 102 existing `ai_outputs`;
  - the `pilot-v1` candidates;
  - `company_split_assignments`.
- **Still empty:** `ai_output_reviews` and `integration_pushes`, both 0 rows.

## 18. Training expansion plan (not started; needs your go-ahead)

**Shortfall.** Training has **73 eligible examples against the 400 target** (51.9 effective after weighting). The target is not reduced. The 26 flagged pilot examples could add up to 26 if a human clears them, so **301–327** more eligible examples are needed.

**Measured eligibility rate:** 73% (pilot 73/100, validation 74/100, test 71/97). The main loss is uncertain source records.

**Draw.** A new queue, `train-v2`, from training groups only (`create_queue`, split `train`, its own seed):

- 1,609 healthcare and 1,624 real estate training companies are still unused;
- **450 candidates** = 225 companies × 2 tasks, alternating segments (113 healthcare / 112 real estate);
- the expected yield is about 330 eligible.

No validation or test company can be drawn, and the leakage check enforces this.

**Cost.** At the measured $0.000461 per attempt and 1.035 attempts per candidate:

- 450 candidates ≈ **$0.21**;
- suggested caps: **$0.30** and **500 attempts**, using the same budgeted runner and ledger.

**Writing diversity: the real bottleneck.** Under `ai-review-rubric-v1`, every corrected outreach target uses one template. Pilot outreach is 37 examples but 15.9 effective. If 165 more templated outreach targets join that structure group, they add almost no effective examples, because the weight cap is 5 per structure. More volume alone will not fix outreach. Options, from most to least recommended:

1. **Minimal-edit correction rubric for training (`ai-review-rubric-v2-train`).**
   - Remove or replace only the violating spans: placeholders, invented needs, praise and specialization, misattributed websites.
   - Add the required demonstration / not-a-commercial-offer sentence.
   - Keep the model's own compliant sentences, so structures vary naturally without invented variation.
   - This needs your approval. It would **not** change the frozen held-out references.
   - Trade-off: models trained on it will score lower on reference overlap against the template-style references. The lints and acceptance rate stay fair, so use them as the primary outreach metrics.
2. **Human-written outreach:** about 40 examples (20 per segment) across 8 or more distinct structures, at about 3–5 minutes each, or 2–3.5 hours.
3. **Keep structure-cap weighting in all cases,** and report examples and effective examples side by side.

**Review method:**

- AI review with the frozen rubric (or the approved v2 for training);
- a human spot-check of every uncertain case (about 120 expected) plus a stratified 10% sample of AI-eligible examples (about 33), in the existing workbench;
- at the measured human pace (median active time about 17 s per decision, n = 7, likely an underestimate) up to about 60 s, that is **about 45 minutes to 2.5 hours**;
- human decisions take precedence automatically.

**Balance targets:** 50/50 by task and 50/50 by segment in the draw. Achieved counts are reported by task × segment × review source, because uncertain rates differ by segment (healthcare had more classification doubts).

## 19. Remaining requirements before Phase 7 is complete

1. **Training set to 400 eligible examples** (§18). This needs your go-ahead for about $0.21 of generation, a decision on the diversity approach (rubric v2 and/or human-written outreach) and the human spot-check.
2. **Human verification of the held-out references, at least for test.** Today every reference is AI-derived. The baseline numbers are AI judgments, labeled as such, not human-verified quality.
3. **Optional:** a human spot-check of the 78 uncertain examples (pilot 26, validation 26, test 26).
4. **Unresolved candidates:** test-v1 #3, #41 and #85 have no output after two attempts each. Generating them again would need new authorization; the test set is complete at 97 without them.
