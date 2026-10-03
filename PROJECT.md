# GTMFlow AI: the complete project record

Everything about GTMFlow from A to Z: what it is, how it works, how it was built, and every number
that was measured along the way. Each figure comes from a dated record in
[`docs/engineering-log/`](docs/engineering-log/README.md), the code itself, or a verification run;
the source is named next to it. For a shorter tour, start with the [README](README.md).

## Contents

1. [At a glance](#1-at-a-glance)
2. [Purpose and users](#2-purpose-and-users)
3. [The product, page by page](#3-the-product-page-by-page)
4. [Architecture](#4-architecture)
5. [Data model](#5-data-model)
6. [Scoring](#6-scoring)
7. [Grounded generation](#7-grounded-generation)
8. [Review and approval](#8-review-and-approval)
9. [Slack delivery ledger](#9-slack-delivery-ledger)
10. [Background jobs](#10-background-jobs)
11. [Lead stages](#11-lead-stages)
12. [Metrics dashboard: every field](#12-metrics-dashboard-every-field)
13. [The fine-tuned model](#13-the-fine-tuned-model)
14. [Security and access](#14-security-and-access)
15. [Testing and verification](#15-testing-and-verification)
16. [API](#16-api)
17. [Configuration](#17-configuration)
18. [Deployment](#18-deployment)
19. [Money spent](#19-money-spent)
20. [Timeline](#20-timeline)
21. [Codebase statistics](#21-codebase-statistics)
22. [Limitations and open items](#22-limitations-and-open-items)
23. [Glossary](#23-glossary)

---

## 1. At a glance

| Item | Value |
|---|---|
| What it is | An internal lead-to-outreach tool for a sales team's reps |
| Drafting model | `qwen3-4b-lora-v1`: Qwen3-4B-Instruct-2507 fine-tuned with LoRA for this project |
| Model result (AI-evaluated) | 49 of 71 held-out drafts acceptable as-is (69%), against 1 of 71 for the base model |
| Backend | Python 3.12, FastAPI 0.141, SQLAlchemy 2.1, Pydantic 2.13, Alembic 1.20 |
| Frontend | Next.js 15 (App Router), React 19, TypeScript 5, Tailwind CSS 3 |
| Database | PostgreSQL (16 tested; 18.6 on Railway); 23 tables, 14 migrations |
| API | 64 operations, deny-by-default |
| Tests | 560 backend on PostgreSQL (555 + 5 skipped on SQLite), 109 frontend, 23 training, 96 live release checks |
| Total recorded spend | about $1.74 across OpenAI and RunPod (section 19) |
| Development | 12 documented phases, then sign-up and guest access, a rep-centered redesign, and the Railway fix |
| Live | `https://gtmflow-ai-production.up.railway.app` (Railway; section 18) |

## 2. Purpose and users

**The problem.** Reps sit on lead lists. Deciding which leads matter, writing something specific to
each, and getting the good ones to the right channel is slow, and afterwards nobody can say how much
of the AI help was actually used.

**The users.** Sales reps and SDRs are the daily users. A RevOps lead or founder sets up what is
sold (the seller profile) and reads Insights. Visitors to the public showcase can use a guest
account.

**Two principles shaped every decision:**

- **Trust:** a draft may only use facts from the company's own record and claims from the seller
  profile; a person approves the exact text that is sent; nothing is sent twice by accident.
- **Honest measurement:** approval is counted once per draft, mock and real activity are never mixed,
  time saved is labeled an estimate, and the model's quality is reported as AI-evaluated.

## 3. The product, page by page

| Page | Route | What it does |
|---|---|---|
| Sign in | `/login` | Sign in with email or username, create an account (emailed 6-digit code), or **Continue as guest** |
| Today | `/` | Greeting, counts of drafts to review, leads ready to send, Hot leads without a draft and leads sent; a "Next up" list ranked Hot first; the drafting model's status; a setup prompt until a seller profile is active |
| Leads | `/leads` | Every lead across imports, ranked Hot first, with stage tabs and counts, search, and a priority filter |
| Lead workspace | `/leads/[id]` | The current draft (who wrote it, the facts it is based on, what is unknown), the review (approve, edit into a revision, reject with a reason), sending to Slack with delivery history, the contact card and the priority breakdown; company fit, eligibility, the review record and the full AI history in a collapsed "Technical details" |
| Imports | `/imports` | CSV upload, scored on import; **Try with sample data** (10 companies through the normal path); the CSV template; past imports |
| Import detail | `/batches/[id]` | One import's leads, background job controls (fit-score, legacy-score, summaries, outreach drafts, push approved Hot leads) and optional scoring details |
| Insights | `/metrics` | The metrics dashboard (section 12) |
| Settings | `/settings` | Drafting model status and how to connect the fine-tuned model; what you sell; the account |
| Seller profile | `/seller-profile` | Versioned seller profile editor with explicit, reviewed activation |

The sample list (`sample_data/leads_sample.csv`, also served as `frontend/public/sample-leads.csv`)
holds 10 companies tuned to score **2 Hot, 4 Warm, 4 Cold**.

## 4. Architecture

```
Browser ──/api──▶ Next.js server (same-origin proxy) ──▶ FastAPI ──▶ PostgreSQL ◀── Job worker
                                                           │                         │
                                                           ├──▶ drafting model ◀─────┤
                                                           └──▶ Slack ledger ◀───────┘
```

- **One origin for the browser.** The Next.js server forwards `/api` to FastAPI
  (`API_PROXY_TARGET`), so the session cookie stays `HttpOnly`, `SameSite=Lax` and `Secure`.
- **FastAPI** applies authentication, CSRF and role checks to every route through one app-wide
  dependency, then calls service functions.
- **The worker** (`python -m app.jobs.worker`) leases jobs from PostgreSQL and calls the same
  service functions, so approval, eligibility and delivery rules are identical in both paths.
- **The drafting model** is chosen by configuration: the fine-tuned model through an
  OpenAI-compatible server, the labeled demo generator, or OpenAI.
- **No Redis or message broker.** The queue, the delivery ledger, sessions and rate limits all live
  in PostgreSQL.

## 5. Data model

23 tables, grouped by purpose (physical names; the README shows a simplified conceptual diagram).

| Group | Tables |
|---|---|
| Leads and imports | `lead_batches`, `leads`, `lead_scores`, `lead_fit_scores` |
| Drafts and review | `ai_outputs`, `ai_output_reviews`, `seller_profiles`, `seller_profile_activations` |
| Delivery | `integration_pushes` (the ledger; unique `delivery_key`) |
| Background jobs | `background_jobs` (unique `dedupe_key`), `background_job_items` (unique per job and lead) |
| Audit | `workflow_events` |
| Accounts | `users`, `user_sessions` (unique `token_hash`), `email_verifications`, `login_throttles` |
| Company-data provenance | `source_snapshots` (unique provider and checksum), `import_runs`, `company_identities` |
| Model work | `split_manifests`, `company_split_assignments`, `annotation_candidates`, `training_annotations` |

Migrations `0001` to `0014` are explicit and never run on boot. A baseline verifier
(`backend/scripts/verify_baseline_schema.py`) adopts a database created before migrations existed
by checking its schema first and stamping it only if it matches exactly.

## 6. Scoring

### Priority score (legacy v1): 100 points, deterministic, no LLM

| Category | Max | Signal |
|---|---|---|
| Industry fit | 25 | property management, housing, multifamily, healthcare, clinic, dental, and similar |
| Company size | 15 | 1–10 → 5; 11–50 → 8; 51–200 → 12; 201–500 → 14; 500+ → 15 |
| Persona / title | 15 | VP Operations, Property Manager, Practice Manager, Founder, RevOps, and similar |
| Pain-point keywords | 20 | leasing, maintenance, tenant, patient, appointment, intake, call volume, and similar |
| Data completeness | 10 | +2 each for website, industry, contact name, email and title |
| Source quality | 10 | referral, webinar, conference, inbound → 10; website, csv → 6; unknown → 2 |
| Penalties | −15 | free-email domain −5; missing title −5; disqualified status −10 |

Bands: **Hot ≥ 80, Warm 55–79, Cold 0–54.** Every score returns its reasoning and matched signals.
Leads with a blocked disposition (`do_not_contact`, `disqualified`, `unsubscribed`) can never reach
Slack in any mode.

### Company fit (v2, `demo-us-sectors-v1`)

| Criterion | Points |
|---|---|
| Exact industry match | 60 |
| Structured US location | 40 |
| Company size | inactive (0) |

Bands: **strong ≥ 80, partial ≥ 50**, otherwise weak; evidence coverage below 80% makes it
**insufficient evidence**. Scores are append-only and versioned; the current score is defined once
(`app/services/fit_queries.py`) and used everywhere. The profile is a broad demonstration, not a
calibrated ideal-customer profile, and it is labeled "not evidence of interest" in generation.

**Real cohort (Phase 4):** 5,000 of 5,000 imported companies scored (2,500 healthcare and 2,500 real
estate, 0 failures), all strong match at 100% coverage, as expected for companies imported on the
same criteria; a rerun skipped all 5,000 as unchanged.

## 7. Grounded generation

- **Context:** every record fact, seller capability and approved claim gets an id
  (`fact-company_name`, `cap-1`, …). Unknowns (buying intent, budget, current problems, current
  tools) are always passed explicitly. Imported data sits inside an escaped `<untrusted_data>`
  fence in the `grounded-v2` prompt.
- **Validation:** output schema `v2` requires the draft to list the ids it used; any id outside the
  context, invented contact details or unsupported figures reject the output. **Invalid output saves
  nothing** (502) and logs only reason codes.
- **Provenance per output:** prompt version, output schema, model and model revision, adapter
  revision, input hash, seller-profile id, version, hash and kind.
- **Runtime checks (`runtime-checks-v1`):** flag the problem phrasings found in the Phase 9 analysis
  (for example "your current outreach strategies" and "Exploring Opportunities" subjects) plus a
  name-aware invented-phrasing check. Approving a flagged draft requires an exact acknowledgement
  (409 otherwise). They were tuned on the 71 test drafts (regression coverage) and flagged 0 of the
  100 untouched validation references; detection of new problem wording is untested.
- **Drafting model selection:** `USE_MOCK_AI=true` always uses the demo generator. Otherwise
  `AI_PROVIDER` picks `qwen3-4b-lora-v1` or `openai` (gpt-4o-mini). With
  `LORA_FALLBACK_TO_MOCK=true`, a 3-second reachability probe (cached 30 seconds) runs before each
  draft; if the fine-tuned server is down, the draft comes from the demo generator and is recorded
  as mock. A request that fails mid-way is never retried on another model.

## 8. Review and approval

- **Exact-draft approval:** an approval stores the SHA-256 of the content shown, plus the input hash
  and seller-profile revision. It authorizes delivery only while all three are unchanged.
- **Edits** create an immutable new revision that needs its own review; the original model output is
  kept.
- **Rejections** require a reason. Retries are idempotent; a changed decision is a separate event.
- **Refused targets:** cross-lead, stale, superseded and annotation drafts.
- **Seller profiles** are immutable versions. Activation needs a review confirmation, and a
  demonstration profile also needs an explicit acknowledgement; its drafts can never be "ready" for
  email.

## 9. Slack delivery ledger

1. Check: Hot (or forced), eligible, approval current, import complete. Blocked and unapproved
   leads are refused **before** any claim.
2. Commit a claim row with a unique `delivery_key` per approved-draft attempt, **before** sending.
3. Send. Outcome: `success` / `mock_success` (delivered), `failed` (definitely not delivered), or
   `unknown` (timeout, dropped connection or dead claim holder; may have arrived).
4. A repeat push replays the stored outcome. `unknown` is never resent automatically; an operator
   resolves it after checking the channel. A deliberate re-send needs `redeliver` (one lead) or
   `force` (batch or job).

**Guarantee:** at most one send per claimed attempt. Not exactly-once, because Slack webhooks take no
idempotency key. **Measured (Phase 11, PostgreSQL):** 8 concurrent API pushes produced exactly 1 send;
the API racing two workers produced one delivery per lead; a restarted job item never re-sent
(mutation-checked). An empty `SLACK_WEBHOOK_URL` routes to a mock webhook; the URL is never logged
or returned.

## 10. Background jobs

| Item | Value |
|---|---|
| Job types | `fit_score`, `legacy_score`, `generate_summary`, `generate_outreach`, `push_hot` |
| Storage | `background_jobs` and `background_job_items` in PostgreSQL (migration `0010`) |
| Leasing | A worker leases a job and renews the lease in the same transaction as each item it commits (a fenced update: a worker that lost its lease cannot commit) |
| Retries | Up to 3 per item for transient failures; pushes are never retried |
| Safety | Dedupe while active, cancel, crash-loop detection, progress counts |
| Crash test | A worker SIGKILLed partway through a 300-lead job; a second worker finished with exactly 300 outputs for 300 leads |
| Audit | Every job records the user who queued it; events written by the worker carry that actor |

## 11. Lead stages

`GET /api/inbox` gives every lead one stage, derived from `review_states()`, the same function the
lead page and delivery use:

| Stage | Meaning |
|---|---|
| Not scored | No priority score yet |
| Needs a draft | Scored, no outreach draft |
| To review | The current draft awaits a decision and is still current |
| Draft out of date | The draft's inputs changed (facts, fit score or seller profile), so approving or sending it would be refused |
| Draft rejected | The current draft was rejected |
| Ready to send | Approved, and the approval authorizes delivery |
| Sent | A Slack delivery succeeded for this lead |

Leads also carry `delivery_unknown` (latest delivery outcome unknown) and `blocked` (do-not-contact).
The whole workspace is ranked in memory, bounded to 5,000 leads.

## 12. Metrics dashboard: every field

All percentages return 0.0 when the denominator is zero; an empty database returns every field at
zero. Source: `GET /api/metrics/dashboard`, documented in [`docs/metrics.md`](docs/metrics.md).

**Lead pipeline**

| Field | Definition |
|---|---|
| `total_leads_uploaded` | All leads |
| `total_leads_processed` | Leads with a priority score |
| `hot_leads`, `warm_leads`, `cold_leads` | Scores per band |
| `automation_coverage` | processed ÷ uploaded × 100 |
| `average_lead_score` | Mean priority score, 2 decimals |
| `missing_data_rate` | Leads missing contact email or website ÷ uploaded × 100 |

**Company fit**

| Field | Definition |
|---|---|
| `fit_scored_leads` | Distinct leads with a current fit score |
| `fit_strong_match`, `fit_partial_match`, `fit_weak_match`, `fit_insufficient_evidence` | Leads per fit band, each counted once by its latest score |

**Outreach review** (cohort: distinct operational outreach drafts; annotation outputs excluded)

| Field | Definition |
|---|---|
| `outreach_generated` | Drafts in the cohort |
| `outreach_approved` | Drafts whose latest review is approved (approve → reject → approve counts once) |
| `outreach_rejected` | Drafts whose latest review is rejected |
| `outreach_pending_review` | Drafts with no review; approved + rejected + pending = generated |
| `approval_rate` | approved ÷ generated × 100; never above 100% by construction |
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

**Mock versus real**

| Field | Definition |
|---|---|
| `generation_by_mode` | Per mock, real and unknown: drafts generated, approved, rejected, pending, approval rate |
| `delivery_by_mode` | Per mock, real and unknown: attempts, delivered, unique leads, failed, unknown, pending, success rate |
| `data_mode` | `empty`, `mock_only`, `real_only` or `mixed`; the dashboard shows a banner |

**Time saved (an estimate, not a measurement)**

| Field | Definition |
|---|---|
| `estimated_time_saved_minutes` | processed leads × 5 (`MINUTES_SAVED_PER_PROCESSED_LEAD`) |
| `estimated_time_saved_hours` | minutes ÷ 60, 2 decimals |

## 13. The fine-tuned model

### Company data (Phase 3)

| Item | Value |
|---|---|
| Source | People Data Labs company dataset on Hugging Face, CC-BY-4.0, company fields only (no contacts) |
| Size | 2.38 GB, 32,330,231 records, checksum-verified; full streaming pass to EOF |
| Eligible pools | healthcare 696,880; real estate 265,005 |
| Selected | 2,500 healthcare + 2,500 real estate (0 shortfall), 0 duplicates, conflicts or invalid rows |
| Concurrency proof | An 8-process concurrent import test produced no duplicate snapshots or batches |

### Datasets (Phases 6 and 7)

| Item | Value |
|---|---|
| Split manifest | `company-groups-v1`: 5,000 leads in 4,988 company groups; train 3,284, validation 840, test 876 leads |
| Pilot (`pilot-v1`) | 100 candidates from 50 training companies, generated with gpt-4o-mini (105 paid calls, about $0.048); human-reviewed 6 (3 accepted, 2 corrected, 1 skipped); AI-reviewed 94 (30 accepted, 64 corrected; 26 flagged uncertain) |
| Training set | `train-combined-v1`: **419 eligible** (target 400; effective weight 397.9), 212 companies; 208 summaries / 211 outreach; 198 healthcare / 221 real estate; AI-reviewed 414, human 5; 128 uncertain excluded; 0 exact duplicates; 0 near-duplicate v2 outreach pairs (≥ 0.8) |
| Training-data generation | 473 attempts, $0.2167 (cap 500 / $0.30); 448 of 450 generated, 2 unresolved |
| Correction policy | `ai-review-rubric-v2-train`, minimal edits; median text preservation 0.72 (0.20 under v1) |
| Held-out | validation 74 and test 71 eligible, AI-reviewed references, frozen with recorded hashes (original target 100 each) |
| Segment balance | 47 / 53 healthcare / real estate in training |

### Training (Phase 8, 2026-09-25)

| Item | Value |
|---|---|
| Base | `Qwen/Qwen3-4B-Instruct-2507`, pinned revision `cdbee75f…`, Apache-2.0 |
| Method | bf16 LoRA, r 16, alpha 32, dropout 0.05, on q, k, v, o, gate, up and down projections; no quantization |
| Optimizer | learning rate 1e-4, linear warmup (5%) then cosine to 0; Adam betas 0.9 / 0.999, eps 1e-8; weight decay 0; gradient clipping 1.0 |
| Batching | batch size 1, gradient accumulation 8, max sequence 4,096 tokens, gradient checkpointing; seed 20260925 |
| Objective | example-weighted loss on answer tokens only (Σ wᵢ·lᵢ / Σ wᵢ) |
| Run | one rented L4: 3 of 3 epochs, 159 steps, 63 minutes of training, pod lifetime 1 h 06 m |
| Validation loss | 1.263 (before) → 0.264 → 0.139 → **0.129**; epoch 3 adapter selected |
| Memory | peak 12.5 GiB reserved |
| Cost | $0.6131 of a $3 cap |

### Evaluation (Phase 9, 2026-09-26): AI-evaluated, not human-verified

Automatic criteria on the test set (n = 71):

| Criterion | gpt-4o-mini | Base Qwen3-4B | **Fine-tuned** |
|---|---|---|---|
| Valid structure | 1.000 | 1.000 | 1.000 |
| Factual support | 0.620 | 0.986 | **0.986** |
| Missing-info handling | 0.577 | 0.507 | **1.000** |
| Writing acceptability (outreach) | 0.000 | 0.167 | **1.000** |
| Token F1 vs reference | **0.636** | 0.502 | 0.585 |
| ROUGE-L vs reference | **0.566** | 0.365 | 0.471 |
| Exact match | **0.310** | 0.000 | 0.000 |

Blind AI review (fresh reviewer, frozen rubric, systems hidden):

| | Base | **Fine-tuned** |
|---|---|---|
| Acceptable as-is | 1 / 71 (1.4%) | **49 / 71 (69.0%)** |
| Summaries acceptable (n = 35) | 0 | **35 (100%)** |
| Outreach acceptable (n = 36) | 1 (2.8%) | **14 (38.9%)** |
| Factual support: supported / partial / unsupported | 1 / 70 / 0 | 57 / 14 / 0 |
| Missing info: good / acceptable / poor | 1 / 69 / 1 | 57 / 14 / 0 |
| Writing quality, mean 1–5 (summaries / outreach) | 2.66 (2.94 / 2.39) | 3.69 (4.00 / 3.39) |

Run facts: one L4, all 8 generation passes, validation gate passed (0 truncated, 0 empty), the
fine-tuned model averaged about 290–295 new tokens against 450–466 for the base, peak GPU memory
21.66 GiB reserved, the pod deleted itself after a verified copy-back, $0.6945 of $1.50 spent.
The one shared test failure was traced to a lint false positive on the company name "Allergy &
Immunology Specialists". The two main outreach faults trace to phrasing kept in the v2 training
targets. **Limits:** AI evaluation only, small test set (36 outreach), one reviewer, imperfect
blinding.

### Integration (Phase 10, 2026-09-26)

The app's own `LocalLoRAClient` ran against the real model on one temporary L4 pod: **all 8 required
acceptance criteria passed** (A1–A3 20 of 20, B1–B3, C1–C2); adapter hashes matched the pins;
latency **p50 22.6 s, p95 28.0 s**; 7 of 20 outputs byte-identical to Phase 9 (batch-versus-single
numerics is the likely, unproven cause); runtime flags differed on 4 of 20 cases, all outreach; pod
time 12 min 22 s, $0.0739 observed; the pod deleted itself.

## 14. Security and access

| Control | Setting |
|---|---|
| Default | Deny: every route needs a session except health checks, sign-in options and the sign-in, sign-up, verification and guest endpoints; `/docs`, `/redoc` and `/openapi.json` too |
| Sessions | 256-bit random token, stored as SHA-256; cookie `gtmflow_session`, `HttpOnly`, `SameSite=Lax`, `Secure`; 60-minute idle, 12-hour absolute; rotated at login; revoked on logout, password change and disable |
| CSRF | `X-CSRF-Token` derived from the session, readable only by a same-origin page |
| Passwords | scrypt N = 2^15, r = 8, p = 3, random salt; 12–256 characters; rehashed when costs rise |
| Lockout | 5 consecutive failures lock an account for 15 minutes |
| Rate limit | 30 attempts per client per 5 minutes, database-backed, separate buckets for sign-in, sign-up, verification and guest |
| Public endpoints | JSON only, allowed-Origin check, 16 KiB body limit, one generic sign-in error, equal timing for unknown users |
| Sign-up | 6-digit code stored as a hash; 10-minute expiry, 5 attempts, 60-second resend cooldown; identical answers whether or not an address exists; optional domain allowlist |
| Guests | Fresh password-less account per visitor, 2-hour session; may change data only while the server is guest-safe (no Slack webhook and no paid per-request AI) |
| Roles | operator (everything), viewer (read-only), guest (operator actions on a guest-safe server) |
| Audit | Every workflow event records its actor, including the worker's |
| SQL | Every query through SQLAlchemy with bound parameters |
| Supply chain | Hash-locked backend install; frontend and locked backend audits 0 findings (2026-09-27); training environment findings documented |

## 15. Testing and verification

**Current (2026-10-03):** 560 backend tests on PostgreSQL, 555 passed + 5 skipped on SQLite;
109 frontend tests; 23 training tests; typecheck and production build clean; release harness
96 of 96 live checks against an empty PostgreSQL database; CI green on `main`.

Growth across the project:

| Point | Backend | Frontend | Live checks |
|---|---|---|---|
| Phase 1 audit | 121 | (typecheck and build) | |
| Phase 2 | 149 | | |
| Phase 4 | 276 | 31 | |
| Phase 5 | 342 | 56 | |
| Phase 6 | 389 | 75 | |
| Phase 7 | 430 | 82 | |
| Phase 10 | 476 (PostgreSQL) | 89 | |
| Phase 11 | 492 (PostgreSQL) | 94 | |
| Phase 12 | 521 (PostgreSQL) | 107 | 84 |
| Sign-up and guest access | 545 (PostgreSQL) | 112 | 96 |
| Redesign (current) | 560 (PostgreSQL) | 109 | 96 |

The frontend count dipped in the redesign because the retired annotation page's tests were removed.

Practices: tests block outbound network calls; PostgreSQL-only tests cover real concurrency and
process kills; key safety tests were **mutation-checked** (the safeguard was removed and the test had
to fail); migrations are tested up, down and up again with `alembic check`.

## 16. API

64 operations; the full table is in the README's API reference, and schemas are at `/docs` once
signed in. Groups: auth (8), imports and leads, scoring and fit, readiness, generation and
revisions, review, delivery and resolution, jobs, seller profiles, inbox and model status, metrics,
the demo run used by the release checks, and the annotation API kept for the model-work tools.

## 17. Configuration

Backend variables are documented in [`backend/.env.example`](backend/.env.example) and the README.
The groups: database (`DATABASE_URL`); drafting model (`USE_MOCK_AI`, `AI_PROVIDER`,
`OPENAI_API_KEY`, `LORA_*`, `LORA_FALLBACK_TO_MOCK`); Slack (`SLACK_WEBHOOK_URL`); sessions and
origins (`SESSION_*`, `ALLOWED_ORIGINS`); sign-up and guests (`SELF_SIGNUP_*`,
`SIGNUP_ALLOWED_EMAIL_DOMAINS`, `GUEST_*`); email (`SMTP_*`, `EMAIL_FROM`). Frontend, read at build
time: `API_PROXY_TARGET` and `NEXT_PUBLIC_API_BASE_URL`.

## 18. Deployment

**Railway (production, as of 2026-10-03):**

| Service | Configuration |
|---|---|
| Frontend | Root `frontend`; start `npm run start`; `NEXT_PUBLIC_API_BASE_URL` set to its own address so the browser stays on one origin; `API_PROXY_TARGET` points at the backend |
| Backend | Root `backend`; pre-deploy `alembic upgrade head`; start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`; drafting with OpenAI (gpt-4o-mini); guest access on |
| Worker | Root `backend`; start `python -m app.jobs.worker` |
| Postgres | Managed PostgreSQL 18.6 |

The production database predated migrations. On 2026-10-03 it was backed up, the adoption was
rehearsed on a restored copy, then stamped as the baseline and upgraded through all 13 later
migrations with every row preserved (3 imports, 30 leads, 12 AI outputs, 6 deliveries,
57 audit events). Verified live afterwards: the sign-in page loads, the session persists across
requests, guest access works, the 30 leads appear, the worker started, and guests are read-only
because OpenAI drafts are paid.

**Hosting estimates:** about $10–15 a month on Railway or about $27 on Render for the app;
about $358 a month for an always-on L4 GPU to serve the fine-tuned model, or a usage-based
serverless setup. Details: [`docs/deployment.md`](docs/deployment.md).

## 19. Money spent

| What | Provider | Amount |
|---|---|---|
| Phase 6 pilot generation (105 calls) | OpenAI | about $0.048 |
| Phase 7 dataset and baseline generation | OpenAI | $0.3121 (of which training data $0.2167) |
| Phase 8 training | RunPod | $0.6131 |
| Phase 9 evaluation | RunPod | $0.6945 |
| Phase 10 acceptance test | RunPod | $0.0739 (observed balance change; final billing may settle) |
| **Total recorded** | | **about $1.74** |

Every paid step had an explicit authorization and cap, and every GPU pod deleted itself.

## 20. Timeline

| Date | Milestone |
|---|---|
| 2026-06-04 | First version: CSV upload, legacy scoring, mock AI, Slack push, metrics |
| 2026-09-22 | Phase 1 audit (14 findings); Phases 2–3: migrations and provenance, 32.3 M-record company import |
| 2026-09-23 | Phase 4 company-fit scoring, readiness and eligibility |
| 2026-09-23 / 24 | Phase 5 grounded generation and seller profiles |
| 2026-09-24 | Phase 6 exact-draft review and the mixed human/AI pilot |
| 2026-09-25 | Phase 7 datasets and baseline; Phase 8 training |
| 2026-09-26 | Phase 9 evaluation; Phase 10 model integration and jobs; Phase 11 delivery ledger and metrics |
| 2026-09-27 | Phase 12 access control, dependency audit, release verification |
| 2026-10-02 | Sign-up with email verification and guest access |
| 2026-10-03 | Rep-centered redesign with the fine-tuned model as the drafting model; Railway production fixed |

## 21. Codebase statistics

| Area | Files | Lines |
|---|---|---|
| Backend application | 120 | 16,399 |
| Backend tests | 50 | 11,133 |
| Frontend source | 41 | 7,138 |
| Frontend tests | 17 | |
| Training code | 9 | 2,693 |
| Tracked files in the repository | 408 | |

`main` has 73 commits (counted 2026-10-03).

## 22. Limitations and open items

- **Model quality is AI-evaluated only**, on a small test set; a human review of outreach drafts is
  the most valuable next step.
- **No persistent model server.** The fine-tuned model ran on temporary GPU pods; production drafts
  with OpenAI.
- **Real Slack delivery is unexercised;** every delivery so far went to the mock webhook or test
  doubles.
- **One shared workspace:** everyone, guests included, works on the same leads.
- **Sign-up needs an email provider** (SMTP) before visitors can receive codes.
- **No MFA or password reset;** old guest accounts and sessions are not cleaned up automatically.
- **The company-fit profile is a demonstration,** and time saved is an estimate.
- **Training environment** has open dependency findings (setuptools) and a PyTorch audit gap.
- **Housekeeping:** the original development database recovery remains a separate open task.

## 23. Glossary

| Term | Meaning |
|---|---|
| Lead | One company (and optional contact) from an import |
| Import (batch) | One uploaded CSV or sample load |
| Priority score | The 100-point Hot/Warm/Cold score |
| Company fit | The versioned evidence-based fit score |
| Seller profile | What you sell; the only source of claims a draft may make |
| Grounded draft | A draft whose every claim cites an id in its context |
| Exact-draft approval | An approval tied to the content hash of the draft shown |
| Delivery ledger | The claim-before-send record that prevents double sends |
| Guest-safe | No Slack webhook and no paid per-request AI, so guests can act |
| LoRA | Low-Rank Adaptation: small trained weights added to a frozen base model |
| AI-evaluated | Judged by an AI reviewer, not by people |
