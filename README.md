# GTMFlow AI

**AI-powered GTM automation: turn a lead CSV into a prioritized, AI-drafted, Slack-pushed outreach queue, with adoption and ROI metrics for the team running it.**

A portfolio project demonstrating end-to-end GTM Engineering: backend service, deterministic scoring, mock-by-default AI generation, webhook delivery, and an internal-tool-style frontend, all with a real test suite and honest demo metrics.

## Problem

Sales and revenue teams sit on stale lead lists. Manually qualifying leads, drafting personalized outreach, and routing the hot ones to the right channel is hours of work per batch, and the work isn't auditable. There's no way to ask "of every AI draft we generated, what % did reps approve and what % got pushed to Slack?"

## Solution

GTMFlow AI ingests a CSV, scores each lead with a deterministic 100-point model, asks an AI client (mock by default) for a structured summary + outreach draft, lets a human approve or reject the draft, and pushes Hot leads to Slack, all while persisting every action as a `WorkflowEvent` so the dashboard can report **adoption** (how often is each step used?) and **ROI estimates** (how much time would this save?).

Both the AI client and the Slack integration ship with a **mock mode** that runs without any keys, so the entire workflow can be demoed in a clean environment.

## Why this maps to GTM Engineering

| GTM Engineer responsibility | How GTMFlow AI covers it |
|---|---|
| Build AI workflow automation | Mock + real `AIClient` interface with strict-JSON prompts and anti-hallucination guardrails |
| Move data across systems via APIs/webhooks | CSV upload → DB → AI client → Slack incoming-webhook |
| Build for non-technical revenue users | Internal-tool-style Next.js dashboard with status badges, priority badges, and one-click actions |
| Track adoption + ROI of new systems | `GET /api/metrics/dashboard` returns 18 fields covering pipeline, review, push, and time saved |
| Use Python and SQL day-to-day | FastAPI service + SQLAlchemy 2.0 + Postgres |
| Use JavaScript/TypeScript day-to-day | Next.js 15 App Router + strict TypeScript |
| Show scoring/prioritization logic | Deterministic, transparent 100-point model with per-category bars in the UI |

## Core workflow

1. **Upload CSV**, drop a lead list at `/upload`; validation errors surface per-row
2. **Score leads**, deterministic 100-point model assigns Hot / Warm / Cold
3. **Generate AI summary + outreach**, mock-by-default; structured JSON with evidence vs inference
4. **Approve or reject outreach**, captured as a `WorkflowEvent` with optional reason
5. **Push Hot leads to Slack**, mock when `SLACK_WEBHOOK_URL` is empty; otherwise real `httpx.post`
6. **Track adoption + ROI**, `/metrics` page shows counts, approval rate, push success rate, and estimated time saved

## Tech stack

| Layer | Choice |
|---|---|
| Backend | FastAPI 0.110+, SQLAlchemy 2.0, Pydantic v2, Python 3.11 |
| Database | PostgreSQL 15+ (SQLite for in-memory tests) |
| AI | Mock client by default; `openai>=1.0` SDK for real mode |
| Integrations | Slack incoming webhooks via `httpx` |
| Frontend | Next.js 15 (App Router) + TypeScript + Tailwind v3.4 |
| Testing | pytest (backend), `tsc --noEmit` + `next build` (frontend) |
| CI | GitHub Actions: `pytest` on backend; typecheck + build on frontend |

## Architecture

```
┌─────────────────────────┐        HTTP / JSON         ┌───────────────────────────────────────────┐
│  Next.js dashboard      │ ─────────────────────────▶ │  FastAPI service                          │
│  (TypeScript + Tailwind)│                            │                                           │
│                         │ ◀───────────────────────── │  ┌────────────────────────────────────┐   │
│  /, /upload, /batches,  │                            │  │ scoring/lead_scoring.py            │   │
│  /batches/[id],         │                            │  │ deterministic 100-point model      │   │
│  /leads/[id],           │                            │  └────────────────────────────────────┘   │
│  /metrics, /demo        │                            │  ┌────────────────────────────────────┐   │
└─────────────────────────┘                            │  │ ai/  (mock or real OpenAI client)  │   │
                                                       │  └────────────────────────────────────┘   │
                                                       │  ┌────────────────────────────────────┐   │
                                                       │  │ integrations/slack.py  (httpx)     │   │
                                                       │  └────────────────────────────────────┘   │
                                                       │  ┌────────────────────────────────────┐   │
                                                       │  │ services/metrics.py                │   │
                                                       │  │ adoption + ROI counters            │   │
                                                       │  └────────────────────────────────────┘   │
                                                       │  ┌────────────────────────────────────┐   │
                                                       │  │ SQLAlchemy models                  │   │
                                                       │  │ Lead / LeadBatch / LeadScore /     │   │
                                                       │  │ AIOutput / IntegrationPush /       │   │
                                                       │  │ WorkflowEvent                      │   │
                                                       │  └─────────────────┬──────────────────┘   │
                                                       └────────────────────┼──────────────────────┘
                                                                            │
                                                                            ▼
                                                                ┌──────────────────────┐
                                                                │  PostgreSQL          │
                                                                └──────────────────────┘
                                                                            │
                                                                            ▼ (optional, real mode)
                                                                ┌──────────────────────┐
                                                                │  OpenAI API          │
                                                                ├──────────────────────┤
                                                                │  Slack webhook       │
                                                                └──────────────────────┘
```

## Features

| Area | Feature | Status |
|---|---|---|
| Ingestion | CSV upload with row-level validation | ✅ |
| Ingestion | Header normalization + unknown-column preservation in `cleaned_data` | ✅ |
| Scoring | Deterministic 100-point model with Hot / Warm / Cold bands | ✅ |
| Scoring | Per-category breakdown + matched-signal lists | ✅ |
| AI | Mock summary + outreach (no key required) | ✅ |
| AI | Real OpenAI mode (lazy-loaded, strict-JSON prompts) | ✅ (mock-by-default) |
| AI | Anti-hallucination guardrails (evidence vs inference) | ✅ |
| Workflow | Outreach approve / reject with optional rejection reason | ✅ |
| Delivery | Slack incoming-webhook (mock by default) | ✅ |
| Delivery | Push audit trail (`IntegrationPush` rows) | ✅ |
| Metrics | Adoption + ROI dashboard (18 fields) | ✅ |
| UI | Internal-tool-style Next.js dashboard (7 user pages) | ✅ |
| CI | GitHub Actions: backend pytest, frontend typecheck + build | ✅ |
| Auth |, | ❌ (out of MVP scope) |
| Integrations | HubSpot / Salesforce / Sheets / Zapier | ❌ (roadmap) |
| Delivery | Real email send | ❌ (intentionally not built) |

## Screenshots

Real captures of the running app at 1440×900, taken via Puppeteer against the FastAPI backend (SQLite demo DB) and the Next.js dev server, with `sample_data/leads_sample.csv` pre-loaded and the full demo flow run (upload → score → summary → outreach → approve → push-hot).

### Home, `/`

![GTMFlow AI home page](docs/screenshots/home.png)

### Upload, `/upload`

![CSV upload form](docs/screenshots/upload.png)

### Batches, `/batches`

![Batches list](docs/screenshots/batches.png)

### Batch detail, `/batches/[batchId]`

The deterministic 100-point scorer applied to the 10-row sample CSV: 2 Hot, 4 Warm, 4 Cold. Status badges show the two Hot leads have already been pushed.

![Batch detail with leads table](docs/screenshots/batch-detail.png)

### Lead workspace, `/leads/[leadId]`

Cascade Modular Homes, full workspace with score breakdown (94/100), matched signals, AI summary, outreach draft, and push history.

![Lead workspace](docs/screenshots/lead-detail.png)

### Metrics dashboard, `/metrics`

After the demo flow: 10 processed, 100% automation coverage, 1 outreach generated and approved, 2 unique leads pushed at 100% success rate, 50 minutes / 0.83 hours saved (estimated).

![Adoption + ROI metrics](docs/screenshots/metrics.png)

### Demo walkthrough, `/demo`

![Demo guide page](docs/screenshots/demo.png)

## Demo workflow

Full step-by-step lives at [`docs/demo-script.md`](docs/demo-script.md). The short version:

1. Start the backend (`uvicorn app.main:app --reload --port 8000`).
2. Start the frontend (`npm run dev`).
3. Open http://localhost:3000/upload and pick `sample_data/leads_sample.csv`.
4. On the batch page, click **Score batch** → 2 Hot, 4 Warm, 4 Cold.
5. Open the Cascade Modular Homes Hot lead.
6. Click **Generate summary** → **Generate outreach** → **Approve outreach** → **Push to Slack**.
7. Open `/metrics` and explain the numbers.

## Setup

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows PowerShell
pip install -r requirements.txt
cp .env.example .env
python -m app.core.init_db         # only after Postgres is reachable
uvicorn app.main:app --reload --port 8000
```

Optional one-liner Postgres:

```bash
docker run --name gtmflow-postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=gtmflow \
  -p 5432:5432 -d postgres:16
```

### Frontend

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

## Environment variables

`.env.example` files live in both `backend/` and `frontend/`. Highlights:

| Variable | Where | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | backend | `postgresql+psycopg://postgres:postgres@localhost:5432/gtmflow` | Required to actually use the DB; tests use SQLite in-memory. |
| `USE_MOCK_AI` | backend | `true` | When `true`, every AI call is served by `MockAIClient`, no key needed. |
| `OPENAI_API_KEY` | backend | (empty) | Only required when `USE_MOCK_AI=false`. Validated at client construction. |
| `SLACK_WEBHOOK_URL` | backend | (empty) | Empty → push status is `mock_success` (no network). Set it to enable real delivery. URL is never logged or returned in responses. |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | `http://localhost:8000` | Pointed at the FastAPI service. |

## Backend API overview

Full curl examples in [`backend/README.md`](backend/README.md). At a glance:

```
GET    /api/health
GET    /health

POST   /api/batches/upload
GET    /api/batches
GET    /api/batches/{batch_id}

GET    /api/leads?batch_id=…
GET    /api/leads/{lead_id}

POST   /api/leads/{lead_id}/score
GET    /api/leads/{lead_id}/score
POST   /api/batches/{batch_id}/score

POST   /api/leads/{lead_id}/generate-summary
POST   /api/leads/{lead_id}/generate-outreach
GET    /api/leads/{lead_id}/ai-outputs
GET    /api/leads/{lead_id}/latest-ai-output?output_type=…

POST   /api/leads/{lead_id}/approve-outreach
POST   /api/leads/{lead_id}/reject-outreach    {reason?: string}

POST   /api/leads/{lead_id}/push               {integration_type: "slack", force?: bool}
POST   /api/batches/{batch_id}/push-hot        {integration_type: "slack", force?: bool}
GET    /api/leads/{lead_id}/pushes

GET    /api/metrics/dashboard
```

## Scoring model

Deterministic, transparent, no LLM. Detailed in [`docs/scoring-model.md`](docs/scoring-model.md).

| Category | Max | Signal |
|---|---|---|
| Industry fit | 25 | property management, housing, multifamily, healthcare, clinic, dental, etc. |
| Company size fit | 15 | 1–10 → 5; 11–50 → 8; 51–200 → 12; 201–500 → 14; 500+ → 15 |
| Persona / title fit | 15 | VP Ops, Property Manager, Practice Manager, Founder, RevOps, etc. |
| Pain-point keywords | 20 | leasing, maintenance, tenant, patient, appointment, intake, call volume, etc. |
| Data completeness | 10 | +2 each for website, industry, contact name/email/title |
| Source quality | 10 | referral/webinar/conference/inbound → 10; website/csv → 6; unknown → 2 |
| Penalties | -15 | free-email domain –5; missing title –5; disqualified status –10 |

**Bands:** Hot ≥ 80, Warm 55–79, Cold 0–54. Detailed reasoning string + matched-signal lists returned per lead.

## AI guardrails

Detailed in [`docs/ai-workflow.md`](docs/ai-workflow.md). Highlights:

- **Mock by default.** No key required to demo the workflow.
- **Real mode is lazy.** OpenAI SDK is imported inside the call; missing key → `AIConfigError` before any network call.
- **Strict-JSON prompts.** Real-mode prompts forbid fake metrics, fake customer names, fake tool stacks, and "email has been sent" framing.
- **Evidence vs inference.** Every inference in mock output is prefixed `"Inference: …"`; evidence rows quote the literal lead field.
- **Pinned in tests.** A dedicated test asserts the guardrail phrases stay in `SYSTEM_RULES`.

## Slack / webhook integration

Detailed in [`docs/integrations.md`](docs/integrations.md). Highlights:

- **Mock by default** when `SLACK_WEBHOOK_URL` is empty, no network call.
- **Real mode** uses `httpx.post(url, json=payload, timeout=10)`.
- **Audit trail**, every attempt persists an `IntegrationPush` row with the full payload + status (`success` / `mock_success` / `failed`) + response text (truncated, ≤ 500 chars).
- **Secret safety**, the webhook URL is never logged, never returned in API responses, and httpx connection errors are sanitized so they can't leak the URL via `repr()`.
- **Hot-only by default** on the single-lead push, with `"force": true` to override. The batch endpoint also has a "skip already-pushed" guard.

## Metrics dashboard

Detailed in [`docs/metrics.md`](docs/metrics.md). One endpoint:

```
GET /api/metrics/dashboard
```

Returns 18 fields across four sections (pipeline, outreach review, push delivery, estimated time saved). All percentages handle divide-by-zero by returning `0.0`. `leads_pushed` is row count; `unique_leads_pushed` deduplicates by lead. Time-saved is `5 minutes × processed leads` and is **explicitly framed as an estimated demo metric**, not real revenue impact.

## Testing

```bash
# Backend (from backend/)
pytest

# Frontend (from frontend/)
npm run typecheck
npm run build
```

The backend test suite covers ingestion, scoring, AI generation in mock mode, Slack payload + send (real-path is monkeypatched, never hits real Slack), outreach review, metrics, and the empty-DB edge cases. Frontend gates on strict TypeScript + a clean `next build`. Both are wired into GitHub Actions.

## Known limitations

- **No auth.** Anyone with API access can call any endpoint.
- **No real email send.** "Outreach" is always a draft persisted in `AIOutput`.
- **No HubSpot / Salesforce / Sheets / Zapier.** Slack is the only delivery target.
- **Time-saved is an estimate.** 5 minutes per processed lead, hardcoded in `services/metrics.py`. Not measured against actual rep time.
- **No background jobs.** Scoring + AI + push all happen synchronously in the request that triggered them.
- **No deployment.** The README's Postgres + Docker instructions are dev-only.

## Roadmap

- Approval-aware push (skip pushing leads whose outreach was rejected)
- Configurable per-tenant scoring weights
- HubSpot / Salesforce sync (CRM as the system of record)
- Background queue for AI generation + Slack delivery (Celery or Arq)
- Auth + per-user audit
- Real per-rep time-saved measurement (replace the 5-min constant)
- Deployment recipe (Railway / Vercel)

## Resume bullets

The honest, project-grounded versions live in [`docs/resume-bullets.md`](docs/resume-bullets.md).

## Docs index

- [`docs/architecture.md`](docs/architecture.md), components + data flow
- [`docs/scoring-model.md`](docs/scoring-model.md), the deterministic 100-point model
- [`docs/ai-workflow.md`](docs/ai-workflow.md), mock vs real, prompts, guardrails
- [`docs/integrations.md`](docs/integrations.md), Slack + secret safety
- [`docs/metrics.md`](docs/metrics.md), adoption + ROI math
- [`docs/demo-script.md`](docs/demo-script.md), 11-step interview demo
- [`docs/interview-notes.md`](docs/interview-notes.md), 20s / 60s / technical / GTM explanations
- [`docs/resume-bullets.md`](docs/resume-bullets.md), five honest bullets, ≤ 17 words each
- [`PROJECT_STATUS.md`](PROJECT_STATUS.md), current state, test counts, what is mock vs real
