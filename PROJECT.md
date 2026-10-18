# GTMFlow AI: the complete project record

Everything about GTMFlow from A to Z: what it does, how every part works, how the data was
built, how the model was trained and judged, how it is tested, what it cost, and every number
measured along the way.

**How to read the numbers.** Each figure comes from one of three places, and the source is
named next to it: a dated record in [`docs/engineering-log/`](docs/engineering-log/README.md),
the code itself (file and constant named), or a count taken from this repository. Model quality
figures are **AI-evaluated, not human-verified** (section 17). For a shorter tour, start with
the [README](README.md).

**Snapshot.** `main` at commit `e9da2e6` (2026-10-03, which includes the outreach assistant) plus
**profile-based lead ranking**, implemented and verified locally on 2026-10-18 (section 8). Both
the assistant (section 14) and profile-based ranking are implemented and tested with mock
integrations only; **neither is deployed**. Deployment facts in section 25 describe the earlier
deployed `main` (which still ranks with the retired fixed-term scorer) until a new release is made.

**What changed on 2026-10-18.** Leads used to be ranked by two fixed rubrics: a legacy 100-point
"priority" score built on hard-coded healthcare and property-management terms, and a
demonstration "company fit" profile (`demo-us-sectors-v1`). Both are gone from every workflow.
The seller profile now says *who* the seller wants as customers (industries, locations, company
sizes, contact roles, keywords) as well as *what* they sell, and one deterministic scorer ranks
every lead against the active profile's target customers. Drafts are generated from that same
profile revision. Historical scores of both retired rubrics are kept in the database.

## Contents

1. [At a glance](#1-at-a-glance)
2. [Purpose, users and principles](#2-purpose-users-and-principles)
3. [The product, page by page](#3-the-product-page-by-page)
4. [The workflow end to end](#4-the-workflow-end-to-end)
5. [Architecture](#5-architecture)
6. [Data model and migrations](#6-data-model-and-migrations)
7. [Data pipelines](#7-data-pipelines)
8. [Scoring](#8-scoring)
9. [Grounded generation](#9-grounded-generation)
10. [Review and approval](#10-review-and-approval)
11. [Seller profiles](#11-seller-profiles)
12. [Slack delivery ledger](#12-slack-delivery-ledger)
13. [Background jobs](#13-background-jobs)
14. [Outreach assistant (tool calling)](#14-outreach-assistant-tool-calling)
15. [Lead stages](#15-lead-stages)
16. [Metrics dashboard: every field](#16-metrics-dashboard-every-field)
17. [The fine-tuned model](#17-the-fine-tuned-model)
18. [GPU runs and their safeguards](#18-gpu-runs-and-their-safeguards)
19. [Security and access](#19-security-and-access)
20. [Audit trail](#20-audit-trail)
21. [Testing and verification](#21-testing-and-verification)
22. [API](#22-api)
23. [Configuration](#23-configuration)
24. [Running it locally](#24-running-it-locally)
25. [Deployment](#25-deployment)
26. [Money spent](#26-money-spent)
27. [Timeline](#27-timeline)
28. [Codebase statistics](#28-codebase-statistics)
29. [The Phase 1 audit and how each finding was resolved](#29-the-phase-1-audit-and-how-each-finding-was-resolved)
30. [Key design decisions](#30-key-design-decisions)
31. [Limitations and open items](#31-limitations-and-open-items)
32. [Glossary](#32-glossary)
33. [Where to find more](#33-where-to-find-more)

---

## 1. At a glance

| Item | Value |
|---|---|
| What it is | An internal lead-to-outreach tool for a sales team's reps: import, rank, draft, approve, route, measure |
| Drafting model | `qwen3-4b-lora-v1`: Qwen3-4B-Instruct-2507 fine-tuned with LoRA for this project |
| Model result (AI-evaluated) | 49 of 71 held-out drafts acceptable as-is (69.0%), against 1 of 71 (1.4%) for the base model |
| Training cost | 3 epochs on one rented NVIDIA L4, 63 minutes, $0.6131 |
| Backend | Python 3.12, FastAPI 0.141, SQLAlchemy 2.1, Pydantic 2.13, Alembic 1.20, psycopg 3, httpx, Uvicorn |
| Frontend | Next.js 15 (App Router), React 19, TypeScript 5 (strict), Tailwind CSS 3, Vitest |
| Model work | PyTorch 2.14, transformers 5.17, PEFT 0.21, accelerate 1.15, on RunPod pods that delete themselves |
| Lead ranking | Deterministic, no LLM: each lead compared with the active seller profile's target customers; weighted 0–100 score, Hot ≥ 75 / Warm ≥ 45 / Cold, "needs data" under 50% evidence (section 8) |
| Database | PostgreSQL (16 tested; 18.6 on Railway); 23 tables, 15 Alembic migrations; no Redis or broker |
| API | 63 operations, deny-by-default (66 before 2026-10-18: four legacy scoring routes and the fixed fit-profile route were removed, two ranking routes added) |
| Tests (2026-10-18, local) | 611 backend on PostgreSQL, 122 frontend, 107 of 107 live release checks; training suite not re-run (no training code changed) |
| Real data | 32,330,231 company records streamed; 5,000 US companies imported (2,500 healthcare, 2,500 real estate) |
| Model data | 419 training, 74 validation and 71 test examples, split by company group and frozen by sha256 |
| Total recorded spend | about $1.74 across OpenAI and RunPod (section 26) |
| Development | 12 documented phases (2026-09-22 to 09-27), then sign-up and guest access, a rep-centered redesign, the Railway fix and the assistant |
| Live (`main`) | `https://gtmflow-ai-production.up.railway.app` (Railway; section 25) |
| Repository | 417 tracked files, 75 commits on this branch |

## 2. Purpose, users and principles

**The problem.** Reps sit on lead lists. Deciding which leads matter, writing something specific
to each, and getting the good ones to the right channel is slow. Afterwards nobody can say how
much of the AI help was actually used, or whether an AI draft that went out was ever checked.

**The question GTMFlow answers:** which of these leads matter, what do we say to them, and how
do we get the good ones to the right rep without anything going out by mistake?

**The users.**

| User | What they do |
|---|---|
| Sales rep / SDR (daily user) | Works the Today page and the Leads list, selects ranked leads to draft, reviews drafts, sends approved Hot leads to Slack |
| RevOps lead or founder | Writes and activates the seller profile (what is sold and who the target customers are), recalculates rankings, reads Insights |
| Operator (admin) | Creates accounts with the CLI, resolves uncertain deliveries, runs batch jobs |
| Viewer | Read-only access (an auditor, a manager) |
| Visitor to the showcase | Uses a guest account, when the server allows it |

**Two principles shaped every decision.**

- **Trust.** A draft may only use facts from the company's own record and claims from the seller
  profile, and the server enforces this, not the prompt. A person approves the exact text that
  is sent. Nothing is sent twice by accident. Every change records who made it.
- **Honest measurement.** Approval is counted once per draft. Mock and real activity are never
  mixed. Time saved is labeled an estimate. The model's quality is reported as AI-evaluated.
  A number that was not produced by an actual run is not written down
  ([implementation contract](docs/engineering-log/implementation-contract.md), rule 6).

## 3. The product, page by page

The app is a sidebar layout: **Today, Leads, Imports, Assistant, Insights, Settings**.

| Page | Route | What it does |
|---|---|---|
| Sign in | `/login` | Sign in with email or username, create an account (confirmed by an emailed 6-digit code), or **Continue as guest**. Returns only to same-site `next` paths |
| Today | `/` | Greeting; counts of drafts to review, leads ready to send, Hot leads without a draft and leads sent; a "Next up" list ranked Hot first; the drafting model's live status; a setup prompt until a seller profile is active; a ranking banner (setup required, or outdated rankings with **Recalculate rankings**); **Try with sample data** on an empty workspace |
| Leads | `/leads` | Every lead across imports, ranked by its current match with the seller profile's target customers (Hot first), each with its score and a one-line reason; stage tabs and counts, search, a priority filter (Hot, Warm, Cold, Needs data); the ranking banner; **select up to 25 ranked leads and Draft outreach for selected** (blocked and unranked leads cannot be selected) |
| Lead workspace | `/leads/[id]` | The current draft (which model wrote it, the facts it is based on, what is unknown, provenance under "Model details"); the review (approve, edit into a revision, reject with a reason; acknowledge any quality flags); Send to Slack with delivery history and "It arrived" / "It did not arrive" buttons for uncertain outcomes; the contact card and **Why this ranking**: score, priority, matches / doesn't match / missing information, each criterion's explanation, an outdated notice, and Rank again. Eligibility, the review record and the full AI history sit in a collapsed "Technical details" |
| Imports | `/imports` | CSV upload (ranked on import; if no target customers are set the import is kept and the page says ranking needs setup), **Try with sample data** (10 companies through the normal path), the CSV template, past imports |
| Import detail | `/batches/[id]` | One import's leads with their rankings; a ranking summary (Hot / Warm / Cold / Needs data, outdated count); Rank leads; background job controls (rank all leads, summaries, outreach drafts, push approved Hot leads) with live progress and cancel; optional ranking details (coverage, missing fields, readiness, eligibility) |
| Assistant | `/assistant` | Pick an import, describe the leads you want, inspect a shortlist of up to five with its tool trace, then click **Prepare drafts** to queue them (section 14) |
| Insights | `/metrics` | The metrics dashboard (section 16), with a data-mode banner |
| Settings | `/settings` | Drafting model status and how to connect the fine-tuned model; what you sell; the account |
| Seller profile | `/seller-profile` | Versioned editor in two parts, **What you sell** (offer, capabilities, sourced claims, exclusions) and **Who you want to sell to** (target customers in words, plus industries, locations, company-size ranges, contact roles and optional keywords used for ranking, with an explanation of the ranking rules); explicit, reviewed activation; the ranking banner |

The redesign (2026-10-03) retired the separate annotation and demo pages; their APIs remain for
the model-work tools and the release checks. `/upload` and `/batches` remain as routes.

**Sample data.** `sample_data/leads_sample.csv` (also served as
`frontend/public/sample-leads.csv`) holds 10 fictional companies across healthcare, housing,
software, retail and education. How they rank now depends entirely on the active seller profile.
Two measured examples (2026-10-18):

| Seller profile | Hot | Warm | Cold |
|---|---|---|---|
| Built-in demonstration profile (B2B software, 11–200 staff, founders and sales leaders) | Loopline Software, Quanta Analytics (100 each) | none | the other 8 (three match on size only, 33) |
| A US healthcare seller (healthcare/hospital, United States, 201+ staff, practice managers, "intake"/"appointment"), entered through the UI | Northbridge Clinics (100), Meridian Diagnostics (80) | Brightline Academy Network, Cascade Modular Homes, Vault Outfitters (45: size and location only) | the other 5 |

Before 2026-10-18 the same file was "tuned to score 2 Hot, 4 Warm, 4 Cold" under the retired
fixed-term scorer.

## 4. The workflow end to end

```mermaid
flowchart TD
    S["Seller profile (active revision)<br/>what we sell + who we want to sell to"] --> C
    A["Upload CSV<br/>bounded, streamed, row-level errors"] --> B["Leads stored<br/>unknown columns kept"]
    B --> C{"Target customers<br/>set?"}
    C -->|no| C1["Setup required;<br/>leads stay unranked"]
    C -->|yes| D["Ranking against target customers<br/>0–100, Hot / Warm / Cold / needs data"]
    D --> E["Rep selects ranked leads"]
    E --> F["Generate summary + outreach<br/>same seller revision, grounded context, cited ids"]
    F --> G{"Server validation<br/>ids in context, schema"}
    G -->|fail| G1["Nothing saved (502)"]
    G -->|pass| H["Draft + runtime quality flags"]
    H --> I{"Rep review"}
    I -->|reject| I1["Rejected with reason"]
    I -->|edit| H
    I -->|"approve exact hash<br/>(acknowledge flags)"| J["Approved"]
    J --> K{"Hot, eligible,<br/>approval current?"}
    K -->|no| K1["Refused with reason"]
    K -->|yes| L["Slack delivery ledger"]
    L --> M["Metrics<br/>mock and real kept apart"]
```

1. **Import.** A CSV is streamed to disk, parsed row by row and committed in chunks of 500.
   Bad rows are reported; unknown columns are kept on the lead.
2. **Rank.** Each lead is compared with the active seller profile's target customers: a
   deterministic 0–100 score with Hot / Warm / Cold (or "needs data" when too little of the record
   could be compared) and a breakdown of matches, mismatches and missing information. Without
   target customers nothing is ranked and the app says setup is required.
3. **Draft.** The rep selects ranked leads (or opens one) and generates outreach. The model sees
   only the company's own facts, the active seller revision (the same one that ranked the lead)
   and that ranking, labeled as not evidence of need or intent, and must cite the id of every fact,
   capability and claim it uses. Output that cites anything else is rejected and never saved.
4. **Approve.** A person approves or rejects the exact draft on screen. The approval is bound to
   the draft's content hash, the hash of its inputs (company facts, ranking, seller content) and
   the seller-profile revision; changing the target customers makes it stop applying.
5. **Route.** Only a current approval lets a currently Hot (or explicitly forced), eligible lead
   go to Slack, through a ledger that claims a row before sending, so repeats are replays and
   timeouts become `unknown`. A blocked lead is refused at any score.
6. **Measure.** The dashboard counts each draft once by its latest review and keeps mock and
   real activity apart.

Long batch work (scoring, generation, routing) runs in a separate worker process. Every step
writes a `WorkflowEvent` with its actor, so a lead's history can be reconstructed.

## 5. Architecture

```mermaid
flowchart TB
    UI["Browser<br/>Next.js 15 pages"] -->|"session cookie<br/>+ X-CSRF-Token"| Proxy["Next.js server<br/>same-origin /api proxy"]
    Proxy --> API["FastAPI<br/>auth · CSRF · roles<br/>66 operations"]
    API <-->|"data, audit events,<br/>job queue"| PG[("PostgreSQL")]
    PG <-->|"leased jobs,<br/>item results"| Worker["Job worker<br/>app.jobs.worker"]
    API --> Gen
    Worker --> Gen
    API --> Ledger
    Worker --> Ledger
    subgraph Gen["Drafting model"]
        direction LR
        LoRA["Fine-tuned qwen3-4b-lora-v1"]
        Mock["Demo generator<br/>(labeled fallback)"]
        OpenAI["OpenAI gpt-4o-mini<br/>(opt-in)"]
    end
    Ledger["Delivery ledger<br/>claim before send"] --> Slack["Slack webhook<br/>(mock when unset)"]
```

| Component | Responsibility |
|---|---|
| Next.js server | Serves the pages and forwards `/api` to FastAPI (`API_PROXY_TARGET`), so the browser sees one origin and the session cookie can stay `HttpOnly`, `SameSite=Lax`, `Secure`. UI visibility never authorizes an action |
| FastAPI | One app-wide dependency applies session, CSRF and role checks to every route; thin routers call services |
| Services | Business rules: ingestion, scoring, grounding, generation, review, delivery, jobs, metrics, inbox, auth, sign-up, assistant. They own database writes and `WorkflowEvent` emission |
| Worker | `python -m app.jobs.worker` leases jobs from PostgreSQL and calls the **same service functions**, so approval, eligibility and delivery rules are identical in both paths |
| `AIClient` | An abstract client with three implementations: `MockAIClient` (deterministic, no clock or randomness), `OpenAIClient` (lazy import, fails closed without a key) and `LocalLoRAClient` (the fine-tuned model through an OpenAI-compatible server) |
| Slack adapter | A pure payload builder plus a sender; mock when no webhook is configured |
| PostgreSQL | All state: leads, scores, drafts, reviews, the delivery ledger, the job queue, sessions, rate limits and the audit trail |

**Layering convention** (from the [implementation contract](docs/engineering-log/implementation-contract.md)):
`api/` routers validate requests and call a service → `services/` own writes and audit events →
`models/` (SQLAlchemy) and `schemas/` (Pydantic v2). `scoring/` and `ai/` are pure layers that
know nothing about the database. The ranking scorer is a pure function of any duck-typed lead
and a seller profile's target preferences.

**Repository layout.**

```
gtmflow-ai/
├── backend/
│   ├── app/
│   │   ├── ai/           Clients (mock, OpenAI, LoRA), prompts, grounding validation, runtime checks, tool calling, model status
│   │   ├── api/          Routers: auth, workspace (inbox, model status), batches, leads, ranking (fit), AI, review, push, jobs, assistant, metrics, demo, annotation
│   │   ├── core/         Settings, database, actor context, HTTP security, hashing, init_db
│   │   ├── datasets/     Dataset checks, deduplication, correction policy, pilot export (model work)
│   │   ├── evaluation/   Frozen held-out criteria, metrics and lints, Phase 9 report and blind review
│   │   ├── integrations/ Slack payloads and sender; verification email (SMTP or local mock)
│   │   ├── jobs/         Queue, leases, runner, handlers, worker
│   │   ├── models/       SQLAlchemy models, including the audit hook
│   │   ├── pdl/          Streaming company-dataset import with provenance
│   │   ├── schemas/      Pydantic request and response models
│   │   ├── scoring/      Ranking against the seller profile's target customers (fit.py), readiness, eligibility
│   │   ├── services/     Business logic
│   │   ├── auth_cli.py   Account management
│   │   ├── dataset_cli.py, annotation_cli.py   Model-work tools
│   │   └── main.py       App factory, deny-by-default dependency, middleware
│   ├── alembic/          14 migrations
│   ├── data/ai_reviews/  Committed AI review decisions behind the training and held-out data
│   ├── scripts/          Release harness, baseline verifier, generation and acceptance scripts
│   └── tests/            Backend test suite
├── frontend/src/
│   ├── app/              Pages
│   ├── components/       App shell, model status, badges, cards, jobs panel, push history, charts
│   ├── hooks/            Paginated history, review timer
│   └── lib/              Typed API client with CSRF handling; sample-data loader
├── training/
│   ├── gtmflow_training/ data.py, weights.py, train.py, generate.py, serve.py
│   ├── configs/          Pinned training and evaluation configs
│   ├── scripts/          Bundle builders, RunPod launcher and pod jobs
│   └── tests/
├── sample_data/          The demo lead list
├── docs/                 Architecture, scoring, AI workflow, integrations, metrics, deployment, demo script, engineering log
└── .github/workflows/    backend, frontend, training, release verification
```

## 6. Data model and migrations

**23 tables** (physical names, from `backend/app/models/`):

| Group | Tables | Notable constraints and columns |
|---|---|---|
| Leads and imports | `lead_batches`, `leads`, `lead_scores`, `lead_fit_scores` | `lead_batches.logical_key` unique; `leads.cleaned_data` keeps unknown CSV columns; rankings (`lead_fit_scores`) are append-only and scoped to a seller-profile revision and a `targeting_hash`; `lead_scores` is frozen history of the retired v1 score (nothing reads or writes it) |
| Drafts and review | `ai_outputs`, `ai_output_reviews` | Outputs carry full provenance and `parent_output_id` for revisions; reviews carry the approved content hash |
| Seller profiles | `seller_profiles`, `seller_profile_activations` | Immutable versions (target preferences live in the `profile` JSON); race-safe activation |
| Delivery | `integration_pushes` | The ledger: unique `delivery_key`, approved output id and hash, attempt, `delivery_mode`, claim, outcome and resolution |
| Background jobs | `background_jobs`, `background_job_items` | Unique `dedupe_key` per active job; one item per job and lead; lease and heartbeat columns |
| Audit | `workflow_events` | `actor` on every new event, `actor_user_id` when a user is known |
| Accounts | `users`, `user_sessions`, `email_verifications`, `login_throttles` | Unique `token_hash`; only hashes of tokens and codes are stored |
| Company-data provenance | `source_snapshots`, `import_runs`, `company_identities` | `source_snapshots` unique on provider and checksum |
| Model work | `split_manifests`, `company_split_assignments`, `annotation_candidates`, `training_annotations` | Frozen company-group splits and the annotation queues |

**Migrations** are explicit and never run on boot (`alembic upgrade head` as a release step):

| # | Name | What it added |
|---|---|---|
| 0001 | `baseline` | The pre-migration schema, autogenerated from the real models |
| 0002 | `provenance_and_review_identity` | Provenance tables, review identity, backfill of reviews from events |
| 0003 | `pdl_identity_constraints` | Company-identity constraints for the PDL import |
| 0004 | `logical_key_and_snapshot_uniqueness` | Unique batch `logical_key` and snapshot (provider, checksum), with a backfill that refuses ambiguity |
| 0005 | `csv_upload_retry` | Crash-safe CSV upload state (`uploading` until complete) |
| 0006 | `lead_fit_scores` | The append-only company-fit table |
| 0007 | `seller_profile_drafts` | Versioned seller profiles |
| 0008 | `seller_activation_and_output_grounding` | Explicit activation and per-output seller provenance |
| 0009 | `review_revisions_and_annotation` | Exact-draft review, revisions, annotation queues and splits |
| 0010 | `background_jobs` | The job queue |
| 0011 | `slack_delivery_ledger` | Ledger columns on `integration_pushes` |
| 0012 | `operator_auth` | Users, sessions, actors |
| 0013 | `auth_hardening` | Login throttle table, job creator user id |
| 0014 | `signup_and_guest` | Email verification and guest accounts |
| 0015 | `profile_based_ranking` | `lead_fit_scores.seller_profile_id` (FK, nullable) and `targeting_hash` (nullable) with indexes; additive, existing rows keep every value and stay as history (2026-10-18) |

A baseline verifier (`backend/scripts/verify_baseline_schema.py`) adopts a database created before
migrations existed: it compares the live schema first and stamps it only on an exact match.
Every migration was tested up, down and up again with `alembic check` on PostgreSQL (`0015` on
PostgreSQL 16 on 2026-10-18, plus a restored-fixture test that keeps a retired-rubric score row
intact through `0015` up and down). Each one
that touched the real development database was first applied to a restored copy of a verified
backup. The first attempt at `0009` failed on PostgreSQL's identifier-length limit on that copy
and was fixed before the real database was touched.

## 7. Data pipelines

GTMFlow has three data pipelines: the CSV upload reps use, the company-data import that supplied
5,000 real companies, and the dataset pipeline that turned AI drafts into training and
evaluation data.

### 7.1 CSV ingestion (`app/services/csv_ingestion.py`, `POST /api/batches/upload`)

| Rule | Value |
|---|---|
| Required column | `company_name` |
| Known columns | `company_name`, `website`, `industry`, `contact_name`, `contact_email`, `contact_title`, `company_size`, `location`, `source`, `status` |
| Unknown columns | Kept in `leads.cleaned_data` and read by the scorer (for example `notes`, `pain_points`) |
| Size cap | 20 MiB, enforced while the upload is streamed to a temp file (never trusted from a header) |
| Row cap | 50,000, enforced while parsing |
| Parsing | `iter_cleaned_leads` streams from the file; the whole file is never held in memory |
| Writes | 500 rows per transaction, read in 1 MiB chunks |
| Errors | Row-level, counts unbounded, at most 50 examples kept |
| Partial import | Hitting the row cap keeps what was committed, returns 422 naming the count, and marks the batch `partial`. A partial or still-uploading batch can never reach Slack, even with `force=true` |
| Crash safety | A batch stays `uploading` until complete; a retry resumes from the committed row count |

### 7.2 Company data: the People Data Labs import (Phase 3, `app/pdl/`)

**Source.**

| Item | Value |
|---|---|
| Provider | People Data Labs company dataset, mirrored on Hugging Face (`andreaaltomani/company-dataset`, revision `0689a4c9…`) |
| License | CC-BY-4.0; company fields only (`id, website, name, founded, size, locality, region, country, industry, linkedin_url`), no contacts |
| File | `free_company_dataset.json.gz`, 2,378,824,357 bytes, JSON Lines |
| Checksum | sha256 `2d529f10…706cfc2`, matching Hugging Face's `x-linked-etag` header (a first claim that it also matched `x-xet-hash` was checked and corrected: that is a different hash scheme) |
| Reported acquisition date | 2025-07-28, per the uploader; not independently verifiable |

**Modules.**

| Module | Job |
|---|---|
| `download.py` | Resumable, checksummed, disk-space-checked download to a `.part` file, renamed only when verified; a `.meta.json` sidecar records `downloaded_at` |
| `source_reader.py` | Streaming gzip and JSONL parsing; rows over 32,768 bytes rejected; a distinct error for a corrupt stream |
| `normalize.py` | Field mapping and eligibility; never fabricates a value |
| `identity.py` | Identity key (PDL id, or a fingerprint of normalized name and location) and a content hash |
| `identity_store.py` | Disk-backed dedup index (temporary SQLite with a 50,000-entry LRU cache) |
| `curate.py` | Eligibility filter, dedup and conflict detection, seeded per-segment reservoir sampling |
| `importer.py` | Idempotent writes in chunks of 500; mandatory provenance before any write; race-safe find-or-create |
| `report.py` | Canonical CSV, a formula-injection-escaped spreadsheet CSV, JSON manifest, quality report |
| `cli.py` | `download`, `inspect`, `curate`, `import`, `report` |

**Selection rules** (mapping version `pdl-alias-v1`): healthcare is the industry exactly
`hospital & health care` or `medical practice`; real estate is exactly `real estate`; country is
exactly `united states`. These three strings were chosen after a full-corpus scan found 8 related
industry values; 5 were excluded on purpose (`commercial real estate`, `health, wellness and
fitness`, `hospitality`, `medical devices`, `mental health care`). Exact matching avoids the
substring bug the audit found (`hospitality` starts with "hospital"). Segment membership is
stored as a *candidate classification*, never as a claim of need or intent.

**The real run (2026-09-22).**

| Measure | Value |
|---|---|
| Records parsed | 32,330,231 of 32,330,231; end of file reached; 0 malformed, 0 oversized |
| Eligible unique records | healthcare 696,880; real estate 265,005 |
| Selected | 2,500 + 2,500 (fixed balanced targets, shortfall 0); seed `20260922` |
| Duplicates / conflicts / invalid | 0 / 0 / 0 |
| Missing website in the sample | healthcare 1,531 of 2,500 (61.24%); real estate 760 of 2,500 (30.40%) |
| Missing company size | 0% in both segments |
| Committed | 5,000 `CompanyIdentity` rows and 5,000 `Lead` rows in one batch |
| Re-running the import | 0 rows added, every time (5,000 reused) |
| Full curation wall-clock | 14 min 24 s |
| Memory (3,000,000-line diagnostic run) | 52.4 MiB peak RSS, 82.7 s |

**What the closeout pass fixed** (same day, after re-checking the first completion claim):

- A missing checksum silently created a new snapshot on every run. The checksum is now derived
  from the file every time and validated before any write (8 focused tests).
- Nothing at the database level stopped two concurrent imports from duplicating a snapshot or
  batch (batches were matched by a name prefix). Migration `0004` added unique constraints and a
  persisted `logical_key`. **Proof:** 8 OS processes behind a barrier raced on disposable
  PostgreSQL and produced exactly 1 snapshot and 1 batch; 7 caught the conflict and read the
  winner's row.
- The dedup index was an unbounded dict; it is now disk-backed, with an equivalence test against a
  plain-dict reference that produces byte-identical selections.
- Scoring a batch overwrote its `partial` status, which would have defeated the push guard. Fixed.

### 7.3 The model-dataset pipeline (Phases 6 and 7)

```mermaid
flowchart LR
    A["5,000 real leads"] --> B["Split manifest<br/>company-groups-v1"]
    B --> C["Queues<br/>pilot-v1, train-v2,<br/>validation-v1, test-v1"]
    C --> D["Budgeted generation<br/>gpt-4o-mini, grounded-v2"]
    D --> E["Review<br/>human (5) + AI"]
    E --> F["Dataset build<br/>checks, dedup, weights"]
    F --> G["Frozen JSONL<br/>pinned by sha256"]
```

**Splits.** `company-groups-v1` groups companies by union-find over identity, domain, LinkedIn
and normalized name, then assigns each group by `sha256(seed:group) % 6` at a 4 : 1 : 1 ratio.
Result: 5,000 leads in 4,988 groups; **train 3,284, validation 840, test 876 leads**; digest
`8fdf98be…`. No group spans two splits, so no company leaks between training and evaluation.

**Budgeted generation** (`app/services/budgeted_generation.py`). Every attempt is written to a
durable ledger before the call and completed after it. An attempt starts only if the attempt cap
and the dollar cap (with a worst-case reserve) still allow it; cost comes from provider-reported
usage; each candidate gets at most 2 attempts; the run stops on any provider error or after 3
consecutive failures; successes are committed at once and never replaced.

| Queue | Purpose | Paid attempts | Generated | Cost | Caps |
|---|---|---|---|---|---|
| `pilot-v1` | First 100 candidates, 50 training companies | 105 | 100 | about $0.048 | per-session authorizations |
| `validation-v1` + `test-v1` | Held-out, 100 each | 207 | 197 of 200 (3 test summaries unresolved) | $0.0954 | 220 attempts / $0.25 |
| `train-v2` | Training expansion | 473 | 448 of 450 (2 unresolved) | $0.2167 | 500 attempts / $0.30 |

Model served on every response: `gpt-4o-mini-2024-07-18`. First-attempt validity on the held-out
run: outreach 100 of 100, summaries 93 of 100 (97 after one retry). Failed attempts were billed,
saved nothing, and were audited as `ai_generation_rejected` (`schema_invalid`,
`unknown_fact_reference`).

**Review.** The original plan called for 100 human reviews; the user revised it to a mixed
human/AI pilot on 2026-09-24.

| Queue | Reviewer | Result |
|---|---|---|
| `pilot-v1` | Human, 6 | 3 accepted, 2 corrected, 1 skipped |
| `pilot-v1` | AI (`claude-opus-5-5`), 94 | 30 accepted, 64 corrected; 26 flagged uncertain. Outreach: 0 of 47 acceptable as generated |
| `validation-v1` | AI, 100 | 74 eligible (20 accepted, 54 corrected), 26 flagged uncertain |
| `test-v1` | AI, 97 | 71 eligible (22 accepted, 49 corrected), 26 flagged uncertain |
| `train-v2` | AI, 448 | summaries 131 accepted / 92 corrected; outreach 225 corrected, 0 accepted (none had the required demonstration label); 102 flagged uncertain |

AI reviews are stored separately (`review_source=ai`, `human_verified=false`) and were never
written through the human annotation path. Decisions are committed under
`backend/data/ai_reviews/`. Each decision pins the output id and content hash it read, and the
build refuses a decision whose pin does not match.

**Most frequent issues in `train-v2`:** invented need 199, placeholder signature 161, missing
demonstration label 158, invented specialization 145, invented fit 134, "My name is from GTMFlow"
75, incomplete label 67, asserted relevance 38, invented praise 38.

**Correction policies.**

| Policy | Used for | Behavior |
|---|---|---|
| `ai-review-rubric-v1` | Pilot and held-out references | Derived from the user's own decisions: state plainly it is a demonstration, not a commercial offer; restate only record facts; no invented specialization, research, praise, needs, results, placeholders or contacts |
| `ai-review-rubric-v2-train` | Training queues only (the build refuses it elsewhere) | Minimal edits: keep supported sentences verbatim, delete only what breaks a rule, allow varied structure. Designed from training evidence only. Median text preservation (ROUGE-L to the original) 0.72, against 0.20 under v1 |

**Uncertain records.** When the record itself looks wrong (a funeral home recorded as health
care, a person's name as a company, a `.co.uk` site for a Pennsylvania address), the example is
flagged and excluded, never repaired. 128 training examples were excluded this way; the 419
target was reached with every flag in place.

**Deduplication and weights** (`app/datasets/dedup.py`, policy `structure-cap-v1`, K = 5): exact
fact-masked structure groups larger than 5 are down-weighted. The 25 eligible pilot outreach
examples in one 32-example template group get weight 0.156; every v2 example gets 1.0. Eligible v2
outreach has 0 near-duplicate pairs at Jaccard ≥ 0.8 out of 15,051 pairs.

**Final datasets.**

| Dataset | Examples | Companies | Summary / outreach | Healthcare / real estate | Notes |
|---|---|---|---|---|---|
| `train-combined-v1` eligible | **419** | 212 | 208 / 211 | 198 / 221 | AI 414, human 5; effective weight 397.9; sha `a69ea054…` |
| `validation-v1` eligible | **74** | 37 | 37 / 37 | 40 / 34 | sha `8306c8d0…`; "evaluation only, never train on it" |
| `test-v1` eligible | **71** | 36 | 35 / 36 | 31 / 40 | sha `9b3f89e2…`; also "never tune prompts or rubric on it" |

Every build passes `check` (files, schema, provenance, duplicates, leakage) and rebuilds
byte-identically. The original targets were 400 / 100 / 100 with human-verified held-out
references: training met it; validation and test are 26 and 29 short; human verification was
deferred by the user's decision.

**Baseline before training** (stored gpt-4o-mini outputs against the frozen AI-reviewed
references, test split): summaries acceptable as generated 63%; **outreach 0 of 36**; demonstration
label pass rate 0.00, no-placeholder 0.25, no-invented-phrasing 0.28.

## 8. Scoring

Since 2026-10-18 there is **one** ranking, `app/scoring/fit.py`, and it compares each lead with
the **active seller profile's target customers**. It is deterministic (no LLM call), pure, and
used by every reader through one query module (`app/services/fit_queries.py`): the lead list and
its sort and filters, the inbox and Today, import pages, the dashboard, batch jobs, the
assistant, draft context and approval checks, and Slack delivery eligibility.

### 8.1 What the seller profile says

The seller profile has two halves (section 11):

- **What we sell:** company and product name, value proposition, capabilities, approved claims
  with sources, exclusions. Only these may appear in a draft.
- **Who we want to sell to:** `target_customer` (free text, passed to drafting as context only)
  and `target_preferences`, the structured criteria ranking uses:

| Field | Shape | Limits |
|---|---|---|
| `industries` | phrases | up to 20, 80 characters each |
| `locations` | countries, states or cities | up to 20 |
| `company_sizes` | `{min_employees, max_employees or null}` ranges | up to 8; UI presets 1–10, 11–50, 51–200, 201–500, 501–1,000, 1,001–5,000, 5,001+, plus custom ranges |
| `contact_roles` | job-title phrases | up to 20 |
| `keywords` | optional phrases searched in notes/description columns | up to 20 |

An empty list means "no preference" and that criterion does not count. With every list empty,
**ranking is refused with "setup required"** (HTTP 409 with a message; banners in the UI) rather
than falling back to any built-in targets.

### 8.2 Formula, weights and thresholds

| Criterion | Default weight | Lead fields compared |
|---|---|---|
| Industry | 35 | `industry` |
| Company size | 25 | `company_size` (a count, a range such as `51-200`, or `1000+`) |
| Location | 20 | `location`, and `cleaned_data` locality / region / country (PDL leads) |
| Contact role | 15 | `contact_title` |
| Keywords | 5 | free-text `cleaned_data` values (notes, descriptions); import labels, places, dates and URLs are excluded |

Each **configured** criterion gets a result: `match` (full weight), `partial` (half weight; company
size only, when the recorded range overlaps a target range without lying inside it), `mismatch`
(the lead has a value outside the targets: 0) or `unknown` (no usable value: 0, reported as
**missing information, not a mismatch**). Criteria the profile does not set are `not_configured`,
weight 0, and listed so their absence is visible.

```
fit_score = round(100 × earned weight ÷ configured weight)        0–100
coverage  = 100 × weight with a known result ÷ configured weight
```

Weights are rescaled over the configured criteria (a profile with only industry and size ranks
on those two: 58.3 and 41.7 of 100). Because unknown criteria earn nothing, a high score always
rests on evidence.

| Band | Rule | Shown as |
|---|---|---|
| `insufficient_evidence` | coverage < 50%, whatever the score | **Needs data** |
| `strong_match` | score ≥ 75 | **Hot** |
| `partial_match` | 45–74 | **Warm** |
| `weak_match` | < 45 | **Cold** |

Size is a preference, not a ladder: an 8-person company scores 100 for a seller targeting 1–10
and 0 for one targeting 1,001+, and the reverse for a 10,001+ company (unit-tested).

**Matching** is whole-word after normalization: case, punctuation, `&`/`and`, a few stopwords,
simple plurals, and a small rewrite table (health care = healthcare, USA = US = U.S. = United
States, UK = United Kingdom, VP = Vice President, CEO/COO/CFO/CTO/CMO/CRO spelled out, Sr, Mgr,
Dir, Ops). So "Hospital" does **not** match "Hospitality", "Clinic" matches "Clinics", and "Soft"
does not match "Software". It is not semantic and does not geocode: "Austin, TX" is not
recognized as being in "United States" (the mismatch explanation says so). The assistant's
industry filter uses the same whole-word rule (it used a substring check before).

**Breakdown.** Every ranking returns its criteria (targets, matched targets, points, an
explanation sentence), a summary grouped into matched / partly matched / mismatched / missing /
not configured, and a one-line `explanation` used in lists and Slack, for example "Target-customer
fit: matches industry, company size; no data for location." or "Not enough information to rank
confidently: …".

**Kept separate from fit.** Contact completeness (`missing_contact_email`) and permission to
contact (blocked statuses `do_not_contact`, `disqualified`, `unsubscribed`; incomplete imports)
are reported as readiness and eligibility, never added to the score. `compute_eligibility` takes
no score, so no ranking can override a block; ranking never writes `Lead.status` or
`LeadBatch.status`. **Readiness gaps**, recomputed live: no seller profile, demonstration seller
profile, missing contact email, no outreach draft, draft not reviewed, draft rejected, lead
excluded from routing, draft not from the active profile, draft inputs changed since generation,
approval does not identify content.

### 8.3 Versioning, outdated rankings and recalculation

- Every ranking is a new append-only `lead_fit_scores` row stamped with the seller-profile
  revision (`seller_profile_id`, `profile_id`, `profile_version`), `scorer_version`
  `fit-scorer-v2`, `normalization_version` `fit-norm-v2`, an input fingerprint and a
  **`targeting_hash`**: SHA-256 of the canonical target preferences (normalized words, so case and
  order do not matter), the weights, the thresholds and both versions.
- A row is **current** only while its `targeting_hash` and versions equal the active profile's.
  Activating a revision with different target customers makes earlier rankings **outdated**:
  they stay in the table (history is never deleted or edited), the API returns them with
  `is_current: false`, lists show "Outdated", and they count as neither ranked nor Hot anywhere. A
  revision that changes only what is sold keeps the same hash, so rankings stay current.
- **Recalculating:** `POST /api/scoring/recalculate` ranks the whole workspace in committed chunks
  of 500 and records `rankings_recalculated`; `POST /api/batches/{id}/fit-score` and the
  `fit_score` background job do one import; `POST /api/leads/{id}/fit-score` one lead. Leads whose
  current row has the same fingerprint are skipped, so repeating is safe. `GET
  /api/scoring/profile` reports the state (`ready`, `no_active_profile`, `setup_required`), the
  active targets, weights, thresholds and counts of current, outdated and never-ranked leads; the
  UI's **Recalculate rankings** banner uses it. Imports are ranked right after upload when a
  profile is set up.
- Single workspace, as before: rankings are scoped to the lead and the profile revision; every
  write needs an operator (or a guest on a guest-safe server) and records its actor.

### 8.4 History: the two retired rubrics

Kept for the record; neither runs anymore. Their stored rows remain: `lead_scores` (frozen, no
code path) and `demo-us-sectors-v1` rows in `lead_fit_scores` (no `targeting_hash`, so never
current). The `legacy_score` job type is refused at enqueue, and a job queued before the upgrade
fails cleanly without writing anything.

#### Retired: priority score (legacy v1), 100 points (`app/scoring/lead_scoring.py`, deleted 2026-10-18)

| Category | Max | How it earns points |
|---|---|---|
| Industry fit | 25 | `industry` matches a target term → 25; a target term elsewhere in the lead's text → 15 |
| Company size | 15 | 1–10 → 5; 11–50 → 8; 51–200 → 12; 201–500 → 14; 500+ → 15; verbal sizes map the same |
| Persona / title | 15 | `contact_title` contains a target title |
| Pain-point keywords | 20 | 1 match → 8; 2 → 14; 3+ → 20, searched across every text field and `cleaned_data` |
| Data completeness | 10 | +2 each for website, industry, contact name, email and title |
| Source quality | 10 | referral, webinar, conference, inbound → 10; website, csv → 6; unknown → 2 |
| Penalties | −15 | free-email domain −5; missing title −5; blocked status −10 |

**Total** = clamp(sum, 0, 100); the maximum reachable is 95. **Bands: Hot ≥ 80, Warm 55–79,
Cold 0–54**, unit-tested at 0, 54, 55, 79, 80 and 100. Every score returns its breakdown, matched
signals and a one-sentence reason.

Target lists: industries (property management, housing, multifamily, apartment management, real
estate management, healthcare, clinic, medical practice, patient services, hospital, dental,
urgent care); titles (CEO, founder, co-founder, VP sales, VP operations, head of operations,
revenue operations, RevOps, property manager, director of leasing, leasing manager, practice
manager, operations manager, office manager); pain keywords (leasing, maintenance, appointment,
scheduling, intake, resident, tenant, patient, paperwork, support, call volume, automation, tour,
booking, front desk, follow-up, forms).

Leads with a blocked disposition (`do_not_contact`, `disqualified`, `unsubscribed`) can never reach
Slack in any mode, whatever their score.

#### Retired: company fit with the demonstration profile `demo-us-sectors-v1` (Phase 4)

| Item | Value |
|---|---|
| Profile | `demo-us-sectors-v1`, version 1; scorer `fit-scorer-v1`; normalization `fit-norm-v1` |
| Exact industry match | 60 points |
| Structured US location | 40 points |
| Company size | Inactive (0) |
| Bands | strong ≥ 80, partial ≥ 50, otherwise weak; evidence coverage below 80% → **insufficient evidence** |
| Missing input | Unknown, not penalized; coverage reported separately |
| Storage | Append-only and versioned; the current score is defined once (`app/services/fit_queries.py`: all four version fields, `created_at DESC, id DESC`) and used by every reader |

The profile is a broad demonstration, not a calibrated ideal-customer profile, and generation
labels it "not evidence of interest". CSV leads without structured country data score as
insufficient evidence.

**Real cohort (Phase 4):** 5,000 of 5,000 scored (0 failures), all strong match at 100% coverage,
as expected for companies imported on the same two criteria; a rerun skipped all 5,000 as
unchanged; a field-by-field verifier matched every stored row.

**v1 versus v2 on real leads.** v1 rates all 5,000 company-only records Cold, as the audit
predicted: it blends company fit with contact signals PDL cannot supply and penalizes the missing
title. Examples: "university radiology-atlantic" v1 29 Cold vs v2 100 strong; "first american
title" v1 0 Cold (v1's keyword list has no plain "real estate") vs v2 100 strong. Neither is a
purchase-probability model.

## 9. Grounded generation

```mermaid
flowchart TB
    Context["Generation context<br/>record facts: fact-company_name, fact-industry, …<br/>seller capabilities: cap-1, cap-2, …<br/>approved claims · stated unknowns: budget, intent, tools"]
    subgraph Pipeline["Generation and checks"]
        direction LR
        P["Prompt grounded-v2<br/>+ output schema v2"] --> M["Drafting model"]
        M --> V{"Cited ids ⊆<br/>context ids?"}
        V -->|no| X["Rejected, not stored"]
        V -->|yes| Q["Runtime checks<br/>runtime-checks-v2"]
    end
    Context --> Pipeline
```

**Context** (`app/ai/grounding.py`, `grounded-context-v1`). Every record fact, seller capability
and approved claim gets an id. Unknowns (buying intent, budget, current problems, current tools)
are always passed explicitly. Free text is bounded (at most 20 fields of 500 characters).
Imported data sits inside an escaped `<untrusted_data>` fence in the `grounded-v2` prompt.

**Ranking in the context (2026-10-18).** The `company_fit` block is the lead's current ranking
under the **same seller revision the draft uses** (for the built-in demo profile, under its
targets): `fit_score`, `band`, `evidence_coverage_pct` and `targeting_hash`, plus a `meaning`
line: "How well the company's recorded attributes (industry, size, location, contact role,
keywords) match the seller's stated target customers. It is not evidence of interest, need,
budget or intent to buy; do not say or imply that the company needs the product or plans to buy."
The structured target preferences themselves are not sent to the model (the seller block's
keys are unchanged, so the fine-tuned model's input format only changed in this block). Buying
intent, budget, current problems and tools stay in `unknowns` for every lead. Because the
block and its meaning changed, drafts generated before 2026-10-18 no longer reproduce their
`input_hash`: their approvals stop applying and they show as "Draft out of date" (intended).

**Validation** (output schema `v2`). The draft must list the ids it relied on. Any id outside the
context (`unknown_fact_reference`, `unknown_capability_reference`, `unapproved_claim_reference`),
invented contact details or unsupported figures reject it. **Invalid output saves nothing** (502)
and logs only reason codes. A reply cut off at the token limit is rejected as `truncated_output`,
never repaired.

**Provenance on every output:** prompt version, output schema, model and model revision, adapter
revision, input hash, seller-profile id, version, hash and kind; plus token usage and finish
reason on the generation event.

**Drafting model selection.**

| Setting | Effect |
|---|---|
| `USE_MOCK_AI=true` (default) | Always the demo generator; wins over every provider setting |
| `AI_PROVIDER=qwen3-4b-lora-v1` | The fine-tuned model through `LocalLoRAClient`: temperature 0, at most 1,024 new tokens, the shared system message. The server must list the served model, otherwise 503 and nothing is generated. Plain http only to a local or private host |
| `AI_PROVIDER=openai` | gpt-4o-mini, paid, opt-in |
| `LORA_FALLBACK_TO_MOCK=true` | A 3-second reachability probe (cached 30 seconds) before each draft; if the fine-tuned server is down, the demo generator writes the draft and it is recorded as mock. A request that fails mid-way is never retried on another model |

`GET /api/ai/status` reports which model drafts right now and whether the fine-tuned server is
reachable; Today and Settings show it.

**Runtime quality checks** (`app/ai/quality_checks.py`, `runtime-checks-v2` since 2026-10-18;
v1 plus `presumed_need_or_intent`). Pure functions of the stored output and its input snapshot.
Every flag asks for review; none blocks by itself.

| Check | Source |
|---|---|
| `presumed_outreach_activity` ("…your current outreach strategies") | Phase 9 blind review |
| `commercial_opportunity_framing` ("Exploring Opportunities…", "potential collaboration"; subject and body only) | Phase 9 blind review |
| `invented_phrasing` | The frozen phrase list, matched only after removing the lead's recorded values (fixes the "Allergy & Immunology Specialists" false positive) |
| `missing_demo_label` | `ai-review-rubric-v1` (demonstration sellers only) |
| `placeholder_text`, `literal_escape_text`, `prospect_web_as_own` | `ai-review-rubric-v1` |
| `need_or_interest_hypothesis` (summaries; a hypothesis that only states an unknown is exempt) | `ai-review-rubric-v1` |
| `presumed_need_or_intent` (outreach subject and body: "since you need…", "you are looking for…", "your need for…", "ready to buy"; a question or a sentence with whether / if / may / might / could is exempt) | The ranking rule that a strong match is not a need or intent (2026-10-18) |

What is and is not shown about these checks (`presumed_need_or_intent` is covered only by unit
tests with written examples, and the mock drafts carry no flags): on the 71 test drafts they were tuned on, flags
coincide with the blind reviewer's rejections on all 71 (regression coverage only). They flagged
0 of 100 untouched validation references (false positives on those references only). **Detection
of new problem wording is untested.**

## 10. Review and approval

- **Exact-draft approval.** `POST /api/leads/{id}/approve-outreach` takes `{ai_output_id,
  content_hash, acknowledged_quality_flags}`. The approval stores the SHA-256 of the content shown,
  plus the input hash and seller-profile revision. It authorizes delivery only while all three are
  unchanged, so a stale browser tab cannot approve a newer draft. The input hash includes the
  lead's ranking under that seller revision, so activating different target customers (a new
  revision) or a changed company fact that changes the ranking makes the approval stop applying;
  re-ranking with identical inputs and targets does not (tested).
- **Flags.** Approving a flagged draft needs `acknowledged_quality_flags` equal to the flags shown,
  otherwise 409 and nothing is recorded. The acknowledgement is stored on the `outreach_approved`
  event.
- **Edits** create an immutable new revision (`parent_output_id`) that needs its own review; the
  model's original output is kept.
- **Rejections** require a reason.
- **Retries** of the exact same decision are idempotent; a changed decision is a separate event.
- **Refused targets:** another lead's draft, stale drafts, superseded drafts and annotation drafts.
- **Separation.** Generation and background jobs never approve. Training annotation and
  operational approval are separate in both directions. Only the labeled synthetic demo
  (`demo-auto-approve`) creates demo approvals.

## 11. Seller profiles

A seller profile describes **what is sold** (capabilities and approved claims, the only source of
claims a draft may make) and, since 2026-10-18, **who it is sold to**: the target preferences that
rank every lead (section 8.1). The editor separates the two, explains every target field and the
ranking rules, keeps saved custom size ranges, and warns when a saved draft has no target
customers.

- One global profile with **immutable versions** (`seller_profiles`); each save is a new version.
- **Activation is explicit** (`seller_profile_activations`): it needs a review confirmation, uses
  an optimistic sequence and a race-safe unique constraint, and writes its audit event atomically.
- A **demonstration profile** also needs an explicit acknowledgement. Its drafts carry a
  "not a commercial offer" label and can never make an email "ready".
- Without an active profile, only summaries can be generated, and nothing is ranked.
- The built-in GTMFlow demonstration template now includes illustrative target preferences (B2B
  software, 11–200 staff, founders and sales leaders), used only where that profile is chosen:
  the standalone demo, or a workspace that activates the template. It is labeled as not a
  validated segment.
- The seller revision is resolved once per request, so a profile change mid-request cannot mix
  versions.

## 12. Slack delivery ledger

```mermaid
sequenceDiagram
    participant Op as Operator / worker
    participant API as Delivery service
    participant DB as PostgreSQL
    participant Slack as Slack webhook
    Op->>API: push lead
    API->>API: check current ranking Hot (or forced), eligible, approval current
    API->>DB: INSERT claim (unique delivery_key) and commit
    alt claim already exists
        DB-->>API: existing claim
        API-->>Op: replay stored outcome (no send)
    else new claim
        API->>Slack: POST message
        alt 2xx
            API->>DB: success
        else error response or no connection
            API->>DB: failed
        else timeout or dropped connection
            API->>DB: unknown (never resent automatically)
        end
        API-->>Op: outcome
    end
```

Every dispatch (single-lead route, batch route, `push_hot` job, demo) goes through one function,
`deliver_lead_to_slack` (`app/services/integration_push.py`).

1. **Gates first:** a current ranking against the active profile that is Hot (or `force`; the
   single-lead route refuses a lead with no current ranking), not blocked, import complete, and a
   current approval of the exact draft. Blocked and unapproved leads are refused **before** any
   claim row exists, whatever the score. The batch route and the `push_hot` job select leads whose
   current ranking is Hot; a lead re-ranked out of Hot after queueing is skipped.
2. **Ledger lookup:** already delivered → replay, nothing sent; claim in progress → 409; last
   outcome unknown → 409 until resolved; last attempt definitely failed → a new attempt is allowed.
3. **Claim:** insert `slack:<output id>:<content hash>:<attempt>` (unique) and **commit before any
   byte is sent**.
4. **Send** with a 10-second timeout, then record the outcome with a conditional update:
   `success` / `mock_success`, `failed` (definitely not delivered: 4xx/5xx, connection refused,
   connect or pool timeout) or `unknown` (a read timeout or dropped connection after the request
   may have left).
5. **Stale claims:** a claim with no outcome after 120 seconds (its holder died) becomes `unknown`.
   A late result from that holder is recorded as `push_late_outcome` and never overwrites.

**Resolving `unknown`:** `POST /api/pushes/{id}/resolve` with `confirmed_delivered` or
`confirmed_not_delivered`, after an operator checks the channel (also the two buttons on the lead
page). Nothing is sent; only "not delivered" allows a new attempt.

**Deliberate re-sends** need `redeliver: true` (single lead) or `force: true` (batch and job), and
go through the same unique key.

**Guarantee:** at most one send per claimed attempt, across concurrent requests, workers and
restarts. **Not exactly-once**, because Slack incoming webhooks accept no idempotency key; a wrong
manual resolution can still duplicate or lose a message.

**Measured on PostgreSQL (Phase 11):** 8 simultaneous API pushes with a slow mocked Slack produced
**exactly 1 send** (the others got a replay or 409); API requests racing two workers produced one
delivery per lead; a restarted job item never re-sent. With a non-unique claim key, the
concurrency test fails (mutation check).

**Message.** Plain Slack `text` (no Block Kit): company, "Target-customer match: N/100 (band)",
industry, contact, "Why it fits" (the ranking explanation, or the summary's reasoning), pain points,
suggested subject, call note, and a link back to the lead. The "🔥 Hot" heading appears only for
Hot leads. An empty
`SLACK_WEBHOOK_URL` routes to a mock webhook. The URL is read at send time and never logged or
returned; transport errors are sanitized so they cannot echo it.

## 13. Background jobs

| Item | Value |
|---|---|
| Job types | `fit_score` (rank against the active profile; fails with the setup message when there are no target customers), `generate_summary`, `generate_outreach`, `push_hot` (current Hot rankings), `assistant_outreach` (section 14) and `selected_outreach` (1–25 leads a rep selected on the Leads page; each must still have a current ranking, otherwise `ranking_not_current`; blocked leads `blocked`; never approves). `legacy_score` is retired: refused at enqueue, and a pre-upgrade queued one fails cleanly |
| Storage | `background_jobs` and `background_job_items` in PostgreSQL (migration `0010`); no new dependency |
| Worker | `python -m app.jobs.worker [--drain]`; polls every 2 s; 60-second lease |
| Leasing | Each item's effect, its status, the progress counts and a **fenced lease renewal** commit in one transaction. A worker that lost its lease cannot commit, and committed items are never repeated |
| Recovery | A killed worker's job is taken over after its lease expires (`job_recovered`); a graceful stop returns the job to the queue at once (`job_released`) |
| Crash loop | A job claimed more than 3 times fails |
| Retries | Up to 3 attempts per item, for transient provider or network errors only; invalid model output is recorded, not retried; pushes are never retried |
| Dedupe | An identical active job is returned instead of queued twice |
| Cancel | Stops before the next item |
| Rules | Each handler calls the same per-lead service as the synchronous endpoints; no job approves, rejects or edits |
| Audit | `job_enqueued`, `job_recovered`, `job_released`, `job_completed`, `job_failed`, `job_cancelled`; every job records who queued it, and worker events carry that user |
| UI | The jobs panel polls only while a job is active and ignores late responses |

**Measured:** a worker subprocess was SIGKILLed after at least 20 items of a 300-lead summary job;
a second worker finished it with **exactly 300 outputs for 300 distinct leads**. When a second
worker took over mid-item, the fence rolled back the first worker's commit (0 outputs) and the
second completed. Removing the fence, the retry bound or the crash-loop limit each makes a test
fail. Not load-tested beyond 300 items and 2 workers.

## 14. Outreach assistant (tool calling)

Added 2026-10-03 on `codex/lead-agent`. Not deployed.

**What the user does.** Open **Assistant**, choose an import and describe the leads wanted. The
assistant searches stored records, inspects company facts and returns up to five candidates with
their unknowns and an action trace ("How these leads were selected"). **Prepare drafts** is a
separate, explicit click that queues only those candidates through the existing worker. Review,
approval and sending stay on the normal lead page.

**Tools** (`app/services/lead_assistant.py`, `app/ai/tool_calling.py`). Exactly three, all
read-only and scoped to one import:

| Tool | Does |
|---|---|
| `search_leads` | Search the selected import's stored leads (at most 20 results) |
| `inspect_leads` | Read the facts of leads already discovered by a search |
| `propose_leads` | Propose a shortlist of inspected leads |

There is no HTTP, browser, SQL, shell, approval, delivery or delivery-resolution tool.

**Server-side bounds.**

| Bound | Value |
|---|---|
| Selected leads | 1 to 5, distinct |
| Eligible records in the import | at most 5,000 |
| Search results | 20 per search |
| User request | 1,000 characters |
| Tool arguments | 2,000 characters |
| Model steps | 6 |
| Time budget | 60 seconds, checked between steps; 15-second SDK request timeout, no SDK retries |

Every candidate must belong to the selected, complete import; ids must be discovered, then
inspected, before they can be proposed; UUIDs, fields, duplicates and limits are checked. Blocked
leads and leads that already have an operational outreach draft are excluded. Imported content is
treated as untrusted: the server **ignores the model's free-form final answer** and builds its own
response from executed actions and stored facts. That limits what the assistant can do; it does
not prove that prompt injection cannot distort which leads are chosen.

**Providers.** `AGENT_PROVIDER` is separate from `AI_PROVIDER`, because the fine-tuned adapter
writes drafts and was never trained for tool selection.

- `mock` (default): a deterministic demonstration that recognizes Hot / Warm / Cold and any of the
  active profile's target industries named in the request (it used to recognize fixed
  healthcare / real-estate terms); the page says other instructions are not interpreted.

**Ranking (2026-10-18).** Search results are ordered by each lead's current ranking against the
active profile (Hot, Warm, Cold, needs data, unranked), the priority filter uses it, inspection
shows "Target-customer match: N/100 (band)" with the explanation and "not evidence of need,
interest or intent", and the planner's request includes the profile's target industries,
locations and roles. The industry filter is whole-word.
- `openai`: native function calling with `AGENT_MODEL` (default `gpt-4o-mini`). Tested only
  against mocked HTTP; no live calls were made.

**The draft job.** `assistant_outreach` takes 1 to 5 lead ids and nothing else (it shares its
validation with `selected_outreach`). It rechecks import
membership and completeness, skips existing drafts, respects blocked statuses and uses the normal
grounding, provenance and quality checks. A different selection while another assistant job is
active returns 409. Each item gets one worker-level attempt on provider failure.

**Access and audit.** Planning needs write access and CSRF, even though its tools are read-only;
viewers cannot run it, and a paid planner makes guests read-only. Each finished or stopped run
emits `assistant_run_finished` with the actor, a request hash, the selected ids, a safe action
trace, mode and model, duration and reported token usage; raw prompts, company facts and provider
errors are not copied into it.

**Verification:** 21 assistant tests (unauthorized, viewer, CSRF and guest limits; missing
configuration; malformed requests; incomplete imports; excluded leads; invented privileged tools;
cross-import, uninspected, duplicate and over-limit ids; injected company instructions; step
limits; sanitized failure; audit actors and usage; explicit confirmation; worker rechecks), plus a
real OpenAI SDK round trip over an in-memory HTTP transport covering the three tool exchanges.
Frontend tests cover mode labeling, no automatic generation, empty and stopped plans, account
access, import changes, errors and retry, cancellation and unmount. The live harness confirms the
selected lead gets one grounded mock draft and no approval or delivery.

**Not established:** live tool-call behavior, natural-language selection accuracy, latency and
cost. Also missing: persisted conversation memory, streaming, a cost quota and a planner rate limit.

## 15. Lead stages

`GET /api/inbox` gives every lead exactly one stage, derived from `review_states()`, the same
function the lead page and delivery enforce (`app/services/inbox.py`):

| Stage | Meaning |
|---|---|
| Not ranked | No current ranking (never ranked, or only against earlier target customers) |
| Needs a draft | Ranked, no outreach draft |
| To review | The current draft awaits a decision and is still current |
| Draft out of date | The draft's inputs changed (facts, ranking or seller profile), so approving or sending it would be refused |
| Draft rejected | The current draft was rejected |
| Ready to send | Approved, and the approval authorizes delivery |
| Sent | A Slack delivery succeeded for this lead |

Leads also carry `delivery_unknown`, `blocked`, and their current ranking (`priority`, `score`,
`band`, `evidence_coverage_pct`, `ranking_reason`, `ranking_outdated`). The list is ordered Hot,
Warm, Cold, needs data, unranked, then by score, in memory, bounded to 5,000 leads; filters are
`stage`, `priority` (Hot, Warm, Cold, `needs_data`) and `q`. The response's `ranking` object gives
the state (`ready`, `no_active_profile`, `setup_required`) and profile version, and `counts`
include `needs_data` and `ranking_outdated`.

## 16. Metrics dashboard: every field

Source: `GET /api/metrics/dashboard` (`app/services/metrics.py`, [`docs/metrics.md`](docs/metrics.md)).
Every percentage returns 0.0 when its denominator is zero; an empty database returns every field
at zero; no path returns NaN or infinity.

**Lead pipeline**

| Field | Definition |
|---|---|
| `total_leads_uploaded` | All leads |
| `total_leads_processed` | Leads with a current ranking against the active profile |
| `hot_leads`, `warm_leads`, `cold_leads` | Leads per band of their current ranking (needs-data leads are in `fit_insufficient_evidence`) |
| `automation_coverage` | processed ÷ uploaded × 100 |
| `average_lead_score` | Mean current ranking score, 1 decimal |
| `missing_data_rate` | Leads missing contact email or website ÷ uploaded × 100 |

**Ranking** (since 2026-10-18 the same source as the pipeline counts above; the retired
`lead_scores` table is not read)

| Field | Definition |
|---|---|
| `ranking_state`, `ranking_profile_version` | Whether leads can be ranked, and against which profile version |
| `fit_scored_leads` | Distinct leads with a current ranking |
| `fit_strong_match`, `fit_partial_match`, `fit_weak_match`, `fit_insufficient_evidence` | Leads per band, each counted once by its latest current ranking |
| `fit_outdated_leads` | Leads ranked only against earlier target customers (not counted as ranked) |

**Outreach review** (cohort: distinct operational outreach drafts, each classified by its latest
review, ordered by `created_at` then `id`; human revisions included; annotation outputs excluded)

| Field | Definition |
|---|---|
| `outreach_generated` | Drafts in the cohort |
| `outreach_approved` | Drafts whose latest review is approved (approve → reject → approve counts once) |
| `outreach_rejected` | Drafts whose latest review is rejected |
| `outreach_pending_review` | Drafts with no review; approved + rejected + pending = generated |
| `approval_rate` | approved ÷ generated × 100; never above 100% by construction, not by clamping |
| `reviewed_approval_rate` | approved ÷ (approved + rejected) × 100 |
| `approval_events`, `rejection_events` | Raw decision events, audit only |

**Delivery**

| Field | Definition |
|---|---|
| `leads_pushed` | Delivered rows (a lead delivered twice counts 2) |
| `unique_leads_pushed` | Distinct leads delivered at least once |
| `push_success_rate` | Delivered rows ÷ all push rows × 100 |
| `failed_push_count`, `push_unknown_count`, `push_pending_count` | Rows per outcome |
| `real_messages_delivered` | Deliveries through a real webhook |
| `mock_messages_delivered` | Deliveries through the mock webhook (never left the app) |

**Mock versus real** (each breakdown sums to the matching totals; a test checks this)

| Field | Definition |
|---|---|
| `generation_by_mode` | Per mock, real and unknown: drafts generated, approved, rejected, pending, approval rate. A human revision inherits its original draft's mode |
| `delivery_by_mode` | Per mock, real and unknown: attempts, delivered, unique leads, failed, unknown, pending, success rate (from the `delivery_mode` recorded at claim time; legacy `failed` rows are unknown) |
| `data_mode` | `empty`, `mock_only`, `real_only` or `mixed`; the dashboard shows a banner |

**Time saved (an estimate, not a measurement)**

| Field | Definition |
|---|---|
| `estimated_time_saved_minutes` | processed leads × 5 (`MINUTES_SAVED_PER_PROCESSED_LEAD`) |
| `estimated_time_saved_hours` | minutes ÷ 60, 2 decimals |

**Worked example** (recorded before 2026-10-18 with the retired scorer: sample CSV scored, outreach
for 3 leads, 2 approved, 1 rejected, Hot leads pushed): 10 uploaded and processed, 2 / 4 / 4 Hot /
Warm / Cold, approval rate 66.67%, 2 pushed, 2 unique, success 100%, 50 minutes (0.83 hours)
saved, average score 56.1, missing data 0%, coverage 100%. The ranking figures now depend on the
active profile (section 3). Note that the standalone demo ranks with its own built-in profile, so
on a workspace with no active profile the dashboard reports no ranked leads after a demo run
(tested); activating the demonstration template makes those rankings current.

**History of the approval metric.** Before Phase 11, `approved` counted events and `generated`
counted every outreach row, so approve → reject → approve on one draft reported 200%. The fix and
its test (`test_approve_reject_approve_counts_one_approved_draft`) are mutation-checked: restoring
event counting makes 2 tests fail.

## 17. The fine-tuned model

### 17.1 Design (Phase 8, `training/`)

| Item | Value |
|---|---|
| Base | `Qwen/Qwen3-4B-Instruct-2507`, revision `cdbee75f…`, Apache-2.0, ungated; a non-thinking instruct model, so it answers in JSON directly |
| Method | bf16 LoRA, r 16, alpha 32, dropout 0.05, on q, k, v, o, gate, up and down projections; no quantization; gradient checkpointing |
| Size | 33,030,144 trainable of 4,055,498,240 parameters; adapter about 132 MB (504 tensors) |
| Optimizer | AdamW, learning rate 1e-4, 5% linear warmup then cosine to 0; betas 0.9 / 0.999, eps 1e-8; no weight decay; gradient clipping 1.0 |
| Batching | 1 example per micro-batch, gradient accumulation 8 (53 steps per epoch); max sequence 4,096 tokens; seed `20260925` |
| Objective | Example-weighted loss on answer tokens only (including end-of-turn): Σ wᵢ·lᵢ / Σ wᵢ; validation loss unweighted |
| Formatting | The backend's own `grounded-v2` prompt builders and system message, loaded by file path; target is the reviewed JSON with sorted keys; nothing truncated |
| Selection | Best validation-loss epoch, early stopping with patience 0 |
| Workload | 881,605 training tokens per epoch (max 2,285 per example); 156,829 validation tokens per evaluation |

**Data guards** (`data.py`): every file must match the sha256 pinned in the config; training must
be all-train with `allowed_use: training`; validation all-validation; **the test split is refused**,
the config may not name one, and the bundle cannot contain one; uncertain examples are refused; the
prompt version must be `grounded-v2`; every weight must lie in (0, 1].

**Reproducibility:** pinned model revision, dataset hashes, dependency versions and seed;
deterministic algorithms requested; two identical CPU smoke runs produced byte-identical logs.
Each run writes `run-manifest.json` (config and its hash, data hashes, library versions, commit,
hardware, command, per-step and per-epoch metrics, wall time, adapter and tokenizer hashes).

### 17.2 The training run (2026-09-25)

| Item | Value |
|---|---|
| Hardware | 1× NVIDIA L4 24 GB, RunPod Secure Cloud, $0.49/hr |
| Steps / epochs | 159 of 159; 3 of 3; no early stop |
| Validation loss | 1.2628 before → **0.2638** → **0.1392** → **0.1292**; epoch 3 selected |
| Training loss (weighted) | 0.3956 → 0.2073 → 0.1806 |
| Time per epoch | 1,241 s, 1,243 s, 1,243 s; training wall time 3,805 s (63 min); pod lifetime 1 h 06 m 28 s |
| GPU memory | 9.27 GiB allocated, 12.49 GiB reserved of 22.03 GiB; device peak 13,087 MiB; mean utilization 98% |
| Example weights | Applied: normalizer 0.9497, 25 examples below weight 1, weight sum 397.906 |
| Cost | $0.6131 of a $3.00 cap (including 3 earlier launch attempts refused before any data transfer, $0.0647) |

**Deviation, recorded:** two refusal tests expected "no CUDA" but, on the GPU pod, actually started
the real (correctly configured) training run. The GPU preflight therefore ran after training
instead of before, and the job ended as `failed:tests`. The tests were fixed to force "no CUDA",
and later pods run tests with the GPU hidden.

### 17.3 Evaluation (Phase 9, 2026-09-26): AI-evaluated, not human-verified

Both Qwen systems ran from one base-model load (adapter disabled for base, enabled for LoRA),
with greedy decoding in batches of 8, at most 1,024 new tokens, identical batches, in the order
validation-eligible, validation-flagged, test-eligible, test-flagged. A validation gate (truncation
or empty output above 10% stops the run) passed with 0.0 for both systems before any test input
was generated. All 8 passes completed; all 197 outputs are distinct.

**Automatic criteria** (`heldout-criteria-v1`, frozen 2026-09-24, via `phase9-report-v1`; test
eligible, n = 71: 35 summaries, 36 outreach; every example in every denominator):

| Criterion | gpt-4o-mini (stored) | Base Qwen3-4B | **Fine-tuned** |
|---|---|---|---|
| Valid structure | 1.000 | 1.000 | 1.000 |
| Factual support | 0.620 | 0.986 | **0.986** |
| Missing-info handling | 0.577 | 0.507 | **1.000** |
| Writing acceptability (outreach) | 0.000 | 0.167 | **1.000** |
| Token F1 vs reference | **0.636** | 0.502 | 0.585 |
| ROUGE-L vs reference | **0.566** | 0.365 | 0.471 |
| Exact match | **0.310** | 0.000 | 0.000 |

By task: summaries factual / missing-info: gpt-4o-mini 1.00 / 0.91, base 1.00 / **0.00**, LoRA
1.00 / 1.00. Outreach: gpt-4o-mini 0.25 / 0.25, base 0.97 / 1.00, LoRA 0.97 / 1.00.

Other splits: test flagged-uncertain (n = 26) LoRA 1.000 / 1.000 / 1.000, token F1 0.565; validation
eligible (n = 74) LoRA 1.000 / 1.000 / 1.000, token F1 0.587; base 0.959 / 0.514 / 0.081.

Word-overlap favours gpt-4o-mini because the references are AI-reviewed edits of its own outputs.

**Blind AI review** (`phase9-blind-review-v1`). A packet of 142 items (70 summaries, 72 outreach,
seed `20260925`) with system names and scores removed and a sealed key; a fresh Claude agent with
no conversation context reviewed it in 8 chunks against the frozen `ai-review-rubric-v1`;
`review-aggregate` refused to unblind until every decision was valid and the packet hash matched.

| | Base | **Fine-tuned** |
|---|---|---|
| Acceptable as-is | 1 / 71 (1.4%) | **49 / 71 (69.0%)** |
| Summaries acceptable (n = 35) | 0 | **35 (100%)** |
| Outreach acceptable (n = 36) | 1 (2.8%) | **14 (38.9%)** |
| Factual support: supported / partial / unsupported | 1 / 70 / 0 | 57 / 14 / 0 |
| Missing info: good / acceptable / poor | 1 / 69 / 1 | 57 / 14 / 0 |
| Writing quality, mean 1–5 (summaries / outreach) | 2.66 (2.94 / 2.39) | 3.69 (4.00 / 3.39) |

**What the reviewer found.** Base summaries invented a need hypothesis in 35 of 35; base outreach
had an incomplete demo label (28), invented needs (27) and missing signatures (14). The fine-tuned
model's outreach faults were concentrated in two phrasings: "I would love to learn more about your
current outreach strategies" (14) and "Exploring Opportunities" / "potential collaboration"
subjects (12). Both trace to the training targets: 57 of 211 contain an "Exploring Opportunities"
subject and 52 "potential collaboration" wording, kept by the minimal-edit v2 policy and rejected
by the v1 rubric. The runtime checks (section 9) were written from this analysis.

**The one shared failure** (`39fd8237…`) is a lint false positive: the pattern `speciali[sz]`
matched the recorded company name "Allergy & Immunology Specialists, LLC" in every system and in
the reference. The frozen scores were left unchanged.

**Run facts:** one L4, 1 h 22 min (estimate 25–60 min; decoding was 2–3× slower than assumed),
peak 21.66 GiB reserved, the fine-tuned model averaged about 290–295 new tokens against 450–466 for
base (about 38% shorter, 32% less generation time), the pod deleted itself with its own key after a
verified copy-back, $0.6945 of $1.50 spent.

**Recommendation:** select the fine-tuned adapter over base; suitable for summaries under review
and for demonstration outreach that is then reviewed; **not** for unreviewed outreach (22 of 36
would still need a correction).

**Limits:** AI evaluation only; small test set (36 outreach, so 14 of 36 has a 95% interval of
roughly ±16 points); two segments and one seller profile; one reviewer with no second rater;
imperfect blinding (the systems' styles differ visibly); greedy decoding only.

### 17.4 Integration (Phase 10, 2026-09-26)

The app's own `LocalLoRAClient` ran against the real model served by
`training/gtmflow_training/serve.py` (OpenAI-compatible, binds 127.0.0.1 behind an SSH tunnel,
one-time bearer token, greedy only, at most 1,024 tokens, adapter sha256 verified before loading,
no prompt or output text in its logs) on one temporary L4 pod.

| Criterion | Required | Result |
|---|---|---|
| A1: 20 test cases complete, no error or truncation | yes | 20 of 20 |
| A2: pass the app's grounding validator | yes | 20 of 20 |
| A3: same Phase 9 report categories | yes | 20 of 20 |
| B1: API generation with provenance | yes | pass (`model_used=qwen3-4b-lora-v1`, pinned adapter revision) |
| B2: a background job by a real worker | yes | pass (2 succeeded, 1 skipped) |
| B3: no review or push created | yes | pass |
| C1: unserved model refused before generation | yes | pass |
| C2: wrong token refused | yes | pass (401) |
| A4: byte-identical to Phase 9 | measured | 7 of 20 (likely batch-of-8 versus single-request bf16 numerics; unproven) |
| D: latency, one request at a time | measured | p50 **22.6 s**, p95 **28.0 s**, range 18.9–30.1 s, mean 292 completion tokens |

The 13 differing outputs stay close (ROUGE-L 0.82–0.99 to the Phase 9 text) and valid. Runtime
flags differed on 4 of the 20 cases, all outreach (4 of 10). A summary plus an outreach draft for
one lead took 48.1 s through the API. Pod lifetime 12 min 22 s; model load 34.8 s; peak 8.22 GiB
allocated; the server logged 29 requests (28 × 200, 1 × 401); $0.0739 observed balance decrease.

## 18. GPU runs and their safeguards

A shell `timeout` does not stop cloud billing, so every pod was built to delete itself
(`training/scripts/runpod/launch.py`).

| # | Safeguard |
|---|---|
| 1 | Hard pod lifetime from first boot, armed by the container start command (works without SSH, survives restarts): 3.25 h training, 2 h evaluation, 75 min acceptance |
| 2 | Claim check: the pod removes itself if the job has not started 30 min after boot |
| 3 | Post-job grace: removal 60 / 30 / 20 min after the job ends, success or failure |
| 4 | A workspace-side watchdog that removes the pod through the API |
| 5 | The orchestrator removes the pod after verified copy-back or on any error, then confirms through the API |

Plus: a price cap ($0.80/hr training, $0.60/hr evaluation and acceptance); one pod at a time; a
pre-transfer check (watchdog armed, pod id, CLI config at mode 600, a GraphQL read of its own pod,
and a terminate probe that must return `POD_NOT_FOUND`) that removes the pod before any data is
sent if anything fails; bundles built from committed code with an allowlist checked on both sides
(a tampered bundle containing test data was refused); a hash-locked GPU environment
(`requirements-gpu-lock.txt`, installed with `--require-hashes` and compared with `pip freeze`;
refuses a driver older than CUDA 12.6); copy-back verification of the archive, every file against
`SHA256SUMS`, and adapter, tokenizer and config hashes; a deadline-aware stop that never starts an
epoch or pass that cannot finish.

**Self-deletion with the pod's own key** was proven three times on 2026-09-26: a dedicated proof
pod, the Phase 9 run pod and the Phase 10 acceptance pod. Earlier, three Phase 8 launch attempts
were refused at the pre-transfer check (Cloudflare blocking the default User-Agent, SSH sessions
not inheriting the pod environment, the v1 `runpodctl` not authorizing with the pod key) and each
pod was removed before any data left the workspace.

## 19. Security and access

| Control | Setting |
|---|---|
| Default | Deny: every route needs a session except `/health`, `/api/health`, `/api/auth/options` and the sign-in, sign-up, verification, resend and guest endpoints. `/docs`, `/redoc` and `/openapi.json` also need a session |
| Sessions | 256-bit random token, stored as SHA-256; cookie `gtmflow_session`, `HttpOnly`, `SameSite=Lax`, `Secure`, `Path=/`; 60-minute idle, 12-hour absolute; rotated at login; revoked on logout, password change and disable |
| CSRF | Every authenticated POST, PUT, PATCH and DELETE needs `X-CSRF-Token`, derived from the session and readable only from same-origin responses |
| CORS | Only configured origins get credentials; allowed headers narrowed to `Content-Type` and `X-CSRF-Token` |
| Passwords | scrypt (Python standard library) N = 2^15, r = 8, p = 3, per-user salt, parameters stored per hash; 12–256 characters; rehashed at login when costs rise; at most 2 concurrent hashes per process |
| Lockout | 5 consecutive failures lock an account for 15 minutes, with serialized updates against parallel bypass |
| Rate limit | 30 attempts per client per 5 minutes, database-backed (shared by all API processes), returned as 429 before hashing; separate buckets for sign-in, sign-up, verification and guest |
| Public endpoints | JSON only, allowed-Origin check, 16 KiB body limit, one generic error ("Invalid username or password."), a dummy hash for unknown users, validation errors that never echo passwords |
| Response headers | `Cache-Control: no-store`, `nosniff`, frame denial, same-origin referrer policy |
| Sign-up | 6-digit code stored as a hash; 10-minute expiry, 5 attempts, 60-second resend cooldown; identical answers whether or not an address exists; optional domain allowlist; with no SMTP host the code is printed to the API log locally and nothing is sent |
| Guests | A fresh password-less account per visitor (its password field is not a valid hash), 2-hour session; may change data only while the server is **guest-safe**: no Slack webhook, drafts from the demo generator or the self-hosted model, and planning mocked |
| Roles | `operator` (everything), `viewer` (read-only; 403 on actions), `guest` (operator actions on a guest-safe server only) |
| Accounts | `python -m app.auth_cli create-user`, `set-password`, `disable`, `enable`, `revoke-sessions`, `list`; passwords prompted without echo |
| SQL | Every query through SQLAlchemy with bound parameters |
| Secrets | From the environment; `.env` git-ignored; Slack URL and API keys never logged or returned; provider errors sanitized |
| Supply chain | Hash-locked backend install (`requirements-lock.txt`, 40 packages); frontend and backend audits 0 findings (2026-09-27), after overriding Next's PostCSS to 8.5.28; training environment findings documented |

**Dependency review (2026-09-27, [`docs/dependency-security.md`](docs/dependency-security.md)).**
Frontend `npm audit`: 0 after the PostCSS override (it fixed four PostCSS advisories). Backend
`pip-audit` over 40 locked packages: 0. Training CPU/GPU pins: two unique setuptools advisories
(legacy `PackageIndex` path traversal, CVE-2025-47273; Unicode normalization bypass of sdist
exclusions, CVE-2026-59890), neither on a path the recorded workflow uses; the vendor torch wheels
are not covered by the scanner.

## 20. Audit trail

Every meaningful state change writes a `WorkflowEvent`. A SQLAlchemy `before_insert` hook,
installed with the models so it runs in every process (API, worker, CLIs), stamps `actor` on
every new event and `actor_user_id` when a user is known, including jobs a user queued
(`job:user:<name>`). Historical labels such as `local-demo-unauthenticated` and
`demo-auto-approve` are kept as they were.

Event types include: `batch_uploaded`, `batch_upload_partial`, `batch_scored` and `lead_scored`
(retired scorer; historical rows, plus `batch_scored` from the demo), `lead_fit_scored` (now with
`targeting_hash` and `priority`), `rankings_recalculated`, `pdl_import_chunk_committed`, `seller_profile_draft_saved` and activation
events, `ai_generation_rejected`, `ai_output_revised`, `outreach_approved`, `outreach_rejected`,
`lead_pushed`, `lead_push_blocked`, `batch_hot_leads_pushed`, `push_outcome_unknown`,
`push_outcome_resolved`, `push_late_outcome`, the six `job_*` events, `assistant_run_finished`,
`split_manifest_frozen`, `annotation_queue_created`, `annotation_candidate_generated`,
`training_annotation_recorded`, and account events (creation, change, revocation, login, logout,
lockout). Passwords and raw tokens never enter audit records.

**Found by testing:** the first Phase 12 live run found 16 worker-written events with no actor,
because the hook was installed only in `app.main`, which the worker never imports. It moved into
the models package, a subprocess regression test was added (mutation-checked), and the re-run had
0 unattributed events.

## 21. Testing and verification

### 21.1 Current results

| Suite | Result (profile-based ranking, 2026-10-18, local, mock integrations only) |
|---|---|
| Backend, disposable PostgreSQL 16 (Docker) | **611 passed** |
| Backend, SQLite | **606 passed**, 5 PostgreSQL-only skips |
| New end-to-end ranking tests (`tests/test_profile_ranking.py`) | 17 passed |
| Frontend (Vitest, jsdom), 18 files | **122 passed** |
| TypeScript `tsc --noEmit` and `next build` | Clean |
| Live release harness (PostgreSQL, API + worker + production frontend proxy) | **107 of 107** checks |
| Migrations on PostgreSQL 16 | `alembic upgrade head` (0001→0015), `downgrade -1`, `upgrade head`, `alembic check` clean each time |
| Browser walk-through (Playwright, Chromium, local stack) | Passed: import before setup (kept, "not ranked"), setup banner, profile with target customers saved and activated, recalculation, ranked list with reasons, lead breakdown, two selected leads drafted by the worker, approval, target customers changed (10 rankings outdated, recalculated: software companies Hot, clinics Cold, the approved draft "out of date" and Push to Slack disabled), dashboard |
| Training (CPU) | Not re-run: no training code or prompt builder changed (23 passed at the Phase 12 record) |
| OpenAPI | 63 operations; `git diff --check` clean |

The 107 live checks are one fewer than 108 because the retired `legacy_score` job (an enqueue
check and a worker-completion check) was replaced by one check that it is refused. Backend test
functions went from 508 to 513 across 48 files: the legacy scorer's two test files were deleted
with the scorer, the fit-scorer unit tests were rewritten for the new rules, and new tests cover
profile-based ranking, setup required, outdated rankings and recalculation, `selected_outreach`,
the `presumed_need_or_intent` check, migration `0015` and the retired job. No CI run is claimed for
these results.

Earlier records: assistant branch (2026-10-03) 581 backend on PostgreSQL, 117 frontend, 108 live
checks; `main` before it 560 / 109 / 96 with CI green; the last recorded CI release run passed on
2026-09-27.

**What the new tests show** (`tests/test_profile_ranking.py`, `tests/test_fit_scoring.py` and
updates elsewhere): the same companies rank differently for a clinic seller and a small-software
seller in the inbox, sorting and filters; a small company ranks well for a small-company seller;
"Hospitality" is a mismatch for a "Hospital" target; missing data is reported as missing (and
"needs data") rather than as a mismatch; with no target preferences every ranking route answers
409 and nothing is written; a lead carrying a retired v1 "Hot" score and a demo-rubric "strong"
row is unranked until recalculated and then ranked only by the new targets in the inbox,
dashboard, lead filters, single push, `push_hot` job and assistant; changing target customers
marks rankings outdated, recalculation adds rows and preserves the old ones, and a revision with
the same targets keeps rankings current; a profile change makes an approved draft's approval stop
applying, refuses delivery, and a new draft uses the new revision and new ranking together;
selected drafting uses the active revision, skips blocked leads and refuses leads whose ranking is
outdated; exact-draft approval, replayed (not duplicated) delivery and blocked-at-any-score still
hold.

### 21.2 What each layer covers

| Layer | Location | Coverage |
|---|---|---|
| Backend unit and API tests | `backend/tests/` (48 files, 513 test functions) | Ranking, ingestion, grounding, generation, review and revisions, seller profiles, jobs and leases, the delivery ledger, metrics, inbox, model status, auth, sign-up and guests, migrations, the PDL pipeline, datasets, evaluation, the assistant |
| PostgreSQL-only tests | Same suite with `TEST_DATABASE_URL` | Real concurrency (simultaneous pushes, lease races, parallel lockout), process kills, worker takeover |
| Frontend | `frontend/src/**/*.test.ts(x)` (18 files) | Today, Leads, Imports, Settings, Assistant, sidebar, lead page (generation, review, interactions), import page, metrics, seller profile and activation, login; API client (CSRF only on writes, cookies always sent, 401 hand-off); auth provider; jobs panel; push history; paginated history; review timer |
| Training | `training/tests/` | Weighted objective and gradient scaling, split guards, test-split refusal, adapter hash checks, launcher secret handling and per-profile state, serving request limits, bundle allowlists |
| Release harness | `backend/scripts/verify_release.py` | 108 live checks against a fresh database (below) |

Backend test files by area:

| Area | Files |
|---|---|
| Ranking | `test_fit_scoring`, `test_profile_ranking`, `test_fit_scoring_cli`, `test_fit_scoring_endpoints`, `test_fit_consistency`, `test_leads_fit_sort_filter` (`test_lead_scoring` and `test_scoring_endpoints` were removed with the legacy scorer) |
| Ingestion and import | `test_csv_ingestion`, `test_csv_upload_bounds`, `test_csv_upload_retry`, `test_csv_upload_interrupted_chunk`, `test_batch_endpoints`, `test_partial_import_recovery`, `test_reimport_preserves_workflow_state`, `test_pdl_curation_fixtures`, `test_pdl_identity_idempotency`, `test_provenance` |
| Generation | `test_ai_generation`, `test_ai_endpoints`, `test_grounded_generation`, `test_lora_client`, `test_quality_checks`, `test_budgeted_generation` |
| Review and seller | `test_outreach_review`, `test_exact_draft_review`, `test_seller_profiles`, `test_seller_activation`, `test_seller_profile_migration` |
| Delivery | `test_slack_integration`, `test_push_endpoints`, `test_delivery_review_enforcement`, `test_blocked_lead_routing`, `test_phase11_delivery` |
| Jobs | `test_background_jobs` |
| Metrics and workspace | `test_metrics`, `test_phase11_metrics`, `test_workspace`, `test_pagination` |
| Auth | `test_phase12_auth`, `test_auth_hardening`, `test_signup_guest` |
| Model work | `test_annotation`, `test_phase7_dataset`, `test_phase9_eval`, `test_phase3_regression_checks` |
| Other | `test_health`, `test_models`, `test_demo`, `test_lead_assistant` |

### 21.3 Practices

- **No outbound network.** The suite blocks external sockets except loopback; the release
  harness's child API and worker reuse that guard. `tests/conftest.py` forces the mock provider, an
  empty key and an empty webhook before settings load, and refuses to run otherwise.
- **Mutation checks.** Key safety tests were verified by removing the safeguard and confirming the
  test fails: CSRF check (1 test fails), making `/api/leads` public (3), actor hook not installed
  (1), job fence, retry bound and crash-loop limit (each fails a test), non-unique delivery key,
  event-counted approval (2), the frontend approve gate, the blind-review packet hash check, and
  the stale-response and resolve-once checks in generation.
- **Deny-by-default proof.** A test enumerates every operation in the OpenAPI schema and asserts
  anonymous requests are refused; sensitive actions return 401 anonymously and 403 for a viewer.
- **Migrations** are tested up, down and up again, with `alembic check` reporting no drift.
- **Hostile environment run.** The Phase 7 suite also passed with real-provider settings and a fake
  key and webhook exported, proving tests cannot pick them up.

### 21.4 The release harness

`backend/scripts/verify_release.py` starts a real API, a real worker and optionally the production
frontend (`next start`), and refuses non-loopback or non-empty PostgreSQL targets. It checks:
migration preservation of synthetic legacy data and audit columns through `0011 → head → 0011 →
head` plus `alembic check`; CLI account creation; anonymous refusal; generic login errors; sign-in
through the proxy with correct cookie flags; CSRF refusal; viewer denial; logout revocation;
sign-up and verification; guest access; ranking jobs against the activated demonstration
profile (two Hot leads) and refusal of the retired `legacy_score` job; the other job types including enqueue dedupe and cancellation;
exact-draft approval and rejection; mock delivery, replay, unknown-outcome resolution and explicit
redelivery; bounded mock metrics; authenticated worker actors; and the assistant flow. All child
processes are stopped on success or failure.

Growth: 27 checks (initial Phase 12) → 81 (SQLite) / 84 (PostgreSQL) → 96 (sign-up and guests) →
108 (assistant) → 107 (profile-based ranking: the retired job's two checks replaced by one refusal check).

### 21.5 CI (`.github/workflows/`)

| Workflow | Trigger | Steps |
|---|---|---|
| `backend.yml` | Changes under `backend/` | Python 3.12, hash-locked install, `pytest -v` (SQLite) |
| `frontend.yml` | Changes under `frontend/` | Node 24, `npm ci`, typecheck, tests, build |
| `training.yml` | Changes under `training/` or `prompts.py` | Pinned CPU install, pinned tokenizer and config only (no weights), offline tests; never trains |
| `release.yml` | `main`, release branches, PRs, manual | PostgreSQL 16 service with test-only credentials; full suite on SQLite and PostgreSQL; frontend install, tests, typecheck, build; migrations and the live release flow through the production frontend. 25-minute limit; no secrets, paid integrations, GPU or deployment |

### 21.6 Growth across the project

| Point | Backend | Frontend | Live checks |
|---|---|---|---|
| Phase 1 audit | 121 | typecheck and build only | |
| Phase 2 | 149 | | |
| Phase 3 closeout | 190 | | |
| Phase 4 | 276 | 31 | |
| Phase 5 | 342 | 56 | |
| Phase 6 | 389 | 75 | |
| Phase 7 | 430 | 82 | |
| Phase 10 | 476 (PostgreSQL) | 89 | |
| Phase 11 | 492 (PostgreSQL) | 94 | |
| Phase 12 | 521 (PostgreSQL) | 107 | 84 |
| Sign-up and guest access | 545 (PostgreSQL) | 112 | 96 |
| Redesign (`main`) | 560 (PostgreSQL) | 109 | 96 |
| Assistant | 581 (PostgreSQL) | 117 | 108 |
| Profile-based ranking (2026-10-18, local) | 611 (PostgreSQL) | 122 | 107 |

The frontend count dipped in the redesign because the retired annotation page's tests were removed.

### 21.7 Defects that verification caught

| Found | Defect | Fix |
|---|---|---|
| Phase 1 audit | Blocked leads could score Hot and be pushed | Hard stop at dispatch (Phase 2) |
| Phase 1 audit | A stale tab approved the wrong draft | Approval bound to `ai_output_id` and content hash |
| Phase 1 audit | `approval_rate` could exceed 100% | Cohort metric (Phase 11) |
| Phase 3 | Approve/reject overwrote a blocked status | Status writes no longer clobber dispositions |
| Phase 3 closeout | A missing checksum created duplicate snapshots; concurrent imports could duplicate | Mandatory checksum, unique constraints, 8-process race test |
| Phase 3 closeout | Scoring a batch overwrote its `partial` status | Fixed in `score_one_batch` |
| Phase 4 finish pass | Stale bulk readiness, version-filter drift, timestamp tie-breaks allowing stale approvals, crash-unsafe uploads, UI races | Fixed with tests |
| Phase 6 | Migration `0009` exceeded PostgreSQL's identifier limit | Caught on the restored copy, fixed before the real database |
| Phase 7 | The test suite loaded a developer `.env` and made 2 real gpt-4o-mini calls (about $0.0008) | `conftest.py` guard plus a regression test |
| Phase 8 | Refusal tests started real training on the GPU pod | Tests force "no CUDA"; pods run tests with the GPU hidden |
| Phase 9 prep | A Phase 9 launch would have overwritten the Phase 8 run record | Per-profile state files, with a test that fails on the old launcher |
| Phase 10 | The job fence mutation survived the SQLite-only suite | A PostgreSQL takeover test added |
| Phase 12 | 16 worker events had no actor | Hook installed with the models |
| Phase 12 | A CPU-bound frontend test exceeded 5 s under parallel load | A 20 s limit for that one test, assertions unchanged |
| Ranking (2026-10-18) | The assistant's industry filter used a substring check ("health" matched "Healthcare"; "hospital" would match "Hospitality") | Whole-word matching shared with ranking; the SDK round-trip test now asks for "healthcare" |
| Ranking (2026-10-18) | An identical re-ranking from a revision with the same targets invalidated approvals, because the draft's ranking block carried the revision id | The block carries the `targeting_hash` instead (the seller block already names the revision); caught by the inbox stage test |

### 21.8 Running the tests

```bash
cd backend
.venv/bin/python -m pytest -q                                            # SQLite
TEST_DATABASE_URL=postgresql+psycopg://… .venv/bin/python -m pytest -q   # disposable PostgreSQL only
.venv/bin/python scripts/verify_release.py --with-frontend               # after a frontend build

cd ../frontend
npm test && npm run typecheck && npm run build

cd ../training
python -m pytest -q
```

Never point `TEST_DATABASE_URL` at a database whose contents matter: fixtures drop tables.

## 22. API

63 operations (`/docs` once signed in). Every route except the public ones needs a session; state
changes also need `X-CSRF-Token` and the `operator` role (or `guest` on a guest-safe server).

| Group | Endpoints |
|---|---|
| Health (public) | `GET /health`, `GET /api/health` |
| Auth | `GET /api/auth/options`; `POST /api/auth/login`, `/register`, `/verify-email`, `/resend-code`, `/guest` (public); `GET /api/auth/session`; `POST /api/auth/logout` |
| Imports | `POST /api/batches/upload`; `GET /api/batches`, `/api/batches/{id}` |
| Leads | `GET /api/leads` (paginated, sort by current ranking score and filter by band or minimum score), `/api/leads/{id}` |
| Ranking | `GET /api/scoring/profile` (state, targets, weights, thresholds, counts); `POST /api/scoring/recalculate` (whole workspace); `POST`/`GET /api/leads/{id}/fit-score` (GET returns an outdated ranking with `is_current: false`); `POST /api/batches/{id}/fit-score`; `GET /api/batches/{id}/fit-summary`, `/fit-scores`. All ranking writes answer 409 "setup required" without target customers. Removed 2026-10-18: `POST`/`GET /api/leads/{id}/score`, `POST /api/batches/{id}/score`, `GET /api/batches/{id}/scores`, `GET /api/scoring/fit-profile` |
| Readiness | `GET /api/leads/{id}/readiness`, `/api/batches/{id}/readiness` |
| Generation | `POST /api/leads/{id}/generate-summary`, `/generate-outreach`; history and `latest-ai-output` reads; `POST /api/leads/{id}/ai-outputs/{output_id}/revisions` |
| Review | `POST /api/leads/{id}/approve-outreach`, `/reject-outreach`; `GET /api/leads/{id}/review-state` |
| Delivery | `POST /api/leads/{id}/push` (`force`, `redeliver`), `/api/batches/{id}/push-hot`, `/api/pushes/{id}/resolve`; push history |
| Jobs | `POST /api/batches/{id}/jobs` (including `selected_outreach` with `{"lead_ids": [...]}`, 1–25); `GET /api/jobs`, `/api/jobs/{id}`, `/api/jobs/{id}/items`; `POST /api/jobs/{id}/cancel` |
| Seller profiles | `GET`, `POST /api/seller-profile`, `…/activate`, `…/deactivate` |
| Workspace | `GET /api/inbox` (with ranking fields and state), `GET /api/ai/status` |
| Assistant | `GET /api/assistant/status`, `POST /api/assistant/plan` (never queues work) |
| Metrics | `GET /api/metrics/dashboard` |
| Demo | `POST /api/demo/run` (seeds and runs the labeled demo; used by the release checks) |
| Annotation | The annotation API kept for the model-work tools |

List endpoints are paginated (default 50, maximum 200 per page) with a server-computed `total` and
a unique tie-breaker ordering. Curl examples with sign-in and CSRF are in
[`backend/README.md`](backend/README.md).

## 23. Configuration

**Backend** (`backend/app/core/config.py`, documented in [`backend/.env.example`](backend/.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | required | `postgresql+psycopg://…`; `postgres://` and `postgresql://` are rewritten automatically |
| `USE_MOCK_AI` | `true` | Mock drafting and planning; wins over both provider settings |
| `AI_PROVIDER` | `openai` | `openai` or `qwen3-4b-lora-v1` when mock is off |
| `AGENT_PROVIDER`, `AGENT_MODEL` | `mock`, `gpt-4o-mini` | Assistant planning, independent of drafting |
| `OPENAI_API_KEY` | empty | Needed for real OpenAI drafting or planning (paid) |
| `LORA_INFERENCE_BASE_URL`, `LORA_INFERENCE_API_KEY` | empty | The OpenAI-compatible adapter server |
| `LORA_SERVED_MODEL`, `LORA_TIMEOUT_SECONDS` | `qwen3-4b-lora-v1`, `120` | Served model name and request timeout |
| `LORA_FALLBACK_TO_MOCK` | `false` | Use the demo generator while the fine-tuned server is offline |
| `SLACK_WEBHOOK_URL` | empty | Empty means mock delivery |
| `SESSION_COOKIE_SECURE` | `true` | `false` only for plain-HTTP localhost |
| `SESSION_IDLE_MINUTES`, `SESSION_ABSOLUTE_HOURS` | `60`, `12` | Session lifetimes |
| `PASSWORD_HASH_N` | `32768` | scrypt cost for new hashes |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Exact browser origins |
| `SELF_SIGNUP_ENABLED`, `SELF_SIGNUP_ROLE`, `SIGNUP_ALLOWED_EMAIL_DOMAINS` | `false`, `operator`, any | Sign-up |
| `GUEST_ACCESS_ENABLED`, `GUEST_SESSION_HOURS` | `false`, `2` | Guest access |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS`, `EMAIL_FROM` | empty, `587`, empty, empty, `true`, empty | Verification email |

**Frontend** (read at build time): `API_PROXY_TARGET` (where the Next.js server proxies `/api`) and
`NEXT_PUBLIC_API_BASE_URL` (leave empty so the browser uses the same-origin proxy).

## 24. Running it locally

Requirements: Python 3.12, Node.js 24, Docker (for a disposable PostgreSQL). No API keys or GPU.

```bash
# Terminal 1: database and API (from the repository root)
docker run -d --name gtmflow-demo-pg -e POSTGRES_PASSWORD=local-demo-only \
  -e POSTGRES_DB=gtmflow_demo -p 127.0.0.1:55432:5432 postgres:16
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-lock.txt
export DATABASE_URL=postgresql+psycopg://postgres:local-demo-only@127.0.0.1:55432/gtmflow_demo
export OPENAI_API_KEY= SLACK_WEBHOOK_URL= LORA_INFERENCE_BASE_URL= LORA_INFERENCE_API_KEY=
export SESSION_COOKIE_SECURE=false ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
export USE_MOCK_AI=false AI_PROVIDER=qwen3-4b-lora-v1 LORA_FALLBACK_TO_MOCK=true AGENT_PROVIDER=mock
export SELF_SIGNUP_ENABLED=true GUEST_ACCESS_ENABLED=true
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers

# Terminal 2: worker (same DATABASE_URL and AI exports)
cd backend && .venv/bin/python -m app.jobs.worker

# Terminal 3: frontend
cd frontend && npm ci
API_PROXY_TARGET=http://127.0.0.1:8000 NEXT_PUBLIC_API_BASE_URL= npm run dev
```

Open http://localhost:3000, **Continue as guest**, press **Try with sample data**, and follow the
[demo script](docs/demo-script.md) (8 steps, about 6 minutes, plus an optional assistant step).
Codespaces, SQLite-only and account maintenance options are in the
[Phase 12 handoff](docs/engineering-log/phase12-release-handoff.md#6-exact-startup-commands-local-mock-mode).

## 25. Deployment

**Railway (production for `main`, as of 2026-10-03):**

| Service | Configuration |
|---|---|
| Frontend | Root `frontend`; `npm run start`; `NEXT_PUBLIC_API_BASE_URL` set to its own address so the browser stays on one origin; `API_PROXY_TARGET` points at the backend |
| Backend | Root `backend`; pre-deploy `alembic upgrade head`; `uvicorn app.main:app --host 0.0.0.0 --port $PORT`; drafting with OpenAI (gpt-4o-mini); guest access on |
| Worker | Root `backend`; `python -m app.jobs.worker` |
| Postgres | Managed PostgreSQL 18.6 |

The production database predated migrations. On 2026-10-03 it was backed up, the adoption was
rehearsed on a restored copy, then it was stamped as the baseline and upgraded through all 13 later
migrations with every row preserved (3 imports, 30 leads, 12 AI outputs, 6 deliveries, 57 audit
events). Verified afterwards: the sign-in page loads, the session persists, guest access works, the
30 leads appear, the worker starts, and guests are read-only because OpenAI drafts are paid (the
server is not guest-safe).

Note: the README and `docs/deployment.md` were written before this fix and still say "not
deployed"; this section is the later record. The assistant branch is not deployed.

**Profile-based ranking is not deployed.** Production still runs the code with the retired scorer
and has not applied migration `0015`. Deploying it would run `0015` (additive) in pre-deploy;
after release, production's existing scores read as outdated history, every lead needs its seller
profile to state target customers before it can be ranked, and drafts approved under the old
context show as out of date until regenerated and re-reviewed.

**Hosting estimates** (checked 2026-10-02; [`docs/deployment.md`](docs/deployment.md)):

| Option | Estimated cost |
|---|---|
| App on Railway | about $10–15 a month ($5 plus usage) |
| App on Render (3 Starter services + Basic-256mb PostgreSQL) | about $27 a month |
| Fine-tuned model, always-on L4 (RunPod Secure, $0.49/hr) | about $358 a month; median 22.6 s per draft |
| Fine-tuned model, serverless L4 ($0.69/hr active) | usage-based, about $7 a month at 50 drafts a day; the container is not built |
| OpenAI drafting | about $0.0005 per draft |

**Before going wider:** a trusted edge proxy (login throttling is per peer), global rate and
resource limits, a backup restore drill, retention jobs, and a decision on MFA and account recovery.

## 26. Money spent

| What | Provider | Amount |
|---|---|---|
| Phase 6 pilot generation (105 calls) | OpenAI | about $0.048 |
| Phase 7 held-out generation (207 attempts) | OpenAI | $0.0954 |
| Phase 7 training-data generation (473 attempts) | OpenAI | $0.2167 |
| Phase 7 accidental test-suite calls (2) | OpenAI | about $0.0008 |
| Phase 8 training, including 3 refused launch attempts | RunPod | $0.6131 |
| Phase 9 self-deletion proof and evaluation | RunPod | $0.6945 |
| Phase 10 acceptance test | RunPod | $0.0739 (observed balance change; billing may settle) |
| **Total recorded** | | **about $1.74** |

Every paid step had an explicit authorization and a cap (Phase 8 $3.00, Phase 9 $1.50, Phase 10
$0.80), and every cap held. Token usage: held-out run 360,276 prompt + 68,921 completion tokens;
training-data run 823,249 + 155,396. Pricing used: gpt-4o-mini $0.15 per 1M input and $0.60 per 1M
output tokens (checked 2026-09-24). The RunPod balance went from $10.00 to $8.5945.

## 27. Timeline

| Date | Milestone |
|---|---|
| 2026-06-04 | First version: CSV upload, legacy scoring, mock AI, Slack push, metrics |
| 2026-09-22 | Phase 1 audit (14 findings); Phase 2 migrations, provenance and the blocked-lead fix; Phase 3 company-data import of 32.3 M records |
| 2026-09-23 | Phase 4 company-fit scoring, readiness and eligibility |
| 2026-09-23 / 24 | Phase 5 grounded generation and seller profiles |
| 2026-09-24 | Phase 6 exact-draft review and the mixed human/AI pilot; Phase 7 held-out sets |
| 2026-09-25 | Phase 7 training expansion and closure; Phase 8 training; move to a new Codespace |
| 2026-09-26 | Phase 9 evaluation; Phase 10 model integration and jobs; Phase 11 delivery ledger and metrics |
| 2026-09-27 | Phase 12 access control, dependency audit, release verification |
| 2026-10-02 | Sign-up with email verification and guest access |
| 2026-10-03 | Rep-centered redesign; Railway production fixed; outreach assistant (merged to `main`, not deployed) |
| 2026-10-18 | Profile-based lead ranking: target customers in the seller profile, one ranking for every workflow, legacy and demo rubrics retired, migration `0015`, selected-lead drafting (implemented and verified locally; not deployed) |

Commits per day: 2026-06-04: 2 · 09-22: 6 · 09-23: 10 · 09-24: 11 · 09-25: 20 · 09-26: 12 ·
09-27: 2 · 10-02: 7 · 10-03: 5.

Every phase moved along a readiness ladder (`code ready → needs human review → needs compute →
trained → evaluated → integrated`); no component skipped a rung in its status report.

## 28. Codebase statistics

Counted on `codex/lead-agent` at `4e1af36` (not recounted for the 2026-10-18 change, except: 15
migrations, 48 backend test files with 513 test functions, 63 API operations):

| Area | Files | Lines |
|---|---|---|
| Backend application (`backend/app`, Python) | 124 | 16,854 |
| Backend tests | 51 | 11,471 (508 test functions; parametrized to 581 tests) |
| Backend scripts | 9 | 1,456 |
| Frontend source (non-test) | 43 | 7,429 |
| Frontend tests | 18 | 3,076 |
| Training code (Python and shell) | 15 | 3,219 |
| Markdown documentation | | about 7,950 |
| Alembic migrations | 14 | |
| Tracked files | 417 | |

Commits: 75 on this branch, 74 on `main`.

## 29. The Phase 1 audit and how each finding was resolved

| Finding | Resolved in |
|---|---|
| No migration framework (`create_all` only) | Phase 2: Alembic and a baseline verifier |
| PDL columns do not match the CSV columns | Phase 3: a dedicated importer and mapping |
| No upload cap, no pagination | Phase 3: 20 MiB / 50,000 rows, streamed; paginated lists |
| Company-only records cap near 41 of 100 and are always Cold | Phase 4: the separate company-fit score; 2026-10-18: one ranking that only counts configured criteria and reports missing data separately |
| Substring false positives in industry and keywords | Phase 3 and 4: exact matching in the fit profile; since 2026-10-18 whole-word matching in ranking and the assistant filter |
| Disqualified leads could score Hot and be pushed | Phase 2 (routing gate), Phase 4 (eligibility) |
| No seller profile; the mock pitched GTMFlow itself | Phase 5: versioned seller profiles |
| Stale tab approves the wrong draft | Phase 2 and 6: `ai_output_id` and content hash |
| No approve/reject state machine | Phase 6 |
| No storage for edits | Phase 6: immutable revisions |
| `approval_rate` above 100% | Phase 11: cohort metric |
| No protection against concurrent pushes | Phase 11: the delivery ledger |
| Mock and real merged on the dashboard | Phase 11: mode breakdowns |
| No auth, permissive CORS, synchronous long operations | Phase 10 (jobs), Phase 12 (auth, CORS) |
| No frontend tests | Phases 4–12: Vitest suite, now 122 tests |

## 30. Key design decisions

| Decision | Why |
|---|---|
| Deterministic scoring, no LLM | A "this is Hot" claim must be defensible and reproducible |
| Rank against the seller's own target customers, with no fallback rubric | A fixed rubric ranks for someone else's market; "setup required" is more honest than silently reusing demo targets |
| Unknown earns nothing, under 50% coverage is "needs data" | A high score must rest on evidence, and missing data must not look like a mismatch |
| Scores scoped by a hash of the target preferences, history kept | Changing who you sell to outdates rankings without destroying them, while a wording change to the offer does not force a re-rank |
| Ranking in the draft context is labeled not evidence of need, plus a runtime flag | A strong match is an attribute match; it must never become "you need this" |
| Grounding enforced by the server | A prompt instruction is a request; a validator is a rule |
| Approval bound to content, inputs and seller revision | So nothing can be sent that a person did not see, as it was |
| Claim before send | The only way to stop duplicates when the receiver has no idempotency key; uncertainty becomes a person's decision |
| Job queue in PostgreSQL, no Redis | One system to back up and reason about; fenced leases give the needed safety |
| Mock by default, fail closed for real modes | The full workflow runs free, and nothing paid starts by accident |
| Explicit migrations, never on boot | A migration is a deliberate, backed-up step |
| Split by company group, frozen by hash | Prevents leakage and silent dataset drift |
| Minimal-edit training corrections | Keep the model's own voice where it was fine; v1 rewrote 80% of each draft |
| Qwen3-4B with LoRA on one L4 | Small enough to train and serve cheaply; Apache-2.0; answers JSON directly |
| Separate planning provider from drafting provider | The adapter was not trained or evaluated for tool use |
| Guests act only on a guest-safe server | A public visitor must never trigger a real message or a paid call |
| Report AI-evaluated results as such | Honest measurement is a product principle, not just a documentation style |

The full record with reasoning is in [`decisions.md`](docs/engineering-log/decisions.md).

## 31. Limitations and open items

- **Model quality is AI-evaluated only,** on a small test set (71 examples, 36 outreach). A human
  review of outreach drafts is the most valuable next step. Outreach still needs review.
- **No persistent model server.** The fine-tuned model ran on temporary GPU pods; production drafts
  with OpenAI.
- **The assistant** has no live-model evidence (selection accuracy, latency, cost), no memory,
  streaming, cost quota or planner rate limit, and is not deployed.
- **Real Slack delivery is unexercised;** every delivery so far went to the mock webhook or test
  doubles. Delivery is at most once per claim, not exactly once.
- **One shared workspace:** everyone, guests included, works on the same leads; no multi-tenancy.
- **Sign-up needs an SMTP provider** before visitors can receive codes.
- **No MFA or password reset;** old guest accounts, sessions, job items and throttle rows are not
  cleaned up automatically.
- **Ranking is lexical, not semantic.** Matching is whole-word with a small synonym table: it does
  not know that "Medical Practice" is healthcare unless the seller lists both, does not resolve a
  city or state to its country, and cannot read verbal sizes ("small"). Weights and thresholds are
  fixed in code (documented, not editable per profile) and were chosen by reasoning, not
  calibrated against outcomes; a ranking is not a purchase probability. Recalculation is manual
  (a banner prompts it) and the workspace endpoint runs synchronously, which is fine at the
  5,000-lead inbox bound but is not tested at larger sizes. Selected drafting allows one active
  selection job per import (up to 25 leads). The fine-tuned model was trained before the
  ranking block's wording changed; its behavior with the new block is not evaluated.
- **Profile-based ranking is not deployed**, and README, `docs/scoring-model.md`, `docs/metrics.md`
  and the screenshots still describe the retired scoring until they are updated.
- Time saved is an estimate. Metrics have no time-window filter.
- **Datasets:** held-out sets are 26 and 29 short of 100; 180 uncertain records unresolved;
  training is 47 / 53 healthcare / real estate; two segments and one seller profile only. The 5,000
  imported PDL companies were selected with the retired demo criteria; their stored rankings are
  history and they need a recalculation against a real profile.
- **Training environment** has open setuptools findings and a PyTorch audit gap.
- **Not load-tested** beyond 300 job items and 2 workers.
- **Housekeeping:** the original development PostgreSQL database (with the 448 train-v2 outputs)
  still lives only in the old Codespace; its recovery is a separate open task. The dataset files
  and committed review decisions preserve that content.

## 32. Glossary

| Term | Meaning |
|---|---|
| Lead | One company (and optional contact) from an import |
| Import (batch) | One uploaded CSV or sample load |
| Ranking | A lead's 0–100 match with the active seller profile's target customers, shown as Hot / Warm / Cold or Needs data |
| Target preferences | The seller profile's structured "who we sell to": industries, locations, size ranges, contact roles, keywords |
| Outdated ranking | A ranking made against different target preferences than the active profile's; kept as history, never current |
| Priority score (retired) | The fixed-term 100-point Hot / Warm / Cold score (legacy v1), removed 2026-10-18 |
| Evidence coverage | The share of the configured criteria's weight the record could answer |
| Seller profile | What you sell (the only source of claims a draft may make) and who you sell to (what leads are ranked against) |
| Grounded draft | A draft whose every claim cites an id in its context |
| Runtime checks | Pattern checks that flag known problem phrasings for the reviewer |
| Exact-draft approval | An approval tied to the content hash of the draft shown |
| Delivery ledger | The claim-before-send record that prevents double sends |
| Replay | A repeat push that returns the stored outcome without sending |
| Unknown outcome | A delivery that may or may not have arrived; resolved by a person |
| Fenced lease | A job lease renewed in the same transaction as each item's work |
| Needs data | A ranking with under 50% evidence coverage: too little of the record to rank confidently |
| Guest-safe | No Slack webhook, no paid per-request AI, planning mocked |
| Company group | Companies linked by identity, domain, LinkedIn or name; never split across train and test |
| LoRA | Low-Rank Adaptation: small trained weights added to a frozen base model |
| AI-evaluated | Judged by an AI reviewer against AI-reviewed references, not by people |
| Readiness ladder | code ready → needs human review → needs compute → trained → evaluated → integrated |

## 33. Where to find more

| Topic | Document |
|---|---|
| Short tour, screenshots, setup | [README](README.md) |
| Architecture | [`docs/architecture.md`](docs/architecture.md) |
| Scoring | [`docs/scoring-model.md`](docs/scoring-model.md) |
| AI workflow and evidence | [`docs/ai-workflow.md`](docs/ai-workflow.md) |
| Slack and integrations | [`docs/integrations.md`](docs/integrations.md) |
| Metrics | [`docs/metrics.md`](docs/metrics.md) |
| Deployment plan and costs | [`docs/deployment.md`](docs/deployment.md) |
| Dependency review | [`docs/dependency-security.md`](docs/dependency-security.md) |
| Demo walkthrough | [`docs/demo-script.md`](docs/demo-script.md) |
| Phase status and evidence | [`phase-status.md`](docs/engineering-log/phase-status.md) |
| Working rules | [`implementation-contract.md`](docs/engineering-log/implementation-contract.md) |
| Phase 1 audit | [`audit.md`](docs/engineering-log/audit.md) |
| Company-data import | [`phase3-data-handoff.md`](docs/engineering-log/phase3-data-handoff.md) |
| Fit scoring | [`phase4-scoring-handoff.md`](docs/engineering-log/phase4-scoring-handoff.md) |
| Grounded generation | [`phase5-generation-handoff.md`](docs/engineering-log/phase5-generation-handoff.md) |
| Review and the pilot | [`phase6-review-handoff.md`](docs/engineering-log/phase6-review-handoff.md), [`phase6-ai-review-report.md`](docs/engineering-log/phase6-ai-review-report.md) |
| Datasets | [`phase7-dataset-handoff.md`](docs/engineering-log/phase7-dataset-handoff.md), [`phase7-correction-policy-v2.md`](docs/engineering-log/phase7-correction-policy-v2.md) |
| Training | [`phase8-training-handoff.md`](docs/engineering-log/phase8-training-handoff.md), [`training/README.md`](training/README.md) |
| Evaluation | [`phase9-evaluation-handoff.md`](docs/engineering-log/phase9-evaluation-handoff.md) |
| Integration and jobs | [`phase10-integration-handoff.md`](docs/engineering-log/phase10-integration-handoff.md) |
| Delivery and metrics | [`phase11-routing-metrics-handoff.md`](docs/engineering-log/phase11-routing-metrics-handoff.md) |
| Access control and release | [`phase12-release-handoff.md`](docs/engineering-log/phase12-release-handoff.md) |
| Assistant | [`lead-assistant-handoff.md`](docs/engineering-log/lead-assistant-handoff.md) |
| Decisions | [`decisions.md`](docs/engineering-log/decisions.md) |
