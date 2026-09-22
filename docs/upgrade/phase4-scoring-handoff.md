# Phase 4 handoff: company fit, evidence coverage, outreach readiness, routing eligibility

Last updated: 2026-09-22 (Phase 4 finish pass). **Stopped before Phase 5.**

Phase 4 added a new, independent, versioned scoring engine (v2) alongside
the unchanged legacy scorer (v1, `app/scoring/lead_scoring.py`), wired it
into the API and the batch / lead / metrics pages, and closed the Phase 3
items carried into this phase.

> **How this document came to be.** Two sessions worked on Phase 4 on
> 2026-09-22. A background session (`e3f3881d`, no worktree isolation,
> directly in the main checkout) finished at 22:02 UTC, including the real
> 5,000-lead scoring run at 21:56 UTC, and wrote a first version of this
> handoff. A second session (branch `worktree-phase4-finish`) started from
> the same checkpoint code, re-inspected it against the brief, found and
> fixed the gaps listed in §7, re-verified the real run with the final
> code, and rewrote this file. Claims below are the final, re-verified
> state; where the first version said something that is no longer true,
> §7 says so explicitly.

## 1. The demonstration rubric: `demo-us-sectors-v1`

Defined in `backend/app/scoring/fit.py`; served read-only by
`GET /api/scoring/fit-profile`.

| Identifier | Value |
|---|---|
| `scorer_version` | `fit-scorer-v1` |
| `profile_id` / `profile_version` | `demo-us-sectors-v1` / `1` |
| `normalization_version` | `fit-norm-v1` |

| Criterion | Points | Rule | Active |
|---|---|---|---|
| Industry | 60 | Exact match (lowercased, whitespace-collapsed) of `lead.industry` against `hospital & health care`, `medical practice`, `real estate` | yes |
| Country | 40 | Exact match of the **structured** `lead.cleaned_data["country"]` against `united states`; free-text `location` is never parsed | yes |
| Company size | 0 | No size preference configured (Phase 5); reported as `not_configured` | no |
| Website, contact details, founded year, company-name keywords, dataset provider | 0 | Declared zero-weight (`ZERO_WEIGHT_CRITERIA`) | no |

- **Unknown earns nothing and nothing is renormalized.** A missing input
  is `unknown`, scores 0, and the maximum stays 100.
- **Evidence coverage** is kept separate from fit: the share of *active*
  weight (industry + country = 100) whose input was interpretable. A
  known mismatch counts as evidence; only `unknown` withholds it.
- **Bands:** `strong_match` ≥ 80, `partial_match` ≥ 50, `weak_match`
  below that. Coverage **below 80%** gives `insufficient_evidence`
  regardless of points (the numeric fit score is still reported).
- Any change to weights, values or thresholds is a new profile version.

## 2. The four outputs, and which are current vs. historical

1. **Company fit** (0–100) with a per-criterion record: weight, source
   field, raw and normalized input, result
   (`match`/`mismatch`/`unknown`/`not_configured`), points, explanation.
2. **Evidence coverage** (see above).
3. **Outreach readiness**, separately for two actions:
   - `outbound_email` gaps: `no_seller_profile_configured` (always, until
     Phase 5), `missing_contact_email`, `no_outreach_draft`,
     `draft_not_reviewed`, `draft_rejected`, `lead_excluded_from_routing`;
   - `internal_slack_handoff` gaps: `lead_excluded_from_routing` only.
4. **Routing eligibility.** The hard exclusions the push pipeline
   actually enforces: lead status in `DISQUALIFIED_STATUSES`
   (`do_not_contact`, `disqualified`, `unsubscribed`), or the lead's batch
   in `INCOMPLETE_BATCH_STATUSES` (`partial`, `uploading`; see §5). It has
   no score or `force` input.

**Fit, coverage, band and criteria are stored** (append-only
`lead_fit_scores`, one new row per scoring). **Readiness and eligibility
are always recomputed from live state on every read**, on every endpoint
(single lead, batch listing, batch readiness): `readiness_is_current` is
always `true`. The snapshot stored with the row is returned separately as
`at_scoring`, labeled historical in the UI, and never gates anything.

## 3. Consistent "current score" selection (`app/services/fit_queries.py`)

Every reader (`GET /api/leads/{id}/fit-score`, `GET
/api/batches/{id}/fit-scores`, `GET /api/batches/{id}/fit-summary`,
`GET /api/leads` sort/filter, the metrics dashboard, the scoring CLI's
skip-unchanged check, `scripts/phase4_verify_stored_fit.py`) uses one
definition:

- **Applicable row:** all four of `scorer_version`, `profile_id`,
  `profile_version` and `normalization_version` equal the current
  constants. Rows from any other version stay as history and are never
  current, so a lead scored only under an old version reads as unscored.
- **Latest:** `created_at DESC, id DESC`, with `id` as the unique
  tie-breaker for equal timestamps. The same rule is now used for "current
  outreach draft" (`GET latest-ai-output`, the approve/reject supersession
  check, and readiness) and for "latest review", so all three agree on
  which draft is current even when timestamps tie.
- **`GET /api/leads` sorting:** unscored leads sort last in both
  `fit_score_desc` and `fit_score_asc`. This uses an explicit
  `fit_score IS NULL` sort key, not dialect-dependent `NULLS` defaults.
  Ties fall back to `created_at DESC, id DESC`. Filtering (`fit_band`,
  which is validated, and `min_fit_score`) and sorting run in SQL before
  `LIMIT/OFFSET`, and totals count unique leads.
- **Distinct counting:** batch summaries and the dashboard count each lead
  once, in the band of its latest applicable row. `score_rows` (history
  included) is reported beside `scored_leads` so rescoring is visible
  without inflating anything.

## 4. Application integration

**API (additive):**

| Endpoint | Purpose |
|---|---|
| `POST /api/leads/{id}/fit-score` | score + persist one lead |
| `GET /api/leads/{id}/fit-score` | latest applicable row + current readiness/eligibility + `at_scoring` |
| `GET /api/leads/{id}/readiness` | current readiness/eligibility, scored or not |
| `POST /api/batches/{id}/fit-score` | score the batch in chunks of 500, one transaction per chunk; skips leads whose inputs are unchanged; returns run counts plus the distinct-lead `summary` |
| `GET /api/batches/{id}/fit-summary` | distinct-lead band counts, unscored count, `score_rows` |
| `GET /api/batches/{id}/fit-scores` | one page of the batch's leads → their current fit rows (bulk, fixed number of queries per page) |
| `GET /api/batches/{id}/readiness` | one page of the batch's leads → current readiness/eligibility for **every** lead |
| `GET /api/batches/{id}/scores` | one page → legacy v1 scores (replaces one request per lead) |
| `GET /api/scoring/fit-profile` | the rubric |
| `GET /api/metrics/dashboard` | now also `fit_scored_leads`, `fit_strong_match`, `fit_partial_match`, `fit_weak_match`, `fit_insufficient_evidence` (distinct leads) |

**UI:**
- **Batch page:** per page of leads, one request each for legacy scores,
  fit scores and current readiness, never one per lead. Legacy columns and
  actions are labeled "(v1)" / "legacy-Hot". The fit column shows band,
  score and coverage. "Routing (current)" shows the exclusion reasons;
  "Email readiness (current)" shows the gaps. A persistent distinct-lead
  fit summary is shown. Push is disabled for an incomplete batch and the
  batch status is re-checked right before pushing.
- **Lead page:** the legacy score is labeled "Legacy priority score (v1)".
  `FitScoreCard` shows fit, criteria, versions, and the collapsed
  `at_scoring` snapshot marked historical. A new **Current readiness &
  eligibility** card (live, shown for unscored leads too) is added. Push
  is disabled while the lead is excluded, and current eligibility is
  re-fetched at click time; nothing is sent if the lead has since become
  excluded.
- **Metrics page:** distinct-lead v2 fit counts in their own card; legacy
  widgets labeled "(legacy v1)".
- `strong_match` is never mapped to "Hot". Badges use a separate palette,
  and every v2 surface says it is a broad demonstration profile, not a
  purchase-probability or approval signal.

**No side effects.** Scoring (API, batch endpoint, CLI) inserts
`lead_fit_scores` rows only. It never clears a blocked disposition,
changes a batch's status, approves or generates a draft, or sends. This is
tested (`test_batch_scoring_has_no_side_effects_beyond_score_rows`, plus
the earlier blocked-lead and partial-batch tests) and verified on the real
database (§6).

## 5. CSV upload recovery (Phase 3 item carried forward)

- **Explicit retry identity.** A retry must pass `resume_batch_id`.
  Without it, a new batch is always created, even for byte-identical
  content, so "retry" and "deliberately upload again" are distinguishable
  by the caller. The stored `upload_content_hash` is only used to
  **reject** a changed file presented as the same retry (409). Resuming a
  batch that is neither `partial` nor `uploading` is rejected (409), as is
  a nonexistent batch (404).
- **Crash-safe incomplete state (new in the finish pass).** A new upload's
  batch row is committed first with status `uploading`, and is only
  replaced once the handler finishes (`uploaded`/`partial`/`failed`).
  Before this, a process dying after a chunk committed left the batch
  looking like a finished, routable `uploaded` batch that couldn't be
  resumed.
- **Resume skips what is actually committed.** It counts the committed
  rows (`COUNT(leads)`), not the stored `processed_leads` counter, which
  lags after a crash. Final `processed_leads` is also taken from the
  table.
- **One definition of incomplete.** `INCOMPLETE_BATCH_STATUSES` in
  `app/models/lead_batch.py` is checked by single push, batch push, v1
  batch scoring (which no longer overwrites the status) and v2
  eligibility.
- Tested on the actual HTTP path (`tests/test_csv_upload_interrupted_chunk.py`,
  every row its own committed chunk):
  - a handled failure after two committed chunks → `partial`;
  - a crash escaping the handler → `uploading`, with the stale counter
    (0) not trusted;
  - both states block push (single and batch, including `force`) and show
    as excluded;
  - an explicit resume finishes with each row exactly once and opens
    routing only then;
  - a second resume after completion → 409 with no duplicates;
  - an identical file without `resume_batch_id` → a separate batch.

  The crash test was run against the checkpoint's upload code and failed
  there, as intended.

## 6. Real scoring run (existing cohort, no download, no corpus rescan)

Database: persistent local Postgres `gtmflow-dev-postgres`
(`localhost:55433/gtmflow`), alembic head `0006_fit_scores`.

**Backup first:** `backend/.backups/gtmflow_pre_phase4_finish_20260922T2220Z.dump`
(`pg_dump -Fc`, exit 0). Restored into the disposable
`gtmflow_restore_verify` (`pg_restore` exit 0). The restored copy's
integrity snapshot (`scripts/phase4_db_snapshot.py`: row counts, statuses,
content digests of leads / identities / snapshots / batches / import runs)
was identical to the real database.

**Run history:**

| When (UTC) | Who / code | Command | Result |
|---|---|---|---|
| 21:56:26–21:56:37 | session `e3f3881d`, checkpoint code | dry run, then `python -m app.scoring.cli --chunk-size 500` | 5,000 rows written (one per lead) |
| ~22:20 | finish pass, final code | `--dry-run --rescore-unchanged --chunk-size 500` (recompute everything, write nothing) | exit 0; attempted 5,000, succeeded 5,000, failed 0 |
| ~22:21 | finish pass, final code | `--chunk-size 500` (real run) | exit 0; attempted 5,000, **failed 0**, newly written 0, **skipped unchanged 5,000** |
| ~22:21 | finish pass | `scripts/phase4_verify_stored_fit.py` | exit 0; 5,000 checked, 0 without a current row, **0 mismatches** (fingerprint, fit, band, coverage, every criterion) |

The final-code real run wrote nothing because every lead's current row
already had the identical input fingerprint; the verifier then confirmed
the stored results equal a fresh recompute field by field. Writing a
second identical generation (`--rescore-unchanged`) would only have
duplicated history.

**By segment (final-code dry run):**

| Segment | Attempted | Succeeded | Failed | Band |
|---|---|---|---|---|
| healthcare | 2,500 | 2,500 | 0 | 2,500 `strong_match` |
| real_estate | 2,500 | 2,500 | 0 | 2,500 `strong_match` |

Healthcare breaks down as `hospital & health care` 984 and
`medical practice` 1,516; all 5,000 have structured country
`united states`.

- **Fit:** 100 for all 5,000 (histogram `{100: 5000}`).
- **Coverage:** 100.0% for all 5,000.
- **Eligibility:** 5,000 `not_excluded`. Every lead is `new` and the one
  batch is `uploaded`.
- **Readiness:**
  - `outbound_email`: `not_ready` for 5,000; gaps
    `no_seller_profile_configured` 5,000, `missing_contact_email` 5,000
    (PDL has no contact fields by construction), `no_outreach_draft`
    5,000;
  - `internal_slack_handoff`: `ready` for 5,000.

**The uniform 100 is honest, not a defect.** The cohort was imported
using exactly these industry and country criteria. No variation was
manufactured. The profile validates the mechanism end to end but has no
discriminating power on this cohort until Phase 5 supplies real seller
criteria.

**Integrity after the runs:** the before/after snapshots are identical.
Tables: leads 5,000; company_identities 5,000; source_snapshots 1;
lead_batches 1 (`uploaded`, 5,000/5,000); import_runs 4;
lead_fit_scores 5,000 (5,000 distinct leads); lead_scores 0;
ai_outputs 0; ai_output_reviews 0; integration_pushes 0; workflow_events
40. Lead statuses: 5,000 `new`. No drafts, reviews, training, v1 score
rows or Slack sends were produced.

## 7. What the finish pass found and changed

Found by re-inspecting the checkpoint (and the first handoff's claims)
against the brief:

1. **Bulk readiness was stale.** `GET /api/batches/{id}/fit-scores`
   returned readiness stored at scoring time (`readiness_is_current=false`).
   It now recomputes readiness in bounded queries (latest drafts, then
   their latest reviews, per page) and returns the stored snapshot
   separately as `at_scoring`.
2. **Version filter drift.** The "current row" filters checked three
   version fields in some places and nothing checked
   `normalization_version`. Now there is one shared definition with all
   four (§3).
3. **Draft tie-breaks.** `latest-ai-output`, the supersession check and
   latest review ordered by `created_at` only. With tied timestamps a
   stale draft could be approved, or "latest" could differ between
   endpoints. Fixed and tested.
4. **Crash-unsafe CSV upload** (§5).
5. **Lead page repainted the old lead.** An action (approve, push,
   generate…) finishing after navigation re-ran the old lead's `load` and
   history reload closures, painting the previous lead's data and
   histories into the new lead's page. Now guarded by the navigation
   generation. The history hook also refuses fetches for a key that is no
   longer current.
6. **Post-action history refresh duplicated rows.** It "reloaded" at
   `offset = items.length`, which in a newest-first list re-fetches
   shifted rows as duplicates. It now uses a new `refresh()` (page 0);
   `reload()` stays the retry of the failed page.
7. **Batch page made one legacy-score request per lead.** Replaced with
   `GET /api/batches/{id}/scores`.
8. **Scoring on the batch endpoint and CLI** was a single unbounded loop,
   and one DB error poisoned the whole chunk. It now runs in chunks with a
   savepoint per lead, a chunk-commit failure is counted as failures, and
   unchanged inputs are skipped.
9. **Model/migration drift.** Migration 0006's composite index
   `ix_lead_fit_scores_lead_profile_created` wasn't declared on the model,
   so `alembic check` proposed dropping it. The model now declares it, and
   `alembic check` reports "No new upgrade operations detected". No new
   migration.
10. **No persistent batch fit summary**, and no fit numbers on the
    dashboard. Both were added, counted by distinct lead.

## 8. v1 vs v2 on real leads

v1 was computed in memory for this comparison and never persisted
(`lead_scores` is still 0). Company names are stored lowercase.

| Company | Industry / size / website | v1 (legacy) | v2 |
|---|---|---|---|
| university radiology-atlantic, llc | hospital & health care / 1-10 / no | **29, Cold**: industry 25, size 5, completeness 2, source 2, penalty −5 | **100, strong_match**, 100% coverage |
| healthcare logic | hospital & health care / 51-200 / yes | **38, Cold**: industry 25, size 12, completeness 4, source 2, penalty −5 | **100, strong_match**, 100% coverage |
| first american title | real estate / 10001+ / no | **0, Cold**: industry 0 (v1's keyword list has "property management" etc. but no plain "real estate"), completeness 2, source 2, penalty −5, floored at 0 | **100, strong_match**, 100% coverage |

In memory, v1 rates all 5,000 real leads **Cold**, as the audit (B.2)
predicted for company-only records.

Why they differ:
- v1 blends company fit with contact and persona signals PDL can't
  supply, penalizes the missing contact title, and uses a hand-written
  keyword list that misses the plain "real estate" segment.
- v2 scores only the two criteria it declares, treats missing input as
  unknown rather than penalizing it, and reports coverage separately.

Neither is a purchase-probability model. `strong_match` means "matches
this demo profile's two criteria", not "Hot".

## 9. Verification evidence (final code; exit codes captured before shortening output)

| Check | Result |
|---|---|
| Backend full suite, SQLite (`DATABASE_URL=sqlite:// python -m pytest -q`) | **272 passed**, exit 0 |
| Backend full suite on disposable Postgres (`TEST_DATABASE_URL=…/gtmflow_phase4_pgtest`, dropped afterwards) | **272 passed**, exit 0 |
| Focused Phase 4 files (fit, consistency, sort/filter, CLI, endpoints, CSV retry + interrupted chunk, scoring, metrics); a subset of the 272 | 103 passed, exit 0 |
| Frontend `npx vitest run` (jsdom + Testing Library; 4 files) | **24 passed**, exit 0 |
| Frontend `npm run typecheck` | exit 0 |
| Frontend `npm run build` | exit 0, 8/8 routes |
| `alembic check` on the restored copy | exit 0, no drift; `upgrade head` a no-op; migrations 0001–0006 unchanged |

Negative controls:
- **Crash-resume test:** fails against the checkpoint's `batches.py`.
- **Lead-page tests** (`page.interactions.test.tsx`), run against the
  checkpoint's page and hook:
  - fail as intended (bugs fixed or behavior new in this pass): stale
    action repaint, duplicate refresh, push while excluded, push without
    a re-check;
  - pass on the old code too (requirements already met, now verified on
    the rendered page): >200 in both panels, failed-page retry, late
    responses after navigation, exact approval target, approval target
    stable while paging. The per-stage late-response tests were added
    after this control was run.

Frontend interaction coverage runs the real page components in jsdom with
only `@/lib/api` faked, not a real browser. Specifically:
- Both history panels page past 200 (231 outputs, 230 pushes).
- A failed later page keeps the loaded rows, and Retry re-requests
  exactly that page.
- A late response for the previous lead doesn't overwrite the current
  one. This is checked separately at each sequential load stage (draft,
  legacy score, readiness, fit) plus both histories and `getLead`.
  Removing the stale-response guard after the score fetch makes the
  score-stage test fail (mutation check).
- An action finishing after navigation doesn't repaint.
- A draft behind 230 newer summaries is rendered.
- Approve sends exactly the rendered draft id, before and after loading
  older history; approve/reject are hidden while the lookup is
  unresolved.

## 10. Remaining limitations

- **No seller/service profile** (the Phase 5 blocker; see §11). Until it
  exists, `outbound_email` can never be `ready`, size can't be scored, and
  fit can't discriminate within the cohort.
- **Concurrent resumes of the same batch aren't locked.** Two
  simultaneous `resume_batch_id` requests for one batch could both insert
  the remainder. A crashed `uploading` batch also can't be told apart from
  one still uploading, so resume it only once the original request has
  clearly died. A row lock or lease is future work.
- **`outreach_rejected` is a readiness gap, not a hard routing
  exclusion**, matching what the push pipeline enforces today
  (`BLOCKED_STATUSES = DISQUALIFIED_STATUSES`). Changing enforcement is
  out of scope.
- **Client-side pre-checks are advisory.** The batch/lead pages disable
  and re-check before pushing, but the backend's dispatch-time checks
  remain the authority.
- Frontend interaction tests run in jsdom, not a real browser.

## 11. Phase 3 acceptance items: status

| Item | Status |
|---|---|
| History panels beyond 200, retry after a failed page, out-of-order safety, buried draft visible, approval target stability | **Closed.** Page-level interaction tests (§9), jsdom, not a real browser |
| CSV retry via the real HTTP path after an interrupted committed chunk, explicit retry identity, changed-content rejection, routing blocked until complete | **Closed** (§5) |
| Company-identity resolution/merge across different snapshots | **Still open**, unimplemented by design (only one snapshot exists) |
| `approval_rate` can exceed 100% on approve→reject→approve | **Still open**, Phase 11 (documented characterization test) |
| Concurrent-push idempotency, mock-vs-real dashboard breakdown | **Still open**, Phase 11 |

## 12. What Phase 5 needs from the user (seller/service information)

Phase 5 must not start by improvising these. Needed:

1. **Seller identity:** company name and the product/service being
   offered (one paragraph, in the seller's own words).
2. **Target customer definition:**
   - which of the current segments (hospital & health care, medical
     practice, real estate) are really in scope, and whether any narrower
     sub-type matters. The current data can't express narrower types;
     only exact PDL industry values exist.
   - geography (US only, or specific states or regions; PDL has structured
     `region`);
   - company-size range(s) as intervals, so the inactive size criterion can
     become real. Specify how partial overlap with PDL's size bands
     (e.g. `51-200`) should count.
3. **Proof points:** a short, bounded list of claims outreach may make
   (case studies, metrics, credentials). Anything not listed must not be
   asserted.
4. **Exclusions and do-not-target rules** beyond the existing
   dispositions, if any.
5. **Scope:** a single global profile (the natural default for this
   single-tenant app) or per campaign/batch.
6. **Who contacts whom:** the target persona(s) or title(s). PDL has no
   contact fields, so outbound email needs a contact source too.

Each answer becomes a new, versioned profile (`profile_version` bump).
Scores under `demo-us-sectors-v1` stay as history and stop counting as
current.
