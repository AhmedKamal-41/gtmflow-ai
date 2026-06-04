# AI workflow

Two output types, two clients (mock + real), one orchestration service. The full flow is mock-by-default so the demo runs without a key.

## Output types

| `AIOutput.output_type` | Content shape |
|---|---|
| `company_summary` | `{company_summary, detected_pain_points, fit_reasoning, evidence, inferences, confidence}` |
| `outreach_email` | `{subject, email_body, personalization_points, call_note, confidence}` |

Both are persisted as JSON in the `AIOutput.content` column.

## The two clients

`backend/app/ai/client.py` defines `AIClient` (ABC) with two methods: `generate_company_summary(ctx)` and `generate_outreach(ctx)`.

### MockAIClient (default)

- Pure function of the lead context dict. Same input → same output, always.
- No imports of `openai`. No network. No timestamps in content.
- Summary composes evidence from real fields; inferences are explicitly prefixed `"Inference: …"` so a reader cannot mistake hypothesis for fact.
- Outreach pulls the latest persisted summary's pain points into the context when available, so the call note is grounded in what the summary already committed to.

### OpenAIClient (real mode)

- Constructed only when `settings.use_mock_ai` is `false`.
- Validates `api_key` at construction time, empty key raises `AIConfigError` *before* any network call.
- `openai` SDK is **lazy-imported** inside `_call(...)`. Missing package surfaces a clean install instruction, not an obscure `ImportError`.
- Calls `chat.completions.create(model=..., response_format={"type": "json_object"})`.
- Result is run through `parse_json_strict` (tolerates ```json fences``` but rejects non-object JSON).

## Factory + settings

`get_ai_client()` reads `settings.use_mock_ai` per request. No state is cached, so flipping the env var mid-process takes effect on the next call.

```python
def get_ai_client() -> AIClient:
    if settings.use_mock_ai:
        from app.ai.mock_client import MockAIClient
        return MockAIClient()
    return OpenAIClient(api_key=settings.openai_api_key)
```

## Prompts (real mode only)

`backend/app/ai/prompts.py` exposes `build_summary_prompt(ctx)` and `build_outreach_prompt(ctx)`. Both inline `SYSTEM_RULES`, which is the anti-hallucination contract:

> You are a careful B2B sales analyst. Use ONLY the lead data the user provides. Never invent facts, statistics, customer names, tool stacks, or product details. Separate evidence (facts present in the data) from inferences (hypotheses). Never claim this company uses any specific tool unless explicitly provided. Never pretend an email was sent. Output STRICT JSON only, no preamble, no commentary, no markdown fences.

The prompt then attaches the lead context as `json.dumps(ctx, sort_keys=True)` plus the exact target JSON schema. Sorting the keys means small reorderings in the context dict do not produce different prompts.

## Guardrail test

`backend/tests/test_ai_generation.py::test_prompt_guardrails_present_in_system_rules` pins four phrases in `SYSTEM_RULES`:

| Anti-pattern guarded against | Required phrase |
|---|---|
| Hallucinating beyond the input | `"only" + "data"` |
| Inventing facts | `"never invent"` or `"do not invent"` |
| Mixing evidence with inference | `"evidence" + "inference"` |
| Free-form prose | `"strict json"` |

If any of these drift out of `SYSTEM_RULES`, the test fails immediately.

## Evidence vs inference

The mock summary's `evidence` list quotes literal lead fields:

```json
"evidence": [
  "industry: Housing",
  "contact_title: VP Operations",
  "cleaned_data.notes: high tenant maintenance request volume and leasing tour scheduling delays",
  "deterministic_score: 94/100 (Hot)"
]
```

The `inferences` list is always prefixed:

```json
"inferences": [
  "Inference: may benefit from automated tenant-facing maintenance and leasing-scheduling workflows.",
  "Inference: operations-focused persona is usually receptive to workflow-automation pitches."
]
```

The frontend `AIOutputCard` renders them as two separate sections so the distinction stays visible.

## What never happens

- No real email is ever sent. "Outreach" is always a draft persisted in `AIOutput`.
- No `"Sent"` or `"Reached"` claims in any prompt or mock output.
- No fake statistics ("we saved X% of teams Y minutes…").
- No fake customer names.
- No claim that this prospect uses any specific tool unless that fact was in the input.

## Service orchestration

`backend/app/services/ai_generation.py`:

- `_build_lead_context(lead)`, composes the dict handed to the AI client. Strictly fields the backend already has. The persisted deterministic score is included so the AI client can ground its fit reasoning.
- `_latest_summary_content(...)`, used by the outreach generator to inject the latest persisted summary into context.
- `generate_summary_for_lead(...)` / `generate_outreach_for_lead(...)`, persist the result + emit a `WorkflowEvent` (`ai_summary_generated` / `outreach_generated`) tagged with `confidence` for auditing.

## How to enable real mode

```bash
# in backend/.env
USE_MOCK_AI=false
OPENAI_API_KEY=sk-…
```

…then `pip install openai` if not already present (`openai>=1.0` is in `requirements.txt`). Tests stay in mock mode unconditionally, they never call OpenAI.
