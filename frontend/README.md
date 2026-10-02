# GTMFlow frontend

Next.js 15, TypeScript and Tailwind. Use **Node 24** (verified locally with
24.19.0); the current test dependencies require a recent Node runtime.

## Local mock startup

Start the database, API, operator account and worker using the
[release startup commands](../docs/engineering-log/phase12-release-handoff.md#6-exact-startup-commands-local-mock-mode).
Then, from this directory:

```bash
npm ci
API_PROXY_TARGET=http://127.0.0.1:8000 NEXT_PUBLIC_API_BASE_URL= npm run dev
```

Open http://localhost:3000 and sign in. The browser uses relative `/api` URLs;
Next proxies them to the loopback backend. For a Codespace, keep ports private
and add its exact HTTPS frontend origin to backend `ALLOWED_ORIGINS`. Do not
overwrite existing environment files.

For a production build served locally (not a deployment):

```bash
API_PROXY_TARGET=http://127.0.0.1:8000 NEXT_PUBLIC_API_BASE_URL= NEXT_TELEMETRY_DISABLED=1 npm run build
npm run start
```

Both the proxy target and public API setting are read at build time; rebuild
when changing them. `frontend/.env.example` shows the same-origin defaults.

## Access and errors

`AuthProvider` resolves the session and redirects signed-out visitors to
`/login`. Return paths stay on this origin; backslashes/control characters are
rejected. `lib/api.ts` sends cookies and a CSRF header on writes. The backend
independently enforces authentication, roles and every approval gate.

Sign-out clears the UI only after revocation is confirmed (or the server says
the session expired). A failed request keeps the session visible and shows a
retry message. Delayed responses cannot restore stale state. Viewers can read
but receive 403 on actions. When the server enables them, `/login` also offers
"Create an account" (confirmed with an emailed 6-digit code) and "Continue as
guest"; the page asks `GET /api/auth/options` which to show.

## Pages

The app is built around a sales rep's day; a sidebar links the five sections.

| Route | Purpose |
|---|---|
| `/login` | Sign in, create an account (emailed code), or continue as a guest |
| `/` | **Today**: what needs attention, first-run setup, drafting-model status |
| `/leads` | **Leads**: every lead ranked Hot first, with stage tabs, search and priority filter |
| `/leads/[leadId]` | Lead workspace: grounded draft, exact-draft review, flag acknowledgement, sending and delivery history; technical details collapsed |
| `/imports` | **Imports**: CSV upload (scored on import), sample data, past imports |
| `/batches/[batchId]` | One import: its leads, background jobs and optional scoring details |
| `/metrics` | **Insights**: cohort approval, delivery outcomes and mock-versus-real breakdown |
| `/settings` | **Settings**: drafting model status, what you sell, account |
| `/seller-profile` | Versioned seller profile and explicit activation |

`/upload` and `/batches` redirect to `/imports`.

Follow the [current demo walkthrough](../docs/demo-script.md), including seller
activation before outreach generation. The worker must be running for jobs.

## Verification

```bash
npm test
npm run typecheck
npm run build
npm audit
```

The CPU-heavy history-pagination test retains all assertions and has a
20-second limit. The PostCSS override stays within major 8; see the
[dependency review](../docs/dependency-security.md). The release workflow also
exercises this production frontend's API proxy with a worker and isolated
PostgreSQL. Current results are in the release handoff.

The screenshots in `docs/screenshots/` are historical captures. Mock operation
is distinct from AI-evaluated model results and the completed temporary GPU
acceptance run; no persistent model host or deployment exists.
