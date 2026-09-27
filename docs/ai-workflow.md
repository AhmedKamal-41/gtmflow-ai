# AI workflow and evidence

The app uses deterministic **mock AI by default**. `USE_MOCK_AI=true` overrides
provider selection. Real generation needs explicit configuration; this document
does not authorize paid calls or model hosting.

## Generation and review

1. Build a context of record facts with IDs, provenance, explicit unknowns and
   the versioned fit score (not evidence of interest).
2. Outreach requires an explicitly activated seller revision, with allowed
   capabilities and sourced claims. A demonstration profile is labeled.
3. Ask the selected AIClient for structured output with fact/claim references.
4. Validate structure, reference membership and completeness before saving
   output and provider/prompt/seller provenance. Invalid output is refused.
5. Apply `runtime-checks-v1` for known unsupported or weak phrasing. These
   patterns do not establish complete factual or writing correctness.
6. A signed-in operator reviews the exact draft/content hash. Current flags
   require acknowledgement. Edits create revisions needing their own approval.
   Generation and background jobs never approve drafts.
7. Delivery rechecks draft, record, seller and approval eligibility. Only the
   separately labeled synthetic demo creates demo approvals.

API and job generation reuse these services. Providers share the same approval
rules. Slack is separate; real email sending is not implemented.

## Providers

| Provider | Current evidence |
|---|---|
| Mock | Complete signed-in workflow verified on synthetic data without external calls. |
| OpenAI | Implemented, opt-in, paid. Earlier dataset generation used it; Phase 12 makes no calls and needs no key. |
| `qwen3-4b-lora-v1` | Client checks availability and rejects truncation. Phase 10 verified the pinned model/adapter on one temporary GPU. No persistent host exists. Output provenance records a configured adapter hash; the acceptance run independently checked the loaded files. |

## AI-evaluated model results

Phase 8 trained Qwen3-4B with LoRA on 419 reviewed examples and selected by
validation loss. Phase 9 compared base and adapter under identical settings
and frozen criteria. One blind **AI** reviewer accepted 49/71 LoRA outputs
(69%) versus 1/71 base: summaries 35/35, outreach 14/36. References and review
judgments are AI-derived, not human quality certification. Coverage is limited
to two segments and one seller profile.

The adapter inherited objectionable outreach phrasing from training data.
Runtime rules matching the review examples used to tune them is regression
coverage. Zero flags on 100 untouched validation references checks false
positives on those references only; detection of unseen bad wording is untested.

Phase 10's temporary inference acceptance passed all eight required criteria.
Only 7/20 texts matched Phase 9 exactly; batching/numerics is a likely, unproven
explanation. Four of ten outreach cases changed runtime flags, so the earlier
review rate cannot be assumed for serving. Outreach still needs review. L4
latency was 22.6 seconds median, 28 seconds p95, one request at a time.

Frozen scores, references, artifacts and training locks are unchanged in Phase
12. The company-name lint false positive remains documented under the original
criteria; runtime checks have their own version. No retraining ran.

## Evidence

- [Training](upgrade/phase8-training-handoff.md)
- [Evaluation and blind review](upgrade/phase9-evaluation-handoff.md)
- [Temporary GPU integration](upgrade/phase10-integration-handoff.md)
- [Dependency findings and audit limits](dependency-security.md)
- [Release checks and deployment blockers](upgrade/phase12-release-handoff.md)
