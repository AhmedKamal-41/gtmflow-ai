# Lead-selection assistant handoff

Date: 2026-10-03. Branch: `codex/lead-agent`, based on main `5e56a4c`.
Implemented and verified with mock integrations. Not deployed. No paid model calls, GPU,
real messages, training, production-data changes or database recovery work.

## What the user can do

Open **Assistant**, choose an import and describe the desired leads. The assistant searches
stored records, inspects company facts and returns up to five candidates with unknowns and an
action trace. **Prepare drafts** is a separate, explicit action: it queues only those candidates
through the existing worker. The normal lead page remains the place to review, approve and send.

The default **Demo assistant** is deterministic, not an LLM: it recognizes Hot/Warm/Cold and
healthcare/real-estate terms. Other instructions are not interpreted, as the page explains.
The optional OpenAI provider chooses tools through native function calling. Drafting continues
to use the independently configured `AI_PROVIDER`, including the existing LoRA client.
The Phase 8 adapter is not being represented as a tool-calling model.

## Implementation and boundaries

- Existing `AIClient`, OpenAI SDK, auth dependency, actor context, grounding services and job
  worker are reused. No new dependency, table or migration. API now has 66 operations.
- Only three tools: `search_leads`, `inspect_leads`, `propose_leads`. No arbitrary HTTP, browser,
  SQL, shell, approval, delivery or delivery-resolution tools.
- Every candidate must belong to the selected complete import. Blocked leads and leads with
  operational outreach drafts are excluded. IDs must first be discovered, then inspected before
  proposal. UUIDs, argument fields, duplicates and limits are checked by the server.
- Bounds: 1–5 selected leads; at most 5,000 eligible records in the import; 20 results per search;
  1,000-character user request; 2,000-character tool arguments; six model steps. A 60-second budget
  is checked between steps and the SDK has a 15-second request timeout with no SDK retries.
  This is not a hard overall wall-clock deadline or an account-wide dollar cap.
- Imported content is treated as untrusted data. The server ignores the model's free-form final
  answer and creates its own response from executed actions and stored facts. This limits action
  scope; it does not prove that arbitrary prompt injection cannot distort candidate selection.
- `assistant_outreach` accepts 1–5 distinct lead IDs and no other options. It rechecks import
  membership/completeness, skips existing drafts, respects blocked statuses and uses normal
  grounding/provenance/quality checks. A different selection while another assistant job is active
  returns 409. Job progress, cancellation and recovery use the existing implementation.
- Each selected item gets one worker-level attempt on provider failure. Existing provider-level
  drafting behavior and worker crash recovery are unchanged; this is not an exactly-once paid-call
  guarantee. Avoid concurrent manual/batch generation for the same leads.
- Planning requires authenticated write access and CSRF even though its tools are read-only.
  Viewer accounts cannot run it. Configuring a paid planner also makes guest accounts read-only,
  including when the drafting model is self-hosted.
- Completed/stopped runs emit `assistant_run_finished`, with authenticated actor ID, request hash,
  selected IDs, safe action trace, mode/model, duration and reported token usage. Raw prompts,
  company facts and provider errors are not copied into this audit event. OpenAI mode necessarily
  sends the request and bounded company facts to OpenAI; the UI states this.

## Try it locally

Requirements: Python 3.12, Node 24 and Docker. Start from this branch's checkout root. These
commands create a separate synthetic demo database and explicitly force mock operation in both
processes; they do not change an existing `.env`.

Terminal 1 — database and API:

```bash
docker run -d --name gtmflow-agent-demo-pg \
  -e POSTGRES_PASSWORD=local-agent-only -e POSTGRES_DB=gtmflow_agent_demo \
  -p 127.0.0.1:55438:5432 postgres:16
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-lock.txt
export DATABASE_URL=postgresql+psycopg://postgres:local-agent-only@127.0.0.1:55438/gtmflow_agent_demo
export USE_MOCK_AI=true AGENT_PROVIDER=mock AI_PROVIDER=openai
export OPENAI_API_KEY= SLACK_WEBHOOK_URL= LORA_INFERENCE_BASE_URL= LORA_INFERENCE_API_KEY= SMTP_HOST=
export SESSION_COOKIE_SECURE=false ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
export GUEST_ACCESS_ENABLED=true SELF_SIGNUP_ENABLED=false
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Terminal 2 — worker, from the checkout root:

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://postgres:local-agent-only@127.0.0.1:55438/gtmflow_agent_demo
export USE_MOCK_AI=true AGENT_PROVIDER=mock AI_PROVIDER=openai
export OPENAI_API_KEY= SLACK_WEBHOOK_URL= LORA_INFERENCE_BASE_URL= LORA_INFERENCE_API_KEY= SMTP_HOST=
.venv/bin/python -m app.jobs.worker
```

Terminal 3 — frontend, from the checkout root:

```bash
cd frontend
npm ci
API_PROXY_TARGET=http://127.0.0.1:8000 NEXT_PUBLIC_API_BASE_URL= npm run dev
```

1. Open http://localhost:3000 and **Continue as guest**.
2. **Try with sample data** imports and scores synthetic leads.
3. Create and explicitly activate a seller profile; the existing demo-template acknowledgements apply.
4. Open **Assistant**, select the import, and use **Find Hot leads for outreach**.
5. Inspect the shortlist and action trace, then click **Prepare drafts**. The worker should finish
   with drafts for those leads only. Open their lead pages for normal review. No approval or send
   occurs as part of the assistant workflow.

Stop API/frontend/worker with Ctrl+C when done. `docker stop gtmflow-agent-demo-pg` stops only this
demo database and retains it. On HTTPS, session cookies must use `SESSION_COOKIE_SECURE=true`.

## Real planning configuration — not exercised in this change

The default is `AGENT_PROVIDER=mock`. An operator may explicitly enable paid planning with
`USE_MOCK_AI=false`, `AGENT_PROVIDER=openai`, a configured `OPENAI_API_KEY`, and optionally
`AGENT_MODEL` (default `gpt-4o-mini`). These settings are backend-only. `AI_PROVIDER` separately
selects who writes drafts. Changing the global mock switch can also enable paid drafting if
`AI_PROVIDER=openai`; it is not an assistant-only switch. A provider failure stops the plan
without switching providers. No live credentials were used for this implementation or verification.

## Verification

Executed against this feature branch on 2026-10-03:

| Check | Result |
|---|---|
| Complete backend suite, isolated PostgreSQL 16.15 | **581 passed** |
| Complete backend suite, SQLite, before the final extra SDK round-trip test | **575 passed, 5 PostgreSQL-only skips** |
| Final assistant suite, including the added SDK round trip | **21 passed** on SQLite; also included in the 581 PostgreSQL tests |
| Complete frontend suite | **117 passed**, 18 files |
| TypeScript and Next.js production build | Passed; `/assistant` compiled |
| Live release harness with production frontend proxy, real API and worker processes, isolated PostgreSQL | **108/108 checks passed** |
| Migrations | Upgrade, downgrade/re-upgrade and model/schema parity passed; historical sentinels preserved |
| OpenAPI and diff checks | 66 operations; `git diff --check` clean |

Assistant tests cover unauthorized/viewer/CSRF/guest restrictions, missing configuration, malformed
requests, incomplete imports, blocked/existing-draft exclusion, invented privileged tools,
cross-import/uninspected/duplicate/over-limit IDs, injected company instructions, step limits,
sanitized failure, audit actors and usage, explicit job confirmation and worker rechecks. A real
OpenAI SDK round trip uses an in-memory HTTP transport and checks the three tool exchanges.
Frontend tests cover mode labeling, no automatic generation, empty/stopped plans, account access,
import changes, errors/retry, cancellation and unmount. The live harness covers the proxy/API/worker
path and confirms the selected lead has one grounded mock draft and no approval or delivery.

The test suite blocks external sockets except loopback; API and worker in the release harness
reuse that guard. The restricted runner initially hung in local AnyIO communication; the same
tests passed with local socket access. No test or assertion was removed. Local PostgreSQL was
created under `/tmp` and stopped after verification; no existing database was used. The release
harness stopped its API, worker and frontend processes. Dependency locks and evaluation files are unchanged.

Reproduce with disposable databases only:

```bash
docker exec gtmflow-agent-demo-pg createdb -U postgres gtmflow_agent_tests
docker exec gtmflow-agent-demo-pg createdb -U postgres gtmflow_phase12_agent_release
cd backend
.venv/bin/python -m pytest -q
TEST_DATABASE_URL=postgresql+psycopg://postgres:local-agent-only@127.0.0.1:55438/gtmflow_agent_tests \
  .venv/bin/python -m pytest -q
cd ../frontend
npm test -- --maxWorkers=2
npm run typecheck
NEXT_TELEMETRY_DISABLED=1 API_PROXY_TARGET=http://127.0.0.1:18000 NEXT_PUBLIC_API_BASE_URL= npm run build
cd ../backend
.venv/bin/python scripts/verify_release.py --with-frontend \
  --database-url postgresql+psycopg://postgres:local-agent-only@127.0.0.1:55438/gtmflow_phase12_agent_release
```

The first two commands create empty test databases; the suite drops test tables and the release harness
refuses a populated database. Never substitute the working project database. The existing CI
pull-request workflows pick up these tests; no deployment workflow was added.

## Remaining limits

- Live OpenAI tool-call behavior, natural-language selection accuracy, latency and cost need a
  separately authorized evaluation. Mock and HTTP-stub success does not establish these.
- One import at a time; first 20 results per search, no browsing/enrichment or semantic index.
  Matches use stored substrings and existing deterministic priority scores. Proposed fit is not
  evidence of buying interest. Mock mode supports only its documented small vocabulary.
- Planning runs synchronously and has no persisted conversational memory, streaming response,
  account-wide cost quota or cross-request planner rate limit. Review these before public paid use.
- Selection traces are retained in workflow events; the UI does not offer a past-run browser.
  Draft jobs remain visible on the import after leaving the assistant page.
- The existing LoRA quality evaluation and temporary GPU integration remain separate evidence.
  The assistant has not been deployed; persistent model hosting and original database recovery
  remain separate tasks.

Portfolio wording supported now: **Built bounded AI tool calling for lead discovery, with validated
tools, explicit draft confirmation, authenticated audit trails and mock integration tests.** Do not
claim measured agent quality, production agent use or autonomous outreach.
