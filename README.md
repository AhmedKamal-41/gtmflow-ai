<div align="center">
  <img src="docs/assets/logo.svg" alt="GTMFlow logo: lead lines converging on a single point" width="110" />
</div>

# GTMFlow AI - Lead-to-Outreach Workflow

An internal go-to-market tool for one sales team. It imports a lead list, scores every company with
transparent rules, drafts outreach grounded only in the company's own record, requires a signed-in
person to approve the exact draft, and routes approved Hot leads to Slack through a delivery ledger
that cannot double-send. Every state change is audited, and the dashboard reports adoption without
mixing mock and real activity.

Drafts are written by **`qwen3-4b-lora-v1`**, a Qwen3-4B model fine-tuned with LoRA for this project
and evaluated against its base model. When its model server isn't connected, the app says so and
uses a labeled demo generator, so the whole workflow still runs.

FastAPI backend · PostgreSQL with Alembic · database-backed job worker · Next.js 15 frontend ·
PyTorch + PEFT for the model work · Slack incoming webhooks.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.1-D71F00?logo=sqlalchemy&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-15-000000?logo=nextdotjs&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)
![PyTorch](https://img.shields.io/badge/PyTorch-LoRA%20%2B%20PEFT-EE4C2C?logo=pytorch&logoColor=white)
![Slack](https://img.shields.io/badge/Slack-webhooks-4A154B?logo=slack&logoColor=white)
[![Release verification](https://github.com/AhmedKamal-41/gtmflow-ai/actions/workflows/release.yml/badge.svg?branch=main)](https://github.com/AhmedKamal-41/gtmflow-ai/actions/workflows/release.yml)

**Runs locally without keys or a GPU:** Slack is mocked and drafts fall back to the demo generator
until the fine-tuned model's server is connected. It is not deployed; see [Local setup](#local-setup).

<p align="center">
  <a href="docs/screenshots/home.png">
    <img src="docs/screenshots/home.png" alt="GTMFlow home page with the headline &quot;Stop hand-sorting leads. Let GTMFlow do the first pass.&quot; and buttons to run the live demo or upload a lead list" width="100%">
  </a>
</p>

## Table of contents

- [Overview](#overview)
- [Key features](#key-features)
- [Engineering highlights](#engineering-highlights)
- [System architecture](#system-architecture)
- [Lead lifecycle](#lead-lifecycle)
- [Grounded generation and review](#grounded-generation-and-review)
- [Slack delivery ledger](#slack-delivery-ledger)
- [Fine-tuned model](#fine-tuned-model)
- [Technology stack](#technology-stack)
- [Security](#security)
- [Screenshots](#screenshots)
- [Testing strategy](#testing-strategy)
- [API reference](#api-reference)
- [Local setup](#local-setup)
- [Environment variables](#environment-variables)
- [Deployment](#deployment)
- [Project structure](#project-structure)
- [Known limitations](#known-limitations)
- [Future improvements](#future-improvements)
- [Engineering log](#engineering-log)
- [Author](#author)

## Overview

GTMFlow answers a revenue team's everyday question: *which of these leads matter, what do we say to
them, and how do we get the good ones to the right rep without anything going out by mistake?*

An operator uploads a CSV of companies. Each company gets two scores: a deterministic 100-point
priority score (Hot, Warm or Cold, with the reasons shown) and a versioned company-fit score that
reports how much evidence it actually had. The operator then generates a short company summary and
an outreach draft. The model receives only the company's own facts and an explicitly activated
seller profile, and it must cite the id of every fact it uses. A draft that cites anything outside
that context is rejected and never saved.

A person approves or rejects the exact draft on screen. The approval is tied to that draft's
content hash, so editing the draft, changing the company's data or switching seller profiles voids
it. Only a current approval lets a Hot lead be routed to Slack. Delivery goes through a
claim-before-send ledger: a repeat is a replay, and a timeout is recorded as **unknown** for an
operator to resolve, never resent automatically.

Long batch work (scoring, generation, routing) runs in a separate worker process, from a job queue
stored in PostgreSQL, with progress, cancellation, bounded retries and crash recovery. Everything
sits behind server-side sessions with CSRF protection and roles, and every audit event records who
did it, including work the worker does on a user's behalf. Visitors can create an account (confirmed
by an emailed code) or continue as a guest, when the server enables those options.

## Key features

- **CSV import with validation:** bounded and streamed; bad rows are reported, and unknown columns
  are kept on each lead for scoring.
- **Explainable scoring:** a 100-point priority score with the breakdown and matched signals, plus a
  versioned company-fit score with evidence coverage and an explicit "insufficient evidence" band.
- **Grounded AI drafts:** a summary and an outreach email per lead, citing record facts and seller
  capabilities by id, with the unknowns stated rather than guessed.
- **The fine-tuned model drafts:** `qwen3-4b-lora-v1` through an OpenAI-compatible server, with a
  live status indicator. If the server is offline, drafts come from a labeled demo generator and are
  recorded as such. OpenAI is also supported.
- **Exact-draft approval:** approve, reject, or edit into a new immutable revision. Runtime quality
  checks flag known problem phrasings, and approving a flagged draft requires an explicit
  acknowledgement.
- **Seller profiles:** versioned, immutable revisions. Activation is explicit, and a demonstration
  profile is labeled as such and can never make an email "ready".
- **Background jobs:** fit-score, legacy-score, summary, outreach and push-hot jobs per batch,
  with live progress and cancellation.
- **Safe Slack routing:** at most one send per claimed attempt, with replay, explicit unknown
  outcomes and operator resolution.
- **Honest metrics:** approval counted once per draft by its latest review, delivery outcomes, mock
  versus real breakdowns, and time saved labeled as an estimate.
- **Built around a rep's day:** **Today** shows what needs attention, **Leads** ranks every lead by
  priority and workflow stage, and each lead page puts the draft, the review and sending in one place.
  New users can **try with sample data**, which imports 10 companies the normal way.
- **Three ways in:** sign in, create an account confirmed with an emailed 6-digit code, or press
  **Continue as guest** to use the app immediately. Sign-up and guest access are server settings,
  off by default.

## Engineering highlights

- **Grounding enforced by the server, not the prompt.** The generation context gives every record
  fact, seller capability and approved claim an id. The output schema requires the draft to list
  the ids it relied on, and `app/ai/grounding.py` rejects any id that is not in the context before
  anything is stored. Unknowns such as budget, intent and current tools are passed in explicitly,
  so the model is told what it must not invent.

- **Approval bound to content, not to a row.** An approval stores the SHA-256 of the exact draft
  shown, together with the hash of the company inputs and the seller-profile revision. Delivery
  re-checks all three, so a stale browser tab cannot approve a newer draft, and a changed lead
  record invalidates an earlier approval instead of sending it.

- **Claim-before-send delivery.** Each approved-draft attempt has a unique `delivery_key`. The
  claim row is committed *before* the webhook call, so concurrent requests and restarted workers
  find the claim and replay its result instead of sending again. In a PostgreSQL test, eight
  simultaneous pushes produced exactly one dispatch. A network timeout is recorded as `unknown`, an
  operator resolves it after checking the channel, and redelivery needs an explicit flag.

- **A job queue without Redis.** Jobs and their items live in `background_jobs` and
  `background_job_items`. A worker leases a job, renews the lease in the same transaction as each
  item it commits (a fenced update, so a worker that lost its lease cannot commit), retries
  transient failures a bounded number of times, and stops a crash loop. In a test, a worker was
  SIGKILLed partway through a 300-lead job; a second worker finished it with exactly 300 outputs.

- **Metrics that cannot exceed 100%.** Approval is computed over distinct operational outreach
  drafts by each draft's latest review, using a window function. A draft approved, rejected and
  approved again counts once. Generation and delivery are each split into mock, real and "not
  recorded", so a demo can never inflate a real number.

- **Deny-by-default access control.** An app-wide FastAPI dependency protects every route; the only
  public paths are the health checks and the sign-in, sign-up and guest endpoints. A test enumerates every operation
  in the OpenAPI schema and asserts that anonymous requests are refused. A SQLAlchemy
  `before_insert` hook stamps the current actor on every `WorkflowEvent`, including events written
  by the worker process.

- **Provenance on every AI output.** Each output records its prompt version, output schema, model
  and adapter revision, input hash and seller-profile revision, so any draft can be traced to the
  exact inputs that produced it.

- **Honest model fallback.** With `LORA_FALLBACK_TO_MOCK=true`, each generation first checks that the
  fine-tuned model's server answers (3-second probe, cached 30 seconds). If it doesn't, the draft comes
  from the demo generator and is recorded as mock, so metrics never count it as real; a request that
  fails mid-way is never silently retried on another model. Without the fallback, real providers fail
  closed. An empty `SLACK_WEBHOOK_URL` routes to a mock webhook, and the URL is never logged or returned.

- **Explicit, verified migrations.** Fourteen Alembic migrations, never run automatically on boot.
  A baseline verifier refuses to stamp a database whose live schema does not match, and the release
  harness checks that the migrations preserve existing data and audit columns.

- **Reproducible model work.** Datasets are split by company group to prevent leakage and frozen by
  sha256. The trainer refuses the test split and any dataset built with a different prompt version.
  GPU pods were launched with five layers of self-termination safeguards and deleted themselves
  with their own credentials.

## System architecture

```mermaid
flowchart TB
    UI["Browser<br/>Next.js 15 pages"] -->|"session cookie<br/>+ X-CSRF-Token"| Proxy["Next.js server<br/>same-origin /api proxy"]
    Proxy --> API["FastAPI<br/>auth · CSRF · roles<br/>64 operations"]

    API <-->|"data, audit events,<br/>job queue"| PG[("PostgreSQL 16")]
    PG <-->|"leased jobs,<br/>item results"| Worker["Job worker<br/>app.jobs.worker"]

    API --> Gen
    Worker --> Gen
    API --> Ledger
    Worker --> Ledger

    subgraph Gen["Generator (one selected)"]
        direction LR
        Mock["Mock (default)"]
        OpenAI["OpenAI (opt-in)"]
        LoRA["qwen3-4b-lora-v1"]
    end

    Ledger["Delivery ledger<br/>claim before send"] --> Slack["Slack webhook<br/>(mock when unset)"]
```

The browser only ever talks to its own origin. The Next.js server proxies `/api` to FastAPI, so the
session cookie can stay `HttpOnly`, `SameSite=Lax` and `Secure`. The API and the worker apply the
same gates (approval, eligibility, delivery claim) because they call the same service functions.

## Lead lifecycle

```mermaid
flowchart TD
    A["Upload CSV<br/>bounded, streamed, row-level errors"] --> B["Leads stored<br/>unknown columns kept"]
    B --> C["Legacy priority score (v1)<br/>100 points → Hot / Warm / Cold"]
    B --> D["Company-fit score (v2)<br/>versioned profile + evidence coverage"]
    C --> E{"Seller profile<br/>activated?"}
    D --> E
    E -->|no| E1["Summary only;<br/>outreach unavailable"]
    E -->|yes| F["Generate summary + outreach<br/>grounded context, cited ids"]
    F --> G{"Server validation<br/>ids in context, schema"}
    G -->|fail| G1["Nothing saved"]
    G -->|pass| H["Draft + runtime quality flags"]
    H --> I{"Operator review"}
    I -->|reject| I1["Rejected with reason"]
    I -->|edit| H
    I -->|"approve exact hash<br/>(acknowledge flags)"| J["Approved"]
    J --> K{"Hot, eligible,<br/>approval current?"}
    K -->|no| K1["Refused with reason"]
    K -->|yes| L["Slack delivery ledger"]
    L --> M["Metrics<br/>mock and real kept apart"]
```

Every step above writes a `WorkflowEvent` with its actor, so the full history of any lead can be
reconstructed.

## Grounded generation and review

```mermaid
flowchart TB
    Context["<b>Generation context</b><br/>record facts: fact-company_name, fact-industry, …<br/>seller capabilities: cap-1, cap-2, …<br/>approved claims · stated unknowns: budget, intent, tools"]

    subgraph Pipeline["Generation and checks"]
        direction LR
        P["Prompt grounded-v2<br/>+ output schema v2"] --> M["Generator<br/>mock · OpenAI · LoRA"]
        M --> V{"Cited ids ⊆<br/>context ids?"}
        V -->|no| X["Rejected,<br/>not stored"]
        V -->|yes| Q["Runtime checks<br/>runtime-checks-v1"]
    end

    subgraph Review["Storage and review"]
        direction LR
        S["Stored AI output<br/>prompt, model, adapter,<br/>input hash, seller revision"] --> R["Approve / reject<br/>bound to content hash"]
    end

    Context --> Pipeline
    Pipeline --> Review
```

The company-fit score is labeled "not evidence of interest", and the model is instructed to ask
rather than assume. The runtime checks come from the phrasings the blind evaluation identified as
problems; they flag a draft for the reviewer rather than silently rewriting it.

## Slack delivery ledger

```mermaid
sequenceDiagram
    participant Op as Operator / worker
    participant API as Delivery service
    participant DB as PostgreSQL
    participant Slack as Slack webhook

    Op->>API: push lead
    API->>API: check Hot, eligible, approval current
    API->>DB: INSERT claim (unique delivery_key) and commit
    alt claim already exists
        DB-->>API: existing claim
        API-->>Op: replay stored outcome (no send)
    else new claim
        API->>Slack: POST message
        alt 2xx
            API->>DB: status = success
        else error response
            API->>DB: status = failed
        else timeout or connection lost
            API->>DB: status = unknown
            Note over Op,DB: Never resent automatically.<br/>An operator checks the channel and resolves it:<br/>POST /api/pushes/{id}/resolve
        end
        API-->>Op: outcome
    end
```

The guarantee is *at most one send per claimed attempt*. It is not exactly-once, because Slack
webhooks have no idempotency key; that is why an uncertain outcome becomes a person's decision.

## Fine-tuned model

| Item | Detail |
|---|---|
| Base model | `Qwen/Qwen3-4B-Instruct-2507`, pinned revision, Apache-2.0 |
| Method | bf16 LoRA, r = 16, alpha = 32, on all attention and MLP projections; no quantization |
| Data | 419 training, 74 validation and 71 test examples, split by company group and frozen by sha256. Training references were reviewed by AI, with 5 human decisions |
| Training | 3 epochs on one rented L4: 63 minutes, $0.61 |
| Held-out test | Factual support 0.986, missing-information handling 1.000, automated writing checks 1.000 |
| Blind AI review | 49 of 71 drafts (69%) acceptable as-is: summaries 35 of 35, outreach 14 of 36. Base model: 1 of 71 |
| Integration | The app's own client against a temporary L4 pod: all 8 acceptance criteria passed, median 22.6 s per request; the pod deleted itself |

```mermaid
flowchart TB
    subgraph Build["Data and training"]
        direction LR
        A["AI-reviewed<br/>candidates"] --> B["Correction policy v2<br/>example weights"]
        B --> C["Frozen splits<br/>train / val / test<br/>by sha256"]
        C --> D["LoRA training<br/>validation loss,<br/>early stopping"]
    end
    subgraph Prove["Evaluation and integration"]
        direction LR
        E["Held-out generation<br/>test split, both systems"] --> F["Automatic criteria<br/>+ blind AI review"]
        F --> G["Runtime checks<br/>from the failure analysis"]
        G --> H["LoraClient in the app<br/>verified on a temporary GPU"]
    end
    Build --> Prove
```

**These results are AI-evaluated, not human-verified**, and the test set is small. The weights and
datasets are not committed. Details: [training](docs/engineering-log/phase8-training-handoff.md),
[evaluation](docs/engineering-log/phase9-evaluation-handoff.md),
[integration](docs/engineering-log/phase10-integration-handoff.md), and the
[training code](training/README.md).

## Technology stack

Backend:

| Concern | Technology | Purpose |
|---|---|---|
| Language | Python 3.12 | Backend and training code |
| Web framework | FastAPI 0.141 | API, dependency-injected auth, OpenAPI docs |
| ORM / migrations | SQLAlchemy 2.1, Alembic 1.20 | Data access and 14 explicit, versioned migrations |
| Validation | Pydantic 2.13, pydantic-settings | Request and response schemas, typed configuration |
| Database driver | psycopg 3 | PostgreSQL access |
| HTTP client | httpx | Slack webhooks, OpenAI-compatible inference |
| Server | Uvicorn | ASGI server |
| Dependencies | `requirements-lock.txt` | Hash-locked install (`--require-hashes`) |

Frontend:

| Concern | Technology | Purpose |
|---|---|---|
| Framework | Next.js 15 (App Router), React 19 | Pages and the same-origin API proxy |
| Language | TypeScript 5 (strict) | Typed API client and components |
| Styling | Tailwind CSS 3 | Design tokens and layout |
| Tests | Vitest, Testing Library, jsdom | Page, component and API-client tests |

AI and model work:

| Concern | Technology | Purpose |
|---|---|---|
| Default generator | Deterministic mock | Full workflow without keys or cost |
| Hosted generator | OpenAI (opt-in) | Paid alternative to the fine-tuned model |
| Fine-tuning | PyTorch, transformers, PEFT | LoRA training of Qwen3-4B |
| Serving | OpenAI-compatible server (`serve.py`) | Adapter inference for the app's `LoraClient` |
| GPU runs | RunPod, self-deleting pods | Training, evaluation and the acceptance test |

Infrastructure and tooling:

| Concern | Technology | Purpose |
|---|---|---|
| Database | PostgreSQL 16 (SQLite for quick tests) | All state, including the job queue |
| CI | GitHub Actions | Backend, frontend, training and release-verification workflows |
| Release check | `backend/scripts/verify_release.py` | 96 live checks against a fresh database |

## Security

Only mechanisms present in the code are listed here.

- **Deny by default.** Every route requires a session except the health checks, sign-in options,
  and the sign-in, sign-up, verification and guest endpoints. `/docs`, `/redoc` and `/openapi.json`
  also require a session.
- **Server-side sessions.** A 256-bit random token per login, stored only as its SHA-256. The
  cookie (`gtmflow_session`) is `HttpOnly`, `SameSite=Lax` and `Secure` by default. Sessions have a
  60-minute idle timeout and a 12-hour absolute lifetime, are rotated at login, and are revoked on
  logout, password change and account disable.
- **CSRF protection.** Every state-changing request needs an `X-CSRF-Token` header derived from the
  session, which only a same-origin page can read from `GET /api/auth/session`.
- **Passwords.** scrypt (N = 2^15, r = 8, p = 3) with a random salt and 12 to 256 characters.
  Hashes are upgraded at login when the cost settings rise. Operator accounts can be created from
  a CLI that prompts without echo.
- **Sign-up with email verification.** A new account cannot sign in until the 6-digit code sent
  to its address is entered. Only a hash of the code is stored; it expires after 10 minutes, allows
  5 wrong guesses, and can be re-sent after 60 seconds. Responses are identical whether or not an
  address already has an account, and sign-up can be limited to approved email domains.
- **Guest access.** Each guest gets a fresh account with no password and a 2-hour session. Guests
  can change data only while the server is guest-safe (no Slack webhook, and drafts come from the demo
  generator or the self-hosted fine-tuned model), so a guest can never trigger a real message or a
  paid, per-request API call.
- **Brute-force limits.** Five consecutive failures lock an account for 15 minutes. A
  database-backed limiter allows 30 attempts per client per 5 minutes across all API processes,
  and an unknown username takes as long as a wrong password.
- **Public endpoint hardening.** Sign-in, sign-up, verification and guest requests are JSON-only,
  Origin-checked, limited to 16 KiB and throttled per client (in separate buckets), with one
  generic error message for every sign-in failure.
- **Roles.** `operator` can change data; `viewer` is read-only; `guest` acts as an operator only
  on a guest-safe server.
- **Audit.** Every `WorkflowEvent` records its actor, including events written by the worker.
- **Secrets.** Configuration comes from the environment. `.env` files are git-ignored, and the
  Slack webhook URL is never logged or returned.
- **Supply chain.** The backend installs from a hash-locked file. As of 2026-09-27 the frontend and
  locked backend audits report 0 findings; see the [dependency review](docs/dependency-security.md).

## Screenshots

Captured from the running application (production build, mock mode, isolated PostgreSQL) at a
1440 px viewport after running the built-in demo. Click any image for full resolution.

### Lead workspace: scoring

The 100-point priority score with its breakdown and matched signals, and the company-fit score,
which reports *insufficient evidence* when a CSV lacks structured fields instead of guessing.

<p align="center">
  <a href="docs/screenshots/lead-scoring.png">
    <img src="docs/screenshots/lead-scoring.png" alt="Lead workspace for Cascade Modular Homes showing a 94 of 100 Hot priority score with a per-category breakdown, matched signals, and a company-fit table" width="100%">
  </a>
</p>

### Lead workspace: readiness, review and the grounded draft

The approval applies to this exact draft. The draft cites record facts and seller capabilities by
id, and lists what is not known from the data. A demonstration seller profile can never make an
email "ready", only an internal Slack handoff.

<p align="center">
  <a href="docs/screenshots/lead-review.png">
    <img src="docs/screenshots/lead-review.png" alt="Readiness panel, an approved review bound to a content hash, and an outreach draft with cited fact ids, seller capability ids and stated unknowns" width="100%">
  </a>
</p>

### Slack delivery

<p align="center">
  <a href="docs/screenshots/push-history.png">
    <img src="docs/screenshots/push-history.png" alt="Push history showing a mock Slack delivery with the Hot lead message, attempt number and mock-success status" width="100%">
  </a>
</p>

### Batch detail

<p align="center">
  <a href="docs/screenshots/batch-detail.png">
    <img src="docs/screenshots/batch-detail.png" alt="Batch detail with background job controls, the company-fit summary and a table of ten leads with priority, fit, routing and readiness columns" width="100%">
  </a>
</p>

### Background jobs

<p align="center">
  <a href="docs/screenshots/background-jobs.png">
    <img src="docs/screenshots/background-jobs.png" alt="Background jobs panel with completed summary, fit-score and legacy-score jobs, each 10 of 10" width="100%">
  </a>
</p>

### Metrics

<p align="center">
  <a href="docs/screenshots/metrics.png">
    <img src="docs/screenshots/metrics.png" alt="Metrics dashboard with a mock-data banner, pipeline funnel, priority split, quality rates, estimated time saved and a mock-versus-real breakdown" width="100%">
  </a>
</p>

### More pages

| Sign in | Upload |
|---|---|
| [![Sign-in form with create-account and continue-as-guest options](docs/screenshots/login.png)](docs/screenshots/login.png) | [![CSV upload form](docs/screenshots/upload.png)](docs/screenshots/upload.png) |
| **Batches** | **Guided demo** |
| [![Batches list](docs/screenshots/batches.png)](docs/screenshots/batches.png) | [![One-click demo page](docs/screenshots/demo.png)](docs/screenshots/demo.png) |
| **Email verification** | **Seller profile** |
| [![Check-your-email step with a 6-digit code field and a resend countdown](docs/screenshots/verify-email.png)](docs/screenshots/verify-email.png) | [![Seller profile editor with an active demonstration profile](docs/screenshots/seller-profile.png)](docs/screenshots/seller-profile.png) |

## Testing strategy

| Layer | Location | What it covers |
|---|---|---|
| Backend | `backend/tests/` | Scoring, ingestion, grounding validation, review and revisions, seller profiles, jobs and leases, the delivery ledger, metrics, auth, migrations |
| PostgreSQL | same suite with `TEST_DATABASE_URL` | Real concurrency (simultaneous pushes, lease races), process-kill recovery, migration preservation |
| Frontend | `frontend/src/**/*.test.ts(x)` | Today, Leads, Imports, Settings, sidebar, lead, import, metrics and seller-profile pages; the API client (CSRF, 401 handling); auth provider, jobs panel, push history |
| Training | `training/tests/` | Weighted objective, split guards, adapter hash checks, launcher secret handling, serving limits |
| Release harness | `backend/scripts/verify_release.py` | 96 live checks: production frontend, API, worker, sign-up and guest access on a fresh database |

Tests block outbound network calls. Key safety tests were mutation-checked: the safeguard was
removed and the test had to fail.

Latest verified results (2026-10-02, local): **545 backend tests on PostgreSQL** (540 passed and 5
skipped on SQLite), **112 frontend tests**, **96 of 96 release checks**, and a clean typecheck and
production build. The same workflows run in [CI](https://github.com/AhmedKamal-41/gtmflow-ai/actions/workflows/release.yml)
on every push.

```bash
cd backend
.venv/bin/python -m pytest -q                                  # SQLite
TEST_DATABASE_URL=postgresql+psycopg://… .venv/bin/python -m pytest -q   # disposable PostgreSQL only
.venv/bin/python scripts/verify_release.py --with-frontend     # after a frontend build

cd ../frontend
npm test && npm run typecheck && npm run build
```

Never point `TEST_DATABASE_URL` at a database whose contents matter.

## API reference

64 operations. Every route except the health checks and the public account endpoints below needs a
session; state changes also need `X-CSRF-Token` and the `operator` role (or `guest` on a guest-safe
server).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/auth/options` | Which of sign-up and guest access this server offers (public) |
| `POST` | `/api/auth/login` | Sign in with email or username (JSON body); sets the session cookie |
| `POST` | `/api/auth/register` | Create an account and email a 6-digit code (public) |
| `POST` | `/api/auth/verify-email` | Confirm the code and sign in (public) |
| `POST` | `/api/auth/resend-code` | Send a new code after the cooldown (public) |
| `POST` | `/api/auth/guest` | Start a guest session (public) |
| `GET` | `/api/auth/session` | Current user, role and CSRF token |
| `POST` | `/api/auth/logout` | Revoke the session |
| `POST` | `/api/batches/upload` | Upload a CSV (multipart) |
| `GET` | `/api/batches`, `/api/batches/{id}` | List and read batches |
| `GET` | `/api/leads`, `/api/leads/{id}` | List and read leads |
| `POST` | `/api/leads/{id}/score`, `/api/batches/{id}/score` | Legacy priority score |
| `POST` | `/api/leads/{id}/fit-score`, `/api/batches/{id}/fit-score` | Company-fit score |
| `GET` | `/api/leads/{id}/readiness`, `/api/batches/{id}/readiness` | Current outreach and routing readiness |
| `POST` | `/api/leads/{id}/generate-summary`, `/api/leads/{id}/generate-outreach` | Grounded generation |
| `POST` | `/api/leads/{id}/ai-outputs/{output_id}/revisions` | Save an edited draft as a new revision |
| `POST` | `/api/leads/{id}/approve-outreach` | Approve `{ai_output_id, content_hash, acknowledged_quality_flags}` |
| `POST` | `/api/leads/{id}/reject-outreach` | Reject `{ai_output_id, content_hash, reason}` |
| `GET` | `/api/leads/{id}/review-state` | Draft, approval and blockers |
| `POST` | `/api/leads/{id}/push` | Deliver one lead `{integration_type, force?, redeliver?}` |
| `POST` | `/api/batches/{id}/push-hot` | Deliver all eligible Hot leads |
| `POST` | `/api/pushes/{id}/resolve` | Resolve an `unknown` delivery |
| `POST` | `/api/batches/{id}/jobs` | Queue a background job |
| `GET` | `/api/jobs`, `/api/jobs/{id}`, `/api/jobs/{id}/items` | Job progress and item results |
| `POST` | `/api/jobs/{id}/cancel` | Cancel a job |
| `GET`, `POST` | `/api/seller-profile`, `…/activate`, `…/deactivate` | Versioned seller profiles |
| `GET` | `/api/inbox` | Every lead with its priority and workflow stage, Hot first; filter by `stage`, `priority`, `q` |
| `GET` | `/api/ai/status` | Which model drafts right now and whether the fine-tuned model's server is reachable |
| `GET` | `/api/metrics/dashboard` | Adoption, delivery and mock-versus-real metrics |
| `POST` | `/api/demo/run` | Seed and run the full demo workflow (used by the release checks) |

Full schemas are at `/docs` once signed in. Curl examples are in [`backend/README.md`](backend/README.md).

## Local setup

| Requirement | Version |
|---|---|
| Python | 3.12 |
| Node.js | 24 |
| Docker | Any recent version, for a disposable PostgreSQL |

No API keys are needed. These commands use a separate demo database and explicit mock overrides,
and never touch an existing `.env`.

**Terminal 1: database and API** (from the repository root)

```bash
docker run -d --name gtmflow-demo-pg -e POSTGRES_PASSWORD=local-demo-only \
  -e POSTGRES_DB=gtmflow_demo -p 127.0.0.1:55432:5432 postgres:16
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-lock.txt
export DATABASE_URL=postgresql+psycopg://postgres:local-demo-only@127.0.0.1:55432/gtmflow_demo
export OPENAI_API_KEY= SLACK_WEBHOOK_URL= LORA_INFERENCE_BASE_URL= LORA_INFERENCE_API_KEY=
export SESSION_COOKIE_SECURE=false ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
export USE_MOCK_AI=false AI_PROVIDER=qwen3-4b-lora-v1 LORA_FALLBACK_TO_MOCK=true   # prefer the fine-tuned model
export SELF_SIGNUP_ENABLED=true GUEST_ACCESS_ENABLED=true   # sign-up codes print in this terminal
.venv/bin/alembic upgrade head
.venv/bin/python -m app.auth_cli create-user admin     # optional: an operator account (prompts for a password)
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

**Terminal 2: worker**

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://postgres:local-demo-only@127.0.0.1:55432/gtmflow_demo
export OPENAI_API_KEY= SLACK_WEBHOOK_URL= LORA_INFERENCE_BASE_URL= LORA_INFERENCE_API_KEY=
export USE_MOCK_AI=false AI_PROVIDER=qwen3-4b-lora-v1 LORA_FALLBACK_TO_MOCK=true
.venv/bin/python -m app.jobs.worker
```

**Terminal 3: frontend**

```bash
cd frontend
npm ci
API_PROXY_TARGET=http://127.0.0.1:8000 NEXT_PUBLIC_API_BASE_URL= npm run dev
```

Open http://localhost:3000 and either **Continue as guest**, create an account (the 6-digit code
appears in terminal 1, because no email is sent locally), or sign in. On **Today**, press **Try with
sample data**. With no model server running, drafts come from the demo generator; set
`LORA_INFERENCE_BASE_URL` to a running model server to draft with the fine-tuned model. The
[demo script](docs/demo-script.md) walks through the rest. Codespaces, SQLite-only and account
maintenance options are in the
[startup guide](docs/engineering-log/phase12-release-handoff.md#6-exact-startup-commands-local-mock-mode).

`SESSION_COOKIE_SECURE=false` is only for plain-HTTP localhost. Over HTTPS, use `true` and set
`ALLOWED_ORIGINS` to the exact frontend URL.

## Environment variables

Backend (`backend/.env.example` documents each one):

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | required | `postgresql+psycopg://…`; `postgres://` URLs are rewritten automatically |
| `USE_MOCK_AI` | `true` | Mock generation; always wins over `AI_PROVIDER` |
| `AI_PROVIDER` | `openai` | `openai` or `qwen3-4b-lora-v1` when mock is off |
| `OPENAI_API_KEY` | empty | Only for `AI_PROVIDER=openai` (paid) |
| `LORA_INFERENCE_BASE_URL`, `LORA_INFERENCE_API_KEY` | empty | The OpenAI-compatible adapter server |
| `LORA_SERVED_MODEL`, `LORA_TIMEOUT_SECONDS` | see the example file | Served model name and request timeout |
| `LORA_FALLBACK_TO_MOCK` | `false` | With the fine-tuned provider selected: use the demo generator while its server is offline |
| `SLACK_WEBHOOK_URL` | empty | Empty means mock delivery; never logged or returned |
| `SESSION_COOKIE_SECURE` | `true` | `false` only for plain-HTTP development |
| `SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS` | `60`, `12` | Session lifetimes |
| `ALLOWED_ORIGINS` | localhost:3000 | Exact browser origins for credentialed requests and login |
| `SELF_SIGNUP_ENABLED` | `false` | Allow sign-up confirmed by an emailed code |
| `SELF_SIGNUP_ROLE`, `SIGNUP_ALLOWED_EMAIL_DOMAINS` | `operator`, any | Role for verified sign-ups; optional domain allowlist |
| `GUEST_ACCESS_ENABLED`, `GUEST_SESSION_HOURS` | `false`, `2` | One-click guest access and its session length |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS`, `EMAIL_FROM` | empty, `587`, …, `true` | Verification email; empty host means the code is printed locally and nothing is sent |

Frontend (read at build time):

| Variable | Description |
|---|---|
| `API_PROXY_TARGET` | Where the Next.js server proxies `/api`, e.g. `http://127.0.0.1:8000` |
| `NEXT_PUBLIC_API_BASE_URL` | Leave empty so the browser uses the same-origin proxy |

## Deployment

**Not deployed.** The plan, with estimated costs, required configuration and the open items, is in
[`docs/deployment.md`](docs/deployment.md).

| Goal | Shape | Estimated cost |
|---|---|---|
| Mock portfolio demo | API, worker, frontend and managed PostgreSQL on one PaaS (Render recommended) | about $27/month |
| Real fine-tuned model | The same app plus a GPU inference server for the adapter | about $358/month always-on, or usage-based serverless |

Migrations run as an explicit one-off step (`alembic upgrade head`), never on boot, and operator
accounts are created with the CLI.

## Project structure

```
gtmflow-ai/
├── backend/
│   ├── app/
│   │   ├── ai/              Generators (mock, OpenAI, LoRA), prompts, grounding validation, runtime checks
│   │   ├── api/             FastAPI routers: auth, batches, leads, scoring, AI, review, push, jobs, metrics, demo
│   │   ├── core/            Settings, database, actor context, HTTP security, hashing
│   │   ├── datasets/        Dataset building, deduplication, correction policy (model work)
│   │   ├── evaluation/      Held-out evaluation criteria and metrics
│   │   ├── integrations/    Slack payloads and sender
│   │   ├── jobs/            Job queue, leases, handlers and the worker
│   │   ├── models/          SQLAlchemy models, including the audit hook
│   │   ├── pdl/             Streaming company-dataset (PDL) import with provenance
│   │   ├── schemas/         Pydantic request and response models
│   │   ├── scoring/         Legacy priority score and versioned company fit
│   │   ├── services/        Business logic: ingestion, generation, review, delivery, metrics, auth
│   │   ├── auth_cli.py      Account management CLI
│   │   └── main.py          App factory, deny-by-default dependency, middleware
│   ├── alembic/             14 migrations
│   ├── data/ai_reviews/     Committed review decisions behind the training data
│   ├── scripts/             Release harness, baseline verifier, evaluation and data scripts
│   ├── tests/               Backend test suite
│   └── requirements-lock.txt
├── frontend/
│   └── src/
│       ├── app/             Pages: Today, sign-in, Leads, lead workspace, Imports, Insights, Settings, seller profile
│       ├── components/      UI components (cards, badges, jobs panel, push history, auth provider)
│       └── lib/             Typed API client with CSRF handling
├── training/
│   ├── gtmflow_training/    Data loading, weighted LoRA training, generation, serving
│   ├── configs/             Pinned training and evaluation configs
│   ├── scripts/             Bundle builders and the RunPod launcher
│   └── tests/
├── sample_data/             Demo lead list (2 Hot, 4 Warm, 4 Cold)
├── docs/
│   ├── engineering-log/     Phase-by-phase handoffs and decisions
│   ├── screenshots/         README screenshots
│   └── *.md                 Architecture, scoring, AI workflow, integrations, metrics, deployment, demo script
└── .github/workflows/       Backend, frontend, training and release verification
```

## Known limitations

- **Not deployed:** no hosting, TLS or public URL, and no persistent model server.
- **Model quality is AI-evaluated only:** references and reviews come from AI, and the test set is
  small (71 examples, 36 of them outreach). Outreach drafts still need a person's review.
- **Real integrations are unexercised:** Slack delivery has only run against the mock webhook and
  test doubles; OpenAI generation is implemented but paid and opt-in.
- **Single workspace:** no multi-tenancy or self-service registration, by design.
- **No MFA or password reset yet.** Sign-up is verified by email, but a forgotten password needs
  the CLI (`set-password`), and old guest accounts are not cleaned up automatically.
- **The company-fit profile is a broad demonstration,** not a calibrated ideal-customer profile, and
  CSV leads without structured country data score as insufficient evidence.
- **Time saved is an estimate** (5 minutes per processed lead), not a measurement.
- **Open dependency findings** in the training environment (setuptools) and a PyTorch audit
  coverage gap; see the [dependency review](docs/dependency-security.md).

## Future improvements

- Deploy the mock demo, with a trusted edge proxy, global rate limits and a tested backup restore.
- Have a person review a sample of the model's outreach drafts and compare with the AI review.
- Package the adapter for serverless GPU inference to avoid an always-on server.
- Exercise real Slack delivery in a test workspace.
- Add CRM destinations (HubSpot, Salesforce) using the same claim-before-send ledger.
- Add retention jobs for old sessions, guest accounts, job items and login-throttle rows.
- Add a password-reset email and optional MFA.

## Engineering log

The project was built in twelve phases, each ending with a handoff that records what was verified
and what was still open. The [engineering log](docs/engineering-log/README.md) indexes them; every
number in this README traces back to one of those documents.

## Author

- **Ahmed Ali**: [GitHub](https://github.com/AhmedKamal-41)

---

<div align="center">

[Run it locally](#local-setup) · [Engineering log](docs/engineering-log/README.md) · [Star this repo](https://github.com/AhmedKamal-41/gtmflow-ai)

</div>
