# Deployment plan

**Status: not deployed.** This is a plan, and no host has run it yet. The verified local procedure is in the [Phase 12 handoff](engineering-log/phase12-release-handoff.md#6-exact-startup-commands-local-mock-mode). Prices were checked on 2026-10-02 and are estimates; confirm them on each provider's pricing page before you sign up.

There are two separate goals:

- **A. Mock portfolio demo:** the full app with mock AI and mock Slack. No model server and no paid API calls.
- **B. Real fine-tuned model:** the same app, plus a GPU inference server for `qwen3-4b-lora-v1`.

Do A first. B is an independent, paid decision.

## A. Mock portfolio demo

### Services

Every service runs from this repository, and all four are required.

| Service | Root | Command | Notes |
|---|---|---|---|
| API | `backend/` | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` | From the `Procfile`. Python 3.12, installed with `pip install --require-hashes -r requirements-lock.txt` |
| Worker | `backend/` | `python -m app.jobs.worker` | Without it, background jobs stay queued |
| Frontend | `frontend/` | `npm ci && npm run build`, then `npm run start` | Node 24. Proxies `/api` to the API, so the browser sees one origin |
| PostgreSQL | managed | — | Version 16 is the tested one. Enable automated backups |

### Recommended host: Render

| Item | Plan | Estimated monthly cost |
|---|---|---|
| API web service | Starter | $7 |
| Background worker | Starter | $7 |
| Frontend web service | Starter | $7 |
| PostgreSQL | Basic-256mb | $6 |
| **Total** | | **≈ $27** |

Railway is a reasonable alternative. It costs $5 a month plus usage, at about $10 per GB of memory and $20 per vCPU per month, and a small idle app can cost less. The steps below are the same on either host.

### Configuration

API and worker (store these as host secrets; never commit them):

| Variable | Value |
|---|---|
| `DATABASE_URL` | The managed database URL (`postgres://` URLs are rewritten to the psycopg driver automatically) |
| `USE_MOCK_AI` | `true` |
| `OPENAI_API_KEY`, `SLACK_WEBHOOK_URL`, `LORA_INFERENCE_BASE_URL`, `LORA_INFERENCE_API_KEY` | Empty |
| `SESSION_COOKIE_SECURE` | `true` |
| `ALLOWED_ORIGINS` | The exact public HTTPS URL of the frontend, e.g. `https://gtmflow-web.onrender.com` |

Frontend (read **at build time**):

| Variable | Value |
|---|---|
| `API_PROXY_TARGET` | The API's URL, private if the host supports it |
| `NEXT_PUBLIC_API_BASE_URL` | Empty, so the browser calls the same-origin `/api` proxy |
| `NEXT_TELEMETRY_DISABLED` | `1` |

### Release steps

1. Create the database and run migrations once, as a one-off command: `alembic upgrade head`. Never run `alembic stamp head`.
2. Deploy the API, the worker and the frontend.
3. Create accounts from a one-off shell. There is no public registration: `python -m app.auth_cli create-user <name>` prompts for the password without echoing it. Add `--role viewer` for read-only access.
4. Smoke test: sign in, run `/demo`, queue a job on a batch, and check `/metrics`.
5. Re-run the release harness against a fresh staging database (`backend/scripts/verify_release.py`). It refuses any database that isn't empty.

### Open items before going public

- **Trusted proxy:** configure which proxy IPs may set `X-Forwarded-For`. Login throttling is per peer, so an untrusted setting either lumps everyone into one bucket or lets clients spoof addresses.
- **Global rate and resource limits** at the edge.
- **Backups:** confirm automated backups and run one restore drill.
- **Retention** for old jobs, sessions and login-throttle rows.
- **Account policy:** decide on MFA and account recovery. Today both are manual, through the CLI.

## B. Hosting the fine-tuned model

The app talks to the adapter through an OpenAI-compatible server (`training/gtmflow_training/serve.py`, verified once in Phase 10). Set `USE_MOCK_AI=false`, `AI_PROVIDER=qwen3-4b-lora-v1`, `LORA_INFERENCE_BASE_URL` and `LORA_INFERENCE_API_KEY` on the API and worker.

| Option | Estimated cost | Trade-off |
|---|---|---|
| Always-on L4 pod (RunPod Secure Cloud, $0.49/hr) | ≈ $358/month | Simplest; median 22.6 s per request in the Phase 10 test |
| Serverless L4 (RunPod, $0.69/hr of active time) | Usage-based, ≈ $7/month at 50 drafts/day | Cold starts; needs a serverless container that has not been built |
| OpenAI (`AI_PROVIDER=openai`) | About $0.0005 per draft (Phase 6) | Not the fine-tuned model |

Before you choose either GPU option, upgrade the training environment's flagged `setuptools`, review the PyTorch wheels ([dependency review](dependency-security.md)), and have a person review a sample of the model's outputs. Its quality is AI-evaluated only.
