# Demo script

Eleven steps. The whole flow runs in mock mode (no OpenAI key, no Slack webhook) so it works on any machine with Python + Node + Postgres for the live app (pytest uses in-memory SQLite only).

Target audience: a recruiter / hiring manager who has 5–7 minutes to watch the project work end-to-end.

## 1. Start the backend

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Confirm at http://localhost:8000/api/health:

```json
{ "status": "ok", "service": "gtmflow-ai-backend", "version": "0.1.0" }
```

> **What to say:** "FastAPI service, mock-by-default AI client, no OpenAI key needed for the demo."

## 2. Start the frontend

```bash
cd frontend
npm run dev
```

Open http://localhost:3000.

> **What to say:** "Next.js App Router, strict TypeScript, internal-tool styling, no auth because this is the workflow surface a sales ops admin would actually use."

## 3. Upload the sample CSV

Navigate to **/upload**. Pick `sample_data/leads_sample.csv`. Type "Demo Leads" in the batch name. Click **Upload**.

Expected result card:
- Total rows: 10
- Valid: 10
- Invalid: 0
- "Open batch detail →"

Click that link.

> **What to say:** "CSV ingestion validates per-row, normalizes the headers, and keeps unknown columns like `notes` in a `cleaned_data` JSON column on each lead."

## 4. Score the batch

On the batch detail page, click **Score batch**.

Action result banner: *"Scored 10 leads, Hot 2 • Warm 4 • Cold 4 • avg 56.1."*

The leads table now shows score numbers and Hot / Warm / Cold pills.

> **What to say:** "Deterministic 100-point model. Industry fit, persona, company size, pain-point keywords from the notes, source quality. No LLM, every score is reproducible from the lead fields alone."

## 5. Open a Hot lead

Click **Cascade Modular Homes** in the table. Lead workspace opens.

The score breakdown card shows 94/100, the per-category bars (Industry 25/25, Pain 20/20, Persona 15/15, etc.), and a row of matched signals.

> **What to say:** "Every category has a bar and a 'matched signals' pill row. The frontend defends against missing categories, a malformed backend response renders 'Not available' instead of a NaN bar."

## 6. Generate the AI summary

Click **Generate summary**.

The AI outputs section now shows:
- **company summary** card with `company_summary`, `detected_pain_points` pills, `fit_reasoning`, an **Evidence** list, an **Inferences** list (each line prefixed `Inference:`), and a confidence label.

> **What to say:** "Mock client. Same lead always produces the same summary. Notice that evidence is quoted from real lead fields, and every inference is explicitly labeled, that's the anti-hallucination contract. The same `SYSTEM_RULES` text governs the real-OpenAI prompts, pinned by a unit test."

## 7. Generate outreach

Click **Generate outreach**.

The AI outputs section now also shows:
- **outreach email** card with `subject`, multi-line `email_body`, `personalization_points`, `call_note`, confidence.

> **What to say:** "The outreach generator pulls the latest summary's pain points into its context so the call note and subject stay grounded in what the summary already said."

## 8. Approve / reject outreach

Two new buttons appear: **Approve outreach** and **Reject outreach**.

- Click **Approve outreach** → action banner "Approve complete." → status badge updates.
- Or click **Reject outreach** → browser prompt for a reason → submit.

Both actions emit a `WorkflowEvent` and flip `Lead.status` to `outreach_approved` / `outreach_rejected`.

> **What to say:** "This is the adoption signal, of every AI draft we generated, what % did a human accept? That ratio is on the `/metrics` page."

## 9. Push to Slack (mock)

Click **Push to Slack**.

The action banner shows "Push complete." The lead status flips to `pushed`. A **Push history** row appears with:
- status pill: `mock_success`
- timestamp
- the full Slack `text` payload (with the 🔥 emoji header, score line, contact line, fit reasoning, pain points, suggested subject, call note, and a lead-detail URL)
- response: `Mocked: Slack webhook URL is not configured.`

> **What to say:** "Mock mode because `SLACK_WEBHOOK_URL` is empty in `.env`. Set it to a real webhook URL and the same code path runs `httpx.post` with a 10-second timeout. The webhook URL is never logged or returned in responses, a unit test asserts that even httpx connection errors don't leak it."

## 10. Open the metrics dashboard

Navigate to **/metrics**.

Four sections:
- **Lead pipeline**, Total 10 / Processed 10 / Automation coverage 100% / Avg score 56.1 / Hot 2 / Warm 4 / Cold 4 / Missing data 0%
- **Outreach review**, Generated 1, Approved 1 (or Rejected 1), Approval rate 100%
- **Push delivery**, Pushed (rows) 1, Unique leads pushed 1, Success rate 100%, Failed 0
- **Estimated time saved**, Minutes 50, Hours 0.83
- Blue info panel framing the numbers as estimated / demo, explaining `leads_pushed` vs `unique_leads_pushed`.

> **What to say:** "This is the JD's 'track adoption and ROI of new systems' bullet. Every metric is a single `COUNT(*)` or `AVG()`. Divide-by-zero returns 0.0. Time-saved is deliberately framed as estimated, five minutes per processed lead, not real revenue impact."

## 11. Explain the results

Wrap up by zooming out:

- **2 Hot, 4 Warm, 4 Cold** out of 10 in the sample data. That ratio came out of the deterministic scorer with no human tuning between CSV and dashboard.
- **AI is mock by default** so the demo runs anywhere. The real-mode prompt template is in `backend/app/ai/prompts.py` with the guardrail phrases pinned by a test.
- **Slack push has an audit trail.** Every attempt (success, mock, or failure) is a row in `integration_pushes` plus a `WorkflowEvent`. Reviewer-friendly transparency.
- **117 backend tests + clean Next build.** GitHub Actions runs both.

> **Closing line:** "What I want this project to show is the GTM Engineering loop: ingest → score → AI → human approve → push → measure. The same loop scales to a real CRM-backed deployment by swapping the Slack module for a HubSpot or Salesforce module, the rest of the architecture stays."
