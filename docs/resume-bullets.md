# Resume bullets

Six bullets, 17 words or fewer each. Every number traces to a recorded result (sources below).

1. Built FastAPI/PostgreSQL GTM workflow app: grounded AI drafting, exact-draft approvals, and audited Slack routing.

2. Fine-tuned Qwen3-4B with LoRA on 419 AI-reviewed examples; blind AI review rated 69% acceptable.

3. Integrated the fine-tuned model through an OpenAI-compatible client, verified once on a self-deleting GPU pod.

4. Built a database-backed job queue with leases and bounded retries; recovered a killed worker without duplicates.

5. Prevented duplicate Slack dispatch with a claim-before-send ledger; eight concurrent requests sent exactly one message.

6. Added session authentication with CSRF protection, roles, lockout, and actor-stamped audit events across every route.

## Word counts

| # | Words |
|---|---|
| 1 | 14 |
| 2 | 14 |
| 3 | 15 |
| 4 | 16 |
| 5 | 15 |
| 6 | 15 |

## What these bullets deliberately do not claim

- **No users, revenue or production:** there are no real users and nothing is deployed.
- **No human-verified model quality:** the training references and the 69% come from **AI** review (Claude), not people, on a small test set (71 examples; 36 outreach). Say "AI-reviewed" and "blind AI review" when asked.
- **No always-on model:** the fine-tuned model ran once, on a temporary GPU pod, for an acceptance test. The app runs in mock mode by default.
- **No real Slack traffic:** every delivery in this project so far was mock (or a test double).
- **No automated email sending:** outreach is always a reviewed draft.

## Sources

- **Bullet 1:** grounded generation with fact-reference validation (Phase 5); exact-draft, content-hash approvals (Phase 6); delivery ledger (Phase 11).
- **Bullet 2:** `docs/upgrade/phase8-training-handoff.md` (419 training examples; the reviews were AI and 5 human decisions) and `docs/upgrade/phase9-evaluation-handoff.md` §12 (blind AI review: 49 of 71 = 69.0% acceptable as-is; base model 1 of 71).
- **Bullet 3:** `docs/upgrade/phase10-integration-handoff.md` §9. One L4 pod, all 8 required criteria passed, and the pod deleted itself with its own key.
- **Bullet 4:** `docs/upgrade/phase10-integration-handoff.md` §3. A SIGKILLed worker on a 300-lead job; a second worker finished it with exactly 300 outputs for 300 leads.
- **Bullet 5:** `docs/upgrade/phase11-routing-metrics-handoff.md` §6. 8 simultaneous pushes on PostgreSQL produced exactly 1 send.
- **Bullet 6:** `docs/upgrade/phase12-release-handoff.md`. A test walks every API route and asserts that anonymous requests are refused.
