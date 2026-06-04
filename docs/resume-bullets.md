# Resume bullets

Five bullets, ≤ 17 words each. All numbers are honest and grounded in the project.

1. Built FastAPI backend with CSV ingestion and deterministic 100-point lead scoring, covered by 117 pytest tests.

2. Designed mock-by-default AI client supporting OpenAI structured JSON with anti-hallucination prompts separating evidence from inference.

3. Implemented Slack incoming-webhook integration with mock mode, 10-second timeout, and an audit trail per push attempt.

4. Shipped Next.js + Tailwind dashboard with seven user pages spanning upload, batches, lead workspace, and metrics.

5. Added adoption/ROI metrics endpoint computing approval rate, push success rate, and estimated time saved (5 min/lead).

## Word counts (for verification)

| # | Words |
|---|---|
| 1 | 16 |
| 2 | 15 |
| 3 | 16 |
| 4 | 16 |
| 5 | 16 |

## What these bullets deliberately do not claim

- No "users", there are no real users.
- No "increased revenue", the project doesn't ship revenue.
- No "production", no deployment recipe exists.
- No specific company-name reference for AI/Slack outputs that didn't come from the lead.
- No "automated email sending", outreach is always a draft.
- Time-saved is explicitly framed as `5 min/lead` (the formula), not as measured rep-time.

## Source numbers

- **117 pytest tests**, `pytest -v` output after Phase 9 (Phase 8 ended at 116; Phase 9 added `test_metrics_unique_leads_pushed_dedupes_repeat_pushes`).
- **Seven user pages**, `/`, `/upload`, `/batches`, `/batches/[batchId]`, `/leads/[leadId]`, `/metrics`, `/demo`. (Next.js's auto-generated `/_not-found` makes it 8 routes in the build output, but only 7 user-visible pages.)
- **100-point**, categories cap at 95 in practice; the clamp is at 100. Either is honest.
- **10-second timeout**, `SLACK_TIMEOUT_SECONDS = 10.0` in `backend/app/integrations/slack.py`.
- **5 min/lead**, `MINUTES_SAVED_PER_PROCESSED_LEAD = 5` in `backend/app/services/metrics.py`.
