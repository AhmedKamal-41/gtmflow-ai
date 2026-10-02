# GTMFlow AI, Backend

FastAPI + Python service for the GTMFlow AI platform.

## Prerequisites

- Python 3.12 (verified runtime)
- PostgreSQL 15+ (the test suite uses in-memory SQLite, no Postgres needed for `pytest`)
- Docker (optional, only for the one-liner local Postgres below)

## Setup

For a fresh isolated mock demo, use the [exact release startup commands](../docs/engineering-log/phase12-release-handoff.md#6-exact-startup-commands-local-mock-mode). They force mock integrations in both API and worker without changing an existing environment file. The commands below describe the general backend setup; inspect configuration before starting a working database.

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows PowerShell
pip install --require-hashes -r requirements-lock.txt
test -f .env || cp .env.example .env
```

## Local Postgres (optional, via Docker)

```bash
docker run --name gtmflow-postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=gtmflow \
  -p 127.0.0.1:5432:5432 -d postgres:16
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

`verify_baseline_schema.py` checks the target database's actual schema against what `0001_baseline` expects before stamping anything, and refuses (with a clear diff) if it doesn't match. See that script's docstring and `docs/engineering-log/decisions.md` for the full rationale.

Migrations are never run automatically by the app process (see `app/main.py`, which wires routes, access control and HTTP protections) -- always an explicit command, never something a web worker triggers on boot.

`python -m app.core.init_db` still exists for quick local/throwaway bring-up without Alembic (e.g. a scratch SQLite file), but is not the path for anything you intend to keep or deploy.

## Operator accounts (Phase 12)

Every route except `/health`, `/api/health` and `POST /api/auth/login` needs
a signed-in operator or viewer. State changes require an operator (except a viewer logging out). `/docs`, `/redoc` and `/openapi.json` are protected too. There is no registration endpoint; accounts are made
with the CLI (the password is prompted twice and never echoed; for scripted
setup it can come from `GTMFLOW_NEW_PASSWORD`):

```bash
python -m app.auth_cli create-user admin                  # role: operator (all actions)
python -m app.auth_cli create-user auditor --role viewer  # read-only
python -m app.auth_cli set-password admin                 # revokes admin's sessions
python -m app.auth_cli disable auditor                    # revokes their sessions
python -m app.auth_cli revoke-sessions admin
python -m app.auth_cli list
```

- **Sign-in:** `POST /api/auth/login {username, password}` sets the
  `gtmflow_session` cookie (HttpOnly, SameSite=Lax, Secure by default) and
  returns a `csrf_token`.
- **CSRF:** send that token as `X-CSRF-Token` on every POST, PUT, PATCH and
  DELETE. `GET /api/auth/session` returns it again for a live session.
  `POST /api/auth/logout` revokes the session.
- **Limits:** passwords need 12–256 characters; scrypt defaults to N=32768, r=8, p=3.
  Five consecutive failures lock the account for 15 minutes, including under
  parallel requests. A shared peer limit allows 30 attempts per five minutes. Sessions expire after
  `SESSION_IDLE_MINUTES` of inactivity or `SESSION_ABSOLUTE_HOURS` in total.
- **Recorded actors:** reviews, revisions, seller activations and delivery
  resolutions record `user:<name>`; background work records
  `job:user:<name>`; every audit event carries `actor`, and user/job events carry a stable `actor_user_id`. Security changes and login/logout are audited too. Rows from before
  Phase 12 keep their original `local-demo-unauthenticated` label.

Login requires JSON and, for browser requests, an allowed Origin. Login bodies
are bounded and passwords are never reflected in validation errors. With curl,
keep a private cookie jar, avoid putting a real password into shell history,
and pass the returned CSRF token:

```bash
umask 077
curl -c jar -H 'Content-Type: application/json' -d '{"username":"admin","password":"…"}' \
  http://localhost:8000/api/auth/login                   # note csrf_token in the response
curl -b jar -H "X-CSRF-Token: $TOKEN" -X POST http://localhost:8000/api/demo/run
```

(Tested with curl 8.5.0: it sends the `Secure` cookie to `localhost` and
`127.0.0.1` over plain http. For any other plain-http host, set
`SESSION_COOKIE_SECURE=false` for local testing only.)

## Run the API

```bash
SESSION_COOKIE_SECURE=false uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Health endpoints:

- http://localhost:8000/api/health
- http://localhost:8000/health (alias)

Both return:

```json
{ "status": "ok", "service": "gtmflow-ai-backend", "version": "0.1.0" }
```

## Background jobs (Phase 10)

Long batch operations can run as durable background jobs instead of inside
the request. Queue them with `POST /api/batches/{batch_id}/jobs` (or the
"Background jobs" panel on the batch page), then run at least one worker:

```bash
python -m app.jobs.worker            # runs until SIGINT/SIGTERM
python -m app.jobs.worker --drain    # processes every claimable job, then exits
```

Job types: `fit_score`, `legacy_score`, `generate_summary`,
`generate_outreach` (param `skip_existing`, default true) and `push_hot`
(param `force`, default false; delivers only approved drafts).

- **Progress:** `GET /api/jobs/{id}` (counts, `progress_pct`); items with
  `GET /api/jobs/{id}/items`; list with `GET /api/jobs?batch_id=`.
- **Cancel:** `POST /api/jobs/{id}/cancel`.
- **Duplicates:** an identical job already queued or running is returned
  instead of queued twice.
- **Worker shutdown:** a stopped worker hands its job back at once. A killed
  worker's job is taken over after its lease (60 s) expires.
- **Retries:** transient provider errors are retried up to 3 times per item;
  Slack deliveries are never retried automatically.

The worker uses the same `.env` as the API. **If `.env` has
`USE_MOCK_AI=false`, the worker makes real (possibly paid) model calls.**

## Slack delivery (Phase 11)

Every push goes through a delivery ledger: a repeat push of an already
delivered draft returns the earlier row with `replay: true` and sends
nothing. A deliberate re-send needs `"redeliver": true` (single lead) or
`"force": true` (batch route and `push_hot` job). A timeout or dropped
connection is recorded as `status: "unknown"` and is never resent
automatically; after checking the channel, record the finding with
`POST /api/pushes/{push_id}/resolve` (`confirmed_delivered` or
`confirmed_not_delivered`). Guarantees: `docs/integrations.md`.

## AI providers

`USE_MOCK_AI=true` (default) uses the deterministic mock. With
`USE_MOCK_AI=false`, `AI_PROVIDER` selects:

- `openai` (default): requires `OPENAI_API_KEY`.
- `qwen3-4b-lora-v1`: requires `LORA_INFERENCE_BASE_URL`, pointing to an
  OpenAI-compatible server that serves the Phase 8 adapter under that name.

Generated drafts carry runtime quality flags (`runtime-checks-v1`).
Approving a flagged outreach draft requires `acknowledged_quality_flags`
listing exactly the flags shown.

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

Sign in first (see "Operator accounts"); `$TOKEN` is the `csrf_token` from
the login response.

```bash
A='-b jar -H Content-Type:application/json'   # the session cookie from the login step
C="-H X-CSRF-Token:$TOKEN"

curl -b jar $C -X POST http://localhost:8000/api/batches/upload \
  -F "batch_name=Demo Leads" -F "file=@../sample_data/leads_sample.csv"
curl -b jar $C -X POST http://localhost:8000/api/batches/<batch_id>/score
curl -b jar $C -X POST http://localhost:8000/api/leads/<lead_id>/generate-outreach   # needs an active seller profile

# Approve the exact draft shown: its id and content_hash come from the
# generate/latest-ai-output response; list any quality_flags codes you reviewed.
curl $A $C -X POST http://localhost:8000/api/leads/<lead_id>/approve-outreach \
  -d '{"ai_output_id": "<id>", "content_hash": "<content_hash>", "acknowledged_quality_flags": []}'

# Deliver (mock unless SLACK_WEBHOOK_URL is set); a repeat is a replay
curl $A $C -X POST http://localhost:8000/api/leads/<lead_id>/push -d '{"integration_type": "slack"}'

# Queue a background job; a worker (python -m app.jobs.worker) runs it
curl $A $C -X POST http://localhost:8000/api/batches/<batch_id>/jobs -d '{"job_type": "fit_score"}'

curl -b jar http://localhost:8000/api/metrics/dashboard
```

## Mock vs real modes

| Subsystem | Default | How to flip to real |
|---|---|---|
| AI | mock (`MockAIClient`) | `USE_MOCK_AI=false` plus `AI_PROVIDER=openai` with `OPENAI_API_KEY` (paid), or `AI_PROVIDER=qwen3-4b-lora-v1` with `LORA_INFERENCE_BASE_URL`. Misconfiguration fails before any network call. |
| Slack | mock (`mock_success`) | `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/…`. Never logged or returned. |
| Database | PostgreSQL (`DATABASE_URL`) | Tests use in-memory SQLite, or `TEST_DATABASE_URL` for a disposable PostgreSQL |

## Test notes

- **Fixtures:** `client` is signed in as an operator and sends the CSRF header. `anon_client` is anonymous, and `app_client()` builds more clients (for example a viewer). All of them override `get_session` with the test database.
- **Setup and isolation:** `tests/conftest.py` forces mock AI, empty keys, mock Slack, non-Secure cookies (the test client uses plain http) and cheap password hashing. It blocks every non-loopback network connection.
- **Access control:** `test_phase12_auth.py` walks every route in the app's OpenAPI schema and asserts that anonymous requests are refused.
- **Postgres-only tests** (concurrency, process kill, takeover) skip unless `TEST_DATABASE_URL` points at a **disposable** PostgreSQL. Every table is dropped around each test.

## Layout

```
app/
├── api/            # routers (thin): auth (login + app-wide access check), batches, leads, scoring,
│                   # fit_scoring, ai, outreach_review, push, jobs, metrics, seller_profile, annotation, demo
├── core/           # config, database, actor (who is acting), hashing
├── models/         # SQLAlchemy models (incl. users/sessions, background jobs, delivery ledger)
├── schemas/        # Pydantic request/response models
├── services/       # DB writes + WorkflowEvents: generation, draft review, delivery, metrics, auth, ...
├── jobs/           # durable background jobs: queue, handlers, runner, worker CLI
├── ai/             # AIClient ABC, mock/OpenAI/LoRA clients, grounding, prompts, runtime quality checks
├── scoring/        # legacy 100-point scorer and versioned company-fit scorer (pure)
├── evaluation/     # frozen Phase 7/9 evaluation criteria and metrics
├── datasets/, pdl/ # dataset building and the PDL importer
├── integrations/   # Slack payload builder + sender
└── auth_cli.py     # operator account CLI
alembic/versions/   # 13 migrations (0001 … 0013_auth_hardening)
tests/              # pytest suite
```

## Release verification

The complete gate is `.github/workflows/release.yml`: Python 3.12, Node 24, a fresh PostgreSQL 16 service, both complete backend suites, frontend tests/typecheck/build, and `scripts/verify_release.py --with-frontend`. The harness requires an empty loopback PostgreSQL database named `gtmflow_phase12_*`; without a URL it uses temporary SQLite and explicitly skips migration verification. Historical migrations require PostgreSQL. Current results and audit coverage: [Phase 12 handoff](../docs/engineering-log/phase12-release-handoff.md), [dependency review](../docs/dependency-security.md).

## Not implemented

Deployment (hosting, TLS, a persistent model server), real email sending,
HubSpot/Salesforce/Sheets, multi-tenancy and self-service registration (by
design). Current status and release blockers:
`../docs/engineering-log/phase-status.md` and `../docs/engineering-log/phase12-release-handoff.md`.
