# Phase 5 handoff: seller-profile foundation

Date: 2026-09-23. Status: **in progress**. The seller-profile draft foundation is implemented and tested. Profile activation and grounded generation remain unfinished, pending the seller information below.

The user accepted Claude Code's recorded Phase 4 completion and instructed us to proceed. Phase 4's real-cohort results remain attributed to the run documented in its handoff; this session did not repeat that run.

## 1. Implemented behavior

The single workspace now has a `/seller-profile` page, accessible through **Seller** in the navigation. It starts empty and collects:

- Whether the profile describes an actual seller or an explicitly labeled demonstration.
- Company name, product/service name, value proposition, and target customers.
- Allowed capabilities, optional proof points with a source for each claim, and exclusions.

Each changed save creates an immutable draft revision with a unique increasing version, a SHA-256 hash of its canonical JSON content, and the existing honest editor label `local-demo-unauthenticated`. The editor shows paginated, read-only revision history. A profile and its `seller_profile_draft_saved` audit event commit together.

Saving does **not** activate the profile, alter scores/readiness, approve outreach, or send anything. The page states that saved profiles are drafts and are not active for outreach. No seller content was prefilled or invented. Entering a proof-point source records the user's evidence reference; it does not establish that the claim was independently verified.

Optimistic concurrency uses `expected_version` and a database unique constraint on revision number. A stale save returns `409`; the editor preserves the user's text and offers an explicit load-latest action that explains it will replace the form. Identical retries of the current or immediately preceding expected revision reuse the saved row and audit event. Save failures preserve form contents, and editing is disabled during a pending save. History failures retain displayed versions and offer retry.

## 2. API, storage, and versions

| Interface | Behavior |
|---|---|
| `GET /api/seller-profile` | Latest draft; `404` when none exists |
| `POST /api/seller-profile` | Typed content plus `expected_version`; `201` new revision, `200` identical retry, `409` conflicting revision |
| `GET /api/seller-profile/versions` | Standard paginated response, newest version first, default 50 and maximum 200 per page |
| `seller_profiles` | UUID, unique integer revision, profile JSON, content hash, editor label, timestamp |
| Alembic `0007_seller_profiles` | Adds only the draft table; parent is `0006_fit_scores` |

The request schema rejects extra fields, blank required strings, invalid profile kinds, negative/non-integer expected versions, overlong fields, and oversized lists. Proof points require both a claim and a source; an empty proof-point list is allowed.

Migrations `0001`–`0006` and the scoring rubric were preserved. `demo-us-sectors-v1` remains unchanged. Existing generation still uses its previous `PROMPT_VERSION="v1"` and `OUTPUT_SCHEMA_VERSION="v1"`; no new prompt or active seller configuration is claimed in this checkpoint. Draft revision numbers are not scorer/profile configuration versions.

Main implementation files:

- `backend/app/models/seller_profile.py`
- `backend/app/schemas/seller_profile.py`
- `backend/app/services/seller_profiles.py`
- `backend/app/api/seller_profile.py`
- `backend/alembic/versions/0007_seller_profile_drafts.py`
- `frontend/src/app/seller-profile/page.tsx`

## 3. Verification actually run

Backend commands ran from `backend/` with a local virtual environment and synthetic SQLite fixtures. Frontend commands ran from `frontend/` using the existing Vitest/Testing Library setup. Exit codes below are the command results, not those of a log-truncation pipeline.

| Check | Command | Result | Exit |
|---|---|---|---|
| Profile API, persistence, migration | `DATABASE_URL='sqlite://' .venv/bin/python -m pytest -q tests/test_seller_profiles.py tests/test_seller_profile_migration.py` | 20 passed | 0 |
| New editor interactions | `npm test -- src/app/seller-profile/page.test.tsx` | 6 passed | 0 |
| Full backend suite | `DATABASE_URL='sqlite://' USE_MOCK_AI=true OPENAI_API_KEY='' SLACK_WEBHOOK_URL='' .venv/bin/python -m pytest -q` | 296 passed; one dependency deprecation warning | 0 |
| Full frontend suite, final run | `npm test` | 37 passed across 5 files | 0 |
| Frontend types | `npm run typecheck` | Passed | 0 |
| Production build | `npm run build` | Passed; includes `/seller-profile` | 0 |
| Whitespace check | `git diff --check` | Passed | 0 |

The focused counts are included in the full-suite totals; they are not additional tests. The first full frontend run, performed alongside other checks, had 36 passes and one existing pagination interaction test exceed its 5-second timeout (exit 1). An unchanged rerun of the full frontend suite on its own passed all 37 tests. No assertion or timeout was relaxed. The first backend command could not start because the environment lacked `.venv` (exit 127); installing the existing requirements into a new virtual environment succeeded before the runs above. No dependencies were added to the repository.

Coverage includes profile immutability, content hashing, audit-event correspondence and rollback, identical retries, stale saves, bounded history, malformed content, and a controlled unique-version conflict. The conflict test simulates a competing insertion between lookup and write; it is not a multi-process Postgres concurrency test.

Frontend interaction tests exercise empty-state creation with a sourced proof point, failed initial loading and retry, failed saving without losing edits, stale-save recovery, disabled editing during save, and later-page history failure/retry with retained versions. The full suite also reran the existing Phase 4 lead-history interaction checks. The backend suite includes the standalone mock demo.

## 4. Migration and data boundary

`test_seller_profile_migration.py` creates a synthetic SQLite fixture shaped like the models before `0007`, takes an actual SQLite backup, restores it into a separate file, and executes the new migration's DDL on that restored copy. It verifies the schema against current metadata and confirms the existing partial batch and blocked lead remain unchanged. Its downgrade check runs only on the disposable copy.

This does not replay the complete historical Postgres migration chain or verify a real Codespace database restore. Migration `0007` has **not** been applied to the real database. Before deploying this Phase 5 schema there, take a fresh Postgres backup, verify restoration, and exercise the migration on that restored copy first, following the existing project procedure.

No real company rows, source files, scores, drafts, reviews, or Slack sends were changed by this work. Tests use synthetic fixtures and mock integrations. No corpus download/rescan, paid AI call, training run, or public deployment occurred.

## 5. Seller information needed next

The actual seller and offer have not been supplied. The next decision is whether outreach should sell **GTMFlow itself as an explicitly labeled demonstration** or **the user's actual product/service**.

For an actual seller, provide company/product names, a plain-language description of the offer, target customer industries/geography/size/roles, capabilities the outreach may claim, and any supported proof points with their evidence sources. Include excluded customers or prohibited claims. Omit unsupported proof points; do not fabricate them to fill the form.

The review-before-prompting requirement comes from `decisions.md`'s seller/service profile requirement and the original Phase 5 status. The completed draft editor makes that content concrete and reviewable. Saving a draft is not equivalent to approving it for generation.

## 6. Remaining Phase 5 work

After seller content is supplied and reviewed:

1. Implement explicit selection/activation of an immutable seller revision and record that state change. Keep draft edits separate from the active revision.
2. Inject approved seller facts and lead evidence into the existing AI-client/service interfaces. Version the prompt/output contract and preserve the exact seller revision and input snapshot on each generated output.
3. Ground summaries and outreach in supplied facts, distinguish unknown information and hypotheses, and prevent previous generated content from being promoted to verified evidence. Replace the existing hardcoded seller pitch in the intended generation path.
4. Define and test missing-profile, restricted-lead, and incomplete-import behavior. Recompute current readiness from current restrictions; generation must not approve drafts or trigger sends.
5. Preserve a clearly labeled standalone mock demonstration. Add focused generation/validation tests and UI feedback; run the required suites and complete the Phase 5 migration verification before claiming deployed integration.

Phase 6 annotation, training, and real Slack delivery are not started. This handoff records a tested Phase 5 foundation, not a completed grounded-generation phase.
