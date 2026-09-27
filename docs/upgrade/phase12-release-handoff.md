# Phase 12 handoff: access control, release checks and portfolio evidence

Initial implementation: 2026-09-26. Follow-up verification: 2026-09-27 (§9). **Status: release-ready as a signed-in, single-workspace application in mock mode, verified on isolated PostgreSQL. Not deployed** (§8 lists the blockers).

- No paid calls, GPU rental, real messages, retraining, deployment or production-data changes.
- Mock defaults stay on. This follow-up used an isolated checkout with no private `.env`; the original Codespace configuration was not changed.
- Database recovery (Phase 9 handoff §8) remains separate and open.
- Deployment has not started.

## 1. Scope

From `phase-status.md` and the audit:

- **F.1:** no authentication anywhere.
- **F.2:** permissive CORS headers.
- **A.3:** untriaged npm audit findings.
- **Frontend coverage and the known flaky test.**
- **The stale `PROJECT_STATUS.md` reference** in the README.
- **Release verification and portfolio evidence.**

## 2. Access control

**Design** (`app/api/auth.py`, `app/services/auth.py`, `app/models/auth.py`, `app/core/actor.py`, migrations `0012_operator_auth` and `0013_auth_hardening`):

- **Deny by default.** One application-level dependency protects every route except `/health`, `/api/health` and `POST /api/auth/login`.
- **CLI-created accounts, no registration.** Accounts come from `python -m app.auth_cli`, whose commands are `create-user`, `set-password`, `disable`, `enable`, `revoke-sessions` and `list`. There are two roles: `operator` (all actions) and `viewer` (read-only; actions return 403 except its own logout). There is no multi-tenancy.
- **Passwords:** scrypt from the Python standard library (no new dependency), with a per-user salt and the cost parameters stored in each hash. 12–256 characters. Default cost N=32768, r=8, p=3, with two hash slots per process; existing lower-cost hashes upgrade on successful login.
- **Login protections:**
  - one generic error for unknown users, wrong passwords, disabled and locked accounts;
  - a dummy hash for unknown usernames to reduce timing differences (not a guarantee of timing indistinguishability);
  - 5 consecutive failures lock the account for 15 minutes, with serialized updates to prevent a parallel-request bypass;
  - shared database-backed throttling: 30 attempts per peer per 5 minutes, including unknown usernames, returning 429 before hashing;
  - JSON-only login, allowed browser Origin, a 16 KiB login-body bound, and validation errors that never echo submitted passwords;
  - `Cache-Control: no-store`, nosniff, frame denial and same-origin referrer policy on API responses. `/docs`, `/redoc` and `/openapi.json` are authenticated.
- **Sessions:** a random 256-bit token per login, with the previous presented session revoked, preventing reuse of a supplied session identifier. Only the token's SHA-256 is stored. The cookie is `gtmflow_session`: HttpOnly, SameSite=Lax, Secure by default, Path=/. A session ends after 60 minutes idle or 12 hours in total, or when the user logs out, is disabled, or changes password.
- **CSRF:** every authenticated POST, PUT, PATCH and DELETE needs `X-CSRF-Token`, a value derived from the session token and readable only from same-origin responses (`GET /api/auth/session` or the login response).
- **CORS:** only configured origins get credentials, and the allowed headers are narrowed to `Content-Type` and `X-CSRF-Token` (audit F.2).
- **Protected actions:** everything, including approvals and rejections, revisions, runtime-flag acknowledgements (via approval), job enqueue and cancel, push and `redeliver`, delivery-outcome resolution, seller activation, annotation, and the demo.

**Actors recorded server-side:**

- Reviews, revisions, seller saves and activations, annotations and delivery resolutions record `user:<name>`.
- New jobs record `created_by` plus a nullable stable `created_by_user_id`; worker events retain both `job:user:<name>` and that UUID. Existing jobs are not rewritten.
- Security events also record account creation/change/revocation, login/logout and lockout. Delivery claims record the operator; the technical dispatch path stays as context.
- An authoritative SQLAlchemy hook, installed with the models so it runs in every process (API, worker, CLIs), stamps `actor` on **every new** `WorkflowEvent`, and `actor_user_id` when a user identity is present (including that user's jobs).
- Export field `reviewer_authenticated` is now true only for `user:` labels.
- Historical labels (`local-demo-unauthenticated`, `demo-auto-approve`) are untouched. The frozen datasets are unaffected, since every existing annotation still yields `false`.

**Frontend:**

- `/login` page, returning only to same-site `next` paths.
- `AuthProvider`: resolves the session, redirects signed-out visitors, and shows the user and a sign-out button in the header.
- `lib/api.ts`: sends cookies, adds CSRF to writes, reports 401s and handles 204. Sign-out errors remain visible without pretending the cookie was revoked. In-flight session responses cannot restore stale state; return paths reject backslashes/control characters.

The Phase 10 acceptance driver now signs in as a throwaway operator in its disposable database.

## 3. Dependency audit

The follow-up resolves the remaining frontend PostCSS finding with a tested
major-8 override, without forcing Next 16. Clean-install `npm audit`: **0**.
The backend's 40 exact packages also report **0**, now captured in a hash-locked
release requirements file. CI and startup use that lock with Python 3.12.

The unchanged training CPU/GPU pins retain two unique setuptools findings:
legacy PackageIndex path traversal and Unicode normalization bypass of sdist
exclusions. Neither affected path is used by the recorded Linux workflow, but
the environment needs a new reviewed lock before reuse. Vendor torch wheels
were skipped by the scanner. The second advisory's fixed-version metadata
conflicts with its maintainer page; no fix is claimed independently verified.

Exact advisories, impact, coverage limits and repeatable commands are in
[`docs/dependency-security.md`](../dependency-security.md). No training stack
was installed or changed; no new application dependency was added.

## 4. The flaky frontend test

"both panels page past 200 entries" renders about 460 history entries through 10 "Load more" clicks. It is CPU-bound, not timing-dependent:

- about 3.1 s alone, with both the old and new components;
- 5,134 ms (over Vitest's default 5 s) once when all test files ran in parallel on 2 vCPUs.

**Fix:** a 20 s limit for that one test, with every assertion unchanged and a comment explaining why.

**Initial recorded evidence (commit `93b6c41`):** 5 consecutive full runs, 101 of 101 each. One run under deliberate contention (the backend suite running at the same time) took **6,351 ms** for this test and passed; it would have failed at the old limit.

## 5. Initial verification record (2026-09-26, recorded in 93b6c41)

These are the original session's results, preserved for context. The independently
repeated checks on the follow-up code are in §9; do not use the old counts as
the current release gate.

| Check | Result |
|---|---|
| Migrations on a fresh disposable PostgreSQL 16 | **12 of 12** applied from empty; head `0012_operator_auth`; `alembic check` reported "No new upgrade operations detected". `0012` downgrade and re-upgrade also passed on a second database. |
| Backend, SQLite | **509 passed, 4 skipped** (the Postgres-only tests) |
| Backend, disposable PostgreSQL | **513 passed** (includes concurrency, process-kill and takeover tests) |
| New auth tests (`test_phase12_auth.py`, 21) | Every one of the app's 57 API operations refuses anonymous requests. The sensitive actions (approve, reject, job enqueue and cancel, push with `redeliver`, delivery resolution, seller activation, demo) return 401 anonymously and 403 for a viewer. Also covered: generic login errors; lockout and unlock; cookie flags; hash-only token storage; CSRF required; logout revocation and login rotation; idle and absolute expiry; disabled users and password changes; CORS allowlist; actors recorded, including worker-run jobs; the CLI; and a subprocess check that the worker and CLI install the actor hook. |
| Mutation checks | CSRF check removed → 1 test fails. `/api/leads` made public → 3 fail. Actor hook not installed with the models → 1 fails. |
| Training (CPU) | 23 passed |
| Frontend | **101 tests** (7 new: CSRF only on writes, cookies always sent, 401 hand-off, token cleared at logout, provider gating and redirect, login error, safe `next`), `tsc --noEmit` exit 0, `next build` exit 0 (with `/login`) |
| **Live release run** (production frontend build via `next start`, proxying to the API in mock mode on a fresh migrated database, plus a real worker process) | **27 of 27**. CLI account creation (passwords never printed). Anonymous read and action refused; health public. Generic wrong-password 401. Sign-in through the proxy with cookie flags `HttpOnly; Secure; SameSite=lax`. An action without CSRF refused. Demo, seller activation, upload, 4 jobs completed by the worker, 3 approvals, a mock delivery, a repeat → replay, `push_hot` delivering 1 and skipping 1. Dashboard approval rate 100% over one cohort, `mock_only`, 0 real messages. Viewer can read but not act. Logout kills the old cookie. `/login` served. |
| Server-side actors (release database) | Reviews: `user:alice` (3) and `demo-auto-approve` (2, the labeled demo). Audit events: `user:alice` (31) and `job:user:alice` (16); **0 without an actor**. Jobs `created_by` = `user:alice`. 0 plaintext tokens stored. |
| Defect found by the live run, and fixed | The first run found **16 worker-written events without an actor**: the hook had been installed only in `app.main`, which the worker never imports. It is now installed with the models, a subprocess regression test was added (mutation-checked), and the re-run had 0 unattributed events. |
| Phase 10 acceptance dry run (replay stub, disposable database) | All 8 required criteria passed with the signed-in driver (labeled DRY RUN) |

The disposable containers (`gtmflow-phase12-testpg`, `gtmflow-phase12-final`) and all local servers were removed afterwards.

## 6. Exact startup commands (local, mock mode)

Use Python **3.12**, Node **24**, and a fresh, isolated database. These commands
run a local demo; they do not migrate the original development database. Run
from the repository root. Existing environment files are never overwritten.

**Terminal 1 — new demo database and API:**

```bash
docker run -d --name gtmflow-phase12-demo-pg -e POSTGRES_PASSWORD=local-demo-only -e POSTGRES_DB=gtmflow_phase12_demo -p 127.0.0.1:55432:5432 postgres:16
timeout 30 sh -c 'until docker exec gtmflow-phase12-demo-pg pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done'
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-lock.txt
export DATABASE_URL=postgresql+psycopg://postgres:local-demo-only@127.0.0.1:55432/gtmflow_phase12_demo
export USE_MOCK_AI=true OPENAI_API_KEY= SLACK_WEBHOOK_URL= LORA_INFERENCE_BASE_URL= LORA_INFERENCE_API_KEY=
export SESSION_COOKIE_SECURE=false ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
.venv/bin/alembic upgrade head
.venv/bin/python -m app.auth_cli create-user admin
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

`create-user` prompts for a 12–256 character password without echoing it. On a
minimal Codespace without `venv` support, `uv venv .venv --python 3.12 --seed`
can create the same environment before the locked install. On later starts,
reuse the database/container and account; do not recreate or reset them.

**Terminal 2 — worker, from the repository root:**

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://postgres:local-demo-only@127.0.0.1:55432/gtmflow_phase12_demo
export USE_MOCK_AI=true OPENAI_API_KEY= SLACK_WEBHOOK_URL= LORA_INFERENCE_BASE_URL= LORA_INFERENCE_API_KEY=
.venv/bin/python -m app.jobs.worker
```

**Terminal 3 — frontend, from the repository root:**

```bash
cd frontend
npm ci
API_PROXY_TARGET=http://127.0.0.1:8000 NEXT_PUBLIC_API_BASE_URL= npm run dev
```

Open http://localhost:3000, sign in, and follow [`docs/demo-script.md`](../demo-script.md).
For a production build served locally, replace the last command with:

```bash
API_PROXY_TARGET=http://127.0.0.1:8000 NEXT_PUBLIC_API_BASE_URL= NEXT_TELEMETRY_DISABLED=1 npm run build
npm run start
```

For **Codespaces**, keep the forwarded port private. Set `ALLOWED_ORIGINS` to
the exact HTTPS frontend URL before starting the API, and use
`SESSION_COOKIE_SECURE=true` for that HTTPS browser origin. Restart the API
after configuration changes. The build bakes in the proxy target. Never trust
arbitrary forwarded headers; the commands above ignore them, so the local proxy
shares one login rate bucket. Deployment needs a properly configured trusted
edge proxy and global resource limits.

**Without Docker:** use a new SQLite file only for a disposable mock demo.
After installing backend dependencies, use `DATABASE_URL=sqlite:///$PWD/phase12-demo.db`
in both backend terminals (with the same mock overrides), and replace
`alembic upgrade head` with `.venv/bin/python -m app.core.init_db`. Then create
the account and start the API/worker as above. Historical Alembic migrations
are PostgreSQL-specific; SQLite does not establish migration verification.

**Account maintenance** (backend environment pointing at the intended database):
`python -m app.auth_cli create-user auditor --role viewer`, `set-password admin`,
`disable auditor`, `enable auditor`, `revoke-sessions admin`, or `list`.
Revocation stops later requests; already queued authorized jobs require explicit
cancellation. No public registration, password-reset service or multi-tenancy.

For an existing database, make and verify a backup before deliberate migration.
Migration 0013 adds a throttle table and nullable job creator FK; it does not
backfill or rewrite historical records. The recovery of the original database
is a separate task and has not been attempted here.

## 7. Documentation updated

- **README:** rewritten, including a four-level evidence table (mock operation; AI-evaluated model results; the verified temporary GPU integration; awaiting deployment), current setup, environment variables, and an API list checked against the app's own OpenAPI schema. The stale `PROJECT_STATUS.md` reference is removed, and the old screenshots are marked as predating the upgrade.
- **Backend README:** operator accounts, curl examples with sign-in and CSRF, current layout and test notes.
- **Frontend README:** auth.
- **Status notes** at the top of `docs/architecture.md` and `docs/ai-workflow.md` (they describe the original design).
- **Portfolio documents:** `docs/demo-script.md` (sign-in, seller activation, jobs, replay, mock banner, and the model talk track labeled AI-evaluated), `docs/resume-bullets.md` (6 sourced bullets, 17 words or fewer, with explicit non-claims) and `docs/interview-notes.md`.
- **`decisions.md`:** Phase 12 decisions.

## 8. Remaining release blockers (before deployment)

1. Hosting, HTTPS/TLS, trusted proxy configuration and global rate/resource limits.
2. Production secrets, explicit frontend origins, Secure cookies, operator
   provisioning and a decision on MFA and account recovery. Accounts remain CLI-only.
3. PostgreSQL backups, a restore drill, retention/cleanup for jobs and sessions,
   and capacity testing beyond the recorded 300-item/two-worker exercises.
4. A persistent inference server and its cost if real LoRA serving is desired.
   None exists; the app stays in mock mode. Refresh and verify the separate
   training/serving dependencies before another model run.
5. Real Slack delivery has not been exercised. Live integration tests require
   separate authorization. The ledger guarantees one dispatch per claim, not
   exactly-once delivery by Slack.
6. Model quality is AI-evaluated only on a small held-out set. Human verification
   and uncertain training-candidate review remain deferred; outreach needs review.
7. Original development database recovery stays open and separate (Phase 9 §8).

API documentation protection, application peer throttling and the bundled
PostCSS issue are now addressed; they are no longer open code gaps.

## 9. Follow-up verification (2026-09-27)

Starting point: existing Phase 12 commit `93b6c41`; Phase 10/11 services and
approval gates are reused. Code commit `999dba2` is on
`recovery/phase8-20260925`. The connected GitHub app published an identical Git
tree after the local shell lacked push credentials; no force update was used.

| Check | Current result |
|---|---|
| Full backend suite, SQLite | **516 passed, 5 PostgreSQL-only skips** |
| Full frontend suite | **107 passed**, including unchanged pagination assertions |
| Typecheck / production frontend build | Both pass with PostCSS 8.5.28 |
| Live local frontend/API/worker, disposable SQLite | **81 checks pass**; expressly not a migration test |
| Backend audit / frontend audit | **0 / 0 reported findings**; training limitations in §3 |
| Isolated PostgreSQL suite | **521 passed**, including parallel lockout, delivery contention and worker takeover |
| PostgreSQL migrations / live production-frontend flow | **84 checks pass**. All 13 migrations, schema parity, downgrade/re-upgrade with sentinel preservation, all five jobs, approvals, routing and metrics |

[Release workflow run](https://github.com/AhmedKamal-41/gtmflow-ai/actions/runs/36293369509)
**passed at 2026-09-27 04:12 UTC**. It uses a standard runner and fresh PostgreSQL 16 service with test-only
credentials. No production credentials, paid integrations, GPU, artifact
uploads or deployment steps are present. CI removed its database service; all harness child processes stopped. The local executor cannot run a
PostgreSQL service, so it does not claim a local PostgreSQL pass.

The new `backend/scripts/verify_release.py` starts an API and actual worker,
optionally the production frontend, and refuses non-loopback or populated
PostgreSQL targets. It verifies synthetic legacy data and audit columns through
0011 → head → 0011 → head, plus `alembic check`. The live flow covers sign-in,
CSRF, viewer denial, logout-cookie revocation, all five jobs, enqueue dedupe,
cancellation, exact-draft approval/rejection, replay, unknown-outcome resolution,
explicit redelivery, bounded mock metrics and authenticated worker actors.
Child API/worker processes reuse the test network guard; external connections
are blocked. The harness stops all child processes on success or failure.

Repeat the full gate via `.github/workflows/release.yml`. For the local
production-frontend smoke (fresh temporary SQLite; no Docker required):

```bash
(cd frontend && API_PROXY_TARGET=http://127.0.0.1:18000 NEXT_PUBLIC_API_BASE_URL= NEXT_TELEMETRY_DISABLED=1 npm run build)
(cd backend && .venv/bin/python scripts/verify_release.py --with-frontend)
```

For PostgreSQL, supply a **fresh empty** loopback database named
`gtmflow_phase12_*` to the same script's `--database-url` option. The workflow
contains the exact service/database creation commands and complete test steps.
Never point `TEST_DATABASE_URL` at data to preserve: suite fixtures drop tables.

No existing data, frozen evaluation artifacts, model weights, private environment
files or historical audit rows were edited or published. Stop here before
deployment; database recovery remains separate.
