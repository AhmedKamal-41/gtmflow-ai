# Current architecture

Phase 12, single workspace. PostgreSQL is the migration-tested database;
SQLite supports tests and disposable local mock demonstrations.

| Component | Responsibility and boundary |
|---|---|
| Next.js | Sign-in, lead/batch pages, seller activation, review, jobs, delivery resolution and metrics. Relative `/api` requests use the same-origin proxy. UI visibility never authorizes an action. |
| FastAPI | Application-wide session, role and CSRF enforcement. Only health and login are public. Documentation routes also require authentication. Thin routers call services. |
| Auth service | CLI-created operator/viewer accounts, scrypt, atomic account lockout, shared peer throttling and revocable sessions stored as token hashes. No registration or multi-tenancy. |
| Domain services | Grounding, scoring, revisions, exact-draft approval gates, dispatch and metrics. Writes retain audit events and authenticated actors. |
| PostgreSQL | Provenance, seller revisions, outputs/reviews, audit history, users/sessions, durable jobs and Slack claims. 13 Alembic revisions; migrations are explicit. |
| Worker | Fenced leases, completed-item commits, crash recovery and cancellation. The enqueueing user's ID follows job events. No extra queue dependency. |
| AIClient | Deterministic mock by default; opt-in OpenAI or pinned LoRA server. Grounding/output checks apply to all providers. |
| Slack adapter | Mock with no webhook configured. Claims before dispatch; repeats replay, unknown outcomes need an operator decision. |

## Request and audit boundaries

The server resolves an HttpOnly session cookie before accessing application
data. Writes need an operator and CSRF header. Browser login needs JSON and an
allowed Origin. Passwords and raw tokens never enter audit records. New events
receive an authoritative actor label and, for a user request or its job, a
stable user UUID. Historical unauthenticated labels remain intact.

A viewer can read all workspace data; an operator can perform all workspace
actions. CLI access is administrative. Revoking a session stops later requests;
it does not automatically cancel jobs already authorized and queued. An
operator can cancel those jobs.

## Workflow invariants

- Versioned company fit is not evidence of purchase intent.
- Outreach requires an activated seller revision and allowed fact/claim IDs.
  Unknowns stay explicit.
- Delivery requires an applicable exact-draft approval, current inputs/seller
  revision and acknowledgement of current quality flags.
- Blocked leads and incomplete imports stay ineligible. Force/redeliver does
  not bypass these gates.
- A claim permits at most one dispatch. Slack does not offer exactly-once
  delivery: a wrong manual “not delivered” resolution can still duplicate it.
- Approval metrics count each operational draft by its latest decision;
  training annotation is excluded. Mock and real activity stay separate.

## Verification and deployment boundary

The release workflow installs locked dependencies, runs complete SQLite and
PostgreSQL suites, and verifies the production frontend/API/worker with
synthetic data and mocks. The harness checks preservation through migration
upgrade/downgrade/re-upgrade and model/schema parity on fresh PostgreSQL. It
refuses a populated target database.

Hosting, TLS/proxy configuration, backups, production secrets and persistent
model serving remain deployment work. Original database recovery is separate.
See the [Phase 12 handoff](upgrade/phase12-release-handoff.md) for commands and
results, and the [AI workflow](ai-workflow.md) for model evidence.
