# Phase 6 handoff: exact-draft review, human correction, training annotation

Date: 2026-09-23. **Stopped before Phase 7.**

**Status:** the review tools are **implemented and verified**. The **100 human reviews have not been done**: zero candidates have generated outputs, and zero examples are human-reviewed. **The workbench is running and ready for your reviews; see §11 for the URLs and exact steps.** Two actions of yours come next (§8):

1. activate a seller revision, for outreach candidates;
2. generate the candidates with a provider you choose, then review them in the workbench.

| Component | State | Evidence |
|---|---|---|
| Exact-draft review (approve / reject with reason / edit as revision), review state, applicability | Code ready, verified with synthetic data | §1, §7 |
| Delivery enforcement in the shared Slack path | Code ready, verified with a transport spy | §2, §7 |
| Annotation workbench, submissions, timing, export | Code ready, verified with synthetic data and on the restored real-DB copy | §3–§5, §7 |
| Migration `0009_review_annotation` | **Applied to the real database** after restored-copy verification | §6 |
| Split manifest `company-groups-v1` | **Frozen on the real database** (5,000 leads, 4,988 groups) | §4 |
| Pilot queue `pilot-v1` | **Created**: 100 candidates, 50 training companies, **0 generated, 0 reviewed** | §4 |
| GTMFlow demonstration seller profile | **Saved as draft version 1; not active** (activation is yours) | §8 |
| Human pilot reviews | **Not started**: needs you | §8 |

Phase 5 was accepted as recorded. Its uncommitted work was first checkpointed as commit `5a92df3` on branch `phase5-generation` (local, not pushed). Phase 6 work is on branch `phase6-review` in `/workspaces/gtmflow-phase5`, committed as `919fc5c` (local, not pushed). The review-preparation follow-up (the opt-in dev API proxy and §11 of this document) is committed on top of it.

## 1. Exact-draft review

**The review workspace is the lead page (`/leads/{id}`).** It shows together:
- company facts, plus source and freshness: "Facts come from a people_data_labs snapshot reported as acquired 2025-07-28… may be outdated and were not re-verified";
- the seller revision actually used (Phase 5 provenance footer);
- the grounded summary or outreach, with the facts it cites and the information it lists as unknown;
- a new **Review** card: status, whether the approval still authorizes delivery (and every reason if not), the last decision with its label and time, and an honest-identity note.

**Actions:**
- **Approve** sends `ai_output_id` and the `content_hash` of the content displayed.
- **Reject** opens an inline form that requires a reason.
- **Edit draft** (grounded v2 drafts only) saves a **new revision**.

`window.prompt` is gone, and all forms reset on navigation.

**Server rules** (`app/services/draft_review.py`, shared by endpoints, readiness and delivery):
- **The review must match exactly what was shown.** The output must exist (404), belong to this lead (400), and be an operational outreach draft (400 for summaries and annotation candidates). Its content hash must equal what was displayed (409), and no newer draft may supersede it (409). Supersession uses the same `created_at DESC, id DESC` order as everywhere else, so loading older history can't change the target.
- **Retries and changed decisions:** an identical repeat returns the same review (`idempotent_replay`). A changed decision is a new row with its own event, and both stay in `GET /api/leads/{id}/reviews`.
- **States** (`GET /api/leads/{id}/review-state`, plus `review_status` on every listed draft): `no_draft`, `pending`, `approved`, `rejected`; older drafts show as `superseded`.
- **An approval authorizes delivery only while** all of these hold:
  - it names the draft's exact content hash;
  - the draft came from grounded generation;
  - its seller is still in force: the active revision, or the unchanged built-in demo profile;
  - rebuilding its input context today gives the same `input_hash`.

  So a regenerated draft, an edit, a seller activation, or a change in company facts, fit values or restrictions each require a fresh review. Historical approvals stay in history but authorize nothing new; pre-Phase-6 reviews without a hash never authorize anything. Blocker codes: `draft_not_reviewed`, `draft_rejected`, `draft_not_from_active_seller_profile`, `draft_inputs_changed_since_generation`, `approval_does_not_identify_content`.
- **Edits** (`POST /api/leads/{id}/ai-outputs/{output_id}/revisions`) create a new row:
  - fields: `origin="human_edited"`, `parent_output_id` pointing at the revised row, `model_used="human_edit"`, `author_label="local-demo-unauthenticated"`, with the parent's input snapshot, hash, and seller/prompt/schema versions copied;
  - the model response is never modified;
  - the edit must name the parent's content hash (409 if stale), pass the same v2 schema and grounding checks (422: e.g. an unsourced "45%", or an unapproved claim id), and actually change something (400);
  - an identical retry returns the same revision.
- **Identity:** request bodies reject extra fields, so a posted `reviewer_label` returns 422. Every review and edit carries `local-demo-unauthenticated`; there is no authentication.
- **Demo auto-approval:** the standalone demo still auto-approves its synthetic batch, recorded as `demo-auto-approve` through the same exact-content review.

## 2. Delivery enforcement

`push_lead_to_slack` is the single dispatch point for the lead route, the batch route and the demo. Before building a payload it now refuses, in order:
1. a blocked lead status;
2. an incomplete import (this check moved into the shared path);
3. a lead with no currently applicable approval (`DeliveryNotApprovedError` → 409, or "blocked" in batch results, with the blocker codes).

Each refusal records `lead_push_blocked` with a reason. The payload is built from the exact approved draft (demo drafts get a `[Demonstration]` subject prefix), and `lead_pushed` records the approved output id, its content hash and the review id.

**`force`:**
- On the single-lead route, `force` bypasses only the documented Hot score threshold.
- On the batch route it still also means "re-push leads already delivered". That is an existing, documented deduplication rule, not a review or restriction rule. Concurrent-push idempotency stays Phase 11 scope.
- Neither route can bypass missing, rejected, stale or edited-away approval, a blocked status, or an incomplete import.

**Readiness:** it reports the same blockers, now on `internal_slack_handoff` too, which requires a current approval. Outbound email additionally requires the draft to come from the active revision, so a built-in demo draft can be handed off internally but is never an email to send. Historical `at_scoring` readiness snapshots are not rewritten.

**No email sending, no delivery redesign, no metrics work.** `approval_rate` can still exceed 100% on approve → reject → approve; the characterization test is kept (Phase 11).

## 3. Annotation workbench (`/annotation`, "Annotate" in the nav)

- **Layout:** the queue on the left (paginated, with status and a mock label); the selected candidate on the right.
- **Shown for each candidate:**
  - company facts with a freshness note and the reminder "Reviewing a writing example is not permission or readiness to contact this company";
  - split, group and manifest;
  - the immutable input (fact ids, free-text marking, unknowns, input hash);
  - the candidate output, via the existing output card (with provenance and a mock banner).
- **Assess the original output:** factual support (supported / partly / unsupported), writing quality (1–5), and missing-information handling (good / acceptable / poor). Notes are optional.
- **Decide:**
  - **Accept as target**: enabled only if fully supported;
  - **Write a correction**: saves a new immutable `human_edited` revision (same validation as operational edits) as the target;
  - **Skip as unsuitable**: requires a reason.
- **Retries:** a failed submission keeps every entry. Retrying the same content reuses the same `submission_id` (idempotent server-side) and flags `resumed_after_failure`. A changed decision is a new annotation row, and the latest one counts.
- **Generation:** "Generate with <provider>" uses only the configured provider, and must name it explicitly (a mismatch returns 409). Outreach candidates need an active seller revision (409 otherwise). An output is attached once and never replaced.
- **Separation from operational review:**
  - candidate outputs have `purpose="annotation"`: they are never a lead's current draft, can't be approved for delivery (400), and are hidden from the lead history by default;
  - annotations write only `training_annotations`;
  - operational approval never creates a training example.

  Tests cover both directions.
- **No bulk approval exists anywhere.** Mock candidates stay labeled `is_mock` through review and export.

## 4. Split manifest and pilot (real database)

**Assignment rules** (`app/services/splits.py`):
- **Grouping:** union-find over the same company identity, website domain, LinkedIn URL, and normalized name (lowercase alphanumerics, with legal suffixes removed). Identities are **not merged**; groups exist only for split isolation.
- **Split:** each group gets `sha256("gtmflow-phase6-split-2026-09-23:" + group_key) % 6` → train 4 / validation 1 / test 1. The group key hashes the members' source record ids, so the result is independent of row order and database UUIDs.
- **Freezing:** a manifest version is written once. Refreezing is refused (CLI exit 2), and `verify-manifest` recomputes the assignment and compares it with what's stored.
- **Pilot:** `pilot-v1` takes training groups only, one company per group, both tasks per company, alternating segments, in seeded-hash group order (`gtmflow-phase6-pilot-2026-09-23`). Leads currently excluded from routing are skipped, and the queue can't be rebuilt.

**Manifest results (`company-groups-v1`):**

| | Train | Validation | Test | Total |
|---|---|---|---|---|
| Leads | 3,284 | 840 | 876 | 5,000 |
| Groups | 3,273 | 839 | 876 | 4,988 |
| Healthcare / real estate leads | 1,634 / 1,650 | 425 / 415 | 441 / 435 | 2,500 / 2,500 |

- **Multi-lead groups:** 7 groups hold 19 leads. Shared *platform* domains (facebook.com, yelp.com, airbnb.com, linktr.ee, sites.google.com) join otherwise unrelated companies. That's over-conservative, but safe for leakage (see §10).
- **Verification:** 0 mismatches, 0 groups spanning splits.
- **Digest:** `8fdf98be8821311a85582bd85dd790476d8621608e46620a3d476a1ec48f5a3a`. It is identical when recomputed independently on the restored copy.
- **Manifest file:** `backend/data/manifests/company-groups-v1.jsonl` (5,000 lines, sha256 `9692b6de…3a8512`). It is git-ignored, like other derived cohort outputs; the database copy is authoritative.

**Pilot (`pilot-v1`):**
- 100 candidates = 50 unique companies × 2 tasks (50 summaries, 50 outreach), 25 healthcare and 25 real-estate companies, all train;
- 0 validation or test companies;
- **0 generated, 0 reviewed, 0 exported.**

The broader experiment target of 400 train / 100 validation / 100 test *examples* remains a target. Reviewed counts are 0.

## 5. Export and review timing

- **Export:** `GET /api/annotation/export?queue=pilot-v1` (JSON Lines), or `python -m app.annotation_cli export --out FILE`. Format: `annotation-export-v1`, one row per candidate whose **latest** annotation accepted or corrected its own output. Unreviewed, skipped and stale annotations are excluded (tested). Each row carries:
  - company and group identifiers, source record id, split, manifest version and task;
  - the immutable input snapshot and hash;
  - the exact target content (a test asserts the corrected text appears verbatim) and its hash;
  - the source and target output ids and origin;
  - seller id, version, hash and kind; prompt, schema, model and model revision;
  - `is_mock`, `is_demo_seller`;
  - reviewer label, with `reviewer_authenticated: false`, review mode, decision and timestamp;
  - the assessment scores, notes and timing.
- **Timing** (`frontend/src/hooks/useReviewTimer.ts`) is measured only from UI events: pointer, click, key, input/change, wheel, focus and visibility:
  - `active_ms` is visible time within 60 s of the last interaction;
  - `hidden_ms` is tab-hidden time; `idle_ms` is the remainder of `wall_ms` (open to submit);
  - flags: `was_hidden`, `had_idle_gap`, `resumed_after_failure`.

  The server rejects inconsistent values (active > wall, negatives; 422) and marks `incomplete` when no interaction or no active time was observed. Nothing is derived from creation timestamps. Abandoned sessions that are never submitted are not recorded (§10).

## 6. Migration and real-database integrity

**Migration `0009_review_annotation`** (parent `0008_seller_activation`; migrations `0001`–`0008` unchanged) is additive:
- `ai_outputs.purpose` (NOT NULL, server default `operational`) and `author_label`;
- `ai_output_reviews.content_hash`;
- the tables `split_manifests`, `company_split_assignments`, `annotation_candidates` and `training_annotations`.

**Steps:**
1. **Integrity baseline:** the Phase 5 full-row digests plus the seller tables. It matched the recorded post-Phase-5 state exactly.
2. **Backup:** `backend/.backups/gtmflow_pre_phase6_migration_20260923T1917Z.dump`, `pg_dump -Fc` exit 0, sha256 `a596a279…6d09ca` (main checkout, git-ignored). It was restored into the disposable `gtmflow_restore_verify` (`pg_restore --exit-on-error` exit 0), byte-identical to the real database.
3. **First restored-copy attempt: failed.** A foreign-key name generated by the naming convention was 67 characters, over Postgres's 63-character limit. DDL is transactional, so the copy stayed at `0008`, verified identical. The name was shortened in the model and migration, and all 29 names in `0009` were checked to be ≤ 63. This is also why the copy runs first.
4. **Restored copy, retry:**
   - `alembic upgrade head` exit 0; `alembic check` exit 0 (no drift); the integrity diff showed only `alembic_version`;
   - downgrade to `0008` restored byte-identical integrity output, and re-upgrading was clean (disposable copy only);
   - manifest frozen and verified, and the pilot created;
   - app check on Postgres with the mock client (demo activated **on the copy only**): wrong provider → 409; two candidates generated (mock, demo, `annotation`); accept and correction → 201; export had 2 rows with the exact corrected text; the lead still had no operational draft or review and status `new`; push refused.
5. **Real database:** unchanged since the backup; `alembic upgrade head` exit 0; `alembic check` exit 0. Integrity after all Phase 6 writes:
   - **unchanged:** all full-row digests of leads (5,000), company identities (5,000), source snapshots (1), import runs (4), lead batches (1, `uploaded`), lead fit scores (5,000) and lead scores (0); lead statuses (5,000 `new`); provenance completeness (5,000/5,000) and lead-to-identity record matches (5,000);
   - **added:** `alembic_version` → `0009_review_annotation`; 1 split manifest; 5,000 assignments; 100 candidates (0 with outputs); 1 seller profile draft (**no activation row**); and 3 events: `split_manifest_frozen`, `annotation_queue_created`, `seller_profile_draft_saved`.
   - `ai_outputs`, `ai_output_reviews`, `training_annotations` and `integration_pushes` are all 0. Nothing was generated, reviewed, pushed or sent.

The restored copy still holds the smoke-test rows; recreate it from the backup at any time.

## 7. Verification actually run

Backend ran from `backend/` with the existing venv; frontend from `frontend/` with the existing `node_modules` (from `npm ci`). No dependencies were added. Exit codes are the commands' own.

| Check | Result | Exit |
|---|---|---|
| Focused backend: `tests/test_exact_draft_review.py test_delivery_review_enforcement.py test_annotation.py test_seller_profile_migration.py test_outreach_review.py test_push_endpoints.py test_blocked_lead_routing.py test_demo.py` | 101 passed (a subset) | 0 |
| **Full backend, SQLite** (`DATABASE_URL='sqlite://' USE_MOCK_AI=true OPENAI_API_KEY='' SLACK_WEBHOOK_URL='' python -m pytest -q`) | **389 passed** | 0 |
| **Full backend, disposable Postgres** (`TEST_DATABASE_URL=…/gtmflow_phase6_pgtest`, created for the run and dropped afterwards) | **389 passed** | 0 |
| Focused frontend: `npx vitest run src/app/annotation src/hooks/useReviewTimer.test.tsx src/app/leads/[leadId]/page.review.test.tsx src/app/leads/[leadId]/page.interactions.test.tsx` | 34 passed (a subset) | 0 |
| **Full frontend** `npm test` | **75 passed**, 10 files | 0 |
| `npm run typecheck` | passed | 0 |
| `npm run build` | passed; includes `/annotation` | 0 |
| `git diff --check` | passed | 0 |

**One failed attempt, then a fix:** the first Postgres run exited 1, with 3 failed and 10 errors, all in the annotation tests. The manifest's `algorithm` string (72 characters) exceeded `VARCHAR(64)`; SQLite doesn't enforce lengths. The column was widened to 200 in the model and in `0009`, which had not been applied anywhere yet, and both suites then passed.

**New tests (not added twice to the totals):**
- **Backend, 47:** 19 exact review, 12 delivery enforcement, 16 annotation/splits/export. The migration test was extended in place to cover `0009`.
- **Frontend, 19:** 5 timer, 8 workbench, 6 review workspace.
- **Existing tests updated:**
  - review calls now send the displayed hash (`tests/conftest.py::review_json`);
  - rejections carry a reason, and "reject without reason" now asserts 422;
  - push tests approve the current draft first (`approve_current_draft`);
  - the approval-target UI test drives the inline reject form and asserts the hash and reason.

  No assertion was loosened.

**Mutation checks** (each run, then restored):
- disabling the approval check in `push_lead_to_slack` fails 9 of 12 delivery tests; the 3 that pass test blocked status, the score threshold and payload content;
- removing the lead page's review-state stale guard fails the navigation test.

**Coverage:**
- wrong-lead, wrong-output, summary and annotation targets;
- a mismatched or missing content hash, and a posted reviewer name;
- stale tabs and superseded drafts;
- repeated approvals, and approve → reject;
- editing and regenerating after approval;
- seller activation and company-fact or fit changes invalidating approval;
- legacy hash-less approvals;
- direct and batch delivery with and without `force`, blocked and incomplete imports, a transport spy, and the exact approved payload;
- separation of annotation and operational approval in both directions;
- group isolation, shared-domain grouping without identity merges, and reproducibility across databases and row orders;
- tamper detection, pilot-from-train-only selection, segment balance and excluded-lead skipping;
- exact corrected-text export, excluding unreviewed and skipped candidates, with the latest decision winning;
- idempotent submission ids, and timing validation with incomplete flags;
- frontend: review, correction, rejection, retry, stale responses, timing and generation refusal.

## 8. What you need to do next

**A. Activate the demonstration seller profile** (needed for outreach candidates; summaries don't need it):
1. Open `/seller-profile`. Draft **version 1** is already saved: "GTMFlow (demonstration)", `profile_kind: demo`, content hash `04588eb8e44c3caa3e2fdcd88d6b0151ad7c668095a1a0ca00e864e6a8192d43`, no proof points.
2. It describes six implemented capabilities: CSV and public-dataset import; deterministic versioned fit scoring; outreach grounded in imported facts; approving, rejecting or correcting each exact draft; Slack routing of approved leads for internal handoff; the workflow audit trail.
3. If you accept it, open Saved versions → version 1, tick "I reviewed version 1…" and "I understand version 1 is a demonstration profile…", then click **Activate version 1**. Its drafts stay labeled demonstration and can never be ready for outbound email.

**B. Choose the candidate provider.**
- *Mock (free, already configured):* candidates are deterministic and labeled mock through review and export, so they are weak as training targets.
- *Real model:* set `USE_MOCK_AI=false` and `OPENAI_API_KEY`. Each candidate is then one paid call, 100 in total, which needs your go-ahead (contract rule 7).

**C. Review.** Start the app: backend with `DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55433/gtmflow uvicorn app.main:app` from `backend/`, and `npm run dev` from `frontend/`. Then open **`/annotation`**. For each candidate:
1. click it;
2. click **Generate with <provider>**;
3. read the input and the output;
4. set the three assessments;
5. then **Accept as target** (only if fully supported), **Write a correction** → **Save correction**, or enter a skip reason → **Skip as unsuitable**.

Progress shows at the top as "N of 100 pilot examples human-reviewed (M unique companies)".

**D. Export** after reviewing: `python -m app.annotation_cli export --queue pilot-v1 --out <file>`, or `GET /api/annotation/export?queue=pilot-v1`. `python -m app.annotation_cli summary` prints the counts.

Contact-email enrichment is **not** needed for any of this.

## 9. Changed files

- **Backend, new:**
  - `app/services/draft_review.py`, `app/services/splits.py`, `app/services/annotation.py`
  - `app/api/annotation.py`, `app/schemas/annotation.py`, `app/models/annotation.py`
  - `app/core/hashing.py`, `app/annotation_cli.py`
  - `alembic/versions/0009_review_revisions_and_annotation.py`
  - tests: `test_exact_draft_review.py`, `test_delivery_review_enforcement.py`, `test_annotation.py`
- **Backend, changed:**
  - `app/api/outreach_review.py` (now thin over the service), `app/api/ai.py` (revisions, operational filter, `review_status`), `app/api/push.py`, `app/api/routes.py`
  - `app/services/integration_push.py`, `app/services/fit_queries.py`, `app/services/ai_generation.py` (purpose, explicit client, input-hash recompute), `app/services/demo.py`, `app/services/seller_profiles.py` (demo template capabilities)
  - `app/scoring/fit.py` (readiness gaps only; rubric untouched)
  - `app/models/ai_output.py`, `app/models/ai_output_review.py`, `app/models/__init__.py`
  - `app/schemas/ai_output.py`, `app/schemas/outreach_review.py`
  - tests: `conftest.py` plus the updated tests listed in §7
- **Frontend, new:** `src/app/annotation/page.tsx` (+ test), `src/hooks/useReviewTimer.ts` (+ test), `src/app/leads/[leadId]/page.review.test.tsx`
- **Frontend, changed:** `src/app/leads/[leadId]/page.tsx`, `src/components/AppHeader.tsx`, `src/lib/api.ts`, `src/types/api.ts`, and the existing lead-page tests (mocks and fixtures)
- **Repo:** `.gitignore` (`backend/data/manifests/`)

## 10. Remaining limitations and Phase 7 prerequisites

- **No human reviews have been done.** The pilot is not complete, and reviewed counts are 0.
- **Candidate quality depends on the provider:** mock candidates are templated; real ones need a key and your go-ahead.
- **Split grouping is conservative.** Shared platform domains (social or listing sites) join unrelated companies. That is safe for leakage but slightly reduces the number of independent groups. Phase 7 may add a reviewed platform-domain exclusion list, as a new manifest version, never by rewriting `company-groups-v1`.
- **Timing gaps:** abandoned sessions (opened, never submitted) are not recorded, and timing is per submission, not per continuous session across reloads.
- **Identity:** reviewer identity is unauthenticated (Phase 12).
- **Testing limits:** the concurrency tests are controlled races, not multi-process load. Frontend tests use jsdom, not a real browser.
- **Carried open items:** `approval_rate` > 100% on approve → reject → approve, concurrent-push idempotency, and batch `force` meaning re-push are Phase 11. Generation for excluded leads remains allowed (delivery refuses).
- **Phase 7 prerequisites:**
  - reviewed pilot examples exist;
  - export validation (schema, duplicates, exact-hash checks) is written against `annotation-export-v1`;
  - validation and test annotation queues are created from their own splits of `company-groups-v1`, without reassigning any reviewed company.

## 11. Start reviewing (prepared 2026-09-23)

### Current pilot counts (read live from the Codespace database)

| | Count |
|---|---|
| Pilot candidates (`pilot-v1`) | 100 (50 company summaries + 50 outreach) |
| Unique companies | 50 (25 healthcare, 25 real estate), all in the **train** split of `company-groups-v1` |
| Candidates with a generated output | **0** |
| Human-reviewed examples (accepted or corrected) | **0** (0 unique companies) |
| Skipped | 0 |
| Validation / test companies in the pilot | 0 |
| Seller profile | draft version 1 "GTMFlow (demonstration)" saved, **not active** (activation sequence 0) |

The company-group assignments and the pilot queue are unchanged since they were frozen. No output has been generated, and no example has been marked reviewed.

### Where to open it

The servers run in the Codespace against the existing database:
- backend: FastAPI on `127.0.0.1:8000`, mock AI, no Slack webhook;
- frontend: Next.js dev server on port 3000. It proxies `/api/*` to the backend through the opt-in `API_PROXY_TARGET` setting, so the backend port stays private.

- Seller profile: `https://orange-space-fortnight-pjp55vgw5jv6h9949-3000.app.github.dev/seller-profile`
- Annotation workbench: `https://orange-space-fortnight-pjp55vgw5jv6h9949-3000.app.github.dev/annotation`

Both are private Codespaces forwarded-port URLs: open them while signed in to the GitHub account that owns the Codespace. If the Codespace has stopped, first start the database, then restart both servers:

```bash
docker start gtmflow-dev-postgres
cd /workspaces/gtmflow-phase5/backend && \
  DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:55433/gtmflow USE_MOCK_AI=true \
  /workspaces/gtmflow-ai/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
cd /workspaces/gtmflow-phase5/frontend && \
  API_PROXY_TARGET=http://localhost:8000 NEXT_PUBLIC_API_BASE_URL= npx next dev -p 3000
```

### Generation provider and cost (decide before generating)

- **Configured now:** `mock` (`mock-deterministic-v2-grounded`). It is free, makes no network call, and is deterministic. Its candidates are template-like, weak as training targets, and labeled mock through review and export.
- **Real alternative:** OpenAI `gpt-4o-mini` (the client's default model), enabled by restarting the backend with `USE_MOCK_AI=false OPENAI_API_KEY=…`. Nothing has been sent to it.
- **Measured input size:** the exact real-mode prompts for all 100 pilot candidates were built locally, without any network call. They total about **157,000 input tokens**: summaries average about 1,550 tokens (max 1,604), outreach about 1,585 (max 1,640). The count is a characters ÷ 4 approximation; no tokenizer was installed.
- **Output estimate:** about 450 tokens per reply, based on the size of the mock's equivalent JSON; allow up to about 800 for a real model's longer replies. That is 45,000–80,000 output tokens.
- **Cost estimate:** at `gpt-4o-mini` standard-tier prices, **verified 2026-09-23** on OpenAI's pricing page (developers.openai.com/api/docs/pricing): **$0.15 per million input tokens, $0.60 per million output tokens** ($0.075 per million cached input). The model is not listed as deprecated (developers.openai.com/api/docs/deprecations). The 100 candidates cost roughly **$0.024 input + $0.027–$0.048 output, about $0.05–$0.07 in total**. Even allowing 30% token undercount and some invalid-output retries, it should stay well under $0.25. Every call is billed, including replies the validator rejects.
- **Not done:** no paid call was made, and none will be without your explicit approval.

**Important:** a candidate's output is generated **once and never replaced**. If you click "Generate with mock" on a candidate, that candidate stays a mock example. Decide on the provider first. If you want real-model targets, approve the cost and the backend will be restarted in real mode *before* any candidate is generated.

### Step 1: activate demonstration profile version 1 (needed for outreach candidates)

1. Open `/seller-profile`. The status banner reads **"No seller profile is active"**.
2. Scroll to **Saved versions** and expand **"Version 1 · GTMFlow (demonstration) · Demonstration draft"**.
3. Read it:
   - value proposition and target customers;
   - six capabilities: CSV and public-dataset import; deterministic, versioned fit scoring; outreach grounded in imported facts; approve, reject or correct each exact draft; Slack routing of approved leads for internal handoff; audit trail;
   - no proof points; content hash `04588eb8e44c…`.
4. If you accept it, tick **"I reviewed version 1 and want new outreach drafts to use it."** and **"I understand version 1 is a demonstration profile, not a real offer."**, then click **Activate version 1**.
5. The banner changes to **"Active: version 1 · GTMFlow (demonstration) · Demonstration profile"**.

Its drafts are labeled demonstration and can never become ready for outbound email. The activation is recorded as `local-demo-unauthenticated`. You can deactivate it later from the same banner.

### Step 2: review your first five pilot examples

Open `/annotation`. The first five queue items are:

| # | Company | Task |
|---|---|---|
| 1 | david m bacha do | Company summary |
| 2 | david m bacha do | Outreach (needs Step 1) |
| 3 | one west associates inc | Company summary |
| 4 | one west associates inc | Outreach (needs Step 1) |
| 5 | accuhealth inc | Company summary |

For each one:

1. **Select it** in the Queue list (for example, "#1 david m bacha do · Summary").
2. **Generate it** once you've chosen the provider: click **"Generate with mock"**, or "Generate with openai" after a real-mode restart you approved. The candidate then shows *Pending review*.
3. **Check the facts:**
   - Compare the output card with **Immutable input**: the fact ids (`fact-company_name`, `fact-industry`, …) and the "Unknown from the data" list.
   - Every statement must come from those facts.
   - Look for invented contact names, intent, budget, problems, tools or figures.
   - The source note gives the data's age: a People Data Labs snapshot reported as acquired 2025-07-28, possibly outdated.
   - For outreach, also check that it describes only the demonstration profile's capabilities and keeps the demonstration label.
4. **Rate the original output:**
   - **Factual support**: Fully supported / Partly supported / Unsupported;
   - **Writing quality**: 1–5;
   - **Missing-information handling**: Good / Acceptable / Poor;
   - optionally **Notes**.
5. **Save one decision:**
   - **Accept as target**: enabled only when Factual support is *Fully supported*;
   - **or Write a correction**: edit the "Corrected …" fields (for example "Corrected company summary", or "Corrected email body" / "Corrected subject" / "Corrected call note"), then **Save correction**. The correction is stored as a new revision; the original output stays unchanged. Unsourced figures, contact emails or unapproved claims are rejected with a message, and your text is kept;
   - **or**, if the example is unusable, enter a **Skip reason** and click **Skip as unsuitable**.
6. **Confirm** the message **"Saved as a human-reviewed training example."** The progress line at the top ("N of 100 pilot examples human-reviewed") increases.
7. **If saving fails,** your entries stay. Click the same button again: the retry reuses the same submission id, so it can't create a duplicate.

Review time is measured only while the page is visible and you're interacting with it; idle and hidden time are recorded separately. Reviewing an example is not permission or readiness to contact the company. Operational outreach approval (on lead pages) is separate and unaffected.

After reviewing, `python -m app.annotation_cli summary` (from `backend/`, with the same `DATABASE_URL`) prints the counts. `python -m app.annotation_cli export --queue pilot-v1 --out <file>` writes the reviewed examples.

## 12. Pilot progress log

Counts: **usable examples** = accepted + corrected (exported); **skipped** examples are counted separately and never exported.

### Session 2026-09-23 (continuation)

**State at start:** unchanged since §11. The pilot has 100 candidates from 50 companies, 0 generated, 0 accepted, 0 corrected (0 usable), 0 skipped. The demo profile is draft version 1, not active (activation sequence 0). The tree is clean at `e1b28e7`.

**Failures and recoveries:**
- The Codespace had been suspended again: `gtmflow-dev-postgres` had exited, and both servers were down. The container was restarted, followed by the backend (mock AI, no Slack webhook) and the frontend (opt-in API proxy). `/seller-profile`, `/annotation` and the proxied API all answered 200.
- No database migration or integrity work was repeated; §6 is unchanged.

**Real provider readiness (checked, nothing called):**
- Pricing and deprecation status were verified on OpenAI's pages (§11).
- **No OpenAI API key is configured.** The check covered the shell environment, `backend/.env` and root `.env` in both worktrees (none exist), and other key-like variable names. Values were never printed.
- The installed `openai` SDK (3.17.0) exposes `OpenAI` and `chat.completions.create(model, messages, response_format)`, the exact call shape `app/ai/client.py` uses (checked offline by introspection). Real mode has still never made a live call; the first approved generation will be its first real exercise.
- **No paid call was made. No mock candidates were generated.** Generation stays blocked on your decision (§11: an output is attached once and never replaced).

**First five candidates** (facts as recorded; unknown fields listed):

| # | Company | Task | Recorded facts | Not in the record |
|---|---|---|---|---|
| 1 | david m bacha do | Summary | medical practice; 1-10; tenafly, new jersey, united states | website, contact name/title/email |
| 2 | david m bacha do | Outreach | same as #1 | same as #1 |
| 3 | one west associates inc | Summary | real estate; 1-10; st. louis, missouri, united states; website onewest.com | contact name/title/email |
| 4 | one west associates inc | Outreach | same as #3 | same as #3 |
| 5 | accuhealth inc | Summary | hospital & health care; 11-50; oklahoma city, oklahoma, united states; website accuhealthsleep.com | contact name/title/email |

**Review note for #3/#4:** `onewest.com` is what the record says, but it may not belong to a 1–10 person St. Louis firm. "Factual support" means *supported by the recorded facts*. An output repeating the recorded website is supported. Flag the doubt in Notes rather than treating the record as verified truth, and correct or skip if the output asserts anything beyond it.

**Progress at end of session:** 0 generated, 0 usable (0 accepted, 0 corrected), 0 skipped, 0 unique companies reviewed. No reviews were submitted on your behalf.

### Session 2026-09-23 (first real generation)

**Configuration:**
- **Where the key went:** the OpenAI key had first been saved into the tracked template `/workspaces/gtmflow-ai/backend/.env.example` (uncommitted). With your approval it was moved into the git-ignored `/workspaces/gtmflow-phase5/backend/.env` (mode 600), and the template was restored with `git checkout`. Neither checkout's tracked files contain the key, it was never printed, and the backend log has no key-like strings.
- **What the `.env` holds:** `DATABASE_URL`, `USE_MOCK_AI=false`, `OPENAI_API_KEY` and an empty `SLACK_WEBHOOK_URL`, so Slack pushes stay on the mock sender.
- **Backend:** restarted with no environment overrides, from `start_backend.sh`, so the `.env` file is the only configuration source. The workbench reports `openai` / `gpt-4o-mini` / available.

**Failures:**
- A first restart attempt used `pkill -f`, whose pattern matched the invoking shell and killed it (exit 144). The old backend had already stopped; the new one was started from a script, and later restarts stop it by exact process ID.
- **Demonstration profile version 1 is still not active.** The database has 0 activation rows, and the backend log shows no activation request.

**Generation run** (candidates #1–#5 in order, the configured provider named explicitly, stop at first error):

| # | Company | Task | Result |
|---|---|---|---|
| 1 | david m bacha do | Summary | **Generated.** One paid `gpt-4o-mini` call; the output passed v2 validation. Output `2dbcde43-7579-4a01-9faf-cd949be2665c`, prompt `grounded-v2`, purpose `annotation`, no seller, content hash `4b13f4aa2b5f…`. It cites only recorded fact ids and lists website, contact fields, intent, budget, problems and tools as unknown. **Pending your review.** |
| 2 | david m bacha do | Outreach | **Refused (409):** "Outreach candidates need an active seller profile revision." The seller check runs before any provider call, so this cost nothing. **Run stopped here.** |
| 3–5 | — | — | Not attempted. |

**Paid calls:** 1. At the verified prices, about $0.0005.

**Progress at end of session:**
- 1 generated, 1 pending your review, 99 awaiting generation;
- 0 usable (0 accepted, 0 corrected), 0 skipped, 0 mock candidates;
- 0 reviews, 0 operational reviews, 0 pushes.

**Next:** activate version 1 on `/seller-profile`, then generate #2–#5. Review #1 at any time.

### Session 2026-09-23 (continuation request for #2–#5)

**Not generated.** You reported the demonstration profile as active, but the server still reports `draft_only` with activation sequence 0 and no activation row. Since the last backend restart, the backend log has received no activation request and no status request from a browser; the only status requests were the operator's own checks. Candidates #2–#5 were therefore not attempted: #2 would have been refused exactly as before. **No paid call was made in this session.**

**Diagnosis so far:**
- One frontend (Next dev, port 3000) and one backend (port 8000). The proxied API answers 200.
- The browser bundle inlines `NEXT_PUBLIC_API_BASE_URL=""`, so it calls relative `/api/...`.
- The Activate controls only render once the page has loaded the seller status. A tab opened while the backend was down (it was restarted several times this session) shows "Could not check which seller profile is active" with **Retry status**, and no Activate button.

**Candidate #1 unchanged:** output `2dbcde43-…`, pending review, content hash `4b13f4aa2b5f…`.

**Progress:** 1 generated, 1 pending review, 99 awaiting generation; 0 usable, 0 skipped; 1 paid call in total so far.

### Session 2026-09-23 (first five generated)

**Activation verified on the server:** version 1 (demo) is active at sequence 1, with `reviewed_confirmation` and `demo_acknowledged` both true, activated 2026-09-23T23:29:57Z by `local-demo-unauthenticated`. The earlier "activated" reports had not reached the server (see the previous entry).

**Generation run** (only missing outputs, `gpt-4o-mini`, stop at first error). **No failures.**

| # | Company | Task | Result | Output id | Content hash |
|---|---|---|---|---|---|
| 1 | david m bacha do | Summary | Skipped (preserved; generated earlier, before activation, so no seller is recorded) | `2dbcde43-…` | `4b13f4aa2b5f…` |
| 2 | david m bacha do | Outreach | Generated (demo seller v1) | `4ea94e9c-…` | `3589f2f8f50c…` |
| 3 | one west associates inc | Summary | Generated (active demo seller v1 recorded) | `e644874a-…` | `eb738faa28b7…` |
| 4 | one west associates inc | Outreach | Generated (demo seller v1) | `fe3aa061-…` | `a70bdc59573d…` |
| 5 | accuhealth inc | Summary | Generated (active demo seller v1 recorded) | `717531ff-…` | `6060b9bf183e…` |

**Paid calls:** 4 this run, 5 in total (well under $0.01 at the verified prices). There were 0 validation rejections, and the backend log has no key-like strings.

**Review notes, not decisions:**
- Draft #2 (61 words) opens "Hello," and mentions the demonstration. It cites company name, industry, size and location, and capabilities cap-1 to cap-3, with no claims.
- Draft #4 (90 words) opens "Hi there," and does **not** mention the demonstration in the body. It cites company name, industry and website (the `onewest.com` caveat applies), and capability cap-3, with no claims.

Both carry demo provenance in the UI and the export.

**Progress:** 5 generated, 5 pending your review, 95 awaiting generation; 0 usable (0 accepted, 0 corrected), 0 skipped, 0 mock. Candidates #6–#100 were not generated.
