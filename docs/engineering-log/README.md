# Engineering log

GTMFlow was built in twelve phases. Each phase ended with a handoff that records what changed, how it was verified, and what was still open. These documents are the evidence behind every number in the main README. They are kept as written at the time. A few early phases appear only in the status table and the decision log, not in a handoff of their own.

Start with [`phase-status.md`](phase-status.md) for the one-table summary, and [`implementation-contract.md`](implementation-contract.md) for the working rules every phase followed.

| Phase | Topic | Record |
|---|---|---|
| 1 | Repository audit and baseline | [`audit.md`](audit.md) |
| 2 | Alembic migrations, provenance, versioned artifacts | [`decisions.md`](decisions.md), [`audit.md`](audit.md) |
| 3 | Streaming company-data (PDL) import | [`phase3-data-handoff.md`](phase3-data-handoff.md) |
| 4 | Company-fit scoring, evidence coverage, readiness and routing eligibility | [`phase4-scoring-handoff.md`](phase4-scoring-handoff.md) |
| 5 | Grounded generation and seller profiles | [`phase5-generation-handoff.md`](phase5-generation-handoff.md) |
| 6 | Exact-draft review and the annotation workflow | [`phase6-review-handoff.md`](phase6-review-handoff.md), [`phase6-ai-review-report.md`](phase6-ai-review-report.md) |
| 7 | Dataset splits, correction policy, baseline evaluation | [`phase7-dataset-handoff.md`](phase7-dataset-handoff.md), [`phase7-correction-policy-v2.md`](phase7-correction-policy-v2.md), [`phase7-heldout-evaluation-record.json`](phase7-heldout-evaluation-record.json) |
| 8 | LoRA training on a rented GPU | [`phase8-training-handoff.md`](phase8-training-handoff.md) |
| 9 | Held-out comparison and blind AI review | [`phase9-evaluation-handoff.md`](phase9-evaluation-handoff.md) |
| 10 | Model integration, durable background jobs, runtime checks | [`phase10-integration-handoff.md`](phase10-integration-handoff.md) |
| 11 | Slack delivery ledger and corrected metrics | [`phase11-routing-metrics-handoff.md`](phase11-routing-metrics-handoff.md) |
| 12 | Access control, dependency audit, release verification | [`phase12-release-handoff.md`](phase12-release-handoff.md) |

Cross-cutting design decisions, with the reasoning behind them, are in [`decisions.md`](decisions.md).

## After Phase 12

- **Self-service sign-up and guest access (2026-10-02).** Migration `0014_signup_and_guest`: sign-up confirmed by an emailed 6-digit code (hashed, 10-minute expiry, 5 attempts, 60-second resend cooldown), and per-visitor guest accounts that can change data only on a mock-only server. Both are off by default. Tests: `backend/tests/test_signup_guest.py`, `frontend/src/app/login/page.test.tsx`, and live checks in `backend/scripts/verify_release.py`.
