# Deploying GTMFlow AI to Railway

> **Outdated: do not follow this guide as-is.** It predates Phases 10–12 and would deploy an incomplete app. It is missing:
> - **the background worker** (`python -m app.jobs.worker`): jobs would queue forever;
> - **operator accounts** (`python -m app.auth_cli create-user …`): nobody could sign in;
> - **session and origin configuration:** `ALLOWED_ORIGINS` must be the exact HTTPS frontend URL, `SESSION_COOKIE_SECURE=true`, and the frontend should proxy `/api` (`API_PROXY_TARGET`, empty `NEXT_PUBLIC_API_BASE_URL`) so the session cookie stays same-origin;
> - **the hash-locked Python 3.12 install** (`pip install --require-hashes -r requirements-lock.txt`);
> - **migration `0013`** and the trusted-proxy rules in `docs/upgrade/phase12-release-handoff.md` §6.
>
> No deployment has been verified on any host. Use the Phase 12 handoff as the source of truth until this guide is rewritten for the chosen platform.

This deploys three services into one Railway project:

1. **Postgres**, managed database
2. **backend**, FastAPI service (root dir `backend/`)
3. **frontend**, Next.js service (root dir `frontend/`)

Railway's Nixpacks builder auto-detects Python and Next.js, so no Dockerfile is
needed. The backend ships a `Procfile` that starts uvicorn. Schema migrations
are explicit (Alembic), not run automatically on boot -- see Step 2.5.

---

## Prerequisites

- A Railway account: https://railway.app
- This repo pushed to GitHub (Railway deploys from a connected repo)

---

## Step 1, Create the project and the database

1. Railway → **New Project** → **Deploy from GitHub repo** → pick this repo.
2. In the project, click **New** → **Database** → **Add PostgreSQL**.

## Step 2, Add the backend service

1. **New** → **GitHub Repo** → this repo.
2. Open the service → **Settings**:
   - **Root Directory**: `backend`
3. **Variables** tab, add:
   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (reference the Postgres service) |
   | `USE_MOCK_AI` | `true` |
   | `ALLOWED_ORIGINS` | *(leave empty for now, set in Step 4)* |
   | `OPENAI_API_KEY` | *(optional, only if `USE_MOCK_AI=false`)* |
   | `SLACK_WEBHOOK_URL` | *(optional, only for real Slack delivery)* |
4. **Settings** → **Networking** → **Generate Domain**. Copy the URL, e.g.
   `https://gtmflow-backend.up.railway.app`. This is your **backend URL**.

> The app rewrites Railway's `postgres://` URL to the `postgresql+psycopg://`
> driver automatically, so `${{Postgres.DATABASE_URL}}` works untouched.

## Step 2.5, Run migrations explicitly (required before first boot, and after every deploy that adds one)

Migrations are never run automatically by the web process (see
`docs/upgrade/decisions.md` for why -- an uncontrolled migration on every
worker boot is exactly what Phase 2 removed). Run them as an explicit,
one-off command against the backend service, using the Railway CLI from your
machine (or Railway's dashboard "Run Command" on the service):

```bash
railway run --service backend alembic upgrade head
```

- **First deploy, empty database**: this creates every table from
  `0001_baseline` forward. Nothing else to do.
- **An existing database created by the old `init_db.py`/`create_all` path**
  (i.e. anything deployed before Phase 2): first verify and stamp it as the
  baseline, then upgrade -- do not run `alembic upgrade head` directly
  against it, and never run `alembic stamp head`:
  ```bash
  railway run --service backend python scripts/verify_baseline_schema.py --stamp
  railway run --service backend alembic upgrade head
  ```
  `verify_baseline_schema.py` refuses to stamp anything if the live schema
  doesn't actually match what `0001_baseline` expects -- see the script's
  docstring and `docs/upgrade/audit.md`'s Phase 2 handoff for what that
  failure looks like and how to resolve it.

The `Procfile` also declares a `release: alembic upgrade head` line, which
some platforms (Heroku) run automatically as a pre-deploy step. This repo
has not verified whether Railway's Nixpacks builder executes `release:` the
same way -- treat the explicit `railway run` command above as the verified
path, and the `release:` line as a bonus for platforms that do honor it.

## Step 3, Add the frontend service

1. **New** → **GitHub Repo** → this repo (same repo, second service).
2. Open the service → **Settings**:
   - **Root Directory**: `frontend`
3. **Variables** tab, add:
   | Variable | Value |
   |---|---|
   | `NEXT_PUBLIC_API_BASE_URL` | the **backend URL** from Step 2 |

   > This is baked in at build time, so it must be set **before** the build.
   > It already is here, so the first build will pick it up.
4. **Settings** → **Networking** → **Generate Domain**. Copy the URL, e.g.
   `https://gtmflow-frontend.up.railway.app`. This is your **frontend URL**.

## Step 4, Close the loop on CORS

1. Go back to the **backend** service → **Variables**.
2. Set `ALLOWED_ORIGINS` to your **frontend URL** (no trailing slash):
   ```
   ALLOWED_ORIGINS=https://gtmflow-frontend.up.railway.app
   ```
3. The backend redeploys automatically. (Comma-separate if you have more than
   one origin.)

## Step 5, Verify

- Backend health: open `https://<backend-url>/health` → expect `{"status":"ok"}`.
- Frontend: open `https://<frontend-url>/` → the dashboard loads.
- Full flow: `/upload` the `sample_data/leads_sample.csv`, score the batch,
  open a Hot lead, generate summary + outreach, approve, push.

---

## Going beyond mock mode (optional)

- **Real OpenAI**: set `USE_MOCK_AI=false` and `OPENAI_API_KEY=sk-...` on the
  backend service.
- **Real Slack**: set `SLACK_WEBHOOK_URL=https://hooks.slack.com/...` on the
  backend service. Empty = mock pusher (no network call).

## Notes & caveats

- **Schema migrations are explicit (Alembic), not run on boot.** See Step 2.5.
  `app/core/init_db.py` (`create_all`) still exists for quick local
  SQLite/Postgres bring-up without Alembic, but is no longer the path used
  for a real deployment -- see `docs/upgrade/decisions.md`.
- **No auth.** Every endpoint is public once deployed. Fine for a demo; add a
  protection layer before putting real data in.
- **Build-time frontend var.** If you ever change the backend URL, you must
  redeploy the frontend for `NEXT_PUBLIC_API_BASE_URL` to take effect.
