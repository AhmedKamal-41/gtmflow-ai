# GTMFlow AI, Backend

FastAPI + Python service for the GTMFlow AI platform.

## Prerequisites

- Python 3.11+
- PostgreSQL 15+ (the test suite uses in-memory SQLite, no Postgres needed for `pytest`)
- Docker (optional, only for the one-liner local Postgres below)

## Setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows PowerShell
pip install -r requirements.txt
cp .env.example .env
```

## Local Postgres (optional, via Docker)

```bash
docker run --name gtmflow-postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=gtmflow \
  -p 5432:5432 -d postgres:16
```

Then point `.env` at it:

```
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/gtmflow
```

## Database initialization

Tables are not auto-created on startup (schema changes should be deliberate). As of Phase 2 this is Alembic-managed:

```bash
# Fresh database (nothing in it yet)
alembic upgrade head

# An existing database that was created by the OLD `init_db.py` /
# `create_all` path (anything deployed before Phase 2) -- adopt it first,
# never run `alembic stamp head` directly:
python scripts/verify_baseline_schema.py --stamp
alembic upgrade head
```

`verify_baseline_schema.py` checks the target database's actual schema against what `0001_baseline` expects before stamping anything, and refuses (with a clear diff) if it doesn't match. See that script's docstring and `docs/upgrade/decisions.md` for the full rationale.

Migrations are never run automatically by the app process (see `app/main.py`, which only wires CORS) -- always an explicit command, never something a web worker triggers on boot.

`python -m app.core.init_db` still exists for quick local/throwaway bring-up without Alembic (e.g. a scratch SQLite file), but is not the path for anything you intend to keep or deploy.

## Run the API

```bash
uvicorn app.main:app --reload --port 8000
```

Health endpoints:

- http://localhost:8000/api/health
- http://localhost:8000/health (alias)

Both return:

```json
{ "status": "ok", "service": "gtmflow-ai-backend", "version": "0.1.0" }
```

## Run tests

```bash
pytest
```

The suite runs against in-memory SQLite via a dependency-overridden `get_session`. No Postgres, no OpenAI key, no Slack webhook required.

## API endpoints

### Health

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | Standard health payload |
| GET | `/health` | Alias |

### Batches & leads

| Method | Path | Notes |
|---|---|---|
| POST | `/api/batches/upload` | multipart (`file`, optional `batch_name`); 201 with `BatchUploadResponse` (per-row errors included) |
| GET | `/api/batches` | List batches, newest first |
| GET | `/api/batches/{batch_id}` | Batch detail with counts |
| GET | `/api/leads?batch_id=…` | All leads, optionally filtered by batch |
| GET | `/api/leads/{lead_id}` | One lead |

### Scoring

| Method | Path | Notes |
|---|---|---|
| POST | `/api/leads/{lead_id}/score` | Upserts `LeadScore`; sets `Lead.status="scored"`; emits `WorkflowEvent("lead_scored")` |
| POST | `/api/batches/{batch_id}/score` | Loops every lead in the batch; returns Hot/Warm/Cold counts + avg |
| GET | `/api/leads/{lead_id}/score` | 404 if never scored |

### AI generation

| Method | Path | Notes |
|---|---|---|
| POST | `/api/leads/{lead_id}/generate-summary` | Persists `AIOutput(output_type="company_summary")`; mock by default |
| POST | `/api/leads/{lead_id}/generate-outreach` | Persists `AIOutput(output_type="outreach_email")`; pulls the latest summary into context |
| GET | `/api/leads/{lead_id}/ai-outputs` | All outputs for one lead, newest first |
| GET | `/api/leads/{lead_id}/latest-ai-output?output_type=…` | 404 if no output of that type |

### Slack push

| Method | Path | Notes |
|---|---|---|
| POST | `/api/leads/{lead_id}/push` | Body: `{integration_type, force?}`. 400 if unscored or non-Hot without `force`. 400 if the lead's status is `do_not_contact`/`disqualified`/`unsubscribed` -- **not overridable by `force`**, regardless of score. Mock when `SLACK_WEBHOOK_URL` empty. |
| POST | `/api/batches/{batch_id}/push-hot` | Pushes every Hot lead; skips already-pushed unless `force`; blocked-status leads are reported separately as `status: "blocked"` in `results`, never pushed |
| GET | `/api/leads/{lead_id}/pushes` | Push history, newest first |

### Outreach review

| Method | Path | Notes |
|---|---|---|
| POST | `/api/leads/{lead_id}/approve-outreach` | Body: `{ai_output_id}` (required). 404 if unknown, 400 if the output belongs to a different lead or isn't outreach, 409 if a newer outreach draft now exists for this lead (stale draft). Idempotent: an identical repeat returns the existing review, `idempotent_replay: true`. |
| POST | `/api/leads/{lead_id}/reject-outreach` | Body: `{ai_output_id, reason?}` (ai_output_id required). Same validation as approve. |

### Metrics

| Method | Path | Notes |
|---|---|---|
| GET | `/api/metrics/dashboard` | 18-field adoption + ROI dashboard. Empty DB returns all zeros. |

## Sample curl commands

```bash
# Upload the demo CSV
curl -X POST http://localhost:8000/api/batches/upload \
  -F "batch_name=Demo Leads" \
  -F "file=@../sample_data/leads_sample.csv"

# Score the whole batch
curl -X POST http://localhost:8000/api/batches/<batch_id>/score

# Generate AI summary + outreach for one lead
curl -X POST http://localhost:8000/api/leads/<lead_id>/generate-summary
curl -X POST http://localhost:8000/api/leads/<lead_id>/generate-outreach

# Approve / reject outreach (ai_output_id is required -- get it from
# generate-outreach's response or GET /api/leads/<lead_id>/ai-outputs)
curl -X POST http://localhost:8000/api/leads/<lead_id>/approve-outreach \
  -H "Content-Type: application/json" \
  -d '{"ai_output_id": "<ai_output_id>"}'
curl -X POST http://localhost:8000/api/leads/<lead_id>/reject-outreach \
  -H "Content-Type: application/json" \
  -d '{"ai_output_id": "<ai_output_id>", "reason": "Too generic"}'

# Push to Slack (mock when SLACK_WEBHOOK_URL is unset)
curl -X POST http://localhost:8000/api/leads/<lead_id>/push \
  -H "Content-Type: application/json" \
  -d '{"integration_type": "slack"}'

# Push every Hot lead in a batch
curl -X POST http://localhost:8000/api/batches/<batch_id>/push-hot \
  -H "Content-Type: application/json" \
  -d '{"integration_type": "slack"}'

# Adoption + ROI dashboard
curl http://localhost:8000/api/metrics/dashboard
```

## Mock vs real modes

| Subsystem | Default | How to flip to real |
|---|---|---|
| AI (`MockAIClient`) | mock | `USE_MOCK_AI=false` + `OPENAI_API_KEY=sk-…`. Missing key surfaces `AIConfigError` *before* any network call. |
| Slack (`send_slack_payload`) | mock (`"mock_success"`) | `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/…`. URL is read at send time, never logged or returned in responses. |
| Database | Postgres (when configured) | Tests use in-memory SQLite via `StaticPool` |

## Test notes

- Endpoint tests use a `client` fixture that overrides `get_session` with a SQLite session, so each test starts from a clean schema.
- The real-OpenAI path is never invoked: a unit test instantiates `OpenAIClient(api_key="")` and asserts the clean `AIConfigError` is raised before any import or call.
- The real-Slack `httpx.post` path is monkeypatched in unit tests (`app.integrations.slack.httpx.post`) and at the service level (`app.services.integration_push.send_slack_payload`).
- A test pins the four anti-hallucination phrases in `SYSTEM_RULES` (`"only" + "data"`, `"never invent"`, `"evidence" + "inference"`, `"strict json"`).

## Layout (Phase 9)

```
app/
├── api/
│   ├── health.py            # GET /health, GET /api/health
│   ├── batches.py           # POST /api/batches/upload, GET /api/batches[/...]
│   ├── leads.py             # GET /api/leads[/...]
│   ├── scoring.py           # POST score endpoints, GET /api/leads/{id}/score
│   ├── ai.py                # generate-summary / generate-outreach / ai-outputs
│   ├── push.py              # POST push endpoints + GET /api/leads/{id}/pushes
│   ├── outreach_review.py   # POST approve-outreach / reject-outreach
│   ├── metrics.py           # GET /api/metrics/dashboard
│   └── routes.py            # aggregator imported by main.py
├── core/                    # config, database, init_db helper
├── models/                  # SQLAlchemy 2.0 models
├── schemas/                 # Pydantic request/response models
├── services/
│   ├── csv_ingestion.py     # CSV parsing + per-row validation (pure, no DB)
│   ├── ai_generation.py     # orchestrates lead -> AI client -> AIOutput
│   ├── integration_push.py  # orchestrates lead -> Slack -> IntegrationPush
│   └── metrics.py           # adoption + ROI counters (read-only)
├── scoring/
│   └── lead_scoring.py      # deterministic 100-point scoring (pure, no LLM)
├── ai/
│   ├── client.py            # AIClient ABC + OpenAIClient + get_ai_client()
│   ├── mock_client.py       # deterministic, no-network MockAIClient
│   ├── prompts.py           # strict-JSON prompt templates (real mode only)
│   └── json_parser.py       # tolerant JSON-object parser
└── integrations/
    └── slack.py             # payload builder + httpx sender (pure, no DB)
tests/                       # pytest suite
```

## Environment

All config is read from `.env` (see `.env.example`). `USE_MOCK_AI` defaults to `true`. `OPENAI_API_KEY` is only required when `USE_MOCK_AI=false`. `SLACK_WEBHOOK_URL` is optional, empty means mock pushes. The webhook URL is never logged or returned in API responses.

## Not implemented yet

Auth, real email send, HubSpot / Salesforce / Sheets / Zapier, deployment recipe. See [`../PROJECT_STATUS.md`](../PROJECT_STATUS.md) and the project roadmap in the root README.
