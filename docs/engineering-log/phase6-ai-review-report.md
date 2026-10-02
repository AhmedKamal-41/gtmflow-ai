# Phase 6 AI review report: pilot `pilot-v1`

Generated 2026-09-24 from `backend/data/ai_reviews/pilot-v1/ai-review-export.jsonl` (sha256 `788038718cab4eff…`, 94 rows). **These are AI reviews, not human reviews.**

## Provenance

- **Reviewer:** `claude-opus-5-5`, working in a Claude Code session (`reviewer_type=ai`, `human_verified=false`, `human_review_time_ms=null`).
- **Method:** the reviewer read each candidate's stored input snapshot (the exact facts given to the generator) and its stored output, and decided per candidate. No model calls were made and nothing was regenerated.
- **Pinning:** each decision records the output id and the content hash that was read, and the build refuses any decision whose pin doesn't match the database.
- **Validation:** every corrected target passed the same v2 validator (schema, fact/capability/claim references, contact and figure checks) against the candidate's recorded input snapshot.
- **Storage:** reviews were **not** written through the human annotation API and were **not** stored in `training_annotations`. The human workbench still shows #7–#100 as not human-reviewed, which is accurate.
- **Rubric:** `ai-review-rubric-v1`, derived from your own decisions. Your corrections of #4 and #6 set the outreach standard: say plainly that it is a portfolio demonstration and not a commercial offer; restate only record facts; no invented specialization, research, praise, needs, results, placeholders or contacts. Your acceptances (#1, #3, #5) allowed "N-M employees" for the size band and "small" for 1–10.
- **Rules applied throughout:**
  - Hypotheses that invent interest or need are removed, even when labeled "unconfirmed".
  - `seller_relevance` is acceptable only if it describes capabilities or is hedged from the seller's side. Wording that asserts the lead's need, interest or relevance as fact is replaced with a neutral statement.
  - Company names follow the record; the only exception is title-casing in corrected outreach.

## Totals (kept separate)

| | Human-reviewed | AI-reviewed |
|---|---|---|
| Candidates | 6 (#1–#6) | 94 (#7–#100) |
| Accepted | 3 | 30 (summaries 30, outreach 0) |
| Corrected | 2 | 64 (summaries 17, outreach 47) |
| Skipped | 1 | 0 |
| Usable examples (accepted + corrected) | 5 | 94 |
| Unique companies among the usable examples | 3 | 47 |
| Flagged uncertain | — | 26 |

**The original requirement of 100 human reviews is not met:** 6 candidates were human-reviewed. The other 94 were reviewed by an AI and are not human-verified.

## What the AI review found

- **Outreach: 47 of 47 needed correction; none was acceptable as generated.** None fully met the demonstration standard. Every one either lacked the "not a commercial offer" statement or had no demonstration label at all, and almost all invented needs, praise or a specialization. Serious recurring faults:
  - presenting the prospect's own website or LinkedIn page as GTMFlow's (#8, #22, #28, #60, #96); #60 also misspells the domain;
  - confusing sender and recipient ("As a medical practice… we understand", #22, #24, #50);
  - literal `\n` escape text (#28, #30, #76);
  - broken signatures ("My name is from GTMFlow", `[Your Name]`).
- **Summaries: 30 of 47 accepted, 17 corrected.** Corrections removed invented-need or interest hypotheses, replaced asserted seller relevance, fixed inferences from company names ("healthcare consulting" #9, a person "operates" #89), and restored a recorded name the model had silently respelled (#77).
- **Issue counts across the 94 AI reviews:** `invented_need` 39, `missing_demo_label` 31, `placeholder_signature` 30, `incomplete_demo_label` 15, `broken_signature` 13, `invented_specialization` 11, `invented_fit` 9, `asserted_relevance` 8, `invented_praise` 7, `misattributed_website` 4, `invented_interest_hypothesis` 4, `invented_need_hypothesis` 3, `perspective_confusion` 3, `literal_escape_sequences` 3, `inferred_from_company_name` 2, `assumed_contact` 2, `misattributed_profile` 2, `invented_company_detail` 1, `invented_activity` 1, `overstated_specialization` 1, `invented_market_claim` 1, `empty_signature` 1, `vague_reference` 1, `fabricated_domain` 1, `altered_recorded_name` 1, `invented_focus` 1, `invented_research` 1, `muddled_relevance` 1, `unlisted_capability` 1, `missing_signature` 1, `unsupported_quality_claim` 1, `invented_result` 1.

## Uncertain cases for optional human review

In each case the output was judged against the record, but the record itself looks doubtful. Checking these requires outside knowledge the reviewer did not use.

| # | Company | Task | AI decision | Why uncertain |
|---|---|---|---|---|
| 11 | national development foundation inc | Summary | accepted | The name ('...Foundation Inc') suggests a nonprofit while the record says real estate; the summary correctly follows the record, but the record itself may be misclassified. |
| 17 | webb sanders funeral home | Summary | corrected | A funeral home is recorded under 'hospital & health care'; the draft follows the record, but the classification itself may be wrong. |
| 18 | webb sanders funeral home | Outreach | corrected | A funeral home is recorded under 'hospital & health care'; the draft follows the record, but the classification itself may be wrong. |
| 21 | playdate lmsw social work pllc | Summary | accepted | A social-work PLLC is recorded as a 'medical practice'; the summary follows the record. |
| 23 | ultimate capital | Summary | corrected | The website is a .co.uk domain while the location is Harrisburg, Pennsylvania; the record may mix two companies. |
| 24 | ultimate capital | Outreach | corrected | The website is a .co.uk domain while the location is Harrisburg, Pennsylvania; the record may mix two companies. |
| 33 | saint francis community and residential services inc. | Summary | accepted | A 'community and residential services' organization is recorded as a 'medical practice'; the summary follows the record. |
| 34 | saint francis community and residential services inc. | Outreach | corrected | A 'community and residential services' organization is recorded as a 'medical practice'; the draft follows the record. |
| 41 | jeremy ryan butler | Summary | accepted | The company name is a person's name; the record may describe an individual's profile rather than an organization. |
| 42 | jeremy ryan butler | Outreach | corrected | The company name is a person's name; the record may describe an individual's profile rather than an organization. |
| 47 | newport news redevelopment & housing authority | Summary | accepted | A public redevelopment and housing authority is recorded as a real estate company; outputs follow the record, but 'company' may mislabel a public agency. |
| 48 | newport news redevelopment & housing authority | Outreach | corrected | A public redevelopment and housing authority is recorded as a real estate company; outputs follow the record, but 'company' may mislabel a public agency. |
| 65 | binita amin | Summary | accepted | The company name is a person's name; the record may describe an individual's profile rather than an organization. |
| 66 | binita amin | Outreach | corrected | The company name is a person's name; the record may describe an individual's profile rather than an organization. |
| 71 | equity solutions and investments, inc. | Summary | accepted | The website (prflending.com) and LinkedIn slug (loansandrealty) do not match the company name; the record may mix sources. |
| 72 | equity solutions and investments, inc. | Outreach | corrected | The website (prflending.com) and LinkedIn slug (loansandrealty) do not match the company name; the record may mix sources. |
| 77 | laurenwood nursing & rehabilation | Summary | corrected | The recorded name is spelled 'Rehabilation' (likely a source typo) and the website (mlpayton.com) does not match the name; a nursing and rehabilitation center is recorded as a 'medical practice'. |
| 78 | laurenwood nursing & rehabilation | Outreach | corrected | The recorded name is spelled 'Rehabilation' (likely a source typo) and the website (mlpayton.com) does not match the name; a nursing and rehabilitation center is recorded as a 'medical practice'. |
| 85 | herbert e todd | Summary | accepted | The company name is a person's name; the record may describe an individual's profile rather than an organization. |
| 86 | herbert e todd | Outreach | corrected | The company name is a person's name; the record may describe an individual's profile rather than an organization. |
| 91 | piedmont securities llc | Summary | accepted | A company named 'Piedmont Securities' is recorded as real estate; outputs follow the record, but the classification may be wrong. |
| 92 | piedmont securities llc | Outreach | corrected | A company named 'Piedmont Securities' is recorded as real estate; outputs follow the record, but the classification may be wrong. |
| 95 | clark capital group, llc | Summary | accepted | The website (hardmoneycentral.com) does not match the company name; it may belong to a related brand or a different company. |
| 96 | clark capital group, llc | Outreach | corrected | The website (hardmoneycentral.com) does not match the company name; it may belong to a related brand or a different company. |
| 97 | statewide properties llc | Summary | corrected | 'Statewide Properties LLC' is recorded under 'hospital & health care'; outputs follow the record, but the classification looks wrong. |
| 98 | statewide properties llc | Outreach | corrected | 'Statewide Properties LLC' is recorded under 'hospital & health care'; outputs follow the record, but the classification looks wrong. |

## Human-review data notes (unchanged)

Your decisions are preserved exactly. Two free-text fields look like placeholders, and you may want to revisit them in the workbench (a new decision is a new row; history is kept):

- **#2** (skipped): skip reason recorded as `j`.
- **#3** (accepted): note recorded as `kuisauewoieoiweoiwoi`.

## Per-candidate AI decisions

| # | Company | Task | Decision | Support | Writing | Missing info | Reason |
|---|---|---|---|---|---|---|---|
| 7 | landmax real estate | Summary | accepted | supported | 4 | good | Every statement (name, industry, US location, 1-10 size, website, LinkedIn URL) matches the record; unknowns listed; seller_relevance null. |
| 8 | landmax real estate | Outreach | corrected | unsupported | 2 | poor | Presents the prospect's own website (landmaxrealestate.com) as GTMFlow's website, invents a specialization ('enhancing lead workflows') and benefits,… |
| 9 | elevation health consulting | Summary | corrected | partially_supported | 3 | good | Calls the company a 'healthcare consulting company', inferred from its name; the record's industry is only 'hospital & health care'. Other facts (Cro… |
| 10 | elevation health consulting | Outreach | corrected | unsupported | 2 | poor | Invents 'a growing company', assumed needs ('streamline lead outreach', 'your initiatives'), a specialization claim, and a '[Your Name]' placeholder;… |
| 11 | national development foundation inc | Summary | accepted ⚠ | supported | 4 | good | Name, real estate industry, Oviedo FL, founded 1998 and 1-10 ('small', as accepted in human review #3) are all in the record; seller_relevance is hed… |
| 12 | national development foundation inc | Outreach | corrected | unsupported | 2 | poor | Broken signature ('My name is from GTMFlow'), invented praise ('a notable player'), 'companies like yours' need framing; no demonstration label. |
| 13 | josephine c. samson, m.d | Summary | accepted | supported | 3 | good | All statements match the record (name as recorded, medical practice, New York NY, 1-10); wording is stiff ('The lead is for a company named...') but… |
| 14 | josephine c. samson, m.d | Outreach | corrected | partially_supported | 2 | acceptable | Addresses the practice's namesake directly although no contact is on file, invents a need ('enhance your operational workflows'), and leaves '[Your N… |
| 15 | redblock realty inc. | Summary | accepted | supported | 4 | good | Name, real estate, Jenkintown PA, 1-10, founded 2012 and website all match; seller_relevance lists only profile capabilities and states it is a demon… |
| 16 | redblock realty inc. | Outreach | corrected | partially_supported | 2 | poor | Founding year and small size are supported, but 'making strides', 'tailored to your needs' and 'goals and challenges' are invented; broken signature… |
| 17 | webb sanders funeral home | Summary | corrected ⚠ | partially_supported | 3 | acceptable | Facts are supported, but the hypothesis invents a need ('may require services related to outreach'); labeling it unconfirmed does not make an invente… |
| 18 | webb sanders funeral home | Outreach | corrected ⚠ | unsupported | 2 | poor | Broken signature ('My name is from GTMFlow'), invented needs ('streamlining operations', 'initiatives and challenges'), '[Your Name]' placeholder; no… |
| 19 | iron wolf ventures | Summary | accepted | supported | 4 | good | Name, real estate, Jordan WV, founded 2019 and 1-10 match; seller_relevance is hedged and says the demonstration has no real customer segment. |
| 20 | iron wolf ventures | Outreach | corrected | unsupported | 2 | poor | Invents activity ('actively engaged'), goals and fit ('given your company's size and focus'); broken signature and '[Your Name]'; no demonstration la… |
| 21 | playdate lmsw social work pllc | Summary | accepted ⚠ | supported | 3 | good | Every statement quotes the record (name, medical practice, 1-10, New York NY, healthcare segment); accurate but stiff, quoting raw values. |
| 22 | playdate lmsw social work pllc | Outreach | corrected | unsupported | 1 | poor | Confuses sender and recipient ('As a small medical practice ... we understand'), offers the prospect's own LinkedIn page as 'our profile', invents a… |
| 23 | ultimate capital | Summary | corrected ⚠ | partially_supported | 3 | acceptable | Facts are supported, but the hypothesis invents interest ('may be interested in B2B services'). |
| 24 | ultimate capital | Outreach | corrected ⚠ | unsupported | 2 | poor | Misattributes the sector ('As a company in the real estate sector, we believe'), invents 'potential synergies', '[Your Name]'; no demonstration label. |
| 25 | halim clinic | Summary | accepted | supported | 4 | good | Name, hospital & health care ('provider' is a fair reading), Holland OH, 11-50 and website all match; no speculation. |
| 26 | halim clinic | Outreach | corrected | partially_supported | 2 | acceptable | Mentions a 'demonstration workflow' but invents praise ('impressed by your commitment'), a need ('streamline their outreach'), and '[Your Name]/[Your… |
| 27 | prairie real estate investments | Summary | corrected | partially_supported | 3 | acceptable | 'Specializing in the real estate industry' overstates the industry label, and seller_relevance invents 'the lead's operational needs'. |
| 28 | prairie real estate investments | Outreach | corrected | unsupported | 1 | poor | Body contains literal '\n' escape text, presents the prospect's own website and LinkedIn as GTMFlow's, and claims the prospect 'specializes'; mention… |
| 29 | alazzo med spa | Summary | corrected | partially_supported | 3 | acceptable | Facts are supported, but the hypothesis invents interest ('may be interested in services related to healthcare operations'). |
| 30 | alazzo med spa | Outreach | corrected | unsupported | 1 | poor | Literal '\n' escape text, broken signature ('My name is from GTMFlow'), invented growth and challenges, '[Your Name]'; no demonstration label. |
| 31 | advisors title network | Summary | corrected | partially_supported | 3 | good | All record facts are correct, but seller_relevance asserts as fact that the capabilities are 'relevant to the lead'. |
| 32 | advisors title network | Outreach | corrected | unsupported | 2 | poor | Broken signature, invented market claims ('the real estate sector can be competitive') and needs, '[Your Name]'; no demonstration label. |
| 33 | saint francis community and residential services inc. | Summary | accepted ⚠ | supported | 4 | good | Name, medical practice, Salina KS and 1-10 match the record exactly; no speculation. |
| 34 | saint francis community and residential services inc. | Outreach | corrected ⚠ | partially_supported | 2 | acceptable | Invents 'strong presence' and needs/strategies, '[Your Name]'; mentions a demonstration but not that it is not a commercial offer. |
| 35 | julian properties inc. | Summary | accepted | supported | 4 | good | Name, real estate, Orlando FL, 11-50, founded 1986 and website all match; no speculation. |
| 36 | julian properties inc. | Outreach | corrected | partially_supported | 2 | poor | Founding year and location are supported, but 'a key player', 'synergies' and 'complement your operations' are invented; '[Your Name]/[Your Title]';… |
| 37 | eddy heritage house nursing and rehabilitation center | Summary | accepted | supported | 3 | good | Name, hospital & health care, New York US, 1-10 and the healthcare segment label all match; accurate, list-like wording. |
| 38 | eddy heritage house nursing and rehabilitation center | Outreach | corrected | partially_supported | 2 | acceptable | Invents a need ('streamlining their outreach processes', 'support your operations'); no demonstration label. |
| 39 | the putkela group | Summary | corrected | partially_supported | 3 | acceptable | Facts are supported, but the hypothesis invents interest ('may be interested in outreach solutions'). |
| 40 | the putkela group | Outreach | corrected | partially_supported | 3 | acceptable | Record facts are right, but it invents 'outreach and growth strategies' to support; no demonstration label. |
| 41 | jeremy ryan butler | Summary | accepted ⚠ | supported | 3 | good | All statements match the record; seller_relevance is hedged ('may be relevant') and lists only profile capabilities. |
| 42 | jeremy ryan butler | Outreach | corrected ⚠ | unsupported | 2 | poor | Broken signature, 'Dear Team at Jeremy Ryan Butler' although the record may describe an individual, invented alignment with 'your needs', '[Your Name… |
| 43 | hanover family builders | Summary | accepted | supported | 4 | good | Name, real estate, Orlando FL, founded 2016 and 51-200 match; no speculation. |
| 44 | hanover family builders | Outreach | corrected | partially_supported | 2 | poor | Founding year, location and size are supported, but 'making an impact', 'strong foundation' and 'synergies' are invented; broken signature and placeh… |
| 45 | david s. lehman, d.d.s | Summary | corrected | partially_supported | 3 | acceptable | Facts are supported, but the hypothesis states interest as fact ('The lead is interested in B2B solutions'), and seller_relevance asserts that GTMFlo… |
| 46 | david s. lehman, d.d.s | Outreach | corrected | partially_supported | 2 | acceptable | Mentions a 'demonstration portfolio' but invents needs ('manage leads more efficiently', 'your organization's needs') and a specialization; does not… |
| 47 | newport news redevelopment & housing authority | Summary | accepted ⚠ | supported | 4 | good | Name, real estate, Newport News VA, founded 1938, 51-200, website and LinkedIn all match; seller_relevance is hedged and seller-side. |
| 48 | newport news redevelopment & housing authority | Outreach | corrected ⚠ | partially_supported | 2 | acceptable | Signs as 'GTMFlow (demonstration)' but invents 'synergies', 'streamlining processes' and needs, claims a specialization, and leaves '[Your Name]'; do… |
| 49 | garza wellness care center, llc | Summary | corrected | partially_supported | 3 | acceptable | Facts are supported, but seller_relevance asserts relevance and invents 'the lead's needs in marketing or operational support'. |
| 50 | garza wellness care center, llc | Outreach | corrected | unsupported | 1 | poor | Confuses sender and recipient ('As a medical practice located in Honolulu, we understand'), invents patient-communication needs, '[Your Name]'; menti… |
| 51 | minerva realty consultants | Summary | accepted | supported | 4 | good | Name, real estate, New York NY, founded 2017, 1-10 and website match; seller_relevance is hedged and seller-side. |
| 52 | minerva realty consultants | Outreach | corrected | partially_supported | 1 | poor | Broken opening ('My name is from GTMFlow') and an empty signature, invented 'synergies' and benefits; no demonstration label. |
| 53 | samuel j perez md inc | Summary | corrected | partially_supported | 3 | good | Facts are supported, but seller_relevance asserts the capabilities are 'relevant to the lead's profile'. |
| 54 | samuel j perez md inc | Outreach | corrected | partially_supported | 2 | acceptable | Mentions a demonstration product but claims a specialization and an assumed need ('support your outreach efforts more effectively'); does not say it… |
| 55 | white knight realty llc | Summary | accepted | supported | 4 | good | Name, real estate, Friendswood TX, 1-10 ('small'), website and LinkedIn match; no speculation. |
| 56 | white knight realty llc | Outreach | corrected | partially_supported | 2 | acceptable | Record facts are right, but it invents benefits and lead-management challenges; no demonstration label. |
| 57 | srb assist, inc | Summary | corrected | partially_supported | 3 | acceptable | Facts are supported, but both hypotheses invent needs ('specific needs related to the healthcare industry', 'seeking solutions to improve their opera… |
| 58 | srb assist, inc | Outreach | corrected | partially_supported | 2 | acceptable | Mentions a demonstration but asserts value for 'your outreach efforts', points to an unspecified 'our demo', and asks about needs; does not say it is… |
| 59 | realtors who shrine | Summary | accepted | supported | 4 | good | Name, real estate, founded 2019, Fair Oaks CA, 1-10 ('small') and website all match; no speculation. |
| 60 | realtors who shrine | Outreach | corrected | unsupported | 1 | poor | Invents praise ('impressive work'), offers the prospect's website as 'our website' and misspells it ('reatorswhoshrine.com', a domain not in the reco… |
| 61 | chateau st mark | Summary | accepted | supported | 4 | good | Name, hospital & health care ('healthcare company', as in human correction #6), Anaheim CA and 1-10 match; no speculation. |
| 62 | chateau st mark | Outreach | corrected | partially_supported | 2 | acceptable | Record facts are right, but it invents a need ('manage leads effectively', 'challenges ... in lead management') and has '[Your Name]'; no demonstrati… |
| 63 | resource title | Summary | corrected | partially_supported | 3 | acceptable | Facts are supported, but seller_relevance asserts that GTMFlow 'is relevant' and 'applicable to the company's needs'. |
| 64 | resource title | Outreach | corrected | partially_supported | 3 | acceptable | Facts are accurate and the tone is restrained, but it assumes a need ('your efforts in connecting with potential leads') and is not labeled a demonst… |
| 65 | binita amin | Summary | accepted ⚠ | supported | 3 | good | All facts match; seller_relevance describes capabilities (not the lead) and says there is no commercial applicability. |
| 66 | binita amin | Outreach | corrected ⚠ | partially_supported | 2 | poor | Broken signature, invented goals and challenges, '[Your Name]'; no demonstration label. |
| 67 | american auctioneers llc | Summary | accepted | supported | 4 | good | Name, real estate, Centre AL, 11-50 and website match; no speculation. |
| 68 | american auctioneers llc | Outreach | corrected | partially_supported | 2 | acceptable | Mentions a 'demo workflow solution' but claims a specialization and benefits 'for your operations' and 'objectives', '[Your Name]'; does not say it i… |
| 69 | soul rehab massage | Summary | accepted | supported | 4 | good | Name, medical practice, Centennial CO, 1-10 and website match; seller_relevance describes GTMFlow's capabilities, not the lead. |
| 70 | soul rehab massage | Outreach | corrected | partially_supported | 2 | poor | Broken signature ('My name is from GTMFlow'), invented 'business needs', '[Your Name]'; no demonstration label. |
| 71 | equity solutions and investments, inc. | Summary | accepted ⚠ | supported | 4 | good | Name, 1-10, Los Angeles CA, founded 2023, real estate and the recorded website match; no speculation. |
| 72 | equity solutions and investments, inc. | Outreach | corrected ⚠ | partially_supported | 2 | acceptable | Founding year and location are supported, but it offers outreach 'tailored to your needs' and help 'with your operations', '[Your Name]'; no demonstr… |
| 73 | san antonio health care | Summary | accepted | supported | 4 | good | Name, hospital & health care, Reseda CA and 11-50 ('employing between 11 and 50 people') match; no speculation. |
| 74 | san antonio health care | Outreach | corrected | partially_supported | 2 | acceptable | Broken signature and invented alignment with 'your goals' and 'status in the healthcare industry'; mentions a 'demonstration offering' but not that i… |
| 75 | a bridge to properties | Summary | accepted | supported | 4 | good | Name, real estate, Athens GA, founded 2023, 1-10 and LinkedIn match; seller_relevance says it is a portfolio demonstration without a real customer se… |
| 76 | a bridge to properties | Outreach | corrected | partially_supported | 1 | poor | Body contains literal '\n' escape text; invents 'a new player' framing and alignment with 'your goals', '[Your Name]'; no demonstration label. |
| 77 | laurenwood nursing & rehabilation | Summary | corrected ⚠ | partially_supported | 3 | good | Silently changes the recorded company name ('Rehabilation' -> 'Rehabilitation') in both the summary and its evidence statement, so the evidence no lo… |
| 78 | laurenwood nursing & rehabilation | Outreach | corrected ⚠ | partially_supported | 2 | poor | Broken signature, invented specialization and care focus ('focusing on providing care'), needs to 'streamline your efforts', '[Your Name]'; no demons… |
| 79 | jls east, llc | Summary | corrected | partially_supported | 3 | acceptable | Facts are supported, but the hypotheses invent marketing needs and expansion, and seller_relevance asserts GTMFlow 'is relevant'. |
| 80 | jls east, llc | Outreach | corrected | partially_supported | 2 | acceptable | Invents 'synergies' and a need to optimize outreach; no demonstration label. |
| 81 | leslie r. capin, m.d., p.c | Summary | accepted | supported | 4 | good | Name, medical practice, Englewood CO and 51-200 match; no speculation. |
| 82 | leslie r. capin, m.d., p.c | Outreach | corrected | partially_supported | 2 | acceptable | Mentions a 'demonstration portfolio' but claims to 'support the unique needs of your medical practice' and a healthcare focus; does not say it is not… |
| 83 | better agent | Summary | accepted | supported | 4 | good | Name, real estate, Las Vegas NV, founded 2022, 1-10, website and LinkedIn all match; no speculation. |
| 84 | better agent | Outreach | corrected | partially_supported | 2 | acceptable | Claims a specialization, 'strategies tailored to your needs', asks about challenges, '[Your Name]'; no demonstration label. |
| 85 | herbert e todd | Summary | accepted ⚠ | supported | 3 | good | Name, medical practice, Lacey WA and 1-10 match; accurate but minimal. |
| 86 | herbert e todd | Outreach | corrected ⚠ | partially_supported | 2 | acceptable | Claims a specialization and that GTMFlow 'could help streamline your outreach efforts'; no demonstration label. |
| 87 | mg property llc | Summary | corrected | partially_supported | 3 | good | Facts are supported, but seller_relevance states flatly that GTMFlow 'is relevant' and 'could be beneficial'. |
| 88 | mg property llc | Outreach | corrected | partially_supported | 2 | poor | Invents research ('while researching real estate entities in Bixby'), asserts the company 'could benefit' from tools to streamline operations, claims… |
| 89 | alisa nowik stern, psy.d | Summary | corrected | partially_supported | 3 | acceptable | Says the named person 'operates a medical practice', an inference from the company name (the record lists a company in the medical practice industry)… |
| 90 | alisa nowik stern, psy.d | Outreach | corrected | partially_supported | 2 | acceptable | Addresses 'Dr. Nowik Stern' personally although no contact is on file, and claims to enhance the practice's efficiency; mentions a demonstration work… |
| 91 | piedmont securities llc | Summary | accepted ⚠ | supported | 4 | good | Name, real estate, Davidson NC and 1-10 match the record; no speculation. |
| 92 | piedmont securities llc | Outreach | corrected ⚠ | partially_supported | 2 | acceptable | Record facts are right, but it claims a specialization and asks about 'current needs'; '[Your Name]'; no demonstration label. |
| 93 | john e lufburrow, dds | Summary | accepted | supported | 3 | good | Name as recorded, medical practice, Havre de Grace MD and 1-10 match; seller_relevance is hedged ('could be relevant') and lists only profile capabil… |
| 94 | john e lufburrow, dds | Outreach | corrected | partially_supported | 2 | acceptable | Invents an 'interesting fit' from practice size and a 'focus on healthcare', and describes a capability GTMFlow does not list ('helps healthcare team… |
| 95 | clark capital group, llc | Summary | accepted ⚠ | supported | 4 | good | Name, 1-10 ('small'), Old Tappan NJ, real estate and the recorded website match; no speculation. |
| 96 | clark capital group, llc | Outreach | corrected ⚠ | unsupported | 1 | poor | Presents the prospect's website as GTMFlow's ('check us out further at hardmoneycentral.com'), claims a specialization, has no greeting structure or… |
| 97 | statewide properties llc | Summary | corrected ⚠ | partially_supported | 3 | good | Facts follow the record, but seller_relevance states flatly that GTMFlow 'is relevant' and 'can support efforts related to the lead'. |
| 98 | statewide properties llc | Outreach | corrected ⚠ | partially_supported | 2 | acceptable | Calls the rubric 'reliable' (an unsupported quality claim) and promises outreach 'tailored to your specific needs'; says 'portfolio demonstration' bu… |
| 99 | 555 cornelia condominium | Summary | accepted | supported | 3 | good | Name as recorded, Chicago IL, real estate, 11-50, website and LinkedIn match; seller_relevance is hedged ('may relate to') and capability-side. |
| 100 | 555 cornelia condominium | Outreach | corrected | partially_supported | 2 | acceptable | Claims a specialization and that GTMFlow 'could streamline your lead generation processes and improve engagement' (invented need and result); no demo… |

⚠ = flagged uncertain.

## Artifacts

- `backend/scripts/phase6_ai_review.py`: the `dump` and `build` tool (read-only against the database).
- `backend/data/ai_reviews/pilot-v1/decisions/batch-01.json` … `batch-10.json`: the AI reviewer's per-candidate decisions (committed).
- `backend/data/ai_reviews/pilot-v1/review_helpers.py`: formats a corrected demonstration outreach and writes a batch file. It only formats; it makes no decisions.
- `backend/data/ai_reviews/pilot-v1/ai-review-export.jsonl`: the machine-readable export, format `ai-review-export-v1`, sha256 `788038718cab4effa429c65865d95237996f50b36e31eb16ea293b53dc23815e`. It is git-ignored because it embeds cohort input snapshots; rebuild it with `python scripts/phase6_ai_review.py build --decisions data/ai_reviews/pilot-v1/decisions --out data/ai_reviews/pilot-v1/ai-review-export.jsonl`.
- Each export row carries:
  - candidate id, position, task, split, manifest version and company group key;
  - lead, identity and source record ids;
  - source output id and content hash, input hash and the full input snapshot, and the source output;
  - seller id, version, hash and kind; prompt, schema and model versions; `is_mock`;
  - the decision, the complete target and its content hash, the assessment, reason, issues and uncertainty;
  - `review_source=ai`, the reviewer model, `human_verified=false` and `human_review_time_ms=null`.

## Limitations

- **Not human-verified.** An AI reviewer can share blind spots with the generator; a human spot-check, starting with the uncertain list, is recommended before training.
- **Record facts only.** Correctness was judged only against the imported record (no external lookup). A misclassified record yields an "accepted" summary that faithfully repeats the misclassification.
- **Low-diversity outreach targets.** The 47 corrected outreach targets share one structure, following your #4 and #6 corrections, and differ only in company, industry and place. They teach grounding and labeling, not varied writing. Phase 7 should consider deduplicating or down-weighting them, or writing a few varied human examples.
- **Judgment calls.** Title-casing company names, accepting "small" or "N-M employees", and accepting hedged seller-side relevance follow the human precedents but remain judgment calls.
- **No review timing.** AI reviews carry no timing by design; timing statistics apply to human reviews only.
