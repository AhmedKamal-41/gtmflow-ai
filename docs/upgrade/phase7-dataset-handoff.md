# Phase 7 handoff: dataset splits and baseline evaluation

Date: 2026-09-24. **Phase 7 is in progress, not complete.** The work stopped before Phase 8.

The dataset tooling, checks and held-out queues are built and verified. The held-out validation and test sets have no generated or reviewed examples yet, so no held-out baseline exists. Finishing Phase 7 needs a small paid generation run (about $0.09 for 200 calls, §9) plus review, and both need your go-ahead.

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

## 9. Remaining work to complete Phase 7 (needs your go-ahead)

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
