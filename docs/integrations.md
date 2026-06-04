# Integrations

The only external delivery channel today is Slack incoming webhooks. The same architecture (payload builder + sender + service-level orchestration + audit row) is designed to be cloned for the future HubSpot / Salesforce / Sheets / Zapier integrations.

## Slack incoming webhook

### Modes

| `SLACK_WEBHOOK_URL` | Mode | Behavior |
|---|---|---|
| unset / empty | mock | No network call. Returns `("mock_success", "Mocked: Slack webhook URL is not configured.")`. |
| `https://hooks.slack.com/services/…` | real | `httpx.post(url, json=payload, timeout=10)`. Status maps to `success` (2xx) or `failed` (4xx/5xx, network error). |

### Payload shape

The builder is a pure function that takes named lead facts and returns a dict ready to POST:

```json
{
  "text": "🔥 Hot GTM Lead: Cascade Modular Homes\nScore: 94/100, Hot\nIndustry: Housing\nContact: Sarah Chen, VP Operations (sarah.chen@cascademodular.com)\nWhy now: Hot fit driven by target industry signals (property management) …\nPain points: leasing, maintenance, scheduling, tenant, tour\nSuggested subject: Quick idea for Cascade Modular Homes's leasing workflow\nCall note: …\nNext step: Review outreach draft in GTMFlow AI, http://localhost:3000/leads/<id>"
}
```

Slack's `text` field renders fine on its own, no Block Kit dependency. Missing optional fields are skipped cleanly (the builder appends each line only if its value is present).

### Service orchestration

`backend/app/services/integration_push.py::push_lead_to_slack` does five things:

1. Pulls the latest `company_summary` and `outreach_email` outputs into the payload context.
2. Calls `build_slack_payload(...)`.
3. Dispatches via `send_slack_payload(settings.slack_webhook_url, payload)`.
4. Persists an `IntegrationPush` row with the **exact payload JSON**, the status, and the truncated response text.
5. Emits `WorkflowEvent("lead_pushed")` and flips `Lead.status` to `"pushed"` on `success` / `mock_success`.

The caller (the API route) is responsible for the request-validation gates: lead exists (404), integration type is `"slack"` (400), lead is scored (400), lead is Hot OR `force=true` (400).

### Audit table

Every attempt, success, mock, or failure, persists exactly one `IntegrationPush` row. Schema:

| Column | Notes |
|---|---|
| `lead_id` | FK to `leads.id` |
| `integration_type` | `"slack"` for now |
| `payload` | JSON: the exact dict POSTed (or built, in mock mode) |
| `status` | `"success"` / `"mock_success"` / `"failed"` |
| `response_text` | Slack body or sanitized error message, truncated to 500 chars |
| `created_at` | tz-aware |

### Secret safety

- `SLACK_WEBHOOK_URL` is read from `settings.slack_webhook_url` *at send time*. It is never logged, never printed, never returned in API responses.
- The httpx error path is sanitized: instead of forwarding `str(httpx.HTTPError(...))` (which embeds the request URL), the sender returns `"HTTP error: could not deliver message to Slack webhook."`. A regression test asserts the response text never contains `hooks.slack.com` or `SLACK_WEBHOOK_URL`.
- Tests use either mock mode (no URL) or `monkeypatch.setattr("app.integrations.slack.httpx.post", …)`, no real Slack call is ever made.

### Skip-already-pushed semantics

The batch endpoint loops every Hot lead in the batch and, by default, skips any lead that already has at least one `success`/`mock_success` Slack push:

```bash
curl -X POST .../api/batches/{id}/push-hot \
  -H "Content-Type: application/json" \
  -d '{"integration_type":"slack"}'
# -> first call: pushed 2, skipped 0
# -> repeat:     pushed 0, skipped 2

# Override the skip:
curl -X POST .../api/batches/{id}/push-hot \
  -d '{"integration_type":"slack","force":true}'
# -> pushed 2, skipped 0
```

The single-lead endpoint has no skip rule (always pushes when the gates pass), but uses the same `force` flag to allow non-Hot leads through.

### Why no retries

A 5xx from Slack persists a `failed` row and the API returns 200 with `status: "failed"` in the body. The caller can re-POST (optionally with `force=true`) to retry. Real ops would want exponential-backoff + idempotency keys; out of MVP scope.

### Why `text` and not Block Kit

`text` renders identically in every Slack client, doesn't couple us to Slack's schema, and survives upstream changes. Block Kit lets us add buttons (Approve / Reject inline in Slack), which is the obvious upgrade once a frontend reviewer wants to act in-Slack rather than in the GTMFlow dashboard. Defer until that's the actual UX request.

## Future integrations

The same shape, `build_*_payload` (pure) + `send_*_payload` (httpx + status) + `services/integration_push` orchestration, applies cleanly to:

- **Google Sheets**, append Hot leads to a sheet via the Sheets API.
- **HubSpot**, create/update contact + log activity.
- **Salesforce**, create lead/contact + log task.
- **Zapier**, POST to a webhook the user owns; lets them route into anywhere Zapier supports.

None of these are built yet. The `IntegrationPush.integration_type` column is intentionally a string (not an enum) so adding `"hubspot"` later is a one-line change.

## Frontend surface

- `frontend/src/app/leads/[leadId]/page.tsx`, **Push to Slack** button. Auto-uses `force=true` for non-Hot leads so the action always works for the lead in front of you.
- `frontend/src/components/PushHistory.tsx`, renders every `IntegrationPush` row with status pill, timestamp, the full Slack `text`, and the response text.
