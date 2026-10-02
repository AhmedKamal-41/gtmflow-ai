# Phase 5 handoff: grounded generation and prompting baseline

Date: 2026-09-23. **Stopped before Phase 6.**

**Status:** implementation is complete and verified with synthetic and mock inputs. The additive migrations `0007` and `0008` are applied to the real Codespace database. What remains is the user's seller content and its review (§8). No real seller profile exists or is active, and no real (paid) generation has run. Phase 5 is therefore **not declared complete**: grounded generation for an actual seller cannot happen until that content is supplied, reviewed, and explicitly activated.

This session continued from checkpoint `50284f5` (branch `codex/phase5-seller-profile`), which added the seller-profile draft foundation. The checkpoint claims (296 backend and 37 frontend tests passing) were re-run before any edit and held (exit 0 for both). Sections 1–5 describe the resulting behavior, §6 the verification actually run, and §7 the real-database migration.

## Where each part stands

| Component | Rung | Evidence |
|---|---|---|
| Seller-profile drafts, activation, deactivation, status | code ready, synthetic-verified | §1, §6 |
| Grounded context, versioned output schemas, validation | code ready, mock/synthetic-verified | §2–§3, §6 |
| Exact generation provenance (`ai_outputs` seller columns) | code ready, mock-verified; schema applied to real DB | §4, §7 |
| Readiness driven by the active profile | code ready, synthetic-verified | §5 |
| UI (seller page, lead page, output card) | code ready, jsdom interaction-tested | §6 |
| Migrations `0007` + `0008` on the real Postgres database | **applied and verified** | §7 |
| Actual seller content | **awaiting the user** | §8 |
| Real (OpenAI) generation | **not run.** Only exercised against a faked provider, with no network | §6 |

## 1. Seller profile: one global, versioned profile with explicit activation

- **Drafts (checkpoint, unchanged):** each changed save creates an immutable revision with a SHA-256 content hash, `expected_version` optimistic concurrency, identical-retry idempotency, and an atomic `seller_profile_draft_saved` event.
- **Activation (new):**
  - `POST /api/seller-profile/activate` activates one exact revision. It requires:
    - `confirm_reviewed: true`: the operator states they reviewed that revision;
    - `expected_activation_sequence`: the activation state the operator saw (0 = never activated);
    - for a `demo` revision, additionally `acknowledge_demo: true`.
  - Missing confirmation returns 422. Missing demo acknowledgement returns 422 with an explanation. A different current state returns 409. An identical retry returns 200 with the same row. An unknown revision returns 404.
  - A database unique constraint on `sequence` turns a concurrent race into 409, or into the winner when both requests were identical.
- **Deactivation (new):** `POST /api/seller-profile/deactivate` with `expected_activation_sequence` returns the workspace to "no active profile".
- **Storage:** the append-only `seller_profile_activations` table. The row with the highest `sequence` is the current state, and a NULL `seller_profile_id` records a deactivation.
- **Audit:** every activation or deactivation writes `seller_profile_activated` / `seller_profile_deactivated` in the same transaction. The event records the revision id, version, hash, confirmation, demo acknowledgement, and `actor_label`.
- **What the confirmation is:** the actor label is `local-demo-unauthenticated`, because the app has no authentication. The confirmation is the operator's statement, not a verified identity or human approval record.
- **Saving never changes the active revision.** Saving only writes `seller_profiles`, and activation points at a specific revision id. Historical revisions are never modified or deleted.
- **`GET /api/seller-profile/status`** reports:
  - `state`: `missing` (nothing saved), `draft_only` (revisions exist, none active), or `active`;
  - the active revision and the last activation row;
  - whether the latest draft is the active one.
- Revision reads (`GET /api/seller-profile`, `/versions`) now mark the active revision as `status: "active"`; all others are `"draft"`.
- **Demonstration template:** `GET /api/seller-profile/demonstration-template` returns an explicitly labeled GTMFlow demonstration profile (`profile_kind: "demo"`). Every statement in it describes what this repository does, and it has **no proof points**. Fetching it saves and activates nothing.
- **Real database:** no profile of any kind was saved or activated there (§7).

## 2. Grounded generation (extends the existing AI client and service)

The existing layering is kept: `AIClient` ABC → `MockAIClient` / `OpenAIClient`, called by `app/services/ai_generation.py` from the existing endpoints. There is no parallel pipeline. New pure module: `app/ai/grounding.py`.

**One request, in order:**

1. **Resolve the seller revision once.** This is the active revision, or an explicit override that only the standalone demo passes. The immutable content is copied into a `SellerContext`, so activating another revision mid-request cannot change what the output uses or records. A test activates version 2 from a second session *during* the model call. The output still records version 1, and the next request uses version 2. Removing the resolve-once behavior makes that test fail (mutation check run).
2. **Build the grounded context** (`grounded-context-v1`):
   - `seller`: company, product, value proposition, target customers, and exclusions, plus capabilities (`cap-N`) and approved claims (`claim-N`, each with its source), all from that revision only. It also carries the revision's source, id, version, and content hash.
   - `lead_facts`: each has an id, field, value, origin (the batch source, e.g. `pdl_import` / `csv`), and kind:
     - `structured_field`: the eight lead columns;
     - `free_text`: `cleaned_data` extras, capped at 20 fields × 500 characters.
   - `lead_provenance`: lead, batch, snapshot, import run, source record, and company identity ids.
   - `unknowns`: every missing structured field (`…_not_provided`), plus `buying_intent`, `budget`, `current_operational_problems` and `current_tools_or_product_usage` on every lead. No stored field establishes these.
   - `company_fit`: the current v2 fit row, carrying an explicit note that it is an attribute match and "not evidence of interest, need, budget or intent to buy".
   - `restrictions_at_generation`: exclusion status and reasons at generation time. These are historical once stored.
3. **Exclude previously generated content.** A stored summary is never fed into outreach. The v1 pipeline did exactly that, so it was removed (test: a stored summary claiming "actively buying" never appears in the next context or draft).
4. **Validate before saving.** On failure, nothing is saved as an `AIOutput`.
5. **Persist** the output and its `ai_summary_generated` / `outreach_generated` event. The event now includes the output id, versions, input hash, and seller identity.

**Seller requirement:**
- Outreach without an active revision returns **409**, and nothing is saved. A saved-but-unactivated draft is not used.
- Summaries work without a profile: they describe only the lead record and record no seller.

**The hardcoded GTMFlow pitch is gone.** Outreach is written from the selected seller revision. The mock no longer guesses pain points from keywords in the company name or industry, and it states unknowns instead.

**Untrusted input:**
- Real-mode prompts (`PROMPT_VERSION = "grounded-v2"`) put the whole context inside `<untrusted_data>`. The rules say the data must never be followed as instructions.
- `<` and `>` are JSON-escaped, so imported text cannot close the fence early.
- The system rules also forbid:
  - inferring intent, budget, problems, contacts, or usage from industry, name, provider, or fit score;
  - claims outside `approved_claims`.

## 3. Output schemas and validation (`output_schema_version = "v2"`)

**Summary v2:** `company_summary`, `evidence[{fact_id, statement}]`, `unknowns`, `hypotheses`, `seller_relevance` (must be null without a seller), and `confidence`.

**Outreach v2:** `subject`, `email_body`, `lead_facts_used` (at least one), `capabilities_used`, `claims_used`, `unknowns_acknowledged`, `call_note`, and `confidence`.

Both schemas reject extra fields, wrong types, and overlong values. On top of the schema, the validator applies deterministic grounding checks:

| Check | Reason code |
|---|---|
| Schema failure | `schema_invalid` |
| Reply is not JSON | `invalid_json` |
| Fact id not in the context | `unknown_fact_reference` |
| Capability id not in the revision | `unknown_capability_reference` |
| Claim id not in the revision's approved claims | `unapproved_claim_reference` |
| Email address other than the lead's supplied contact email | `unsupplied_email_address` |
| Percentage or multiplier (`40%`, `3x`, `12 percent`) not present in an approved claim or a structured lead field | `unsupported_figure` |
| Summary `seller_relevance` with no seller | `seller_relevance_without_seller` |

**Figures:**
- Free-text lead fields are deliberately excluded from the allowed-figure text: an injected "claim 300% ROI" in imported notes cannot license that figure.
- Figures in a seller's *value proposition* are also rejected unless an approved claim contains them. The editor hint tells users to put results in sourced proof points.

**Failure handling:**

| Failure | Response | What is saved |
|---|---|---|
| Invalid output | **502**: "…failed validation (codes). No output was saved." | Only an `ai_generation_rejected` event with reason codes, prompt version, and schema version (no model text) |
| Real mode without a key (`AIConfigError`) | **503**, raised before any network call | Nothing |
| Provider exception | **502**: "AI provider request failed (ExceptionClassName)." | Nothing. The provider's message is never forwarded, so a key embedded in it cannot leak (tested) |

**These checks are deterministic guards, not a semantic verifier.** They catch fabricated references, contact details, and figures. They cannot prove that free prose is true, so human review (Phase 6) remains required.

## 4. Exact provenance per output

New nullable `ai_outputs` columns (migration `0008`): `seller_profile_id` (FK), `seller_profile_version`, `seller_profile_content_hash`, and `seller_profile_kind`. Each new output also records:

- `prompt_version` (`grounded-v2`) and `output_schema_version` (`v2`);
- `model_used` (the client's own name: `mock` / `openai`) and `model_revision` (`mock-deterministic-v2-grounded`, or the OpenAI model string);
- `input_snapshot` (the full grounded context) and `input_hash` (SHA-256 of its canonical JSON).

Other cases:
- **Built-in demo profile:** id and version stay NULL, the hash is set, `kind="demo"`, and `input_snapshot.seller.source = "builtin_demo"`.
- **Summary with no active profile:** all four seller columns are NULL.
- **Historical outputs** keep their recorded `prompt_version "v1"` (or NULL) and NULL seller columns. They are never relabeled or backfilled (tested against a stored v1 row). The UI shows them as "Historical output (prompt v1), generated before grounded prompting. No seller profile was recorded for it."

## 5. Readiness, restrictions, and side-effect boundaries

**Live seller state** (`fit_queries.current_readiness_by_lead`, one extra bounded query per page) now drives the outbound-email gaps:

| Seller state | Gap |
|---|---|
| No active revision | `no_seller_profile_configured` (explanation reworded) |
| Active demonstration revision | new `seller_profile_is_demonstration` |
| Current draft generated from a different revision, or before grounding | new `draft_not_from_active_seller_profile` |

An active real-kind revision, a contact email, and an approved draft generated from exactly that revision make outbound email `ready` (synthetic test).

**What is unchanged:**
- The Phase 4 rubric: no scoring constant or criterion was edited, and the diff of the rubric lines is empty.
- Stored `lead_fit_scores` rows, including their historical readiness snapshots (tested before and after activation). Those snapshots still show the old gaps, labeled `at_scoring`.

**Generation never changes status.** It does not change lead status or batch status, create reviews, approve, push, or send (tested with a `do_not_contact` lead in a `partial` batch). Blocked-lead generation is still allowed, as before, because two Phase 3 regression tests depend on it. The draft records `restrictions_at_generation`, and every push path still refuses excluded leads.

**Exact-draft review targeting and the Phase 4 stale-response protections are unchanged.** Their tests pass unmodified apart from the new `getSellerProfileStatus` mock and nullable fixture fields.

**Standalone mock demo:** `POST /api/demo/run` passes the built-in demonstration profile per call. It needs no saved or active profile, never activates one, and its drafts begin "[Demonstration draft … Not a real offer.]". Its existing, labeled `demo-auto-approve` and mock push are unchanged.

**UI:**
- **Seller page:**
  - a status banner: missing, none active, or active version N with its hash, flagged if it is a demonstration or if a newer draft is not active;
  - per-revision activation behind an explicit review checkbox, plus a demonstration checkbox for demo revisions;
  - a Deactivate button;
  - 409 recovery that reloads the real state;
  - failure with retry, and a stale-response guard;
  - "Load GTMFlow demonstration template", which fills the form only.
- **Lead page:**
  - a seller-status note naming the version and hash, with a link when none is active;
  - Generate outreach disabled when no revision is active (left enabled when the status is unknown, since the server decides);
  - status failure with retry.
- **Output card:**
  - a provenance footer: seller revision and hash or built-in demo, prompt, schema, model, and input hash;
  - "demonstration" and "not from active seller revision" badges;
  - grounded content views: cited facts, capabilities, claims, unknowns, unconfirmed hypotheses.
- **Batch page:** unchanged; it renders the new gap codes the server returns.

## 6. Verification actually run

Backend ran from `backend/` using the existing requirements venv, and frontend from `frontend/` after `npm ci` from the existing lockfile. No dependencies were added. Exit codes are the commands' own, not a pipeline's.

| Check | Command | Result | Exit |
|---|---|---|---|
| Checkpoint baseline, before edits | `DATABASE_URL='sqlite://' USE_MOCK_AI=true OPENAI_API_KEY='' SLACK_WEBHOOK_URL='' python -m pytest -q` / `npx vitest run` | 296 passed / 37 passed | 0 / 0 |
| Focused Phase 5 backend (seller profiles, activation, grounded generation, migration, AI generation/endpoints, provenance, demo) | `pytest -q tests/test_seller_profiles.py tests/test_seller_activation.py tests/test_grounded_generation.py tests/test_seller_profile_migration.py tests/test_ai_generation.py tests/test_ai_endpoints.py tests/test_provenance.py tests/test_demo.py` | 96 passed (a subset of the full suite) | 0 |
| **Full backend, SQLite** | same env as the baseline, `python -m pytest -q` | **342 passed**, 1 dependency deprecation warning | 0 |
| **Full backend, disposable Postgres** | `TEST_DATABASE_URL=…/gtmflow_phase5_pgtest python -m pytest -q` (database created for the run, dropped afterwards) | **342 passed** | 0 |
| Focused Phase 5 frontend | `npx vitest run src/app/seller-profile src/app/leads/[leadId]/page.generation.test.tsx` | 25 passed (a subset) | 0 |
| **Full frontend** | `npm test` | **56 passed**, 7 files | 0 |
| Frontend types | `npm run typecheck` | passed | 0 |
| Production build | `npm run build` | passed; 9 routes including `/seller-profile` | 0 |
| Whitespace | `git diff --check` | passed | 0 |

**One failed attempt:** the first Postgres suite attempt ran after the Codespace had stopped the container (clean shutdown at 17:39 UTC). It ended with 97 passed, 245 connection errors, exit 1. After restarting the container and waiting for readiness, the unchanged rerun passed all 342.

Full-suite totals are not added to the focused counts. New tests:
- 46 backend: 15 activation, 31 grounded generation. The migration test was rewritten in place to cover 0007 → 0008.
- 19 frontend: 10 seller-activation, 9 lead-generation.

Obsolete v1 assertions (keyword pain points, stored summary fed into outreach, the GTMFlow pitch) were rewritten to assert the new behavior, not deleted.

**Coverage map:**

| Area | Tests |
|---|---|
| Draft vs active selection | status transitions; saving a draft keeps the active revision; an unactivated draft is not used |
| Concurrency | stale activation 409; identical retry; database-constraint race to 409; identical race returns the winner; stale deactivation 409 |
| Exact revision capture | activation during the model call (mutation-checked) |
| Missing profile / sparse company | outreach 409 with nothing saved; summary without seller; a company-name-only lead gets "Hi there,", no email, low confidence, and unknowns |
| Fit is not intent | a strong-fit lead with "pain/backlog" notes: intent, budget, and problems stay unknown, and the words never appear in prose |
| Unsupported claims / untrusted input | unknown claim or capability id; unsourced figure vs an approved-claim figure; an injected note is only data (nothing approved, pushed, or changed; the fence is escaped); a model that obeys an injection is rejected |
| Invalid output | missing field, extra field, bad enum, empty citations, unknown fact, non-JSON, invalid summary |
| Configuration / provider | 503 before any call; provider error with the key in its message never leaks; a faked valid real-mode reply passes the same validation |
| Blocked leads / incomplete imports | `do_not_contact` in a `partial` batch: status, batch, and push block unchanged |
| Historical preservation | a v1 row is unchanged and unrelabeled; drafts from older revisions are flagged |
| Frontend | activation confirmation, demo acknowledgement, conflict, failure/retry, busy-state lockout, stale status (mutation-checked), deactivation, template; lead page disabled/enabled generation, provenance, error/retry, historical labeling, demo labeling, status failure/retry, stale status on navigation (mutation-checked), late generation after navigation |
| Mock demo | runs with no profile; drafts labeled demo; workspace profile stays missing |

Frontend tests run the real pages in jsdom with React Testing Library; only `@/lib/api` is faked, not a browser. The multi-session concurrency tests use SQLite plus the full suite on Postgres; they are controlled races, not multi-process load tests.

## 7. Real database: backup, restored-copy verification, migration

Database: persistent `gtmflow-dev-postgres` (`localhost:55433/gtmflow`), at `0006_fit_scores` beforehand. Its Phase 4 snapshot digests matched the values recorded in the Phase 4 verification exactly.

1. **Integrity baseline:** full-row MD5 digests of every pre-existing table, status counts, and provenance-completeness checks. `ai_outputs` is digested over its pre-0008 columns.
2. **Backup:** `pg_dump -Fc` to `backend/.backups/gtmflow_pre_phase5_migration_20260923T1811Z.dump` in the main checkout (git-ignored), exit 0, sha256 `a22db6e9…23c5b2`.
3. **Restore:** into the disposable `gtmflow_restore_verify` (dropped and recreated), `pg_restore --exit-on-error` exit 0. Its integrity output was **byte-identical** to the real database.
4. **Restored copy first:**
   - `alembic upgrade head` (0006 → 0007 → 0008) exit 0; `alembic check` "No new upgrade operations detected" exit 0; re-upgrade was a no-op.
   - Integrity diff: only `alembic_version` changed.
   - The new tables exist and are empty. The four `ai_outputs` columns are nullable, and constraint names match the models.
   - Downgrade to 0006 restored byte-identical integrity output, then re-upgrade and check passed. This downgrade ran **on the disposable copy only**.
   - **Application check on the copy:** one real PDL lead, the mock client, and the demonstration template saved and activated with acknowledgement. The results:
     - outreach without a profile returned 409; demo activation without acknowledgement returned 422;
     - the output recorded `grounded-v2`/`v2`/mock, version 1, `demo`, a matching hash, and all eight provenance ids;
     - buying intent, budget, problems, and tools were listed as unknowns;
     - readiness showed `seller_profile_is_demonstration`, and lead status stayed `new`.
   - Afterwards, the only integrity differences on the copy were the expected +2 outputs, +4 events, and the new-table rows.
5. **Real database:**
   - Integrity output rechecked identical to the backup baseline.
   - `alembic upgrade head` exit 0 (0006 → 0007 → 0008); `alembic check` exit 0, no drift.
   - Integrity diff: **only `alembic_version`**. Leads 5,000; company identities 5,000; source snapshots 1; import runs 4; lead batches 1 (`uploaded`, 5,000/5,000); lead fit scores 5,000 (5,000 distinct leads); workflow events 40; lead scores, outputs, reviews, and pushes 0. All full-row digests are unchanged, and provenance is complete for 5,000/5,000 leads and identities.
   - The Phase 4 snapshot script changed only in `alembic_version`.
   - Read-only application check: status `missing`; outbound gaps unchanged; the recheck wrote nothing.

**Not done on the real database:** no profile was saved or activated, nothing was generated, rescored, reviewed, or pushed, and no downgrade was run. The corpus was not downloaded or rescanned, no paid AI call was made, and no Slack message was sent. The restored copy still holds the disposable smoke rows; it can be recreated from the backup at any time.

## 8. Seller content still needed from the user

The technical work does not depend on these, but grounded outreach for an actual seller does. Each answer becomes a new draft revision that is then reviewed and explicitly activated:

1. **Seller company name** and **product/service name**.
2. **Value proposition**, one paragraph in the seller's own words, without figures that a proof point doesn't support.
3. **Target customers:** which of the current segments (hospital & health care, medical practice, real estate) are in scope; geography; company-size ranges; the roles to reach.
4. **Capabilities** the outreach may describe, up to 12, and only ones actually offered.
5. **Proof points:** each claim with its source. Omit any that aren't supported; none is required.
6. **Exclusions:** do-not-target rules or prohibited claims.
7. **Who reviews and activates the revision.** Activation records an unauthenticated operator statement, not a verified approval.
8. **A contact source**, if outbound email is the goal: PDL has no contact fields, so `missing_contact_email` remains on all 5,000 leads regardless of the profile.

**Alternatively:** decide to use GTMFlow itself as an explicitly labeled demonstration. The template exists; its drafts stay flagged, and outbound email can never be `ready` from it.

Also open: whether the company-size fit criterion should become active once size ranges are supplied. That would be a new versioned rubric (`profile_version` bump), not an edit to `demo-us-sectors-v1`.

## 9. Remaining limitations

- The grounding checks are deterministic and structural (§3). They cannot verify the truth of free prose; human review (Phase 6) is still required before any use.
- No real OpenAI generation has run. Real mode is covered only by a faked provider with no network, and it needs a key plus the user's go-ahead (contract rule 7).
- Activation confirmation is an unauthenticated statement (Phase 12: authentication).
- Some PDL-import extras in `cleaned_data` are importer-derived labels, not source attributes. Examples are `candidate_segment` and `segment_is_candidate_classification_only`. They appear as `free_text` facts with the import's origin; a finer origin split would be a small follow-up.
- Generation for currently excluded leads is still allowed, with delivery blocked at every push path. Refusing it outright would need a decision and changes to the Phase 3 regression tests.
- Phase 6 annotation, training, and real Slack delivery have not started.

## Main files

- Backend:
  - `app/ai/grounding.py` (new)
  - `app/ai/prompts.py`, `app/ai/mock_client.py`, `app/ai/client.py`
  - `app/services/ai_generation.py`, `app/services/seller_profiles.py`, `app/services/fit_queries.py`, `app/services/demo.py`
  - `app/api/ai.py`, `app/api/seller_profile.py`
  - `app/models/seller_profile.py`, `app/models/ai_output.py`
  - `app/schemas/seller_profile.py`, `app/schemas/ai_output.py`
  - `app/scoring/fit.py` (readiness only)
  - `alembic/versions/0008_seller_activation_and_output_grounding.py` (new)
- Frontend:
  - `src/app/seller-profile/page.tsx`
  - `src/app/leads/[leadId]/page.tsx`
  - `src/components/AIOutputCard.tsx`
  - `src/lib/api.ts`, `src/types/api.ts`
- Tests:
  - backend: `tests/test_seller_activation.py`, `tests/test_grounded_generation.py` (new), `tests/test_seller_profile_migration.py`
  - frontend: `page.activation.test.tsx`, `page.generation.test.tsx` (new)
