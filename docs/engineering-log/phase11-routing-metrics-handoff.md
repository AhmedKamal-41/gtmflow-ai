# Phase 11 handoff: reliable Slack routing and corrected metrics

Date: 2026-09-26. **Status: done and verified with mocked Slack and disposable PostgreSQL.** Mock mode stayed on throughout. No paid calls, GPU, deployment, automatic approvals or real messages. Database recovery (Phase 9 handoff §8) remains open and separate. Phase 12 has not started.

## 1. Scope (from `phase-status.md` and the audit)

- **D.3:** no protection against concurrent or duplicate pushes.
- **E.1:** `approval_rate` could exceed 100%. Phase 3 caught exact-repeat retries only.
- **D.4/E.3:** mock and real deliveries were merged in every metric.
- **D.2** (blocked leads reaching Slack) was fixed in Phases 2 and 3. It is preserved, and re-tested here.

## 2. Duplicate-dispatch prevention (delivery ledger)

**Code:** `deliver_lead_to_slack` in `backend/app/services/integration_push.py`, the single dispatch point used by the single-lead route, the batch route, the `push_hot` job and the demo. `push_lead_to_slack` remains as a wrapper. Migration `0011_slack_delivery_ledger` adds nullable columns to `integration_pushes`: `delivery_key` (unique), `approved_output_id`, `approved_content_hash`, `attempt`, `delivery_mode`, claim, completion and outcome fields, and the resolution.

**Flow:**

1. **Unchanged gates:** blocked status, incomplete import, and a current approval of the exact draft.
2. **Ledger lookup:** delivered → replay; claim in progress → 409; unknown → 409; failed → a new attempt allowed.
3. **Claim:** insert the unique key in a savepoint and commit **before** sending.
4. **Send, then record** the outcome with a conditional update.
5. **Stale claims:** a claim older than 120 s without an outcome becomes `unknown`.

**Uncertain outcomes:**

- `send_slack_payload` now returns `unknown` for timeouts and dropped connections after the request may have left. Connection-refused, connect-timeout and pool-timeout errors stay `failed`, since nothing was sent.
- An `unknown` attempt blocks every further send of that draft: single, batch or job, with or without `force`/`redeliver`.
- An operator resolves it with `POST /api/pushes/{id}/resolve` (`confirmed_delivered` / `confirmed_not_delivered`), or with the "It arrived" / "It did not arrive" buttons in the lead's push history. The finding is recorded as `push_outcome_resolved`; the original outcome code is kept.
- A worker that outlives its claim cannot overwrite a claim that was declared unknown in the meantime; the late result is recorded as `push_late_outcome`.

**Delivery guarantees** (also in `docs/integrations.md`):

- **At most one send per claimed attempt**, across concurrent API requests, workers and restarts.
- **No automatic resend** of anything.
- **Not exactly-once:** Slack webhooks take no idempotency key, so an `unknown` outcome can only be resolved by a person checking the channel. A wrong resolution can cause a duplicate ("not delivered") or a loss ("delivered").
- A new or edited, and newly approved, draft is a separate delivery.
- Legacy rows are not deduplicated retroactively.

**Behavior changes:**

- A repeat push of a delivered draft now returns the earlier row with `replay: true` and sends nothing. It used to send again.
- A deliberate second delivery needs `redeliver: true` (single lead), or `force: true` (batch and job, the existing meaning).
- Two existing tests that pushed twice to create two rows now use `redeliver`; their assertions are otherwise unchanged.
- The batch summary gains `uncertain`.

## 3. Corrected approval metrics

- **Cohort:** distinct operational outreach drafts. Human revisions are included; annotation candidates are excluded (the old `outreach_generated` counted them).
- **Classification:** each draft by its latest operational review (`created_at`, then `id`), the same definition the approval gate uses.
- **Fields:**
  - `approval_rate` = approved drafts / drafts, never above 100% by construction;
  - new: `outreach_pending_review`, `reviewed_approval_rate`, and the audit-only `approval_events` / `rejection_events`.
- **The Phase 3 characterization test** (`approve → reject → approve` = 200%) is now `test_approve_reject_approve_counts_one_approved_draft`: 1 approved draft, 100%, with 2 approval events and 1 rejection event kept.

## 4. Mock-versus-real breakdowns

- `generation_by_mode` (the model that wrote the draft; a human revision inherits its original draft's mode) and `delivery_by_mode` (the webhook used). Each has `mock`, `real` and `unknown`, and each sums to the top-level totals (a test checks this).
- `real_messages_delivered`, `mock_messages_delivered`, `push_unknown_count`, `push_pending_count`, and `data_mode` (`empty`, `mock_only`, `real_only`, `mixed`).
- **Dashboard:**
  - a data-mode banner ("no message left this app" for mock-only data);
  - "Pending review", "Approved of reviewed" and "Delivery outcome unknown" cards;
  - a "Mock versus real" card with the two tables;
  - a rewritten "How to read" note.

## 5. Rules preserved

- **Blocked statuses, incomplete imports and draft-specific approvals:** the same gate runs before any claim. Tests: a blocked lead and an unapproved new draft are refused with **no claim row and no send**.
- **Runtime-flag acknowledgements:** unchanged (approval path untouched); their Phase 10 tests pass.
- **Audit history:** every attempt is its own row. Events `lead_pushed`, `push_outcome_unknown`, `push_outcome_resolved` and `push_late_outcome` are added, and nothing is deleted or rewritten.

## 6. Verification (2026-09-26; all exit 0)

| Check | Result |
|---|---|
| Backend, SQLite | **488 passed, 4 skipped** (the Postgres-only tests) |
| Backend, disposable PostgreSQL 16 (container `gtmflow-phase11-testpg`, removed afterwards) | **492 passed** |
| New delivery tests (`test_phase11_delivery.py`, 11) | replay versus explicit redelivery; failed then explicit retry; unknown blocks single, batch and redeliver sends until resolved; resolution both ways; a dead holder's stale claim becomes unknown (a young claim is in progress); a late outcome never overwrites; blocked and unapproved leads create no claim; each approved draft is its own delivery; a restarted `push_hot` job item never sends twice |
| **Concurrency (Postgres)** | 8 simultaneous API pushes with a slow mocked Slack: **exactly 1 send**, 1 row, other responses 200 (replay) or 409 (in progress). API requests racing two workers: **exactly one delivery per lead**. |
| Mutation check | a non-unique claim key → the concurrency test fails |
| Metrics tests (`test_phase11_metrics.py`, 5, plus the updated Phase 3 test) | repeated decisions count once by the latest review; a regenerated draft is a new cohort member; revisions inherit the mode and annotations are excluded; real and mock generation and delivery are separated (legacy row as unknown); an all-mock database says so; breakdowns sum to totals and rates stay within 0–100 |
| Mutation check | restoring event-counted `outreach_approved` → 2 tests fail |
| Migration `0011` (Postgres) | upgrade, `alembic check` ("No new upgrade operations detected"), downgrade to `0010`, upgrade again, check again |
| Frontend | **94 tests** (5 new: dashboard cohort and breakdowns, data-mode banner, unknown-outcome resolution buttons), `tsc` and `next build` |
| Live mock run | API over HTTP on a fresh migrated database: demo (10 leads, 2 Hot, 2 delivered); a repeat push → `replay: true`, `mock_success`, mock mode; dashboard `approval_rate` 100.0, 2 mock and 0 real messages, `data_mode` `mock_only` |

**Known flaky test (pre-existing, not caused by this phase):** "both panels page past 200 entries" failed once at 5,134 ms (5 s limit) while all test files ran in parallel on 2 vCPUs. Alone it passed 3 of 3 times, at about 3.1 s with both the old and new `PushHistory`.

## 7. Phase 10 evidence backup (private)

| | |
|---|---|
| File | `/workspaces/gtmflow-restore/phase10-acceptance-backup-20260926.tar.gz` (122,294,671 bytes, mode 600, outside git) |
| sha256 | `e491fd5aa6374db37f97f8329bdc1810ca4b2adc98823cf1ba9d1cd3250d3e2a` (also in the `.sha256` file) |
| Contents | `training/runs/phase10-accept-v1/`: the acceptance result and driver log, the served-run archive, the server manifest and request log, and pod evidence. Also the launcher records (`state-phase10-accept-v1.json`, `launch-phase10-accept.out`, `events.jsonl`) and the exact bundle sent to the pod (`4bde2e6a…`, which contains the adapter weights). |
| Verified | Checksum OK; extracted copy identical to the live files (21 files); inner run `SHA256SUMS` OK; bundle byte-identical; no configured credential value and no key or token pattern in any file |

## 8. Open items

- **Push idempotency on Slack's side** is impossible with incoming webhooks. A future Slack API app integration (`chat.postMessage` with a message ledger lookup) could check the channel instead of asking an operator.
- **The 120 s stale-claim window** is a constant. A worker that genuinely stalls longer than that mid-send produces a `push_late_outcome` event, not a duplicate.
- **No authentication** on the resolve endpoint or anywhere else (audit F.1, Phase 12).
- **Database recovery:** still open and separate.
