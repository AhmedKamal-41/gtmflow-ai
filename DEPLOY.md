# Deploying GTMFlow AI to Railway

This deploys three services into one Railway project:

1. **Postgres**, managed database
2. **backend**, FastAPI service (root dir `backend/`)
3. **frontend**, Next.js service (root dir `frontend/`)

Railway's Nixpacks builder auto-detects Python and Next.js, so no Dockerfile is
needed. The backend ships a `Procfile` that creates the DB tables on boot and
starts uvicorn.

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

- **Schema creation** runs on every backend boot via `python -m app.core.init_db`
  in the `Procfile`. `create_all` is idempotent, it only creates missing tables
  and never alters existing ones. There are no migrations yet, so a schema change
  to an existing table would need a manual migration.
- **No auth.** Every endpoint is public once deployed. Fine for a demo; add a
  protection layer before putting real data in.
- **Build-time frontend var.** If you ever change the backend URL, you must
  redeploy the frontend for `NEXT_PUBLIC_API_BASE_URL` to take effect.
