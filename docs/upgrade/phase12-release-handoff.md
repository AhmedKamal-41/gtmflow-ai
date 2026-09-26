# Phase 12 handoff: access control, release checks and portfolio evidence

Date: 2026-09-26. **Status: release-ready as a signed-in, single-workspace application in mock mode, verified on isolated PostgreSQL. Not deployed** (§8 lists the blockers).

- No paid calls, GPU rental, real messages, retraining, deployment or production-data changes.
- `backend/.env` stays `USE_MOCK_AI=true`.
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

**Design** (`app/api/auth.py`, `app/services/auth.py`, `app/models/auth.py`, `app/core/actor.py`, migration `0012_operator_auth`):

- **Deny by default.** One application-level dependency protects every route except `/health`, `/api/health` and `POST /api/auth/login`.
- **Operators only, no registration.** Accounts come from `python -m app.auth_cli`, whose commands are `create-user`, `set-password`, `disable`, `enable`, `revoke-sessions` and `list`. There are two roles: `operator` (all actions) and `viewer` (read-only; any state change returns 403). There is no multi-tenancy.
- **Passwords:** scrypt from the Python standard library (no new dependency), with a per-user salt and the cost parameters stored in each hash. At least 12 characters.
- **Login protections:**
  - one generic error for unknown users, wrong passwords, disabled and locked accounts;
  - a dummy hash for unknown usernames, so response timing doesn't reveal which exist;
  - 5 consecutive failures lock the account for 15 minutes;
  - `Cache-Control: no-store` on session responses.
- **Sessions:** a random 256-bit token per login, with the old session revoked, so session fixation is impossible. Only the token's SHA-256 is stored. The cookie is `gtmflow_session`: HttpOnly, SameSite=Lax, Secure by default, Path=/. A session ends after 60 minutes idle or 12 hours in total, or when the user logs out, is disabled, or changes password.
- **CSRF:** every POST, PUT, PATCH and DELETE needs `X-CSRF-Token`, a value derived from the session token and readable only from same-origin responses (`GET /api/auth/session` or the login response).
- **CORS:** only configured origins get credentials, and the allowed headers are narrowed to `Content-Type` and `X-CSRF-Token` (audit F.2).
- **Protected actions:** everything, including approvals and rejections, revisions, runtime-flag acknowledgements (via approval), job enqueue and cancel, push and `redeliver`, delivery-outcome resolution, seller activation, annotation, and the demo.

**Actors recorded server-side:**

- Reviews, revisions, seller saves and activations, annotations and delivery resolutions record `user:<name>`.
- Jobs record `created_by`, and everything a worker does for a job is recorded as `job:user:<name>`.
- A SQLAlchemy hook, installed with the models so it runs in every process (API, worker, CLIs), stamps `actor` and `actor_user_id` on **every** `WorkflowEvent`.
- Export field `reviewer_authenticated` is now true only for `user:` labels.
- Historical labels (`local-demo-unauthenticated`, `demo-auto-approve`) are untouched. The frozen datasets are unaffected, since every existing annotation still yields `false`.

**Frontend:**

- `/login` page, returning only to same-site `next` paths.
- `AuthProvider`: resolves the session, redirects signed-out visitors, and shows the user and a sign-out button in the header.
- `lib/api.ts`: sends cookies, adds the CSRF header to writes, reports 401s, and handles 204 responses.

The Phase 10 acceptance driver now signs in as a throwaway operator in its disposable database.

## 3. Dependency audit

| Scope | Before | Action | After / remaining |
|---|---|---|---|
| Frontend (`npm audit`) | 6: 1 critical (`next`), 3 high (`postcss` bundled in Next, `sharp`, `browserslist`), 1 moderate, 1 low | `npm audit fix` without `--force`. Lockfile only; `package.json` unchanged. Next.js 15.5.19 → **15.5.26**, sharp 0.34.5 → **0.35.4**, and the transitive fixes. | **2 remaining (1 high, 1 moderate): the same issue.** Next.js 15 pins its own copy of `postcss@8.4.31` (GHSA-qx2v-qp2m-jg93, GHSA-6g55-p6wh-862q, GHSA-fxqj-rqcc-2cmp, GHSA-r28c-9q8g-f849). The only fix npm offers is Next.js 16 (major), which I did not force, and I did not override Next's pinned copy. **Actual impact:** this PostCSS copy only processes the project's own, trusted CSS at build time. The advisories need attacker-controlled CSS or `sourceMappingURL` comments, and the app never processes user-supplied CSS. |
| Backend (installed environment, 40 packages; `pip-audit` on exact versions) | — | none needed | **0 findings** |
| Training CPU and GPU locks (40 and 59 packages) | `setuptools 78.1.0`: PYSEC-2025-49 / CVE-2025-47273 (fixed in 78.1.1) and PYSEC-2026-3447 (fixed in 83.0.0) | **Not changed.** These locks are the exact environment the Phase 8–10 GPU runs were verified in. | **Open.** CVE-2025-47273 concerns setuptools' legacy package-index download path, which these installs never use (uv installs pinned, hashed wheels). I did not review PYSEC-2026-3447's details. Update the lock and re-verify it before any new GPU run. |

`pip-audit` ran as a throwaway tool (`uv tool run`); nothing was added to the project.

## 4. The flaky frontend test

"both panels page past 200 entries" renders about 460 history entries through 10 "Load more" clicks. It is CPU-bound, not timing-dependent:

- about 3.1 s alone, with both the old and new components;
- 5,134 ms (over Vitest's default 5 s) once when all test files ran in parallel on 2 vCPUs.

**Fix:** a 20 s limit for that one test, with every assertion unchanged and a comment explaining why.

**Evidence:** 5 consecutive full runs, 101 of 101 each. One run under deliberate contention (the backend suite running at the same time) took **6,351 ms** for this test and passed; it would have failed at the old limit.

## 5. Verification (2026-09-26; all exit 0 unless stated)

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

```bash
# Database (local development)
docker run -d --name gtmflow-postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=gtmflow -p 5432:5432 postgres:16

# Backend
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                                  # USE_MOCK_AI=true, empty keys, mock Slack
.venv/bin/alembic upgrade head
.venv/bin/python -m app.auth_cli create-user admin    # prompts for a 12+ character password
.venv/bin/uvicorn app.main:app --port 8000

# Worker (second terminal, backend/)
.venv/bin/python -m app.jobs.worker

# Frontend (third terminal): one origin for the browser via the proxy
cd frontend && npm ci
API_PROXY_TARGET=http://localhost:8000 NEXT_PUBLIC_API_BASE_URL= npm run dev   # http://localhost:3000
```

**Production-style frontend** (as verified in §5): `API_PROXY_TARGET=… NEXT_PUBLIC_API_BASE_URL= npm run build && npx next start -p 3000`.

## 7. Documentation updated

- **README:** rewritten, including a four-level evidence table (mock operation; AI-evaluated model results; the verified temporary GPU integration; awaiting deployment), current setup, environment variables, and an API list checked against the app's own OpenAPI schema. The stale `PROJECT_STATUS.md` reference is removed, and the old screenshots are marked as predating the upgrade.
- **Backend README:** operator accounts, curl examples with sign-in and CSRF, current layout and test notes.
- **Frontend README:** auth.
- **Status notes** at the top of `docs/architecture.md` and `docs/ai-workflow.md` (they describe the original design).
- **Portfolio documents:** `docs/demo-script.md` (sign-in, seller activation, jobs, replay, mock banner, and the model talk track labeled AI-evaluated), `docs/resume-bullets.md` (6 sourced bullets, 17 words or fewer, with explicit non-claims) and `docs/interview-notes.md`.
- **`decisions.md`:** Phase 12 decisions.

## 8. Remaining release blockers (before any deployment)

1. **Hosting:** a platform, TLS termination, secret management for `DATABASE_URL`, any `OPENAI_API_KEY`, `SLACK_WEBHOOK_URL` and `LORA_INFERENCE_API_KEY`, PostgreSQL backups, and restore drills.
2. **Production configuration:**
   - `ALLOWED_ORIGINS` set to the real frontend, and `SESSION_COOKIE_SECURE=true` (the default) behind HTTPS;
   - **disable or protect `/docs`, `/redoc` and `/openapi.json`**, which are currently public in every environment (route shapes only, no data);
   - per-IP login throttling at the reverse proxy.
3. **Accounts:** no password reset flow, no MFA, and no admin UI (CLI only). This is acceptable for a single team but needs deciding.
4. **Model serving:** no persistent inference server. Choose serving (vLLM or `serve.py`) and its cost; `serve.py` handles one request at a time at about 20–30 s per draft on an L4.
5. **Real integrations are unexercised:** no real Slack webhook has been used, and real OpenAI generation (paid) was last used in Phases 6–7.
6. **Model quality is AI-evaluated only,** on a small test set. Human verification of the evaluation, and of the 180 uncertain training-candidate records, is still deferred.
7. **Dependencies:** Next.js 16 (for the bundled PostCSS) needs a planned upgrade and re-test. Update `setuptools` in the training locks before any new GPU run.
8. **CI:** the GitHub Actions workflows were not run in this session. The backend workflow pins Python 3.11, while local verification used 3.12.
9. **Data:** recovery of the original development database stays separate and open (Phase 9 handoff §8).

Phase 12 stops here, before deployment.
