# Decisions, dependencies, and open questions

This document records architecture decisions made so far, dependencies later phases will need, unresolved questions the user needs to weigh in on, and the seller-profile requirement in detail. Demonstration assumptions (things true for the mock/portfolio version of this project but not for a real deployment) are explicitly labeled as such — don't let them leak into later phases as if they were requirements.

## Decisions made in Phase 1

| Decision | Reasoning | Status |
|---|---|---|
| No code changes during Phase 1 | Explicit scope: audit only, stop before Phase 2. | Followed — verified nothing under `backend/app` or `frontend/src` was modified this session; only `docs/upgrade/*.md` was added. |
| Use the existing `conftest.py` SQLite-in-memory + `TestClient` pattern to reproduce findings, rather than a throwaway script hitting a real DB | Matches repo convention, needed no new dependency, and reuses exactly the fixture pattern the real test suite uses so the reproductions are representative. | Done — trace script lives in the session scratchpad, not the repo. |
| Verify the PDL dataset's schema via a live fetch of its Hugging Face card rather than assuming the user's description was complete | Working rule 6 ("never fabricate... training results") plus the acceptance criterion that findings reference actual evidence — the exact column names (`name` not `company_name`, no `source` field, etc.) materially affect the Phase 3 import design. | Done — confirmed: `id, website, name, founded, size, locality, region, country, industry, linkedin_url`; 32,330,231 rows; Parquet; CC-BY-4.0; no contact/intent/outreach fields. Not independently re-verified by downloading the actual parquet file. |

## Decisions made in Phase 2

Carried forward verbatim from the user's Phase 2 brief, recorded here so they are not re-asked:

| Decision | Reasoning / detail | Status |
|---|---|---|
| Alembic for migrations | Already SQLAlchemy 2.0, `Base.metadata` fully declared. Added `alembic>=1.13,<2.0` to `backend/requirements.txt`; the resolved version in this session's venv was **1.20.0**. Justification: it's the de facto standard for SQLAlchemy projects, integrates with the existing `Base`/`get_engine()` setup with minimal glue (`alembic/env.py` reads `settings.database_url` at runtime — no URL or credential is committed). | Done |
| Phase 3 will curate ~5,000 eligible US companies; filtering is a separate streaming preparation step | Recorded for Phase 3 planning. Phase 2 built the schema hooks (`SourceSnapshot`, `ImportRun`, `CompanyIdentity`) this implies but did not implement any filtering/curation logic itself. | Recorded, not yet acted on |
| Source file is `free_company_dataset.json.gz`; Hugging Face's auto-converted Parquet exists but is not what the pipeline uses | Recorded for Phase 3. No import code was written in Phase 2, so nothing yet reads either format — this is a decision for whoever implements Phase 3's importer to honor, not something Phase 2 needed to touch. | Recorded, not yet acted on |
| Phase 4 will separate company fit, evidence coverage, outreach readiness, and blocked status; scoring stays deterministic | Recorded for Phase 4. Phase 2 did not change `scoring/lead_scoring.py`'s point model at all — the `−10` soft penalty for blocked statuses (audit.md B.5) is unchanged. What Phase 2 *did* change is downstream: blocked status is now a hard stop at the Slack-push dispatch point, independent of score (see Part A below). Phase 4 still owns turning "blocked" into a first-class scoring dimension rather than a penalty. | Recorded, not yet acted on (scoring itself untouched) |
| One versioned seller profile for the single-workspace app; its actual content is Phase 5's job and does not block Phase 2 | Recorded. No `SellerProfile` model/table was added in Phase 2 — deliberately out of scope, confirmed not needed for the provenance/identity/review schema work. Phase 5 will need its own small migration when it lands. | Recorded, not yet acted on |
| Training targets come from deliberate human annotation, starting with a 100-example pilot; may be written or corrected from company facts | Recorded for Phase 6/7. Phase 2's `AIOutputReview.review_kind` field already distinguishes `"operational_outreach"` (what Phase 2's approve/reject populates) from `"training_annotation"` (unused until Phase 6/7), and `AIOutput.origin`/`parent_output_id` exist so a future "correction" can be stored as a new immutable row pointing at the one it corrects — but no annotation UI, no edit endpoint, and no pilot data collection exists yet. | Schema hooks in place; workflow not started |
| Move the blocked-lead-reaches-Slack fix into Phase 2; keep delivery reliability (concurrency, retries) and metric redesign in Phase 11 | Done exactly as scoped — see Part A of the Phase 2 handoff. Concurrent-push idempotency and the mock-vs-real metrics breakdown were deliberately left for Phase 11. | Done |

Additional engineering decisions made while implementing Phase 2 (not pre-specified by the user, decided in-session):

| Decision | Reasoning |
|---|---|
| Added a `NAMING_CONVENTION` to `Base.metadata` (`app/core/database.py`) | Autogenerate's first attempt at migration 0002 produced unnamed FK constraints, which broke `downgrade()` (Alembic can't `drop_constraint` something it can't name). This is a one-time, low-risk addition — it only affects names Alembic assigns to *new* DDL going forward; nothing about existing constraint names (created by the old `create_all` path) changes. |
| `AIOutputReview` decision-idempotency keyed on (ai_output_id, decision), not a client-supplied idempotency token | Simpler, and sufficient for the stated requirement ("identical retried review requests are idempotent"). A client-supplied idempotency key would add API surface for a case (network retry of the exact same approve/reject) that's already fully covered by comparing against the latest recorded decision for that exact output. |
| Stale-draft rejection uses HTTP 409, keyed on "does a newer outreach AIOutput exist for this lead" | Matches the actual failure mode (a stale tab is, precisely, out of date relative to a newer draft) rather than requiring the client to prove anything itself. 409 Conflict is the standard code for "the resource changed underneath you." |
| Legacy `WorkflowEvent` → `AIOutputReview` backfill links only when the event's stored `ai_output_id` resolves to a real `AIOutput` belonging to the *same lead* | This is provable from data the app itself already wrote (every approve/reject event in this codebase's history has always included `ai_output_id` in `event_data`) — not a guess. Unresolvable events become `legacy_unlinked=True` rows with `ai_output_id=NULL`, never linked via "latest output" or timestamp proximity, per the user's explicit instruction. Verified against seeded legacy data including one deliberately-dangling event (see the Phase 2 handoff). |
| `reviewer_label` is a fixed constant (`"local-demo-unauthenticated"` for interactive use, `"demo-auto-approve"` for the `/api/demo/run` path, `"legacy-migrated-unknown-reviewer"` for backfilled rows) | Honest labeling per the user's explicit Part D.7 instruction — this app has no auth (audit.md F.1), so nothing resembling a real reviewer identity should be implied anywhere in the data. |
| Company-identity *resolution* (deciding which Lead rows point at the same `CompanyIdentity`) was NOT implemented in Phase 2 | Out of the stated "smallest schema" scope for Part C — resolution/merge logic is a Phase 3 concern (it needs the actual PDL data and a real matching strategy, not something to improvise against schema-only Phase 2 work). `Lead.company_identity_id` stays NULL for every lead created in Phase 2, including in tests; the round-trip tests prove the *plumbing* works, not that resolution exists. |

## Decisions made in Phase 3

Full detail in `docs/upgrade/phase3-data-handoff.md`; summarized here so they're not re-asked.

| Decision | Reasoning / detail | Status |
|---|---|---|
| Fixed selection rules: 2,500 healthcare (`hospital & health care` + `medical practice`) + 2,500 real_estate (`real estate`), US only, exact industry/country string matching, no size floor, website optional | This resolves the Phase 2 "curation criteria" open question directly — the user supplied fixed rules rather than leaving it to be designed. The exact three industry strings were cross-checked against a real full-corpus scan (see phase3-data-handoff.md §3) before being hardcoded, rather than trusted blind. | Done — both segments hit target with zero shortfall |
| Reuse Phase 2's `SourceSnapshot`/`ImportRun`/`CompanyIdentity`/`Lead` provenance fields and Alembic setup | As instructed. No Phase 2 table was redefined; Phase 3 only adds columns/constraints additively via a new migration (`0003_pdl_identity`). | Done |
| Move the blocked-status-survives-review regression fix and the `approval_rate` partial-fix correction into Phase 3, ahead of importing real data | Explicit instruction: fix any regression found before touching real records. A real regression *was* found (see below) and fixed first. | Done |
| Keep Part E (bounded web uploads, pagination) scoped to size/row caps + pagination + frontend updates; explicitly NOT a background-queue redesign | Instructed: "Background queue redesign remains Phase 10 work." No async/queue infrastructure was added. | Done, scoped as instructed |
| Do not generate AI drafts, implement Phase 4 scoring, train a model, or send Slack messages in this phase | Explicit instruction. Verified: `outreach_generated`/`outreach_approved` stayed at 0 for all 5,000 PDL leads after import; no lead was scored; no Slack call was made (mock mode throughout, no webhook configured). | Done — verified via `/api/metrics/dashboard` after import |

Additional engineering decisions made while implementing Phase 3 (not pre-specified, decided in-session):

| Decision | Reasoning |
|---|---|
| `curate` and `import` are separate CLI subcommands, with `curate` writing a full-fidelity JSON manifest that `import` reads (rather than `import` re-scanning the 32M-row source, or manifest and CSV needing to agree on every field) | Directly satisfies Part D.6 ("separate import attempts from the logical dataset selection"). Also makes repeated `import` runs fast (~seconds, reading a 5.7 MB manifest) instead of repeating a ~14-minute full-corpus scan every time idempotency needs re-checking. |
| Deterministic per-segment reservoir sampling (Algorithm R), seeded via `sha256(f"{seed}:{segment}")` rather than Python's built-in `hash()` | Python's `hash()` for strings is randomized per-process (`PYTHONHASHSEED`) by default — using it would have silently broken reproducibility across separate runs/machines, defeating the "identical source+config+seed reproduces the selection" requirement. |
| Chose exact (case/whitespace-normalized) industry-string matching over substring or fuzzy matching for segment classification | Directly motivated by `docs/upgrade/audit.md` B.3 (the `"hospital"` ⊂ `"hospitality"` substring bug). Verified during Phase 3's own corpus scan that `hospitality` genuinely appears as a distinct real industry value and would have been wrongly captured by substring matching. |
| `Lead.source_record_id` stores the full identity key (`"id:<pdl_id>"` or `"fp:<hash>"`), not the bare PDL id | Lets one column carry both real PDL ids and the fingerprint fallback for id-less records uniformly, so the `UNIQUE(source_snapshot_id, source_record_id)` constraint (migration 0003) covers both identity-confidence cases with one mechanism rather than two. |
| ~~A `LeadBatch` for a PDL import is found-or-created by exact name match, not by a new dedicated FK/key column~~ **Superseded by the Phase 3 closeout, see below** | The original reasoning ("keep migration 0003 minimal") traded away a real correctness/concurrency guarantee for a smaller diff. The closeout corrected this: `LeadBatch.logical_key` is now a real, persisted, uniquely-constrained column (migration `0004_logical_key`), and batch names are purely cosmetic and freely editable. | Corrected — see "Phase 3 closeout" below |

## Phase 3 closeout: gaps found in the original Phase 3 pass, and what changed

A follow-up closeout pass re-inspected Phase 3's own claims (per the user's explicit instruction not to assume "the command that eventually worked" meant the underlying code was actually fixed) and found several real gaps. Full evidence and re-verification results are in `docs/upgrade/phase3-data-handoff.md` (revised in place, not left contradicting itself); summarized here as decisions.

| Gap found | What was actually true | Fix | Verification |
|---|---|---|---|
| "Missing `--source-checksum` created a duplicate `SourceSnapshot`" was treated as resolved by remembering to pass the flag | The code's reuse-lookup was gated on `if checksum:` — with no checksum, it silently created a new row on *every* invocation, with no error. Nothing in the code prevented recurrence; only operator memory did. | `curate` now derives the checksum automatically by re-hashing the actual cached/downloaded file; the flag is an optional cross-check assertion only. `import` validates required provenance fields and refuses to write anything if they're missing/empty/malformed (`ProvenanceError`, raised before any DB write). | 8 unit tests covering each missing/empty/malformed field; re-ran the real import with the fixed code — same snapshot/batch ids reused, 0 new rows. |
| No database-level uniqueness backed the application's find-or-create for `SourceSnapshot` or `LeadBatch` | A textbook TOCTOU race: two concurrent import attempts could both find "nothing exists" and both insert, duplicating a snapshot or batch. Batches were matched by a name-prefix, which isn't an identity mechanism at all (renaming a batch could have broken matching, though this was never actually observed since batches weren't renamed in Phase 3's original pass). | Migration `0004_logical_key`: `UNIQUE(provider, checksum)` on `source_snapshots`, `UNIQUE(logical_key)` on `lead_batches` (a new, persisted column, backfilled from the existing batch's real provenance with ambiguity-detection that aborts the migration rather than guesses). Both find-or-create functions now catch the resulting `IntegrityError` and re-read the winning row. | **Real concurrent-writes test**: 8 OS processes racing on disposable Postgres, synchronized via a `multiprocessing.Barrier`, converged to exactly 1 snapshot and 1 batch, with exactly 1 process each correctly reporting `created=True`. |
| The curation dedup index was an in-memory `dict`, holding one entry per eligible record (~962,000 on the real scan) | Correctly "not the full corpus" but still unbounded in the general case, exactly as the closeout brief flagged. | Replaced with a disk-backed (temp SQLite), LRU-cached `IdentityHashStore`. | Equivalence-proof test against a plain-dict reference implementation on a fixture (byte-identical selections); a bounded 3M-line diagnostic run measured 52.4 MiB peak RSS (not the full corpus — explicitly labeled as a bounded sample, not a benchmark of the real run). |
| The browser CSV upload path capped bytes/rows while reading, but still joined everything into one in-memory buffer and wrote all rows in one transaction | Bounded by size, but not the requested streaming-parse-plus-bounded-writes; also had no partial-failure semantics (a mid-stream error had no path to "some rows committed, clearly labeled, excluded from push" — it either fully succeeded or the whole request failed with nothing said about what, if anything, had already been written). | True streaming generator (`iter_cleaned_leads`) reading from a temp file; chunked (500-row) commits; a mid-stream row-limit violation now flushes and keeps whatever was already parsed, marks the batch `"partial"`, and returns 422 with an accurate count. | `test_csv_upload_bounds.py` (updated), `test_partial_import_recovery.py` (new, 5 tests) including a real interrupted-chunk-then-retry scenario. |
| A partial batch had no push-time guard | `POST /api/leads/{id}/push` and `POST /api/batches/{id}/push-hot` never checked batch completeness at all. | Both now reject a lead/batch whose batch is `"partial"`, not overridable by `force=true`. | Transport-spy tests proving no Slack send is attempted, for both entry points, both force values. |
| (Found *while* fixing the above) `POST /api/batches/{id}/score` unconditionally set `batch.status = "scored"` | Would have silently overwritten `"partial"` back to a status the new push guard doesn't check for — the same status-overwrite bug class Phase 2 fixed for `Lead.status`, recurring one level up. | Guarded: scoring no longer touches `batch.status` if it's `"partial"`. | Covered by the same `test_partial_import_recovery.py` suite (batch stays `"partial"` through a subsequent score call). |
| The lead-detail page's AI-output/push history panels fetched at most 200 items and converted every request error into a silent empty page | Indistinguishable "no history" from "the request failed"; no way to see more than 200; "current outreach draft" was inferred from whatever page happened to be loaded. | New `usePaginatedHistory` hook: independent per-panel loading/error/total state, retry-without-losing-items, a generation counter guarding against a stale lead's late response overwriting the newly selected lead. "Current draft" now resolved via the existing `GET .../latest-ai-output` endpoint. | Verified past 200 (250-row fixture, walked via the exact API shape the frontend uses) and the authoritative-lookup endpoint's correctness. **Not verified**: live browser click-through (Claude in Chrome wasn't connected this session) — stated as an open gap, not claimed as done. |
| The handoff claimed the downloaded file's checksum matched **both** `x-linked-etag` and `x-xet-hash` | Only `x-linked-etag` actually matches; `x-xet-hash` is a different value (Hugging Face's internal Xet content-addressing hash, not a plain file SHA-256). | Corrected in `phase3-data-handoff.md` directly; `x-xet-hash` is not used for verification anywhere. | Re-checked both header values against the computed sha256 directly. |
| `SourceSnapshot.retrieved_at` was being set to "now" at import time, not the actual download time; no distinction from `curated_at` | Conflated three different timestamps (source's claimed acquisition date, actual download time, actual curation time) into effectively one ("whenever a DB row happened to be created"). | `download.py` now records and persists a real `downloaded_at` (a `<file>.meta.json` sidecar); the existing cached file predates this mechanism, so its value is honestly labeled as a file-mtime proxy, not a precise recorded event. `curated_at` is now a distinct field in the manifest, set when `curate` actually runs. | See phase3-data-handoff.md §2. |
| `LeadBatch.processed_leads` (rows ingested) and the metrics dashboard's `total_leads_processed` (rows scored) read as if they might be the same concept | They were never actually inconsistent as *data*, but the naming invited exactly this misreading, which the closeout brief flagged directly. | Added a distinct, always-current `scored_leads` field to the batch endpoints (computed via a grouped COUNT, never persisted/stale). | Verified directly: a 2-lead batch with 1 lead scored reports `processed_leads: 2, scored_leads: 1`. |

## Two Phase 2 claims re-checked in Phase 3 (Part A), before importing real data

1. **`approval_rate` exceeding 100%: only a PARTIAL fix, not "fixed."** Reproduced: approve → reject → approve on one generated draft still yields `outreach_generated=1, outreach_approved=2, approval_rate=200.0`. Phase 2's idempotency check only catches an *exact* repeat of the same decision on the same output; a genuine decision change is, correctly, not treated as a repeat, so it's not caught by that mechanism and was never going to be. **`docs/upgrade/phase-status.md`'s Phase 11 row has been corrected to reflect this.** The full fix (redefine the metric) stays Phase 11 scope, as instructed.
2. **A real, previously-undetected regression was found and fixed**: `approve-outreach`/`reject-outreach` were unconditionally overwriting `Lead.status`, silently erasing a blocked disposition (`do_not_contact`/`disqualified`/`unsubscribed`) and defeating the Phase 2 push-time status check through a second path Phase 2 never tested. Reproduced before the fix (approving outreach on a blocked lead flipped its status, after which it was successfully pushed to Slack), fixed in `app/api/outreach_review.py` and `app/services/demo.py` using the same status-preserving guard Phase 2 already applied to scoring, and closed with 5 focused regression tests **before any real PDL data was imported**, per the explicit instruction to fix this class of bug first.

## Decisions made in Phase 4

| Decision | Reasoning | Status |
|---|---|---|
| New `lead_fit_scores` table, not a rewrite of `lead_scores` | v1's table is unique on `lead_id` (single mutable row); v2 needed append-only, multi-row-per-lead, version-stamped history. Retrofitting the old table would have required either destroying v1's semantics or bolting versioning onto a table that was never designed for it. A new table is the minimal correct change; v1 stays untouched and authoritative for existing consumers. | Done — migration `0006_fit_scores` |
| `demo-us-sectors-v1`'s industry/country criteria intentionally mirror the PDL importer's own eligibility filter | The user's brief specified these exact values; independently declared (not shared code) and independently versioned in `app/scoring/fit.py`, distinct from `app/pdl/config.py`'s `MAPPING_VERSION`. This is why the real cohort scores uniformly 100 — documented as expected, not papered over. | Done |
| Company-size criterion present in every response but inactive (weight 0) | No seller size preference exists yet (Phase 5). Reporting it as an explicit `not_configured` criterion, rather than omitting it, makes its absence visible per the brief's instruction. | Done |
| CSV upload resume requires an **explicit `resume_batch_id`**, not automatic content-hash matching | A mid-phase checkpoint review correctly identified that hash-only matching cannot distinguish "retry of that specific attempt" from "deliberately separate re-upload of identical content" — both are legitimate. Redesigned from the original hash-auto-detect approach (which the checkpoint flagged as insufficient) to require the caller to pass back the specific `batch_id` it's retrying; hash is still checked, but only to *reject* a resume attempt whose content has changed, never to *discover* a resume target on its own. | Done, tests rewritten accordingly |
| `outreach_rejected` lead status is reported as a v2 readiness gap, not added as a new hard routing-eligibility exclusion | Verified by reading `app/services/integration_push.py`: `BLOCKED_STATUSES = DISQUALIFIED_STATUSES`, which does **not** include `outreach_rejected` today. Adding a new hard exclusion to the real push pipeline was out of this phase's explicit scope ("preserve existing approval/blocking rules... add no automatic sends"); the brief's listing of it as a "current" hard exclusion was checked against the actual code and found not to be true today. Recorded honestly in the handoff rather than silently treated as already-enforced or silently added without instruction. | Done — flagged, not silently assumed or added |
| Readiness and eligibility are always recomputed live; the stored snapshot is returned separately as `at_scoring` | The first completion let the bulk batch listing return readiness as stored at scoring time (`readiness_is_current=false`), which the finish pass rejected: a page labeled "readiness" must show what applies now. Recomputed in a fixed number of queries per page (latest drafts, then their latest reviews); the stored snapshot is kept as a labeled audit trail and never gates an action. | Done (finish pass) |
| One shared definition of the current fit row (`app/services/fit_queries.py`) | Filters had drifted between endpoints (three version fields in some, none checked `normalization_version`). All readers now use all four version fields and `created_at DESC, id DESC`. | Done (finish pass) |
| Draft/review "latest" uses the same `(created_at, id)` order everywhere | With tied timestamps, the latest-draft lookup and the supersession check could disagree, so a stale draft could be approved. Readiness uses the same rule. | Done (finish pass) |
| CSV batches start `uploading` (committed before any lead chunk); `INCOMPLETE_BATCH_STATUSES` = {`partial`, `uploading`} is the single definition every routing/scoring guard checks | A crash after a committed chunk previously left a batch looking like a finished, routable `uploaded` batch that couldn't be resumed. Resume now counts committed rows instead of trusting a counter that lags after a crash. No schema change, since status is an existing string column. | Done (finish pass) |
| Rescoring unchanged inputs is skipped by default (batch endpoint and CLI; `--rescore-unchanged` overrides) | Pressing "score" twice would otherwise pile up identical history rows. Skips are reported, not hidden. | Done (finish pass) |
| Real-cohort run verified rather than duplicated | The 5,000 rows were written at 21:56 UTC by a parallel session (`e3f3881d`) using the same scorer code. The final-code run skipped all 5,000 as unchanged (identical fingerprints), and `scripts/phase4_verify_stored_fit.py` matched every stored row field by field against a fresh recompute. Writing a second identical generation would have been history noise. | Done (finish pass) |

## Phase 4 checkpoint: a real gap found mid-phase, and how it was fixed

A checkpoint review of the in-progress work correctly identified that the
first draft of the CSV-retry mechanism (automatic resume via content-hash
matching against any existing `status="partial"` batch) could not express
the difference between "retry this exact failed attempt" and "upload this
same file again as a deliberate, separate operation" — a legitimate case the
original design would have silently mishandled by always merging into the
old partial batch. Fixed by requiring an explicit `resume_batch_id` (see the
decisions table above and `phase4-scoring-handoff.md`'s Part A.4); the
content hash is still stored and still checked, now only to reject a
resume attempt whose file content doesn't match the batch being resumed. All
affected tests were rewritten, not just patched, since the underlying
contract changed. The same checkpoint required consistent latest-score tie-breaks,
unscored-last sorting, and a distinction between historical assessment and
current readiness. The final implementation uses all four version fields
and `created_at DESC, id DESC`, keeps unscored leads last in both sort
directions, recomputes readiness on every endpoint, and returns the stored
assessment separately as `at_scoring`.


## Phase 4 finish pass: re-checking the first completion claim

A first session reported Phase 4 complete (251 backend + 7 frontend tests,
5,000 leads scored). A second pass re-inspected that code against the brief
rather than trusting the report and found problems the existing tests didn't
cover:
- stale readiness in the bulk listing;
- version-filter drift;
- draft timestamp tie-breaks;
- a crash-unsafe CSV upload state;
- the lead page repainting the previous lead when an action finished after
  navigation;
- duplicated history rows after a post-action refresh;
- per-lead legacy score requests on the batch page;
- a model/migration index mismatch that `alembic check` flagged.

Each fix came with a test. Where practical, the test was also run against
the old code to show it fails there. The details, and the explicit
"no longer true" list, are in `phase4-scoring-handoff.md` §7. The earlier
sort-direction item was already fixed in the checkpoint code; it is now
also enforced dialect-independently (an explicit `IS NULL` sort key) and
tested page by page on Postgres as well as SQLite.

## Phase 4 independent verification fixes

Starting branch: `worktree-phase4-finish`, commit `4915e63`. The rubric and
migrations are preserved. Evidence and its database-access boundary are recorded in
`phase4-scoring-handoff.md` §13. The user accepted Phase 4 on 2026-09-23
and instructed Phase 5 to begin; no additional real run is asserted.

| Decision | Reason |
|---|---|
| Fit persistence is shared through `app/services/fit_scoring.py`; the CLI no longer imports an API router | Restores the implementation contract's service ownership of database writes. |
| Each new fit row emits `lead_fit_scored` in the same savepoint | Restores the required audit trail. Dry runs and unchanged skips emit nothing; metrics count distinct leads, independently of audit history. Existing scores receive no invented retrospective events. |
| Establish SQLite's outer transaction before score savepoints; count batch commit failures only after rollback | Reproduced a CLI chunk reported failed while its score rows survived rollback, and an API chunk failure that aborted the whole run. Tests now check stored rows and successful retry. |
| Navigation invalidates old batch actions and resets lead action state; each history request also has an identity | Reproduced stale batch repaint, a stuck approval button, and a late history page overwriting refreshed history. Retry retains the failed request's exact offset. |
| Frontend CI runs the existing interaction suite | Component behavior is an acceptance gate; typecheck/build alone cannot verify clicks or response ordering. No new dependency. |
| Phase 4 accepted on the recorded Claude Code verification | On 2026-09-23 the user directed us to accept the completed phase and begin Phase 5. Database results retain their original attribution; this is acceptance of existing evidence, not a claim that another real run occurred here. |

## Decisions made in Phase 5 (seller-profile foundation)

| Decision | Reason / status |
|---|---|
| Keep the agreed single global, versioned seller profile | No per-campaign or multi-workspace redesign. Each changed save creates a new immutable draft revision. |
| Collect actual seller content before activating grounded generation | The offer, target customers and supportable claims are business inputs, not facts to invent from PDL. The draft editor is usable now; activation and prompt integration remain Phase 5 work after content review. |
| Add only `seller_profiles` in migration `0007_seller_profiles` | Existing score/output tables and migrations 0001–0006 stay intact. Migration testing uses a backed-up/restored synthetic SQLite fixture; no real database migration or PostgreSQL verification is claimed here. |
| Require `expected_version` when saving, backed by a unique version constraint | Stale changes return 409 and preserve the editor's text. An identical retry of the latest save reuses its revision and does not duplicate audit events. |
| Save a profile draft and its `seller_profile_draft_saved` event in one transaction | No partly saved profile without its audit record. `local-demo-unauthenticated` accurately labels the existing app's lack of authentication. |
| Proof points include a claim and source/reference; absence is allowed | Unsupported evidence is not manufactured. Saved source text is user-supplied provenance, not independent verification of its truth. |
| Draft content does not activate readiness, alter fit criteria, or replace the existing generator | A draft is not an approved seller profile. The Phase 4 rubric remains `demo-us-sectors-v1`; future scoring changes need an explicit versioned rubric decision. |
| Reuse existing SQLAlchemy, Alembic, React, Vitest and shared pagination | No new dependency. The editor distinguishes empty state from failed loading, preserves failed-save input, and exposes read-only paginated history. |

## Decisions made in Phase 5 (activation and grounded generation)

| Decision | Reason / status |
|---|---|
| Activation is a separate, explicit, append-only record (`seller_profile_activations`, migration `0008_seller_activation`) | Saving a draft can never change what generation uses. The highest `sequence` row is current, a NULL profile id records a deactivation, and history is kept. It requires `confirm_reviewed: true` plus the `expected_activation_sequence` the operator saw (409 on mismatch, database-unique `sequence` for races). The event `seller_profile_activated`/`_deactivated` is written in the same transaction. The actor stays `local-demo-unauthenticated`: the confirmation is a statement, not a verified approval. |
| A demonstration revision needs a separate `acknowledge_demo`, and readiness flags it (`seller_profile_is_demonstration`) | A demo profile is never activated by accident or treated as a real offer. Nothing was activated on the real database. |
| Outreach without an active revision is refused (409); summaries work without one | A draft pitching nothing, or a hardcoded pitch, would be a misleading success record. Summaries describe only the lead record. |
| Resolve the seller revision once per request and copy its immutable content | An activation mid-request cannot change what an output used or records (mutation-checked test). |
| Record exact provenance in four new nullable `ai_outputs` columns, plus the existing prompt/schema/model/input-hash fields | Queryable for readiness (`draft_not_from_active_seller_profile`) and for the UI. Old outputs keep NULL and their recorded `v1` versions and are never relabeled or backfilled. |
| Grounded context `grounded-context-v1`, `PROMPT_VERSION = "grounded-v2"`, `OUTPUT_SCHEMA_VERSION = "v2"`, mock `mock-deterministic-v2-grounded` | Lead facts have ids and origins. Buying intent, budget, problems and product usage are always unknown. Fit is included only with an explicit "not evidence of interest" note. Previously generated content is excluded; v1 fed the stored summary into outreach, and that was removed. |
| Validate before saving: a versioned pydantic schema plus deterministic reference, contact and figure checks | Invalid output saves no `AIOutput`, only an `ai_generation_rejected` event with fixed reason codes, and returns 502. Figures must appear in an approved claim or a structured lead field, never only in free text. This is a guard, not a truth verifier; human review is still required. |
| Real-mode prompts fence the context as `<untrusted_data>` and JSON-escape `<`/`>` | Imported text is data, not instructions, and cannot close the fence. |
| Provider exceptions become `AIProviderError` naming only the exception class; unconfigured real mode returns 503 before any call | Provider messages can embed keys or request details, and they are never forwarded (tested with a key in the message). |
| The standalone demo passes a built-in, labeled GTMFlow demonstration profile per call | The demo still works with no paid service and no saved profile, and it never activates anything. Its drafts are labeled `demo` and begin with a demonstration notice. |
| Keep allowing generation for currently excluded leads | Generation changes no status, and every push path still refuses them. Two Phase 3 regression tests depend on this. The draft stores `restrictions_at_generation`. Refusing outright is a possible later decision. |
| Readiness reads the active profile live; the fit rubric and stored scores are untouched | `compute_readiness` gains seller-state inputs with restrictive defaults. No scoring constant or criterion changed, and historical `at_scoring` snapshots are not rewritten (tested). |
| No new dependency | Existing FastAPI, SQLAlchemy, Alembic, pydantic, React and Vitest only. |

## Decisions made in Phase 6 (review, correction, annotation)

| Decision | Reason / status |
|---|---|
| One shared review state (`app/services/draft_review.py`) used by review endpoints, readiness and Slack delivery | Readiness and delivery can't disagree. An approval authorizes delivery only while it names the current draft's exact content hash, the draft is grounded, its seller is still in force, and its recomputed `input_hash` is unchanged. Otherwise ordered blocker codes are returned. |
| Reviews carry the displayed `content_hash`; review requests reject extra fields | They prove what was seen; a posted reviewer name is refused. The label stays `local-demo-unauthenticated`. Pre-Phase-6 reviews (no hash) are kept but authorize nothing. |
| A rejection requires a reason (API and UI) | Required by the Phase 6 brief. The old "reject without reason" test now asserts 422. `window.prompt` replaced by an inline form. |
| Human edits are new immutable `ai_outputs` rows (`origin="human_edited"`, `parent_output_id`, `author_label`, copied input/seller provenance), validated like model output | The model response is never overwritten. An edit supersedes the draft and needs its own review. Identical retries are idempotent. |
| Delivery requires an applicable approval at the shared dispatch point; the incomplete-import check also moved there | Every route (single, batch, demo) inherits it. `force` bypasses only the Hot score threshold. On the batch route it also keeps its existing, documented "re-push already delivered" meaning, a deduplication rule left to Phase 11. |
| `internal_slack_handoff` readiness now requires a current approval | Mirrors enforcement. Historical `at_scoring` snapshots unchanged. |
| `ai_outputs.purpose` (`operational` / `annotation`) instead of a separate candidate table | Annotation candidates reuse generation and provenance but never become a lead's draft, can't be approved for delivery, and are excluded from operational queries. |
| Training annotations in their own append-only table with a client `submission_id` | Kept separate from operational `AIOutputReview` (the unused `training_annotation` review kind stays unused). Latest row per candidate wins; retries are idempotent. Accept requires "fully supported"; skip requires a reason. |
| Split isolation by union-find groups (identity, domain, LinkedIn, normalized name) with a seeded hash split 4:1:1; a manifest version is frozen once | Over-grouping is the safe failure; identities are never merged. Group keys use provider record ids, so the manifest is reproducible across databases (verified: real and restored-copy digests identical). |
| Pilot: 50 training companies × 2 tasks, one company per group, segments alternated, seeded order, excluded leads skipped | Keeps a company's tasks together, uses only training groups, and reports examples and unique companies separately. |
| Candidates are generated only on explicit request with the configured provider named; nothing is pre-generated on the real database | Provider choice (labeled mock vs paid real model) and demo-profile activation are the user's decisions. |
| GTMFlow demonstration profile saved as a draft on the real database, not activated | Preparation only. Activation needs the user's explicit confirmation. Capabilities now include human review and Slack routing; no proof points. |
| Review timing measured only in the browser (active / hidden / idle / wall, flags) and validated server-side | No inference from timestamps; sessions without interaction are flagged `incomplete`. |
| Split manifest file git-ignored under `backend/data/manifests/` | Same rule as other derived cohort outputs. The database copy is authoritative; the digest is in the handoff. |
| Migration `0009_review_annotation`, additive; explicit short FK name | The first restored-copy upgrade failed on Postgres's 63-character identifier limit (rolled back cleanly). Fixed before touching the real database. |

## Decisions made in Phase 6 (mixed human/AI review pilot, 2026-09-24)

| Decision | Reason / status |
|---|---|
| Phase 6 changes, at the user's instruction, from "100 human-reviewed pilot examples" to a mixed human/AI review pilot | Human reviews of #1–#6 are kept exactly. The assistant reviewed the other 94. **The original 100-human-review requirement is recorded as not met.** |
| AI reviews are stored in a separate export (`ai-review-export-v1`), never through the human annotation path | The annotation API records the unauthenticated operator label and UI timing as a human review, so writing AI reviews there would falsify provenance. No schema change was needed, and the frozen splits and human data are untouched. |
| Every AI review is pinned to the output id and content hash read; corrected targets must pass the v2 validator against the recorded input snapshot | Traceable to the exact source and consistent with the same grounding rules as generation and human edits. The build refuses mismatched pins and any human-reviewed candidate. |
| AI review rubric `ai-review-rubric-v1` is derived from the user's own decisions | Corrections #4 and #6 set the outreach standard: an explicit portfolio-demonstration / not-a-commercial-offer label, record facts only, no invented needs, research, praise, specialization, results, placeholders or contacts. Acceptances #1, #3 and #5 allow "N-M employees" and "small". Invented-need hypotheses are removed even when labeled unconfirmed. `seller_relevance` must not assert the lead's need or relevance as fact. |
| Doubtful source records are flagged `uncertain` (26) rather than skipped | The output was judged against the record. Whether the record itself is wrong needs outside knowledge and a human. The flags list is the optional human spot-check set. |
| Decision files and the review tool are committed; the export JSONL is git-ignored and rebuildable | Consistent with the rule that derived cohort data embedding PDL input snapshots is not committed. The judgments and corrected text remain in version control. |
| Human and AI totals are reported separately everywhere | Human: 6 reviewed (3 accepted, 2 corrected, 1 skipped). AI: 94 reviewed (30 accepted, 64 corrected, 0 skipped). They are never summed as "reviewed" without the source. |
| Phase 7 must merge both exports with `review_source` preserved and decide on weighting or deduplication of the templated outreach targets | The 47 corrected outreach targets share one structure; treating them as 47 independent writing examples would overstate diversity. |

## Decisions made in Phase 7 (dataset and baseline evaluation, 2026-09-24)

| Decision | Reason / status |
|---|---|
| The user accepted the mixed human/AI pilot (6 human + 94 AI reviews) in place of 100 human reviews | A recorded scope change. The original requirement stays recorded as not met, and most training targets are AI-reviewed (5 of 73 eligible are human-verified). |
| One export format, `gtmflow-sft-v1`, with `review_source`, `human_verified` and the full provenance on every row | Human and AI targets can be filtered or weighted separately later. Originals, corrections, ids, input hashes, seller revisions and prompt/schema/model versions are preserved. |
| The latest human decision takes precedence; skipped and stale human decisions are excluded; AI rows must match the stored output and input hashes | Human judgment is never overridden by an AI row, and a stale or mismatched AI row can't reach the dataset. |
| The 26 uncertain AI-reviewed examples go to a separate `flagged-uncertain.jsonl`, excluded from `eligible.jsonl` until a human resolves them | They question the source record, not the output. No company fact is repaired. A confirmed-plausible record moves in; a wrong record is dropped, not rewritten (training on facts absent from the input would teach invention). |
| Dedup policy `structure-cap-v1`: drop exact duplicates (same target hash, or same task and input hash); weight repeated structures by `min(1, 5 / group size)` | Measured on the pilot: 0 exact duplicates; one outreach template covers 32 examples. Weighting avoids inflating diversity without deleting, copying or inventing examples. Counts and effective counts are reported side by side. |
| Pilot examples stay in train; held-out queues `validation-v1` and `test-v1` are drawn only from their own frozen splits with their own seeds | `company-groups-v1` is not reassigned. The leakage check verifies every example against the manifest, groups spanning splits, and train groups appearing in held-out queues. |
| Seller provenance is all-or-none and must match the stored output | Pilot #1 was generated before a seller profile was active. It legitimately has no seller fields and no seller block in its input, so it is not a provenance error. |
| Evaluation metrics `baseline-metrics-v1`: validator pass, rubric lints, exact match, token F1, ROUGE-L; results are labeled in-sample or held-out by split | Deterministic and free to rerun. In-sample numbers are descriptive only. Reference metrics are anchored on the reviewed outputs, a known bias. |
| The test suite forces the mock provider, no key and no webhook, and refuses to run otherwise | After 2 unintended real calls (about $0.0008) when pytest loaded the developer `.env` (commit `9fda368`). |
| Derived datasets are git-ignored under `backend/data/datasets/`; the manifest records file hashes | They embed PDL input snapshots. The build is reproducible: a second build gave a byte-identical manifest. |

## Decisions made in Phase 7 (held-out generation, review and baseline, 2026-09-24)

| Decision | Reason / status |
|---|---|
| Held-out criteria `heldout-criteria-v1` frozen in code (`app/evaluation/criteria.py`) and digest-pinned in a test, committed before any held-out generation or review | Nothing can drift toward the results. `evaluate` records the id and digest in every result and refuses a held-out "source" evaluation whose predictions were not produced by the frozen system (gpt-4o-mini, `grounded-v2`, schema v2). |
| Paid generation goes through a budgeted runner with a durable ledger and worst-case reservation | The cap holds even for a pathological call. Attempts and spend survive restarts. One retry per candidate, in a second pass. Stops on provider/config errors and after 3 consecutive failures. |
| The test suite also blocks non-loopback DNS and connections | Defense in depth beyond forcing mock settings. Verified with a real client and a fake key, and with a full suite run under hostile environment variables. No live call was used to verify it. |
| The held-out candidates are AI-reviewed with the unchanged `ai-review-rubric-v1`, stored as separate AI exports, and keep the original predictions | Predictions and reference targets stay separate objects. Evaluation scores the original predictions against the references, never a target against itself. |
| Held-out results are labeled "AI-derived reference targets; not human-verified quality" | Every held-out reference is AI-reviewed. Human verification of at least the test set remains a Phase 7 requirement. |
| Uncertain held-out examples are excluded from the headline numbers and evaluated separately | Same policy as training: doubtful records are flagged, never repaired. A value an output had altered is restored to the recorded value. |
| Dataset manifests record `allowed_use`; only all-train datasets may be used for training, and test is also excluded from prompt or rubric tuning | Keeps validation and test out of training and tuning by construction. |
| The training target stays at 400; expansion (about 450 train candidates, about $0.21) and a minimal-edit training rubric are proposed, not started | 73 eligible examples today. Templated outreach corrections add almost no effective examples, so diversity needs a rubric change or human-written examples, not just volume. |

## Decisions made in Phase 7 (training expansion and reconciliation, 2026-09-25)

| Decision | Reason / status |
|---|---|
| Training expansion via a new queue `train-v2` (520 candidates, 260 companies; first wave 450, reserves 70) from the frozen train split, excluding every group already in any queue | Authorized: 500 attempts / $0.30. Actual: 473 attempts, $0.2167. The target was met from wave 1; reserves were not generated. |
| New correction policy `ai-review-rubric-v2-train` (minimal edit), for training queues only | Designed from training evidence only (pilot outputs and findings, human #4/#6). v1 template corrections left 37 pilot outreach examples worth 15.9 effective. v2 keeps a median 0.72 ROUGE-L of each original versus 0.20 for v1. Refused on held-out queues. Gated by validator plus lints. |
| The required demonstration sentence rotates across 4 fixed phrasings; everything else comes from the original | The label is mandatory; its wording is not presented as organic diversity. |
| Held-out references stay under v1 and are frozen, as are predictions, criteria and assignments | A different policy for training and evaluation is documented. Overlap metrics against v1 references may understate v2-trained models; lints and acceptance remain the primary outreach metrics. |
| You accepted AI-reviewed validation/test references for this experimental version; independent human validation is deferred | All held-out results are relabeled "AI-evaluated". The re-run produced identical metrics (0 differences). No claim of human-verified quality. |
| Held-out sizes are reported as they are: validation 74, test 71 (not 100) | Generation failures, unresolved candidates and uncertain exclusions are preserved in `phase7-heldout-evaluation-record.json`. |
| Uncertain records stay excluded unless resolved with evidence; flags are never removed to reach a count | 419 eligible was reached with all 180 flags in place. Where an output had altered a recorded value, the recorded value was restored and the record flagged. |
| Near-duplicate similarity (Jaccard of fact-masked token sets) is reported alongside exact structure groups, without changing weights | v2 outreach: 0 pairs ≥ 0.8. Weights stay `structure-cap-v1` (effective 397.9 of 419 raw). |
| Correction: the second-checkpoint docs said "16" held-out evaluation runs; the correct count is 8 | Fixed in the handoff with a note. |

## Dependencies later phases will need

- ~~**Phase 2**: a migration tool.~~ **Resolved**: Alembic, installed (see table above).
- ~~**Phase 3**: column mapping / streaming reads.~~ **Resolved**: implemented entirely with the Python standard library (`gzip`, `json`, `csv`) — no `pandas`/`pyarrow`/`ijson` needed, since the source turned out to be JSON Lines, not a giant JSON array or Parquet.
- **Phase 8**: a LoRA/PEFT training stack (e.g., `peft`, `transformers`, `bitsandbytes` or equivalent) and a base model choice. None of this exists in `requirements.txt` today. Requires GPU access and the user's explicit go-ahead before any run (per contract rule 7) — this is compute spend, not a research decision to make unilaterally.
- **Phase 10**: a background job system. `docs/architecture.md`'s roadmap already names Celery or Arq as candidates; no decision has been made yet.

## Unresolved questions for the user

Resolved since Phase 2 (moved out of this list): PDL curation criteria (fixed rules supplied and implemented, zero shortfall), blocked-status-survives-review regression (found and fixed), approval_rate fix status (corrected to "partial"). Resolved in Phase 4: scoring model redesign scope (implemented as `demo-us-sectors-v1`, see phase4-scoring-handoff.md — point allocations and band thresholds were specified in the Phase 4 brief itself, not left to be improvised).

Still open, listed in the order they'll come up:

1. **The seller/service profile content (Phase 5's remaining input).** The mechanism is complete: drafts, explicit activation, grounded generation and provenance, with migrations applied to the real database. No real profile exists, so `no_seller_profile_configured` still fires on all 5,000 real leads. Still needed from the user:
   - seller company and product names, value proposition, and target customers (segments, geography, size ranges, roles);
   - capabilities, sourced proof points, and exclusions;
   - who reviews and activates the revision;
   - a contact source if outbound email is the goal.

   Alternatively, an explicit decision to use the labeled GTMFlow demonstration profile. See `phase5-generation-handoff.md` §8. Whether the size criterion should become active is a separate versioned-rubric decision.
2. **Training data for Phases 6–9.** The annotation workflow now exists (Phase 6). The frozen split manifest `company-groups-v1` and the 100-candidate `pilot-v1` queue are on the real database, with 0 reviewed. Still open:
   - who reviews, and over what time frame;
   - which candidate provider: labeled mock or a paid real model (needs go-ahead);
   - whether to activate the GTMFlow demonstration profile for outreach candidates.

   See `phase6-review-handoff.md` §8.
3. **Concurrent-push idempotency mechanism for Phase 11** (D.3) — a DB-level unique constraint / row lock vs. an application-level idempotency key vs. a queue-based dedup once Phase 10's background jobs exist. Not decided; explicitly deferred per the user's instruction to keep delivery reliability in Phase 11.
4. **Company-identity resolution/merge strategy for a future second PDL snapshot.** Phase 3 imported exactly one snapshot, so no two `Lead` rows have ever needed to be resolved to the same `CompanyIdentity`. The matching strategy (fuzzy name match? domain + locality heuristic? manual review queue?) is still undesigned — flagging now since it wasn't yet a live question with only one snapshot in the database, but will be the moment a second PDL pull happens.
5. **Whether `outreach_rejected` should become a real hard routing-eligibility exclusion**, not just a v2 readiness gap. Verified in Phase 4 that the current Slack-push pipeline does not treat it as a hard block (`BLOCKED_STATUSES` excludes it); left unchanged since modifying that enforcement was out of Phase 4's scope, but flagging since the Phase 4 brief's own wording assumed it already was one.

## Schema relationships added in Phase 2 (Part C + D)

```
SourceSnapshot (1) ──── (N) ImportRun
SourceSnapshot (1) ──── (N) CompanyIdentity
SourceSnapshot (1) ──── (N) Lead            [nullable FK; NULL for CSV/demo leads]
ImportRun       (1) ──── (N) Lead            [nullable FK; NULL for CSV/demo leads]
CompanyIdentity (1) ──── (N) Lead            [nullable FK; NULL until Phase 3 resolves it]

AIOutput (1) ──── (0..1) AIOutput            [parent_output_id, self-referential;
                                               a human_edited row points at the
                                               generated row it corrects]
AIOutput (1) ──── (N) AIOutputReview         [ai_output_id nullable -- NULL only
                                               for legacy_unlinked backfilled rows]
Lead     (1) ──── (N) AIOutputReview
```

`CompanyIdentity.website_domain` is indexed but **not** unique, deliberately (see the model docstrings in `app/models/company_identity.py`) — businesses can share a domain. `CompanyIdentity.source_record_id` and `Lead.source_record_id` carry a real uniqueness constraint, but scoped to `(source_snapshot_id, source_record_id)` together (migration `0003_pdl_identity`), not on `source_record_id` alone — this is the idempotent-reimport guarantee (Part D.7), not a claim that PDL ids are globally stable; the same PDL id in a *different* future snapshot is a distinct, allowed row. `CompanyIdentity.identity_confidence` (`"source_id"` | `"fingerprint"`) was also added in Phase 3. **Phase 3 closeout addition**: `ImportRun.logical_key` (added in `0003`, indexed but not unique -- multiple attempts legitimately share one) is joined by `LeadBatch.logical_key` (added in `0004_logical_key`, and here it IS uniquely constrained -- this is the real "same selection converges to the same batch" guarantee, replacing an original name-prefix-matching approach that had no database backing at all; see the "Phase 3 closeout" section above). Resolution/merge logic across *different* snapshots remains undesigned (see the open questions above) — only one snapshot has ever been imported, so it was never exercised.

## Backup and recovery (Part B.8)

- **Before running any migration against a database with real data**: take a full backup first. For managed Postgres (Railway, RDS, etc.) use the provider's snapshot/backup feature; for self-hosted, `pg_dump`. This was not automated as part of Phase 2 — there is no backup step baked into the Alembic workflow, and there shouldn't be one that runs silently (a backup step a human doesn't know ran is not a safety net).
- **Upgrading (`alembic upgrade head`) is additive and safe to re-run.** Verified in this session: re-running `alembic upgrade head` against an already-current database is a no-op (confirmed via a disposable Postgres container — see the Phase 2 handoff). `0002`'s new columns are nullable or carry a server-default (`ai_outputs.origin`), so applying it against a populated `leads`/`ai_outputs` table doesn't fail or lose existing rows — verified against seeded legacy data.
- **Downgrading (`alembic downgrade`) is destructive and was intentionally documented as such directly in the migration file** (`alembic/versions/0002_provenance_and_review_identity.py`'s `downgrade()` docstring), not just here: it drops every provenance/identity column and the entire `ai_output_reviews` table, including any real reviews or provenance data written after the migration ran, not just the backfilled legacy data. **Never run `alembic downgrade` against a database that has accumulated real Phase 3+ data without a fresh backup taken immediately before.** This was verified to *execute* correctly (schema-only, against disposable/empty-of-new-data databases), but was never run against a populated database in this session, consistent with the instruction not to test destructive operations against a real working database.
- **If `0001_baseline` itself needs to be "undone"** (i.e. drop the entire application schema), its `downgrade()` is standard `drop_table` calls — also destructive, also never run against anything but a disposable database in this session.
- **Phase 3 update**: migration `0003_pdl_identity` is additive (new columns + new unique constraints on existing, mostly-NULL-for-old-rows columns) and was verified safe to apply against the Phase 2 disposable-DB baseline with no data loss. Its `downgrade()` drops the new columns/constraints only — it does not touch `ai_output_reviews`, `source_snapshots`, `import_runs`, or `company_identities` rows themselves, so it's less destructive than `0002`'s downgrade, but still never run against the real, persistent Phase 3 database (that database was only ever upgraded, never downgraded).
- **The real Phase 3 data lives in a *persistent* Postgres container** (`gtmflow-dev-postgres`, named Docker volume `gtmflow-dev-postgres-data`), deliberately distinct from Phase 2's disposable verification container (which was removed at the end of Phase 2, as intended). Nothing in Phase 3 or its closeout deleted or downgraded this database — see phase3-data-handoff.md §7 for exactly what's in it and how to reconnect to it.
- **Phase 3 closeout addition — an actual backup was taken and its restorability verified, not just described as a good idea**: before migration `0004_logical_key` or any other closeout change touched the real database, `pg_dump -Fc` produced `backend/.backups/gtmflow_pre_closeout_<timestamp>.dump` (git-ignored). Restoration was verified into a separate disposable database (`gtmflow_restore_verify`, same Postgres instance, never the real one) via `pg_restore --clean --if-exists`, exit code 0, row counts matching the real database exactly at that point (5,000/5,000/1/1/3). Migration `0004` (including its data-backfill step) and the concurrent-writes race test were both run against that disposable restored copy first, and only applied to the real database after being proven safe there — per the explicit instruction to check schema changes on a restored copy before touching the populated database.
- **Migration `0004_logical_key`'s data backfill is the one step in this phase's migrations that reads and depends on existing data**, not just schema shape — see its own docstring and `phase3-data-handoff.md` §5 for exactly what it does and the ambiguity-detection that makes it abort (rather than guess) if it can't derive an existing batch's `logical_key` unambiguously. It was exercised for real, not just in the abstract: the real database's one existing PDL batch was successfully backfilled, and the resulting key was independently cross-checked against a fresh `compute_logical_key()` call using the same inputs.

## The seller/service profile requirement (F.4 in the audit, detailed here)

**Status (Phase 5):** resolved in code. The hardcoded pitch was removed. Outreach is generated from an explicitly activated seller revision through a grounded context (`app/ai/grounding.py`), and every output records the revision it used. What remains is the actual seller content (see the unresolved questions above). The original problem statement is kept below for history.

**Problem, restated precisely (pre-Phase 5):** `MockAIClient.generate_outreach` (`backend/app/ai/mock_client.py:243-247`) hardcoded the pitch as GTMFlow itself — "GTMFlow turns lead lists into prioritized outreach..." There is no model, config field, or prompt input anywhere in the codebase that says what a *deployment* of this tool is meant to be selling on behalf of an actual seller. This is fine for a portfolio demo where the tool is pitching itself, but it cannot produce meaningful outreach for a real company's product without this.

**What's needed, at minimum**, for Phase 5 to be attemptable:

- A `SellerProfile` (or similarly named) concept: company name, one-paragraph value proposition, target ICP description, and a short list of concrete proof points / capabilities the outreach is allowed to reference (mirroring the "evidence vs. inference" discipline already used for lead facts in `mock_client.py` and `prompts.py`'s `SYSTEM_RULES`).
- This profile needs to be injected into `_build_lead_context` (`app/services/ai_generation.py:22-46`) alongside the existing lead facts, and the prompt rules in `app/ai/prompts.py` need updating so the model is told explicitly "here is what you're selling" rather than being asked to write outreach with nothing to pitch.
- Scope is already agreed: one versioned seller profile for the single-workspace app. Phase 5 needs its actual content; per-batch/per-campaign configuration is not required.

**Demonstration assumption (label, don't treat as a requirement):** for continuing to demo GTMFlow-as-the-product-being-sold, Phase 5 replaced the hardcoded pitch with an explicitly labeled built-in demonstration profile. The standalone demo passes it per call, and the seller page offers it as a template that must be saved and explicitly activated with a demonstration acknowledgement. The requirement above only applies once this tool is meant to generate outreach for an actual different seller's product — which is implied by pairing it with a real external company dataset (PDL) rather than only the synthetic demo CSV.

## Demonstration assumptions to keep separate from real requirements

- `sample_data/leads_sample.csv` and the `/demo` endpoint's embedded 10-row CSV are explicitly synthetic/fictional (confirmed: `sample_data/README.md:3` states this outright). Nothing about their specific companies, scores, or content should inform scoring-model or prompt design beyond "this is the shape of a CSV row."
- The 5-minutes-per-lead time-saved constant (`MINUTES_SAVED_PER_PROCESSED_LEAD`) is explicitly a portfolio placeholder per `docs/metrics.md:92` and the audit's E.4 — it is not a target to preserve or a real measurement to build on.
- The "2 Hot / 4 Warm / 4 Cold" tuning of the sample CSV (`sample_data/README.md:9-17`) is deliberately shaped for a 30-second demo narrative, not a realistic distribution of a real lead list or the PDL dataset. Don't use it as a sanity check for Phase 4's rescoring work — a real distribution (especially of company-only PDL data, per B.2) will look very different, skewed heavily toward Cold under the current model.
- `docs/demo-script.md` and `docs/interview-notes.md` exist to support presenting this as a portfolio project; they describe intended narrative, not functional requirements, and weren't treated as such in this audit.
