# Architecture

GTMFlow AI is a two-service portfolio app plus one database and two optional external integrations. Everything is mock-able so the demo runs without any external accounts.

## Top-level

```
┌────────────────────────┐     HTTP / JSON      ┌──────────────────────────────────────────────────────┐
│  Next.js dashboard     │ ───────────────────▶ │  FastAPI service                                     │
│  (TypeScript + Tailwind│                      │                                                      │
│   • App Router         │ ◀─────────────────── │  ┌──────────────────────────────────────────────┐    │
│   • Client + server    │                      │  │ scoring/          deterministic 100-point     │    │
│     components)        │                      │  │                   pure function, no LLM       │    │
│                        │                      │  ├──────────────────────────────────────────────┤    │
│  7 user routes:        │                      │  │ ai/               AIClient ABC                 │    │
│  /, /upload,           │                      │  │                   MockAIClient (default)      │    │
│  /batches,             │                      │  │                   OpenAIClient (lazy-import)  │    │
│  /batches/[id],        │                      │  ├──────────────────────────────────────────────┤    │
│  /leads/[id],          │                      │  │ integrations/     Slack incoming webhook       │    │
│  /metrics, /demo       │                      │  │                   payload builder + httpx send│    │
└────────────────────────┘                      │  ├──────────────────────────────────────────────┤    │
                                                │  │ services/         orchestration layer          │    │
                                                │  │                   (csv, ai_generation,         │    │
                                                │  │                    integration_push, metrics)  │    │
                                                │  ├──────────────────────────────────────────────┤    │
                                                │  │ models/           SQLAlchemy 2.0 ORM           │    │
                                                │  │ schemas/          Pydantic v2 request/response │    │
                                                │  │ api/              FastAPI routers              │    │
                                                │  └─────────────────────────┬────────────────────┘    │
                                                └────────────────────────────┼──────────────────────────┘
                                                                             │
                                                            ┌────────────────┴─────────────────┐
                                                            ▼                                  ▼
                                                  ┌────────────────────┐         ┌────────────────────────────┐
                                                  │  PostgreSQL        │         │  External (optional)       │
                                                  │  6 tables          │         │   • OpenAI API             │
                                                  │  + indexes         │         │     (real AI mode)         │
                                                  │                    │         │   • Slack webhook          │
                                                  │  Tests use SQLite  │         │     (real push mode)       │
                                                  │  in-memory via     │         │                            │
                                                  │  StaticPool        │         │  Both default to mock      │
                                                  └────────────────────┘         └────────────────────────────┘
```

## Data model

Six tables. UUID primary keys; cross-platform via `sqlalchemy.types.Uuid(as_uuid=True)`. Timestamps are tz-aware via Python-side `datetime.now(timezone.utc)`.

```
LeadBatch ──── (1:N) ──── Lead ──── (1:1) ──── LeadScore
                │                          (matched_signals + breakdown
                │                           JSON live in score_breakdown)
                │
                ├── (1:N) ──── AIOutput        (company_summary | outreach_email)
                ├── (1:N) ──── IntegrationPush (success | mock_success | failed)
                └── (1:N) ──── WorkflowEvent   (audit trail of every action)

LeadBatch ──── (1:N) ──── WorkflowEvent
```

`WorkflowEvent` is the single audit log: `batch_uploaded`, `lead_scored`, `batch_scored`, `ai_summary_generated`, `outreach_generated`, `outreach_approved`, `outreach_rejected`, `lead_pushed`, `batch_hot_leads_pushed`. The metrics service computes adoption + ROI counts by querying `WorkflowEvent`, `IntegrationPush`, and `LeadScore` directly, no separate aggregation table.

## Request flow: upload → score → AI → push

```
1. POST /api/batches/upload          (multipart)
   └─ services/csv_ingestion.parse_csv  →  CleanedLead dataclasses + row errors
   └─ create LeadBatch + Lead rows + WorkflowEvent("batch_uploaded")

2. POST /api/batches/{id}/score
   └─ for each lead:  scoring/lead_scoring.score_lead(lead)  (pure)
   └─ upsert LeadScore                                       (matched_signals
                                                              folded into JSON)
   └─ set LeadBatch.status="scored"
   └─ WorkflowEvent("batch_scored")

3. POST /api/leads/{id}/generate-summary  (or generate-outreach)
   └─ services/ai_generation._build_lead_context(lead)       (lead facts only)
   └─ get_ai_client()  →  MockAIClient | OpenAIClient
   └─ persist AIOutput
   └─ WorkflowEvent("ai_summary_generated" | "outreach_generated")

4. POST /api/leads/{id}/approve-outreach   (or reject-outreach)
   └─ find latest outreach_email AIOutput
   └─ WorkflowEvent("outreach_approved" | "outreach_rejected", reason?)
   └─ Lead.status="outreach_approved" | "outreach_rejected"

5. POST /api/leads/{id}/push
   └─ services/integration_push.push_lead_to_slack
       └─ pull latest summary + outreach into the Slack payload
       └─ integrations/slack.send_slack_payload(settings.slack_webhook_url, payload)
           └─ empty url   → "mock_success"
           └─ httpx.post  → "success" | "failed"
       └─ persist IntegrationPush row
       └─ WorkflowEvent("lead_pushed")
       └─ Lead.status="pushed" on success/mock_success

6. GET /api/metrics/dashboard
   └─ services/metrics.compute_dashboard → 18 fields
```

## Why the boundaries look this way

- **Pure scorer.** `scoring/lead_scoring.py` accepts any duck-typed object and returns a dict. The same function powers both unit tests (via `SimpleNamespace`) and the live service (via SQLAlchemy ORM). Easy to test, easy to reuse.
- **AI client behind an ABC.** `MockAIClient` and `OpenAIClient` are interchangeable; the routes don't care which is active. Tests pin the guardrail prompts so production stays honest.
- **Slack split into builder + sender.** Payload construction (pure) and HTTP send (impure) live in separate functions. The builder is fully unit-testable; the sender is monkeypatched at the `httpx.post` call site.
- **Services own DB writes.** Routes do request validation; services own the persistence + workflow-event emission. Keeps router functions thin and testable.
- **Metrics is read-only.** No background aggregation. Every call to `/api/metrics/dashboard` issues plain `COUNT(*)`s. With Postgres + the indexes already on the FK columns, this comfortably scales past the demo size.

## Frontend

- App Router with `"use client"` only where hooks are needed (forms, fetch loops). The home and demo pages are static.
- One typed fetch wrapper (`lib/api.ts`) + one `APIError` class. Component code consumes `APIError.detail` directly.
- TypeScript types in `types/api.ts` mirror the FastAPI response shapes; `LeadScore.score_breakdown` is intentionally `Partial<…>` so a malformed backend response can't render a `NaN`-width bar.

## CI

GitHub Actions:

- `backend.yml`, Python 3.11 + `pip install -r requirements.txt` + `pytest -v`
- `frontend.yml`, Node 22 + `npm ci` + `npm run typecheck` + `npm run build`

Both jobs are filtered to paths so PRs that touch only docs don't rerun the full pipeline.
