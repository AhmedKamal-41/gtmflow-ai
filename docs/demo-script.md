# Demo script

Nine steps, about 6 minutes. Everything runs in **mock mode**: deterministic mock AI and a mock Slack webhook, no keys, and no message leaves the app. Setup commands are in the README ("Setup (local, mock mode)").

Audience: a recruiter or hiring manager with 5–7 minutes.

## 1. Sign in

Open http://localhost:3000. You are sent to **/login**. Sign in with the account you created with `python -m app.auth_cli create-user`.

> **What to say:** "Every data route requires a signed-in user; actions require an operator, enforced on the backend, not just hidden in the UI. Accounts come from an admin CLI; there's no public sign-up, because this is a single-team tool. Each approval and delivery records who did it."

## 2. Run the demo batch

Open **/demo** and click **Run the full demo**. It loads a synthetic 10-lead list, scores it (2 Hot, 4 Warm, 4 Cold), drafts and approves outreach for the two Hot leads, and delivers them to the mock Slack webhook.

> **What to say:** "The demo is labeled as synthetic, and its auto-approval is labeled 'demo-auto-approve'. For real leads, a person approves every draft."

## 3. Activate a seller profile, upload a list, and use background jobs

Outreach needs an explicitly activated seller profile. On **/seller-profile**:

1. Click **Load GTMFlow demonstration template**, then **Save draft**.
2. Under the saved version, tick both confirmations ("I reviewed version 1…" and "…is a demonstration profile, not a real offer").
3. Activate it.

Then upload `sample_data/leads_sample.csv` on **/upload** and open the batch. In **Background jobs**, run **Legacy-score all leads** (to produce the Hot/Warm/Cold bands), **Fit-score all leads**, and **Generate outreach drafts**. The worker you started (`python -m app.jobs.worker`) picks them up; the panel shows progress and counts.

> **What to say:** "Long batch work runs in a worker, not in the web request. Jobs survive a worker crash: another worker resumes unfinished items. Completed database effects are not repeated; uncertain external deliveries need an operator decision."

## 4. Open a Hot lead

Click **Cascade Modular Homes**. You see the legacy score breakdown, the company-fit score with its evidence coverage, and the current outreach draft.

> **What to say:** "Two scores, kept apart: the original 100-point heuristic and a versioned company-fit score that reports how much evidence it had."

## 5. Read the grounded draft

The draft cites the record facts it used (fact ids), states what is unknown (intent, budget, current tools), and shows its provenance: prompt, model and seller profile.

> **What to say:** "The model may only use facts in the record and claims in the activated seller profile. The server rejects output that cites anything else, so an invalid draft is never saved. The same checks apply to every model: the mock, OpenAI, or the fine-tuned Qwen model."

## 6. Review the exact draft

Click **Approve outreach**. If the draft had runtime quality flags (for example "Exploring Opportunities…" framing), a checkbox would require you to acknowledge them first. You can also reject with a reason, or edit (an edit becomes a new revision that needs its own review).

> **What to say:** "An approval names the exact content hash I looked at. If anything changes afterwards (the draft, the company facts, the seller profile) the approval stops authorizing delivery."

## 7. Push to Slack, twice

Click **Push to Slack**: the push history shows `mock_success · mock webhook · attempt 1`. Click it again: nothing new is sent. The response is a replay of the same delivery.

> **What to say:** "Delivery claims a database row before sending, so two tabs, two workers or a restarted job can't send the same approved draft twice. If Slack times out, the outcome is recorded as unknown and never resent automatically. An operator checks the channel and records what happened."

## 8. Metrics

Open **/metrics**. A banner says the data is mock-only. The approval rate counts each draft once, by its latest review, so it can't exceed 100%. The "Mock versus real" card splits generation and delivery.

> **What to say:** "Real messages delivered: zero. The dashboard says so instead of counting mock deliveries as real ones. Time saved is an explicit estimate: five minutes per lead."

## 9. The model work (talk track, no live GPU)

Show `docs/engineering-log/phase9-evaluation-handoff.md` and `phase10-integration-handoff.md`.

> **What to say:** "I fine-tuned Qwen3-4B with LoRA on 419 AI-reviewed examples for about 60 cents of GPU time. On held-out data, a blind AI review rated 69% of its drafts acceptable as-is, against 1% for the base model. Summaries were fine; outreach was the weak spot, and I traced its two main faults to phrasing kept in the training data. All of that is AI-evaluated, not human-verified. Then I ran the app's own client against the real model once, on a temporary GPU pod that deleted itself, and all acceptance checks passed. There's no always-on model server; the app runs on the mock by default."

> **Closing line:** "The loop is ingest → score → grounded draft → human approval → safe delivery → honest metrics, with every step audited and every claim tied to evidence in the repo."

## Reproducible portfolio evidence

The [Phase 12 release run](https://github.com/AhmedKamal-41/gtmflow-ai/actions/runs/36293369509) verifies 521 backend tests on isolated PostgreSQL, 107 frontend tests, typecheck/build and 84 live mock checks. It makes no real sends and uses no production database. Show this alongside the dated Phase 9/10 handoffs; historical screenshots alone do not demonstrate the current sign-in or routing behavior.
