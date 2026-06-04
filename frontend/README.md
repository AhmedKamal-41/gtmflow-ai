# GTMFlow AI, Frontend

Next.js 15 + TypeScript + Tailwind dashboard for the GTMFlow AI platform.
Internal-tool styling, no auth, mirrors every backend endpoint.

## Prerequisites

- Node.js 20+ (Node 22 recommended)
- Backend API reachable at the URL in `.env.local` (see `../backend/README.md` to start the API)

## Setup

```bash
cd frontend
cp .env.example .env.local
npm install
```

`.env.local` only needs one variable; the default is fine if the backend runs locally:

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

## Scripts

```bash
npm run dev         # local dev server on http://localhost:3000
npm run build       # production build (also runs Next's lint+type check)
npm run start       # serve the production build
npm run typecheck   # tsc --noEmit (matches the CI check)
```

## Pages

| Route | Purpose |
|---|---|
| `/` | Home, workflow overview, stack badges, CTA to Upload |
| `/upload` | CSV upload form with row-level error feedback |
| `/batches` | List of every uploaded batch, newest first |
| `/batches/[batchId]` | Batch detail with leads table, Score batch, Push all Hot leads |
| `/leads/[leadId]` | Lead workspace, details, score breakdown, AI summary, outreach, push history, approve/reject buttons |
| `/metrics` | Adoption + ROI dashboard (lead pipeline, outreach review, push delivery, estimated time saved) |
| `/demo` | Step-by-step walkthrough of the end-to-end flow |

## Demo workflow

1. Upload `sample_data/leads_sample.csv` at `/upload`.
2. On the resulting batch page, click **Score batch**.
3. Open the Cascade Modular Homes Hot lead.
4. Click **Generate summary**, then **Generate outreach**.
5. Click **Approve outreach** (or **Reject outreach** to capture a reason via `window.prompt`).
6. Click **Push to Slack** (mock by default).
7. Open `/metrics` and read the numbers.

Same checklist lives at `/demo`. The 11-step interview version is at [`../docs/demo-script.md`](../docs/demo-script.md).

## Backend dependency

The frontend talks to the FastAPI service over HTTP. Start the backend first:

```bash
cd ../backend
uvicorn app.main:app --reload --port 8000
```

See `../backend/README.md` for `init_db`, Slack webhook config, and mock vs real AI mode. The frontend has no notion of those modes, it just calls the API and renders the responses.

## Screenshots

> Drop PNGs into `../docs/screenshots/` to populate these. Files referenced below are placeholders; the build does not depend on them.

- `../docs/screenshots/home.png`, landing page with workflow steps
- `../docs/screenshots/upload.png`, CSV upload form + success card
- `../docs/screenshots/batch-detail.png`, leads table with priority badges + Score / Push buttons
- `../docs/screenshots/lead-detail.png`, score breakdown + AI outputs + approve/reject + push history
- `../docs/screenshots/metrics.png`, `/metrics` dashboard

## Layout

```
src/
├── app/                 # Next.js App Router pages and layouts
├── components/          # shared UI primitives (Button, Card, badges, tables…)
├── lib/                 # typed fetch wrappers over the FastAPI backend
└── types/               # TypeScript mirrors of the backend response shapes
```

## Error handling

Errors from the backend are surfaced verbatim from `detail` where available, with the raw HTTP status as fallback. 404 responses on `/score`, `/ai-outputs`, and `/pushes` are interpreted as "no data yet" rather than treated as errors, the UI shows a plain "Not scored yet", "No AI outputs yet", or "Not pushed yet" message. `ScoreBreakdown` defends against missing / `NaN` category values so a malformed backend response never renders a `NaN`-width bar.

## CI

`.github/workflows/frontend.yml` runs `npm ci` + `npm run typecheck` + `npm run build` on every push and PR that touches `frontend/`.

## Not implemented yet

No auth, no real email send, no HubSpot / Salesforce / Google Sheets / Zapier, no deployment. Time-saved is a portfolio estimate (5 minutes per processed lead), not real revenue impact.
