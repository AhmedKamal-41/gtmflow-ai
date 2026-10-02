# Implementation contract for Phases 2–12

This document records the working rules and repository conventions that later phases must follow. It exists so that a phase started weeks from now (by a human or another agent) doesn't have to re-derive them, and so that progress claims stay honest and checkable against `docs/engineering-log/phase-status.md`.

This contract was established during Phase 1 (repository audit, see `docs/engineering-log/audit.md`). It should be updated, not silently ignored, if a later phase needs to deviate from it — deviations get recorded in `docs/engineering-log/decisions.md` with a reason.

## 1. Carried-over working rules

These were given for Phase 1 and apply to every subsequent phase unless a specific phase's scope explicitly overrides one:

1. Read this file and `docs/engineering-log/phase-status.md` before starting work on any phase. Read `docs/engineering-log/decisions.md` before making an architecture choice that isn't already decided there.
2. Preserve existing repository conventions (see section 2) and existing working code. Don't rewrite something that isn't in scope for the current phase just because you're nearby.
3. Straightforward, readable Python and TypeScript. Match the existing style (see section 2) rather than introducing a new pattern for the same problem.
4. Reuse existing components and services where suitable. The scorer, the AI client ABC, the Slack builder/sender split, and the service-layer/router split are all intentional and documented in `docs/architecture.md` — extend them, don't parallel them.
5. The mock demonstration (`USE_MOCK_AI=true`, no `SLACK_WEBHOOK_URL`, `docker-free`, no paid service) must keep working without paid services at the end of every phase. If a phase adds a real-mode capability (e.g., a trained LoRA adapter, a real embedding call), the mock path must still work standalone.
6. Never fabricate company information, human approvals, training results, or evaluation numbers. If a number can't be produced by an actual run, don't write it down as if it were one — write "not yet measured" or omit it.
7. No paid GPU jobs, no paid service calls, no real Slack sends, no public deployment without the user's explicit go-ahead in that session. This includes LoRA training runs (Phase 8) and any held-out evaluation that costs money to run (Phase 9) — get sign-off before launching, not after.
8. Record what was actually run (the command, the environment, the result) for every claim of "this works" or "this test passes." Distinguish verified behavior from assumptions and from checks that were blocked (no access, no budget, no time).
9. Don't rewrite unrelated code or add dependencies merely to make a phase's job easier. If a new dependency is genuinely required (e.g., a training library in Phase 8), name it, justify it, and record it in `docs/engineering-log/decisions.md` before adding it to `requirements.txt` / `package.json`.
10. Finish the useful work in a phase's scope without repeatedly stopping to ask permission for routine steps; do stop and ask before anything irreversible, costly, or that touches systems outside this repo (real Slack sends, real API keys, deployment, GPU spend).

## 2. Repository conventions observed in Phase 1 (verified, not to be broken silently)

- **Backend layering**: `api/` (routers — thin, do request validation, call a service, return a schema) → `services/` (own DB writes + `WorkflowEvent` emission) → `models/` (SQLAlchemy ORM) / `schemas/` (Pydantic v2 request/response). The scorer (`scoring/`) and AI client (`ai/`) are pure-function/ABC layers that don't know about the DB. Don't put persistence logic in a router; don't put request validation in a service.
- **Every meaningful state change emits a `WorkflowEvent`.** This is the audit trail the metrics dashboard reads. If a new action is added (e.g., "human edited draft," "training example exported"), it should emit a `WorkflowEvent` with a new `event_type`, not a side-channel log line.
- **The scorer is a pure function** (`score_lead(lead: Any) -> dict`) that accepts any duck-typed object with the right attribute names — it's tested with both `SimpleNamespace` and the live ORM model. Any scoring change (Phase 4) should preserve this: no I/O, no DB access, no network, inside `scoring/`.
- **The AI client is behind an ABC** (`ai/client.py::AIClient`) with `MockAIClient` (deterministic, pure function of the context dict, no randomness, no clock reads) and `OpenAIClient` (lazy-imports `openai`, fails closed before any network call if unconfigured). Phase 5/8 grounded generation and any trained-model integration should add a new `AIClient` implementation (e.g., `LocalLoRAClient`) rather than branching inside the existing two.
- **Mock-by-default, fail-closed for real mode.** `USE_MOCK_AI` defaults `true`; `SLACK_WEBHOOK_URL` defaults empty (mock push). Real mode requires explicit configuration and validates that configuration *before* any network call (see `OpenAIClient.__init__` raising `AIConfigError` on an empty key). Any new external integration (a training-inference endpoint, a real webhook) must follow the same pattern.
- **Secrets never logged, never returned in API responses.** Verified for `SLACK_WEBHOOK_URL` and `OPENAI_API_KEY` in Phase 1. Any new secret (a HF token, an inference API key) must follow the same rule, and any new external-call error path must sanitize exceptions the way `send_slack_payload` does (never forward `str(exception)` if it could embed a URL or key).
- **No migrations exist yet** (`init_db.py` uses `Base.metadata.create_all` only). Phase 2 is responsible for introducing a real migration tool (Alembic is the natural choice given SQLAlchemy 2.0 is already in use) before any other phase adds or changes a column. Until Phase 2 lands, don't add model fields casually — every new column added before a migration tool exists makes the eventual first migration bigger and riskier.
- **Tests use SQLite in-memory via the `conftest.py` `db_engine`/`db_session_factory`/`client` fixture pattern** (`backend/tests/conftest.py`). Postgres-only behavior (if any gets introduced) needs its own marker/skip, not a silent assumption that SQLite and Postgres behave identically.
- **Frontend**: one typed fetch wrapper (`lib/api.ts`) + one `APIError` class; types in `types/api.ts` mirror backend response shapes. `next build` + `tsc --noEmit` are the only frontend gates today — no test runner exists. If Phase 6/10/11 frontend work introduces meaningfully complex client logic (e.g., draft-id-aware approve/reject, background-job polling), consider whether that phase should also introduce a frontend test runner, and record that decision explicitly rather than adding untested complexity to a codebase with zero frontend test coverage.
- **CI is path-filtered per workflow** (`.github/workflows/backend.yml`, `frontend.yml`). New workflows for training/evaluation (Phase 8/9) should follow the same path-filter pattern and must not attempt to run actual GPU training in CI — CI should validate that training/eval code is *invocable* (imports cleanly, a smoke-test on a tiny synthetic split), not that it produces a real trained model.

## 3. The readiness ladder — mandatory for every phase from here on

Every phase's status report (in `docs/engineering-log/phase-status.md` and in any summary given to the user) must place its claimed work on this ladder, per component, not just declare the phase "done":

```
code ready → needs human review → needs compute → trained → evaluated → integrated
```

Definitions:

- **code ready** — the code exists, is readable, and either passes its own tests or a smoke test on synthetic/tiny data. It has not been reviewed by a human for correctness of approach.
- **needs human review** — code ready, and a human (the user, in this project) has looked at the approach and either approved it or asked for changes. Record who approved it and when, briefly, in `decisions.md`.
- **needs compute** — reviewed and approved, but the actual run (training job, large-scale import, held-out evaluation) has not happened yet, usually because it costs money/GPU time and requires explicit go-ahead per rule 7 above.
- **trained** — a training run has actually executed and produced model weights/an adapter. This status requires: the command that was run, the machine/environment it ran on, wall-clock time, and where the artifact is stored. **A prepared, reviewed, even test-run-on-a-tiny-sample training script is still only "code ready" or "needs compute," never "trained." "Trained" requires an actual completed run against the real training data, with an artifact you can point to.**
- **evaluated** — the trained artifact has been scored against a held-out split with a stated metric, and the numbers are reproducible from a command that's recorded (not just quoted from memory). "Evaluated" requires an actual eval run, same standard as "trained": command, data, numbers, where the results are stored.
- **integrated** — the evaluated artifact (or the decision *not* to promote it) has been wired into the live application path (e.g., `LocalLoRAClient` is what `get_ai_client()` returns when configured to) and exercised through at least one real request in this environment.

**A component cannot skip a rung when reporting status.** If Phase 8 produces a training script that has never been run against real data, the correct status is "code ready" (or "needs compute" if it's been reviewed and just needs the go-ahead to run) — never "trained," never "the model is trained," never implied training results in a summary sentence like "the LoRA model learned to...". If no run has happened, there are no results to describe, honestly or dishonestly — say so.

## 4. What "done" means for a phase

A phase is not done because code was written. A phase is done when:

1. The relevant rung(s) of the readiness ladder are honestly reported for every component the phase touched.
2. `docs/engineering-log/phase-status.md` is updated with actual evidence (commands run, files changed, test results) — not a checkbox with no citation.
3. Any new architecture decision, dependency, or unresolved question surfaced during the phase is recorded in `docs/engineering-log/decisions.md`.
4. The mock demo (rule 5) still works, verified by actually running it, not assumed.
5. Nothing from the Phase 1 audit's findings list was silently worked around instead of fixed or explicitly deferred with a reason.

## 5. Escalation rules carried forward

- Stop and ask before: spending money (GPU, paid API calls), sending a real Slack message, deploying anywhere public, deleting or overwriting data that isn't clearly scratch/generated-by-this-session, or force-pushing/rewriting git history.
- Don't stop and ask before: writing code, running the existing test suite, running a new test you wrote, reading files, running read-only commands, or doing routine refactors clearly inside the current phase's stated scope.
