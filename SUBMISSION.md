# Signal / Noise

## One-sentence pitch

**Signal / Noise is an evidence-inspection prototype that helps investigators inspect a news spike and separate relevant coverage, repeated reporting, and uncertainty using traceable cached sources.**

## Problem

A rise in news matches is not necessarily a new story, independent corroboration, or an early signal. Broad queries can mix different incidents, syndicated text, translated editions and unrelated material. Publication labels, platform observation times and retrieval times are also different clocks. A compelling chart can hide those distinctions.

For Voloridge’s “Signal in the Noise” challenge, we built a small, reproducible investigation that makes these distinctions visible instead of claiming a predictive result we could not validate.

## Implemented approach

The local demo follows one complete investigation around the **reported MSC Ulsan III / Novorossiysk booking suspension**:

1. Show a broad shipping-query aggregate as context, explicitly separate from the inspected sample.
2. Browse cached pages with publisher, language, discovery provenance and recorded relevance labels.
3. Filter other stories and possible shared reporting; separately fold a confirmed matching-text group. Counters always describe the selected inventory, not the news population or independent story origins.
4. Replay the three retained pages by publisher-claimed date. Date-only evidence remains date-only; missing observations are not zeros.
5. Open source panels containing cached excerpts, URLs, timestamp provenance, relevance rationale and unresolved details.

The relevance and text-sharing judgments are recorded, agent-assisted inspections of this case—not an automatic multilingual event classifier. No live LLM is used by the application.

## Actual data and sample sizes

These are different populations and must not be treated as a funnel:

| Quantity | What it actually means |
|---|---|
| **694** | Matching articles in one cached **English shipping-query TimelineVolRaw daily bin**, August 31, 2026. This is an aggregate count, not an ArticleList length or a count of unique stories. We have not established what caused this spike. |
| **22** | Returned Spanish article-metadata records in one archived ArticleList response, requested with a cap of 250. Evidence discovery only; not a population estimate, even though fewer than 250 records were returned. |
| **32** | Unique-URL records in the curated evidence inventory: 22 GDELT ArticleList records, 9 externally discovered records and 1 bulk-metadata hint. |
| **13** | Publisher pages whose bodies were inspected: **6 Spanish GDELT-result pages + 7 external/contextual pages**. They are not a representative sample of the 694 English matches. |
| **3 / 10** | Of those 13 inspected pages, 3 explicitly concern the MSC suspension; 10 concern other stories. “Other” is relative to this specific case, not necessarily an invalid broad shipping match. |
| **5 English / 7 Spanish / 1 Chinese** | Languages of the 13 inspected pages—not a balanced language sample. |
| **2 pages / 1 group** | The confirmed copied-text example: La Verdad and Diario Vasco have 22 identical extracted lines in the reviewed spans. Folding yields 12 cards from 13 pages; it does not measure independent reporting. |
| **19** | Uninspected/unavailable inventory records: 16 GDELT metadata-only records, 2 cached publisher HTTP 401 responses and 1 GKG metadata hint. |

Supporting feasibility data includes 24 returned daily bins for English shipping and four diagnostic GKG ZIP files totaling **20,850,439 compressed bytes**. The bulk slices are not a complete time series or substitutes for DOC language denominators. The three retained MSC pages were externally discovered; their membership and observation times in the GDELT query results remain unverified.

## Technical architecture

- **Python + Flask 3.1.2**, Jinja template, local CSS and plain browser JavaScript. No Node build step, API keys, database service or LLM runtime dependency.
- Frozen JSON artifacts and cached publisher-text excerpts are loaded locally. Python supplies deterministic filters and counts; the client renders results without recalculating population statistics.
- Source excerpts are rendered as text, never executed as publisher HTML. HTTP(S)-only source links, a restrictive Content Security Policy, loopback binding and disabled debug mode limit the local demo’s exposure.
- The original response cache preserves request URLs, timestamps, HTTP outcomes and SHA-256 hashes. The judging audit checked cached bodies, extracted text, displayed excerpts, timestamp fields and the aggregate’s source bin.
- Browser verification exercises the documented walkthrough against a **fresh application process and fresh browser context**, denying outbound server connections/DNS and non-local browser requests. Loopback HTTP is permitted. System-wide network settings are not changed.

## Verified findings

- The three retained publisher pages explicitly connect MSC, the named vessel, Novorossiysk and a booking/service suspension. This establishes what the pages report—not independent confirmation of the event.
- The inspected La Verdad/Diario Vasco passages match. Other possible shared reporting, including upstream overlap among the MSC pages, remains unconfirmed. No copying direction or earliest origin is inferred.
- Cached English and Spanish publisher metadata supplies explicit offsets; the Chinese page supplies only a date. Publisher claims are not certified first-publication times.
- The English account describes departure from Novorossiysk, while the Chinese account describes travel toward it. Attack timing and upstream provenance also remain uncertain.
- Bulk records dated September 18 exist despite the earlier DOC responses ending September 13. That endpoint is not evidence of a GDELT-wide halt; its specific cause and interval completeness remain unresolved.
- The final judging audit screened all 19 additional inventory candidates. None has a successful cached publisher body available for inspection, so **no pages were promoted and no synthetic examples were added**. An additional matching-headline pair was documented as a possible metadata-level relationship only.

The audit verifies provenance and faithful presentation of the cached reporting. It does not independently establish the truth of publisher claims.

## Verification and judging readiness

**17 tests passed:** 10 data/API tests, 6 browser regression tests and one fresh-start offline judging-path test. They cover artifact counts, all filter combinations, confirmed-versus-possible sharing, timestamp precision, source panels, focus restoration, mobile layout, literal rendering of hostile source text and the documented reset procedure.

The fresh-start run recorded no external browser requests, no blocked outbound server attempts and no JavaScript errors. It also checked that protected cached evidence was unchanged. Presentation fallback screenshots capture the actual walkthrough states—not synthetic mockups.

- [90-second walkthrough and startup/reset instructions](DEMO.md)
- [Presentation fallback screenshots](results/judging/screenshots.md)
- [Cache-only claim and candidate audit](results/judging/claim_audit.md)
- [Machine-readable offline verification](results/judging/offline_walkthrough.json)

## Limitations and future work

This is an **evidence-inspection prototype with a narrow story replay**, not a live radar or independently verified account of the incident. The sample is small, curated, incomplete and mixed-provenance. The original carrier notice and upstream source reports were not verified. Distinct URLs and languages do not establish independent reporting.

Comparable full-window topic counts and per-language denominators from the same corpus/bins were not recovered. No language lead, lead time, first publication, alert precision, recall or false-alarm rate has been validated. Retrospective caches do not reproduce what a system could have known at each historical moment.

One non-story record has a raw `+0900` timestamp whose legacy derived UTC field is empty. The UI now says the conversion was not supplied in the cached artifact, rather than falsely saying no explicit offset exists. Raw values remain visible; no timestamp is invented.

**Early warning is unvalidated future work.** It would require reliable, complete polling snapshots, aligned language denominators, stronger event-identity and syndication validation, preceding-only alert baselines, and held-out evaluation with false-alarm reporting. Those capabilities are not claimed by this submission.

## How Devin contributed

Under the user’s scope and evidence constraints, Devin explored GDELT’s contracts and Voloridge’s tools; authored and ran reproducible cached feasibility probes; inspected publisher evidence and recorded provisional classifications; implemented the Flask investigation flow; wrote and ran count, timestamp, security and offline tests; and prepared the judging audit, walkthrough and fallback screenshots.

Devin was a development and analysis assistant, **not a live news-analysis dependency or an independent fact-checking authority**. The user set the product direction and required the separation between observed evidence and unsupported early-warning claims.
