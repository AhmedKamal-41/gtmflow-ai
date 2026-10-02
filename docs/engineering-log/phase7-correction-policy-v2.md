# Correction policy `ai-review-rubric-v2-train`

Version: `ai-review-rubric-v2-train`. Introduced 2026-09-25 for Phase 7 training expansion.

Code:

- `backend/app/datasets/correction_policy.py`: policy ids and the automatic gate.
- `backend/data/ai_reviews/train_helpers.py`: the minimal-edit editor.
- `backend/scripts/phase6_ai_review.py build`: enforcement.

## Scope

- **Applies to:** new training examples only (queue `train-v2`, split `train`). The review build refuses v2 decisions for any validation or test candidate.
- **Leaves unchanged:**
  - the human reviews of pilot #1–#6;
  - the pilot-v1 AI reviews under `ai-review-rubric-v1`;
  - the validation-v1 / test-v1 references;
  - `heldout-criteria-v1`.

  All of these stay exactly as recorded.
- **Status:** every target produced under this policy is **AI-reviewed** (`review_source=ai`, `human_verified=false`).

## Why a new policy (training evidence only)

The evidence is:

- the pilot-v1 outputs and their AI reviews (training split);
- your human corrections of pilot #4 and #6 (training split).

Validation and test outputs, references and results were **not** used to design or tune this policy. The same AI reviewer did see held-out outputs while building the held-out references earlier. This policy's rules are all traceable to the pilot findings below, and it is never applied to held-out data.

**Pilot findings under v1** (`phase6-ai-review-report.md`, training data):

- 47 of 47 AI-corrected outreach drafts were rewritten into one template.
  - Structure analysis of the pilot export: 32 corrected outreach targets share one fact-masked structure.
  - Eligible pilot outreach is 37 examples but only 15.9 effective examples after `structure-cap-v1` weighting.
  - A training set built that way teaches one email, not grounded writing.
- The faults that made corrections necessary were mostly local:
  - a missing demonstration label (31) or an incomplete one (15);
  - `[Your Name]` placeholders (30) and "My name is from GTMFlow" (13);
  - invented needs (39), specialization (11), fit (9) and praise (7);
  - the prospect's website or profile presented as GTMFlow's (6);
  - literal `\n` escape text (3).
- Most drafts also contained supported sentences that could be kept, such as the GTMFlow capability sentences, the record facts and neutral calls to action.
- Your human corrections (#4, #6) fixed exactly these faults: the label, record-only facts, and no invented needs, contacts or placeholders. Accepted summaries (#1, #3, #5) show that faithful restatement of the record is acceptable as written.

## Rules

1. **Keep what is good, verbatim.** An original sentence stays unchanged if it:
   - is supported by the input record or the seller revision (listed capabilities);
   - is well formed;
   - contains nothing prohibited (rule 4).
2. **Make the smallest necessary correction:**
   - delete a prohibited clause or sentence;
   - rewrite only that sentence when a deletion would break the flow;
   - restore any recorded value the output altered;
   - fix mechanical faults: literal `\n`, placeholder lines, "My name is from GTMFlow" and "My name is [Your Name]" openers.
3. **Allow variety.** Greeting, structure, length, subject, sign-off and call to action may vary. Examples include a brief conversation, seeing a sample draft, replying if useful, or seeing how the review step works. They are not normalized to a template.
4. **Never manufacture or keep:**
   - company facts not in the record;
   - needs, interest, goals, challenges or initiatives attributed to the company;
   - results, customers or a track record;
   - praise ("impressed", "strong presence", "notable player", "making strides");
   - specialization for the prospect ("we specialize in helping companies like yours");
   - fit or "synergies";
   - "proven" or other unsupported quality claims;
   - an assumed contact person;
   - the prospect's website or LinkedIn presented as GTMFlow's;
   - sender–recipient confusion.

   GTMFlow's listed capabilities may be stated plainly, for example "GTMFlow can import company lists…", but not as a specialization for the recipient.
5. **Keep demonstration outreach clearly labeled.** Every outreach target states that GTMFlow / the message is a (portfolio) demonstration and **not a commercial offer**. The sentence may be worded in several ways. The signature contains no placeholder: the original sign-off is kept, or "GTMFlow (demonstration)" is used.
6. **Summaries:**
   - remove hypotheses that assert need or interest, even when labeled unconfirmed;
   - replace `seller_relevance` that asserts relevance with the neutral statement;
   - restore altered recorded values;
   - keep everything else.
7. **Doubtful records are flagged, never repaired.** They are marked `uncertain` with the concern and stay excluded until the concern is resolved with evidence. Flags are not removed to meet a count.

## Automatic enforcement (the review build refuses a target that fails any of these)

- The v2 output validator against the recorded input snapshot: schema, fact, capability and claim references, contacts and figures.
- The `baseline-metrics-v1` rubric lints:
  - outreach: `demo_label`, `no_placeholder`, `no_escape_text`, `no_invented_phrasing`, `no_prospect_web_as_own`;
  - summaries: `no_need_hypothesis`.
- The v2 policy is refused on non-training queues.
- Every v2 target records `preservation_rouge_l`, the ROUGE-L between the original prediction and the target. It is reported so that "minimal edit" is measured, not asserted.

## Relationship to evaluation

The held-out references stay under v1, so their outreach references are template-style. A model trained on v2 targets may score lower on reference overlap against them without being worse. Per `heldout-criteria-v1`, the validator, the lints and the acceptance rate are the outreach metrics to rely on. Overlap metrics are reported with that caveat.
