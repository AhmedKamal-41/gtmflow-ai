# Phase 1 audit: GTMFlow AI baseline

Date: 2026-09-22
Scope: repository audit only. No production code was changed to produce this document.

This audit distinguishes three kinds of statement throughout:

- **Verified** — I ran a command or read the exact code and observed the result myself (command/file cited).
- **Documented** — the repo's own docs/README say this; I did not independently re-derive it, but nothing I found contradicts it.
- **Blocked / not checked** — I did not have the access or time budget to verify this in Phase 1.

No AGENTS.md or CLAUDE.md exists anywhere in this repository (verified: `find . -iname AGENTS.md -o -iname CLAUDE.md` returned nothing). `PROJECT_STATUS.md`, which the root README links to, also does not exist (verified: `ls PROJECT_STATUS.md` → No such file or directory). Repository conventions below are inferred from the README, `docs/`, and the code itself.

---

## A. Architecture and baseline

### A.1 Backend inventory (verified via directory listing)

Routers, one file each, mounted in `backend/app/api/routes.py`:

| Router | File | Endpoints |
|---|---|---|
| health | `app/api/health.py` | `GET /api/health`, `GET /health` |
| batches | `app/api/batches.py` | `POST /api/batches/upload`, `GET /api/batches`, `GET /api/batches/{id}` |
| leads | `app/api/leads.py` | `GET /api/leads`, `GET /api/leads/{id}` |
| scoring | `app/api/scoring.py` | `POST/GET /api/leads/{id}/score`, `POST /api/batches/{id}/score` |
| ai | `app/api/ai.py` | `POST /api/leads/{id}/generate-summary`, `POST .../generate-outreach`, `GET .../ai-outputs`, `GET .../latest-ai-output` |
| push | `app/api/push.py` | `POST /api/leads/{id}/push`, `POST /api/batches/{id}/push-hot`, `GET /api/leads/{id}/pushes` |
| outreach_review | `app/api/outreach_review.py` | `POST /api/leads/{id}/approve-outreach`, `POST .../reject-outreach` |
| metrics | `app/api/metrics.py` | `GET /api/metrics/dashboard` |
| demo | `app/api/demo.py` | `POST /api/demo/run` |

Services (`app/services/`): `csv_ingestion.py` (pure parse, no I/O), `ai_generation.py` (orchestrates AI client + persistence), `integration_push.py` (orchestrates Slack push + persistence), `metrics.py` (read-only aggregation), `demo.py` (seeds + runs the full pipeline for the one-click demo).

Models (`app/models/`, SQLAlchemy 2.0 declarative): `Lead`, `LeadBatch`, `LeadScore` (1:1 with Lead), `AIOutput`, `IntegrationPush`, `WorkflowEvent`. Six tables, UUID primary keys, tz-aware timestamps. Relationships confirmed by reading `app/models/lead.py`.

AI provider: single abstraction `app/ai/client.py::AIClient` (ABC) with two implementations — `MockAIClient` (`app/ai/mock_client.py`, pure deterministic function of the lead context dict) and `OpenAIClient` (lazy-imports the `openai` package, raises `AIConfigError` before any network call if `OPENAI_API_KEY` is empty). Selected by `get_ai_client()` based on `settings.use_mock_ai` (default `true`).

Integration: single provider, `app/integrations/slack.py` (payload builder, pure) + `send_slack_payload` (httpx POST or mock). No HubSpot/Salesforce/Sheets/Zapier — confirmed absent from the codebase, matches README's "roadmap, not built."

Frontend pages (`frontend/src/app/`, verified via directory listing): `/`, `/upload`, `/batches`, `/batches/[batchId]`, `/leads/[leadId]`, `/metrics`, `/demo` — 7 routes, matches README. Components: `AIOutputCard`, `AppHeader`, `BatchSummaryCard`, `Button`, `Card`, `ErrorMessage`, `Icon`, `LeadTable`, `LoadingState`, `PageHeader`, `PriorityBadge`, `PushHistory`, `ScoreBreakdown`, `StatCard`, `StatusBadge`, `charts.tsx`.

### A.2 Database init / migrations (verified)

`backend/app/core/init_db.py` calls `Base.metadata.create_all(bind=get_engine())` — a one-shot, all-or-nothing schema creation. There is **no migration framework**: `find . -iname "alembic*"` returned nothing, and no `versions/` directory or `alembic.ini` exists anywhere in the repo. Any future schema change (Phase 2 provenance columns, etc.) has no upgrade/downgrade path today; it would require either a fresh `create_all` against an empty DB or hand-written `ALTER TABLE` statements run out of band. This is the primary blocker named in Phase 2 of the roadmap below.

### A.3 Baseline test/build results (verified, run 2026-09-22)

Backend, from `backend/` with a fresh venv (`python -m venv .venv`, `pip install -r requirements.txt`, Python 3.14.2 locally vs. 3.11 pinned in CI):

```
$ DATABASE_URL="sqlite://" python -m pytest -q
121 passed, 1 warning in 2.96s
```

**121 tests pass, not 117.** I could not find a document in the current tree claiming 117 (`PROJECT_STATUS.md` doesn't exist), so I cannot say whether 117 was ever accurate or is a stale number from outside this repo snapshot — flagging per the working rules rather than assuming either way. Test files present (verified via listing): `test_ai_endpoints.py`, `test_ai_generation.py`, `test_batch_endpoints.py`, `test_csv_ingestion.py`, `test_demo.py`, `test_health.py`, `test_lead_scoring.py`, `test_metrics.py`, `test_models.py`, `test_outreach_review.py`, `test_push_endpoints.py`, `test_scoring_endpoints.py`, `test_slack_integration.py`. No pre-existing failures — the suite is currently green.

Frontend, from `frontend/` (Node 24.21.0 / npm 11.19.0 locally vs. Node 22 pinned in CI):

```
$ npm install        # 108 packages, 7 vulnerabilities reported by npm audit (1 low/1 moderate/4 high/1 critical) — not triaged in Phase 1
$ npm run typecheck  # tsc --noEmit — clean, no output, exit 0
$ npm run build      # next build — "Compiled successfully", all 8 routes generated, exit 0
```

CI (`.github/workflows/backend.yml`, `.github/workflows/frontend.yml`, verified by reading): backend job runs `pytest -v` only (no lint, no type-checker like mypy). Frontend job runs `typecheck` + `build` only (no frontend test runner — there is no Jest/Vitest/Playwright anywhere in `frontend/`, confirmed by the file listing). Both jobs are path-filtered so a docs-only PR doesn't trigger either pipeline.

The 7 npm audit vulnerabilities were not triaged (severity breakdown noted above but package names/CVEs not enumerated) — this is a blocked check for Phase 1 scope; flag for whoever picks up dependency hygiene in Phase 12.

### A.4 One sample lead traced through the full mock workflow (verified)

Using a `TestClient` against an in-memory SQLite DB (same fixture pattern as `backend/tests/conftest.py`), `USE_MOCK_AI` default (mock) and `SLACK_WEBHOOK_URL` unset (mock):

1. Uploaded a 1-row CSV (`Cascade Modular Homes`, property management, VP Operations, referral) → `POST /api/batches/upload` → `201`, `valid_rows: 1`.
2. `POST /api/leads/{id}/score` → `total_score: 72`, `priority: "Warm"` (industry 25 + size 12 + persona 15 + pain 0 + completeness 10 + source 10 + penalties 0).
3. `POST /api/leads/{id}/generate-summary` → mock `confidence: "high"`.
4. `POST /api/leads/{id}/generate-outreach` → mock subject `"Quick idea for Cascade Modular Homes's leasing and maintenance workflow"`, pitching **GTMFlow itself** (see F.4 below — this is a real gap, not a demo artifact).
5. `POST /api/leads/{id}/approve-outreach` → `200`, `event_type: "outreach_approved"`, `ai_output_id` = the generate-outreach output's id.
6. `POST /api/leads/{id}/push` with `force:false` on a Warm lead → `400 "Only Hot leads can be pushed unless force=true."` Retried with `force:true` → `200`, `status: "mock_success"`.

Every step persisted a `WorkflowEvent` as documented in `docs/architecture.md`. The full mock pipeline (CSV → score → summary → outreach → approve → push) works end to end as described. This trace also produced three of the concrete findings below (B, C, E) — see those sections for the exact numbers.

---

## B. Scoring correctness

Source: `backend/app/scoring/lead_scoring.py` (313 lines, pure function `score_lead`), documented in `docs/scoring-model.md`.

### B.1 The 95-vs-100 arithmetic (verified, already correctly documented — not a bug)

Category maxima: industry 25 + size 15 + persona 15 + pain 20 + completeness 10 + source 10 = **95**. Penalties are a separate floor of −15, not a positive category. `docs/scoring-model.md:21` already states this explicitly: *"Theoretical max with all positives = 95. The clamp at 100 is defensive."* The README's summary table lists the same six category maxima but doesn't restate the 95 ceiling next to them, which is a minor documentation-consistency gap (the number 100 appears as "100-point model" language throughout marketing copy), but the underlying code and the detailed scoring doc are internally consistent and non-misleading once you read past the README table. **No code change is needed here**; if anything, the README's headline framing ("100-point model") could gain a footnote pointing at the 95 ceiling, since a reader who never opens `docs/scoring-model.md` would reasonably expect a lead to be able to reach 100 on merit alone rather than only via the defensive clamp.

### B.2 Company-only PDL records score as Cold even on a perfect industry/size match (verified, reproduced)

I ran `score_lead()` directly against a `SimpleNamespace`-style object shaped like a PDL company record: `company_name`, `website`, `industry="Property Management"` (direct target-industry match), `company_size="51-200"`, and **no** `contact_name`, `contact_email`, `contact_title`, `source`, or `cleaned_data` — i.e., exactly what the PDL dataset (verified via its Hugging Face card: `id, website, name, founded, size, locality, region, country, industry, linkedin_url` — no contact fields at all) can provide.

Result:

```json
{
  "total_score": 38,
  "priority": "Cold",
  "score_breakdown": {
    "industry_fit": 25, "company_size_fit": 12, "persona_title_fit": 0,
    "pain_point_keywords": 0, "data_completeness": 4, "source_quality": 2,
    "penalties": -5
  }
}
```

38/100, **Cold**, despite a direct industry-taxonomy match and a mid-size company. The company scores this low because: persona/title (15 pts) is structurally unreachable with no contact, pain-point keywords (20 pts) requires free text this dataset doesn't have, data completeness caps at 4/10 (only website+industry present), source quality defaults to 2 (no `source` field in PDL), and the missing-`contact_title` penalty (−5) fires automatically. **Best case for a company-only PDL record, even with a perfect industry match and top company-size band, is 25 + 15 + 0 + 0 + 4 + 2 − 5 = 41 — always below the Warm threshold (55), let alone Hot (80).** This is not a bug in the arithmetic; it's a category design that conflates *company fit* (something PDL data can answer) with *contact/outreach readiness* (something it structurally cannot). Every PDL-sourced company, no matter how good a fit, will be misclassified Cold by this scorer as it stands. This is the central finding motivating Phase 4 ("Company-fit scoring and outreach readiness") — the score needs to separate a company-fit sub-score (answerable from PDL fields alone) from a contact/outreach-readiness sub-score (requires enrichment), rather than blending both into one number that quietly penalizes company-only data as if it were a worse company.

### B.3 Industry/pain-keyword substring matching produces real false positives (verified, reproduced)

`_score_industry` and `_score_pain_points` in `lead_scoring.py` do plain Python `term in haystack` substring checks (lines 156–169, 226). I ran the literal taxonomy terms against plausible real-world strings:

| Target term | False-positive match in | Confirmed via `in` check |
|---|---|---|
| `"hospital"` | `"hospitality"` | ✅ `"hospital" in "hospitality"` |
| `"tour"` | `"tourism"`, `"detour"` | ✅ |
| `"forms"` | `"platforms"`, `"uniforms"`, `"informs"` | ✅ |
| `"resident"` | `"president"`, `"vice president"` | ✅ |
| `"clinic"` | `"clinical"` | ✅ (looser but plausible: "clinical trials company") |

Concrete reproduction: a hotel company with `industry="Hospitality"` and `contact_title="Vice President"` scores `industry_fit=25` (direct match on `"hospital"`) and picks up the pain-point keyword `"resident"` (matched inside `"Vice President"`) — both false positives, both in the `TARGET_INDUSTRIES` / `PAIN_POINT_KEYWORDS` tuples in `lead_scoring.py:18-31,50-68`. The `"housing"` term matching inside `"co-housing"` / `"affordable housing"` is a defensible broad match, not listed as a bug. The `"hospital"`⊂`"hospitality"` and `"resident"`⊂`"president"` cases are unambiguous false positives that will misclassify hospitality and executive-titled leads as healthcare-fit / pain-signal leads. This is a concrete gap for Phase 4: substring matching needs word-boundary regex (`\bhospital\b`) at minimum, and ideally an explicit exclude-list or curated phrase list for the worst offenders.

### B.4 Missing contacts penalize completeness and pain-signal detection, compounding B.2

Confirmed by reading `_score_data_completeness` (`lead_scoring.py:237-248`) and `_score_pain_points` (`lead_scoring.py:222-234`): both draw on `contact_name`, `contact_email`, `contact_title`, and free-text fields that a company-only PDL row cannot supply. This is the same root cause as B.2, restated at the category level: the model has no signal path that lets a contact-less, high-fit company reach even the Warm band.

### B.5 Disqualified / do-not-contact leads are not blocked from scoring Hot or being pushed (verified, reproduced — see D.2)

`_score_penalties` treats `status in {do_not_contact, disqualified, unsubscribed}` as a soft −10 penalty (`lead_scoring.py:274-276`), not a hard exclusion. I built a lead with `status="do_not_contact"` but otherwise strong signals (industry, size, persona, 3 pain keywords, referral source) and it scored **83/100, Hot** — the −10 penalty wasn't enough to move it out of the Hot band. See section D for what happens next (it gets pushed).

---

## C. Reviews and draft versions

Source: `backend/app/api/outreach_review.py`, `backend/app/schemas/outreach_review.py`, `backend/app/services/ai_generation.py`.

### C.1 Approval references "the latest output," not a specific one, by design (verified)

`_latest_outreach()` (`outreach_review.py:25-34`) is called fresh inside both `approve_outreach` and `reject_outreach` and always resolves to whichever `outreach_email` `AIOutput` row has the newest `created_at` **at the moment the request is handled**. Neither the request schema (`RejectOutreachRequest` has only `reason: str | None`; approve takes no body at all — `schemas/outreach_review.py`) nor `frontend/src/lib/api.ts::approveOutreach(leadId)` / `rejectOutreach(leadId, reason?)` send an `ai_output_id`. The client never tells the server which draft it's approving — only which lead.

### C.2 Stale-tab approval bug (verified, reproduced)

I generated outreach draft v1, approved it (approval correctly referenced v1's id), then regenerated outreach — `generate-outreach` has no guard against being called again after approval (confirmed: `post_generate_outreach` in `app/api/ai.py:38-53` never reads `lead.status`) — producing draft v2 with a different id. I then called approve-outreach again, simulating a browser tab that still has v1 rendered on screen (the tab has no reason to know v2 exists yet). The response's `ai_output_id` was **v2's id, not v1's** — confirmed programmatically: `approve_after_regen["ai_output_id"] == outreach_resp_2["id"]` is `True`, `== outreach_resp_1["id"]` is `False`. A reviewer looking at one draft in their browser can approve a *different* draft they never saw, with no error and no warning. This is a real correctness gap for Phase 6, not a hypothetical: the fix requires the approve/reject request to carry the `ai_output_id` it's reacting to and the server to reject the call (409-style) if that id is no longer the latest.

### C.3 Duplicate approvals, approve-then-reject, and editing after approval (verified / confirmed absent)

- **Duplicate approvals**: nothing in `approve_outreach` checks whether `lead.status` is already `"outreach_approved"` — calling it twice on the same draft just writes two `WorkflowEvent` rows. Reproduced: this inflates `outreach_approved` in the metrics dashboard from 1 to 2 against `outreach_generated=1`, producing an **approval rate of 200.0%** — see E.1.
- **Approve-then-reject**: same absence of a state guard; `lead.status` can flip `outreach_approved → outreach_rejected → outreach_approved` indefinitely, each flip recorded as its own `WorkflowEvent`, with no "supersedes" link between them.
- **Editing after approval**: there is no edit/correction endpoint anywhere. `find`/`grep` for PUT or PATCH routes under `app/api/` returned nothing. `AIOutput.content` (`app/models/ai_output.py:30`) is a single JSON column written once at generation time and never updated in place. There is no `is_edited` flag, no `original_content` vs `corrected_content` split, and no way to record "a human changed this draft before/after approving it." **This is a structural gap, not a missing feature flag** — Phase 6/7 (human review → training data) cannot produce corrected-output training pairs until this exists.

### C.4 What's missing for exporting genuinely human-reviewed training examples (derived from C.1–C.3)

To export `(input, approved_output)` or `(input, model_output, human_correction)` pairs that are actually trustworthy, the system needs, at minimum: (1) approve/reject tied to a specific `ai_output_id` sent by the client and validated server-side against the current latest; (2) a way to record an edited/corrected version of a draft distinct from the raw model output, so corrections aren't silently overwritten; (3) a de-duplication rule so repeated approve events on the same output don't get exported as N separate positive examples; (4) a state machine (or at least a documented precedence rule) for approve→reject→approve sequences so export logic knows which event is authoritative. None of this exists today. This is squarely Phase 6/7 scope.

---

## D. Slack routing

Source: `backend/app/api/push.py`, `backend/app/services/integration_push.py`, `backend/app/integrations/slack.py`, `docs/integrations.md`.

### D.1 Single-lead vs. batch routing gates (verified by reading + reproduction)

`push_one_lead` (`push.py:39-65`) gates on: lead exists (404) → integration_type == "slack" (400) → `lead.score is not None` (400) → `lead.score.priority == "Hot" or force` (400). That's the entire gate. `push_batch_hot_leads` (`push.py:73-158`) only ever selects `priority == "Hot"` leads regardless of `force`; `force` there only bypasses the "already successfully pushed" skip (`lead_has_successful_slack_push`, `services/integration_push.py:50-60`). This matches `docs/integrations.md`'s description almost exactly, except for one gap called out below.

### D.2 `force=true` does not check `lead.status`, and neither does the non-force path (verified, reproduced — this is the most important Slack-routing finding)

Neither `push_one_lead` nor `push_batch_hot_leads` ever reads `lead.status`. Concretely reproduced: a lead uploaded with `status="do_not_contact"` in its CSV row (the CSV `status` column is passed straight through to `Lead.status` in `app/api/batches.py:70-71` with no validation) that scores 83/Hot (see B.5) was pushed successfully via `POST /api/leads/{id}/push` with **`force:false`, not even needing the override** — because the endpoint only checks `priority == "Hot"`, and a disqualified lead can still be Hot. The Slack message that goes out — reproduced verbatim — reads `"🔥 Hot GTM Lead: Blocked Prop Co ... Score: 83/100 (Hot)"` with no indication anywhere that the lead is flagged `do_not_contact`. Rejected-outreach leads are equally unprotected: nothing in the push path checks whether the lead's latest outreach review status is `outreach_rejected`. `force=true` on the single-lead endpoint exists specifically to let a Warm/Cold lead through the Hot-only gate (documented, intentional); it is **not** documented as also being the only thing standing between a disqualified lead and Slack, because in practice nothing stands between a disqualified-but-Hot-scored lead and Slack at all — `force` isn't even required for that case, as reproduced above.

### D.3 Duplicate requests, retries, timeouts, concurrency (verified from code, not load-tested)

- `send_slack_payload` (`slack.py:79-109`) uses a hard 10-second `httpx.post` timeout, catches `httpx.HTTPError` generically and returns a sanitized `"failed"` status/message (verified the sanitization: the exception's `str()` — which could embed the webhook URL — is never returned, only a fixed string).
- No retry logic exists anywhere in the push path; `docs/integrations.md:76-78` explicitly documents this as an MVP scope cut ("Why no retries"). Matches code.
- No idempotency key: two POSTs to `/api/leads/{id}/push` (or two racing requests) both pass the gate independently and each produce its own `IntegrationPush` row and its own Slack send — the batch endpoint's `lead_has_successful_slack_push` skip check only protects against *already-succeeded* re-pushes, not concurrent in-flight duplicates (there's no row lock or unique constraint on `(lead_id, integration_type, status)` — verified by reading the `IntegrationPush` model, which has no such constraint). This is a real gap for concurrent pushes but not something I load-tested; flagging as **verified-from-code, not empirically reproduced under concurrency**.

### D.4 Mock vs. real deliveries are distinguishable at the row level but not on the metrics dashboard (verified)

`IntegrationPush.status` is one of `success` / `mock_success` / `failed` — mock and real are distinguishable per-row (verified in `slack.py:79-109` and the model). But `services/metrics.py::compute_dashboard` (`SUCCESS_PUSH_STATUSES = ("success", "mock_success")`, line 28) merges both into every push-related metric (`leads_pushed`, `unique_leads_pushed`, `push_success_rate`) with no separate mock-vs-real breakdown exposed via the API. In an all-mock demo environment (the only mode this repo currently runs in without a real webhook), 100% of "successful" pushes reported by the dashboard are mock, and there is no field that says so. This is consistent with the project's honest framing elsewhere (time-saved is explicitly labeled an estimate) but push success is not similarly labeled.

---

## E. Metrics

Source: `backend/app/services/metrics.py`, `docs/metrics.md`.

### E.1 Repeated review events inflate `approval_rate` past 100% (verified, reproduced — the most concrete metrics defect found)

`docs/metrics.md:93` already documents that generated/approved aren't deduped by lead and that "re-generating outreach for the same lead and re-approving will double-count both sides of the approval_rate ratio," adding "the math stays internally consistent." I reproduced the scenario directly: 1 outreach draft generated, approved twice (no guard prevents this — see C.3) →

```
outreach_generated: 1
outreach_approved: 2
approval_rate: 200.0
```

**A 200% approval rate is not "internally consistent" as a percentage** — it's a value outside the valid range for a rate. I checked the frontend rendering directly (`frontend/src/app/metrics/page.tsx:224` and `frontend/src/components/charts.tsx:156-168`): the plain `StatCard` at line 224 renders `${m.approval_rate}%` with **no clamping**, so it would literally display `"200%"`. The `Gauge` component used just above it (line 175) *does* clamp internally (`Math.max(0, Math.min(100, percent))`, `charts.tsx:168`) — so the same dashboard would show a visually-full gauge next to a text readout claiming 200%, a self-contradictory display rather than an obviously-broken one. The existing doc undersells this: it frames it as a double-counting nuance, not as a metric that can exceed 100% and render inconsistently. This is a straightforward fix (dedupe by lead+output, or by "most recent status per lead") but is currently unaddressed in code.

### E.2 Units and denominators (verified against `services/metrics.py`, matches `docs/metrics.md` exactly)

All 18 fields, formulas, and divide-by-zero handling (`_pct` returns `0.0` when `denominator <= 0`, verified at `metrics.py:34-37`) match the documentation precisely. No discrepancy found between code and `docs/metrics.md`.

### E.3 Mock deliveries count as real deliveries in every push metric

Restated from D.4: `leads_pushed`, `unique_leads_pushed`, and `push_success_rate` all treat `mock_success` as a success with no separate counter. This is accurate to the documented intent (`docs/metrics.md:36-39` explicitly defines success as `status IN ('success','mock_success')`) but means none of the push metrics can currently answer "how many messages actually left this system," only "how many push attempts passed validation."

### E.4 Time-saved is clearly labeled as an estimate (verified, already handled correctly)

`MINUTES_SAVED_PER_PROCESSED_LEAD = 5` (`metrics.py:31`) is a hardcoded constant multiplied by `total_leads_processed`. Both `docs/metrics.md:44-50` and the README explicitly and repeatedly label this as an estimate, not a measurement, and the frontend surfaces that framing (per `docs/metrics.md:106-108`, not independently re-verified against the live component render in Phase 1 — noted as a documented-not-independently-reverified claim). No other metric in the dashboard is presented as a measured empirical result when it isn't; `average_lead_score`, the pipeline counts, and the push counts are all genuine `COUNT`/`AVG` queries against real rows.

---

## F. Operational gaps

### F.1 No authentication anywhere (verified)

`app/main.py` wires only `CORSMiddleware`; no auth dependency, no API key check, no session/user concept exists in any router or model. Matches README's "Known limitations: No auth." Any client that can reach the API can call every endpoint, including push and CSV upload.

### F.2 CORS (verified)

`allowed_origins` defaults to `("http://localhost:3000", "http://127.0.0.1:3000")` when `ALLOWED_ORIGINS` is unset (`core/config.py:30-34`), `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]` (`main.py:9-15`). Not a wildcard-origin configuration (good), but `allow_methods`/`allow_headers` are maximally permissive; low risk given there's no auth to have credentials for.

### F.3 Upload limits and pagination (verified, gap)

`upload_batch` (`app/api/batches.py:31`) does `raw_bytes = await file.read()` with no size cap, then holds the entire decoded CSV and every parsed row in memory before a single `session.commit()` (`batches.py:56-87` — one loop that calls `session.add()` per lead, one commit at the end, no batching/streaming). `list_leads`, `list_batches`, `list_lead_ai_outputs`, and `list_lead_pushes` (`app/api/leads.py`, `app/api/batches.py:106-111`, `app/api/ai.py:60-76`, `app/api/push.py:161-181`) all run unbounded `SELECT`s with no `LIMIT`/`OFFSET`/cursor parameter — verified by reading every one of these handlers; none accepts a page-size or offset query param. This is fine at demo scale (10 rows) and will not survive a PDL-scale import (the dataset's Hugging Face card, fetched during this audit, reports **32,330,231 rows** / ~24M company records). This is the central blocker for Phase 3 ("streaming PDL import") — both the upload path and every list endpoint need to change shape, not just gain a `LIMIT`.

### F.4 No seller/service profile exists — the mock AI pitches GTMFlow itself (verified, reproduced — important for Phase 5)

`MockAIClient.generate_outreach` (`app/ai/mock_client.py:243-247`) hardcodes the pitch: `"GTMFlow turns lead lists into prioritized outreach with a full audit trail, so {title} can focus on the work the model flags as Hot."` This string is not derived from any configuration — there is no `Seller`/`ServiceProfile`/`Offering` model, no settings field, nothing in `app/core/config.py` that describes what product or service the outreach is supposed to be selling. `build_outreach_prompt` (`app/ai/prompts.py:60-69`, used only in real/OpenAI mode) is generic enough that a real LLM call would have to invent the offering itself (in violation of the "never invent facts" system rule) or produce vacuous outreach with nothing to actually pitch. **The model has no way to know what it's selling.** Every generated outreach email, mock or real, is either about GTMFlow itself (nonsensical once this becomes a tool for outreach about a real seller's product) or ungrounded. This must be resolved before Phase 5 (grounded generation) can produce meaningful non-demo outreach — a seller/service profile (company name, value prop, ICP, proof points) needs to exist and be injected into the AI context (`_build_lead_context`, `app/services/ai_generation.py:22-46`) alongside the lead facts.

### F.5 Long-running synchronous operations (verified)

CSV upload, scoring (single and batch), summary generation, outreach generation, and Slack push are all synchronous, in-request operations with no background job/queue (confirmed: no Celery/Arq/RQ dependency in `requirements.txt`, no `BackgroundTasks` usage found in any router). `docs/architecture.md` and the README both document this as a known limitation, not a surprise. For a PDL-scale batch, `POST /api/batches/{id}/score` synchronously loops every lead in the batch in a single request/transaction (`app/api/scoring.py:119-161`) — this will time out well before reaching thousands of rows. This is the central blocker for Phase 10 ("durable background jobs").

### F.6 Secret handling (verified, handled correctly)

`SLACK_WEBHOOK_URL` and `OPENAI_API_KEY` are read from env via `python-dotenv`, never logged (no logging calls reference either setting — verified via grep for `slack_webhook_url`/`openai_api_key` usage, both confined to `config.py`, `client.py` construction, and `slack.py`'s send call), and the webhook URL is never returned in any API response schema (verified: no schema field carries it). The httpx error sanitization in `send_slack_payload` (D.3) is a genuine, tested guardrail (`docs/integrations.md:54` references a regression test asserting the response text never contains `hooks.slack.com`).

### F.7 Meaningful test coverage exists but is backend-only and has no security/negative-path emphasis (verified)

121 backend tests pass (A.3). No frontend test suite exists at all — typecheck + build are the only frontend gates. No test in the current suite exercises the stale-tab approval bug (C.2), the duplicate-approval metrics inflation (E.1), or the disqualified-Hot push (D.2/B.5) — I could not find assertions for any of these three scenarios in `test_outreach_review.py`, `test_push_endpoints.py`, or `test_metrics.py` (read all three). These would be natural additions once fixes for those findings land.

---

## Findings-to-phase mapping

| Finding | Section | Target phase |
|---|---|---|
| No migration framework (`create_all` only) | A.2 | Phase 2 |
| PDL column names don't match `KNOWN_COLUMNS` (`name` not `company_name`, `size` not `company_size`, `locality/region/country` not `location`, no `source`) | F.3 (derived) | Phase 3 |
| No upload size cap, no pagination on any list endpoint | F.3 | Phase 3 |
| Company-only PDL records structurally cap at ~41/100, always Cold/low-Warm | B.2, B.4 | Phase 4 |
| Industry/pain-keyword substring false positives | B.3 | Phase 4 |
| Disqualified/do-not-contact leads can score Hot and be pushed with no status check | B.5, D.2 | Phase 4 (score) + Phase 11 (routing gate) |
| No seller/service profile — mock/real AI has nothing grounded to sell | F.4 | Phase 5 |
| No `ai_output_id` on approve/reject → stale-tab approves wrong draft | C.2 | Phase 6 |
| No duplicate-approval guard, no approve/reject state machine | C.3 | Phase 6 |
| No edit/correction storage for AI outputs | C.3, C.4 | Phase 6 / Phase 7 |
| `approval_rate` can exceed 100% | E.1 | Phase 6 (fix) + Phase 11 (metrics) |
| No idempotency/dedup on concurrent pushes | D.3 | Phase 11 |
| Mock vs. real push not broken out in dashboard | D.4, E.3 | Phase 11 |
| No auth, no request size limits, synchronous long-running endpoints | F.1, F.5 | Phase 10 / Phase 12 |
| No frontend tests, no coverage for the 3 reproduced bugs above | F.7 | Phase 12 |

---

## What I could not verify in Phase 1

- The PDL dataset's exact field-level content quality (nulls, dedup rate, encoding issues) — I confirmed the schema (`id, website, name, founded, size, locality, region, country, industry, linkedin_url`, 32,330,231 rows, Parquet, CC-BY-4.0) via the dataset's Hugging Face card through a web fetch, not by downloading and inspecting the actual parquet file. Row-level data quality is unverified.
- npm audit's 7 vulnerabilities were not individually triaged (package/CVE/fix-availability).
- Concurrent-push behavior (D.3) was verified by reading the code, not by an actual concurrency test.
- The `/metrics` page's rendering of an out-of-range `approval_rate` was verified by reading the component source (`StatCard` unclamped, `Gauge` clamped — see E.1), not by running the dev server and taking a screenshot. The component-level behavior is confirmed; the actual rendered pixels were not visually inspected in Phase 1.
