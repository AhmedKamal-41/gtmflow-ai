# Metrics

`GET /api/metrics/dashboard` returns 18 fields that answer two questions:

1. **Adoption**, how often is each step in the workflow used?
2. **ROI estimate**, how much time would this save vs. doing it manually?

All percentages handle divide-by-zero by returning `0.0`. Empty database returns every field at zero.

## Fields

### Lead pipeline

| Field | Formula | Notes |
|---|---|---|
| `total_leads_uploaded` | `COUNT(*) FROM leads` | |
| `total_leads_processed` | `COUNT(*) FROM lead_scores` | A lead is "processed" once it has a score. |
| `hot_leads` / `warm_leads` / `cold_leads` | `COUNT(*) FROM lead_scores WHERE priority = …` | |
| `automation_coverage` | `processed / uploaded × 100` | What % of uploaded leads have been scored. |
| `average_lead_score` | `AVG(total_score) FROM lead_scores` | Rounded 2dp. `0.0` when no scores exist. |
| `missing_data_rate` | leads with NULL/empty `contact_email` OR `website` ÷ uploaded × 100 | Cheap data-hygiene signal. |

### Outreach review

| Field | Formula | Notes |
|---|---|---|
| `outreach_generated` | `COUNT(*) FROM ai_outputs WHERE output_type='outreach_email'` | Row count, not distinct leads. |
| `outreach_approved` | `COUNT(*) FROM workflow_events WHERE event_type='outreach_approved'` | |
| `outreach_rejected` | `COUNT(*) FROM workflow_events WHERE event_type='outreach_rejected'` | |
| `approval_rate` | `approved / generated × 100` | The core adoption signal: "of every AI draft we made, what % did a human accept?" |

### Push delivery

| Field | Formula | Notes |
|---|---|---|
| `leads_pushed` | `COUNT(*) FROM integration_pushes WHERE status IN ('success','mock_success')` | **Row count.** A lead pushed twice contributes 2. |
| `unique_leads_pushed` | `COUNT(DISTINCT lead_id) FROM integration_pushes WHERE status IN (...)` | **Distinct leads.** Pushed twice still counts as 1. |
| `push_success_rate` | `successful_rows / total_push_rows × 100` | |
| `failed_push_count` | `COUNT(*) FROM integration_pushes WHERE status='failed'` | |

The two push counters are deliberate. `leads_pushed` is the audit count, how many Slack messages have we sent? `unique_leads_pushed` is the ops count, how many leads have been delivered at least once? Both are surfaced on the frontend with a one-line explanation.

### Estimated time saved

| Field | Formula | Notes |
|---|---|---|
| `estimated_time_saved_minutes` | `total_leads_processed × 5` | The 5-minute constant lives in `backend/app/services/metrics.py` as `MINUTES_SAVED_PER_PROCESSED_LEAD`. |
| `estimated_time_saved_hours` | `minutes / 60`, rounded 2dp | |

**This is a demo estimate.** Both the API caller and the `/metrics` page get an explanation that this is not measured rep time, just a portfolio-style approximation.

## Divide-by-zero handling

```python
def _pct(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)
```

Used for every percentage. There is no path that returns `NaN`, `Infinity`, or raises.

## Example response

After uploading the sample CSV, scoring the batch, generating outreach for 3 leads, approving 2, rejecting 1, and pushing all Hot leads:

```json
{
  "total_leads_uploaded": 10,
  "total_leads_processed": 10,
  "hot_leads": 2,
  "warm_leads": 4,
  "cold_leads": 4,
  "outreach_generated": 3,
  "outreach_approved": 2,
  "outreach_rejected": 1,
  "approval_rate": 66.67,
  "leads_pushed": 2,
  "unique_leads_pushed": 2,
  "push_success_rate": 100.0,
  "failed_push_count": 0,
  "estimated_time_saved_minutes": 50,
  "estimated_time_saved_hours": 0.83,
  "average_lead_score": 56.1,
  "missing_data_rate": 0.0,
  "automation_coverage": 100.0
}
```

## Limitations and honest framing

- **Time-saved is an estimate**, not a measurement. The 5-min constant is a placeholder; in a real deployment you'd replace it with measured rep-time-per-lead from your sales ops team.
- **Generated/approved aren't deduped by lead.** Re-generating outreach for the same lead and re-approving will double-count both sides of the `approval_rate` ratio. The math stays internally consistent, but the ratio is per-action, not per-lead.
- **No time-window filtering.** Metrics are lifetime counts of every row in the database. A "last 7 days" view is easy to add (`WHERE created_at >= NOW() - INTERVAL '7 days'`) but isn't built yet.
- **No tenant scoping.** The whole project is single-tenant.

## Why two push counters

A reviewer asked: "if we push the same lead 5 times, are 5 leads pushed?" Strictly the original `leads_pushed` field said yes (row count). After Phase 9, the dashboard surfaces both:

- `leads_pushed` = 5 (audit count, five Slack messages were sent)
- `unique_leads_pushed` = 1 (ops count, one lead has been delivered)

The frontend shows both labeled side-by-side so the distinction is obvious.

## Frontend

`frontend/src/app/metrics/page.tsx` renders four sections, **Lead pipeline**, **Outreach review**, **Push delivery**, **Estimated time saved**, plus a blue info panel that frames the numbers as estimated/demo and explains the `leads_pushed` vs `unique_leads_pushed` distinction. The dashboard is read-once on mount; there's no auto-poll.

## Tests

`backend/tests/test_metrics.py` covers:

- Empty database → every field is zero
- Counts on uploaded / processed / Hot / Warm / Cold
- Average score matches independent computation
- Time-saved estimate matches formula
- Missing data rate (50% when 1 of 2 rows has missing contact_email + website)
- Outreach generated count
- Approval rate including divide-by-zero
- Push success rate counts both `mock_success` and `success`
- `unique_leads_pushed` dedupes when the same lead is pushed twice
- Failed-push count (via `monkeypatch.setattr` on `send_slack_payload`)
