# Demo script

Eight steps, about 6 minutes, following a sales rep's day. The default setup drafts with the **fine-tuned model when its server is connected** and falls back to a labeled demo generator otherwise. Slack is mocked, so no message leaves the app. Setup commands are in the README ("Local setup").

Audience: a recruiter or hiring manager with 5–7 minutes.

## 1. Get in

Open http://localhost:3000. On the sign-in page, press **Continue as guest** (or sign in, or create an account confirmed with an emailed 6-digit code).

> **What to say:** "Every route needs a session, enforced on the backend. Visitors can use a guest account that is safe by construction: guests can only change data while the server can't send real messages or call a paid AI API. Sign-up is verified by email, and every action is audited with who did it."

## 2. Today

The **Today** page shows what needs attention and which model is drafting. On an empty workspace it offers **Import leads** or **Try with sample data**; press **Try with sample data**. It imports 10 companies through the normal upload and scores them (2 Hot, 4 Warm, 4 Cold). Nothing is drafted, approved or sent automatically.

> **What to say:** "The sample goes through exactly the same path as a real CSV."

## 3. Tell GTMFlow what you sell

Today shows **One step before drafting**. Open **Set up seller profile**, click **Load GTMFlow demonstration template**, **Save draft**, open the saved version, tick both confirmations and activate it.

> **What to say:** "Drafts may only describe the product the way this profile does. Activation is an explicit, reviewed step, and a demonstration profile can never make an email 'ready'."

## 4. Leads

Open **Leads**: every lead ranked Hot first, with stage tabs (To review, Ready to send, Needs a draft, …), search and a priority filter. Open **Cascade Modular Homes**.

> **What to say:** "The stage comes from the same rules that review and delivery enforce, so the list never promises something the lead page will refuse."

## 5. Generate and read the draft

Press **Generate outreach**. The draft says which model wrote it, lists the facts it is based on and what is not known (intent, budget, current tools), and keeps its provenance under **Model details**.

> **What to say:** "The model may only use facts in the record and claims in the seller profile. The server rejects output that cites anything else, so an invalid draft is never saved. The same checks apply to every model: the demo generator, OpenAI, or the fine-tuned Qwen model."

## 6. Review the exact draft

Press **Approve outreach**. If the draft had runtime quality flags, a checkbox would require acknowledging them first. You can also **Edit draft** (a new revision that needs its own review) or reject with a reason.

> **What to say:** "An approval names the exact content I looked at. If the draft, the company facts or the seller profile change, the approval stops authorizing delivery."

## 7. Send to Slack, twice

Press **Push to Slack**: the history shows `mock success · attempt 1`. Press it again: nothing new is sent; the response replays the same delivery. Back on **Today**, the lead now counts as **Sent**.

> **What to say:** "Delivery claims a database row before sending, so two tabs, two workers or a restarted job can't send twice. A timeout is recorded as unknown and never resent automatically; an operator confirms what happened."

## 8. Insights and the model

Open **Insights**: a banner says the data is mock-only, approval counts each draft once, and mock and real activity are split. Then open **Settings → Drafting model**: the fine-tuned model, its adapter, and whether its server is connected.

> **What to say:** "I fine-tuned Qwen3-4B with LoRA on 419 AI-reviewed examples for about 60 cents of GPU time. On held-out data, a blind AI review rated 69% of its drafts acceptable as-is, against 1% for the base model; outreach was the weak spot, and the runtime checks come from that analysis. It is AI-evaluated, not human-verified. I ran the app's own client against the real model on a temporary GPU that deleted itself; when no model server is connected, the app says so and uses the demo generator."

> **Closing line:** "Import → prioritize → grounded draft by a fine-tuned model → human approval → safe delivery → honest metrics, with every step audited."

## Evidence

Tests, the release harness and the dated handoffs are listed in the README ("Testing strategy") and the [engineering log](engineering-log/README.md).
