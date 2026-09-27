# Interview notes

Pre-written answers at four lengths. Every number traces to `docs/upgrade/` (see `resume-bullets.md` for the sources).

## 20-second pitch

> GTMFlow is a lead-to-outreach tool for one sales team. It imports and scores companies, drafts outreach grounded only in the company record, requires a signed-in person to approve the exact draft, and routes approved Hot leads to Slack with a claim-before-send delivery ledger. I also fine-tuned a small open model for the drafting step and evaluated it against a baseline. That evaluation was done by AI review, not people. The app runs in mock mode by default and isn't deployed.

## 60-second pitch

> The backend is FastAPI, SQLAlchemy and PostgreSQL with 13 Alembic migrations; the frontend is Next.js and TypeScript.
>
> - **Generation:** the model gets a context of record facts and an activated seller profile, and must cite the ids of everything it uses. The server rejects any draft that cites something outside the context, so an invalid draft is never saved.
> - **Review:** a person approves the exact content hash they saw. Runtime checks flag known bad phrasings and require an explicit acknowledgement.
> - **Jobs and delivery:** batch work runs in a database-backed job queue with leases and crash recovery. Slack delivery uses a claim-before-send ledger, so concurrent requests or restarted workers can't send twice, and a timeout becomes an explicit "unknown" instead of a blind resend.
> - **The model:** Qwen3-4B with a LoRA adapter, trained on 419 AI-reviewed examples. A blind AI review rated 69% of its held-out drafts acceptable, versus 1% for the base model. I verified the integration once on a temporary GPU.
> - **Access:** everything is behind server-side sessions with CSRF protection, roles, and an actor on every audit event.

## Technical deep dive (5 minutes)

**Grounding and review**
- **Grounded context:** the context holds record facts with ids, their provenance, the stated unknowns, and the fit score (labeled "not evidence of interest"). The output schema requires fact, capability and claim ids that exist in the context. Validation failures save nothing.
- **Exact-draft approvals:** an approval stores the content hash that was shown. Delivery re-checks that the draft, company inputs and seller revision are unchanged. An edit is a new immutable revision.

**Model work (Phases 7–10)**
- **Data:** splits by company group (no leakage), with training, validation and test sets frozen by hash.
- **Training:** LoRA r=16 on Qwen3-4B-Instruct in bf16, with per-example weights, on one rented L4: 63 minutes and $0.61. The pod has five layers of self-termination safeguards.
- **Evaluation:** frozen criteria, failure-inclusive scoring, and a blind AI review by a fresh agent that saw no scores or system names.
  - The two main outreach faults were traced to phrasing kept in the training data.
  - The shared test failure turned out to be a lint false positive on a company name.
- **Integration:** an OpenAI-compatible client that fails closed without configuration. On a temporary pod, 7 of 20 outputs were byte-identical to the evaluation outputs and all 20 got the same quality verdicts. The likely cause of the differences is batch-versus-single-request numerics; that wasn't proven by a controlled run.

**Reliability**
- **Jobs:** each item commits with a fenced lease renewal, so a stale worker can't commit. Retries are bounded, and a crash loop is detected. The worker SIGKILL test is in the Phase 10 handoff.
- **Slack ledger:** a unique claim key per approved-draft attempt. Outcomes are `failed` or `unknown`, and resolving an unknown is an operator action. At most one send per attempt, but not exactly-once: Slack webhooks have no idempotency key.

**Access control (Phase 12)**
- **Deny by default:** an app-wide dependency protects every route except health checks and login.
- **Sessions and passwords:** scrypt passwords, sessions stored only as hashes, and HttpOnly, SameSite=Lax, Secure cookies. Idle and absolute expiry, rotation at login, revocation on logout and on user changes, atomic account lockout, and shared peer throttling.
- **CSRF:** a token derived from the session and required on every state change. Viewers are read-only.
- **Audit:** a SQLAlchemy hook stamps the actor on every audit event, including work done by the background worker.

**Testing**
- **Backend:** runs on SQLite and on disposable PostgreSQL, including real concurrency and process-kill tests. Key tests were mutation-checked (the safeguard was removed and the test had to fail).
- **Frontend:** Vitest and Testing Library, typecheck and a production build. Counts are in the Phase 12 handoff.

## GTM / business explanation

> Revenue teams lose time deciding which leads matter, writing something specific to each, and getting the good ones to the right rep. GTMFlow makes that one workflow, with two things I'd insist on in a real team.
>
> First, trust: a draft can only use facts from the record, a person approves exactly what gets sent, and the ledger prevents automatic repeat dispatch; uncertain outcomes need an explicit operator decision.
>
> Second, honest measurement: the approval rate counts each draft once, mock and real activity are separated, and time saved is labeled as an estimate.
>
> The model work follows the same rule. I report what the evidence supports, "AI-evaluated, 69% acceptable on a small test set", and not more.

## EliseAI JD mapping

| JD capability | How GTMFlow demonstrates it |
|---|---|
| AI workflow automation | Grounded generation across three providers (mock, OpenAI, fine-tuned Qwen), runtime quality checks, background jobs |
| APIs and webhooks | About 57 API operations; a Slack webhook with a delivery ledger and explicit handling of uncertain outcomes |
| Python | FastAPI, SQLAlchemy 2.x, Pydantic v2, Alembic; PyTorch, PEFT and transformers for the model work |
| JavaScript / TypeScript | Next.js 15 App Router, strict TypeScript, Vitest and Testing Library suites |
| SQL / PostgreSQL | 13 migrations; unique constraints and conditional updates for concurrency; window functions for "latest review" metrics |
| Scoring / prioritization | Deterministic legacy score plus a versioned company-fit score with evidence coverage |
| Adoption / ROI tracking | Cohort-based approval metrics, delivery outcomes, mock-versus-real breakdowns, an explicit time-saved estimate |
| Non-technical usability | One-click review, flag acknowledgement, "It arrived / It did not arrive" resolution, plain-language dashboard notes |
| Working in ambiguity | 12 documented phases, each ending with evidence and an honest readiness level (`docs/upgrade/phase-status.md`) |

Current release evidence: [isolated PostgreSQL CI run](https://github.com/AhmedKamal-41/gtmflow-ai/actions/runs/36293369509), 521 backend tests, 107 frontend tests and 84 live mock checks. Exact commands and limitations are in the Phase 12 handoff.
