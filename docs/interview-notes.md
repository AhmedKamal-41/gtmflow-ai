# Interview notes

Four pre-written answers at four lengths. Use the right one for the question.

## 20-second pitch

> GTMFlow AI is a portfolio project that takes a CSV of sales leads, scores them on a deterministic 100-point model, drafts a personalized outreach email with a mock-by-default AI client, lets a human approve or reject the draft, and pushes Hot leads to Slack, with an adoption dashboard that shows approval rate, push success rate, and an estimated time-saved figure.

## 60-second pitch

> The project is full-stack: FastAPI backend, Postgres, Next.js dashboard, all wired together so a non-technical revenue team could actually run the workflow.
>
> Lead scoring is a deterministic 100-point model, industry fit, persona, company size, pain-point keywords. No LLM in the score; it's auditable. The AI client is behind an interface with a `MockAIClient` and an `OpenAIClient`, mock by default so the demo runs without keys. The mock generator labels every inference explicitly and keeps evidence separate, and the real-mode prompt has anti-hallucination guardrails pinned by a unit test.
>
> Slack push is the integration. Mock when `SLACK_WEBHOOK_URL` is empty; otherwise `httpx.post` with a 10-second timeout. Every attempt persists an `IntegrationPush` row with the full payload and the response, that's the audit trail.
>
> The metrics endpoint computes adoption and ROI, approval rate, push success rate, automation coverage, and an estimated time-saved figure I'm careful to frame as a demo number, not real revenue. The full backend has 117 passing tests; the frontend builds clean. The whole thing is the GTM-Engineering loop end-to-end.

## Technical explanation (5 minutes, deep dive)

**Architecture**
- Backend: FastAPI, SQLAlchemy 2.0 declarative ORM, Pydantic v2, Postgres. Six tables. Tests run on in-memory SQLite via a `StaticPool`-backed engine, with `get_session` overridden in pytest fixtures.
- Frontend: Next.js 15 App Router, strict TypeScript, Tailwind v3.4. Seven user routes. One typed fetch wrapper + one `APIError` class; no SWR / React Query / Redux.
- The two are decoupled by a single typed surface, TypeScript types in `frontend/src/types/api.ts` mirror Pydantic response models.

**Lead scoring**
- Deterministic 100-point model with seven categories and a penalty bucket. Hot ≥ 80, Warm 55–79, Cold 0–54.
- `score_lead()` is a pure function. Same input → same output, no I/O. Same function powers unit tests (via `SimpleNamespace`) and the live service (via SQLAlchemy ORM instances), the function only reads attributes.
- 11 unit tests including the band-boundary edges (54, 55, 79, 80) and a determinism test (3× identical runs).

**AI workflow**
- `AIClient` ABC with `MockAIClient` and `OpenAIClient` implementations.
- `MockAIClient` is fully deterministic, same lead context produces the same JSON output.
- `OpenAIClient` lazy-imports the openai SDK inside `_call(...)`, so missing the package surfaces a clean error instead of an `ImportError` at app startup. The key is validated at construction time; empty key → `AIConfigError` before any network call.
- Prompts use `response_format={"type": "json_object"}` and a strict system rule about evidence vs inference. Four guardrail phrases are pinned by a unit test that fails immediately if they drift.

**Slack integration**
- Payload builder + sender are separate functions. Builder is pure (great unit test surface). Sender uses `httpx.post` with a 10-second timeout.
- Mock mode when `SLACK_WEBHOOK_URL` is empty, no network call, just persists with `status: "mock_success"`.
- Secret safety: the URL is never logged or returned. The httpx error path is sanitized, connection errors return a generic message instead of forwarding `str(err)`, which can embed the URL. Asserted by a regression test.

**Adoption + ROI metrics**
- `GET /api/metrics/dashboard` returns 18 fields. Every metric is one `COUNT(*)` or `AVG()`.
- Divide-by-zero returns `0.0` (via a `_pct(num, denom)` helper).
- Two push counters: `leads_pushed` (row count, audit-friendly) and `unique_leads_pushed` (distinct lead count, ops-friendly). Both are exposed because the distinction matters.
- Time-saved is `5 minutes × processed leads`, deliberately framed as estimated, not real revenue impact.

**Testing strategy**
- 117 backend tests across 12 modules.
- Endpoint tests use a dependency-overridden in-memory SQLite session, so each test starts from a clean schema.
- Real OpenAI and real Slack paths are *never* exercised in tests. Both are unit-tested via construction-time errors (`OpenAIClient(api_key="")`) and `monkeypatch` of `httpx.post`.
- Frontend gates: strict TypeScript (`tsc --noEmit`) + a clean Next.js production build (`next build`).
- GitHub Actions: `pytest` on backend, `typecheck + build` on frontend, path-filtered so doc-only PRs don't rerun the full pipeline.

## GTM / business explanation

> The bottleneck in a lot of revenue teams isn't the lead list, it's the time it takes to decide which leads to prioritize, write something personalized to each one, and route the hot ones somewhere the right rep will see them quickly. GTMFlow AI compresses that into one workflow: upload, score, AI draft, human approve, Slack push.
>
> The two parts I'm most proud of from a GTM-Engineer perspective are the audit trail and the adoption dashboard. Every action, scoring, AI generation, approve, reject, push, emits a `WorkflowEvent`. So when the dashboard says "approval rate is 67%", that number is grounded in actual rows in the database, not a counter on the wire. That same audit trail is what lets a sales ops team say "of every AI draft we made this week, here's what reps approved, here's what got pushed, and here's how much time the workflow saved us if we assume five minutes per lead." The "five minutes per lead" assumption is wrong, you'd want to measure that in a real deployment, but the dashboard is designed so swapping the constant for a measured value is one line.
>
> What this also makes possible is iteration. If approval rate drops below 50%, that's a signal the prompt or the scorer is off and the team should look at it. That's the JD's "iterate based on results" loop.

## EliseAI JD mapping

| JD capability | How GTMFlow AI demonstrates it |
|---|---|
| AI workflow automation | Mock + real `AIClient` interface; `services/ai_generation.py` orchestrates lead → context → client → `AIOutput` → `WorkflowEvent`; mock-by-default with anti-hallucination guardrails. |
| APIs and webhooks | FastAPI backend with ~25 endpoints across 8 routers; Slack incoming-webhook integration via `httpx` with a 10s timeout and full audit trail. |
| Python | Backend is Python 3.11 + FastAPI + SQLAlchemy 2.0 + Pydantic v2; 117 pytest tests; type hints throughout. |
| JavaScript / TypeScript | Next.js 15 App Router with strict TypeScript, 7 user routes, custom `APIError` class, typed fetch wrappers. |
| SQL / PostgreSQL | 6-table schema with FK relationships, JSON columns, indexes; tests run on in-memory SQLite via `StaticPool` so they're fast and isolated. |
| Scoring / prioritization logic | Deterministic 100-point model with 7 categories + penalties; Hot/Warm/Cold bands; per-category UI breakdown + matched-signal pills. |
| Adoption / ROI tracking | `GET /api/metrics/dashboard`, 18 fields across pipeline, outreach review, push delivery, and estimated time saved. Divide-by-zero safe. |
| Non-technical team usability | Internal-tool-style Next.js dashboard with one-click actions (Score / Generate / Approve / Reject / Push), status badges, priority badges, clear error messages, no jargon. |
| Working in 0-to-1 ambiguity | Built 8 phases bottom-up: scaffold → models → ingestion → scoring → AI → Slack → frontend → metrics → polish. Each phase ended with an explicit "checkpoint" before moving on. |
