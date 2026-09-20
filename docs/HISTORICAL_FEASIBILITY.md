# Before the Headline — historical language feasibility

## Decision

**Usable multilingual historical records exist in GDELT’s separate translated stream. The blank-language S3 sample was not a sufficient reason to abandon the investigation.**

**GO for a revised, bounded language-provenance and metadata-corpus experiment. Not yet GO for an English-attention lead claim.** Native-document language coverage, historical text identity, missing intervals and corpus cost still need explicit gates. No 28-day detector, held-out alert evaluation or historical UI was built; alert counts, success fractions and lead times are **not evaluated**, not zero.

The working Flask demo and its existing evidence remain unchanged. The historical work is separate from the MSC replay.

## 1. What was verified

The diagnostic timestamps were 00:00 and 00:15 UTC on March 1 in 2015, 2017 and 2019. They were chosen before inspecting stories, not for known English-language news outcomes.

### Native S3 sample

The initial unsigned `gdelt-open-data` sample retained 18 size-listed, uncompressed files: GKG, mentions and events for those six timestamps. Payload: **202,269,121 bytes**; initial response-body ledger including documentation/listings: **202,855,221 bytes**, below its 250 MB cap.

- 15,339 GKG records; every translation-language field blank and no translated-record marker.
- 42,557 mentions rows; every translation-language field blank.
- Missing language remained **unknown**, never inferred from country, hostname, URL or absence of `T`.
- Exact typed identifiers gave 15,335 web-document identifiers in GKG and 6,682 in mentions. Mentions rows are not articles.
- 1,636 GKG record IDs referred to different document identifiers. Do not deduplicate documents by record ID alone; retain raw object + physical row provenance.
- Exact collection/URL matching linked 42,495 of 42,514 web-mention rows to same-batch GKG records. This is a many-to-many event/document relationship, not proof of source independence or complete coverage.

See [native quality report](../historical/results/quality_report.json), [raw download manifest](../historical/results/download_manifest.json) and [missing-bin report](../historical/results/missing_bins.json). All 96 nominal object bins were listed on each of the three diagnostic dates for each table; only two consecutive bins per date were content-inspected.

### Separate translated stream

Inspected the official [latest translated pointer](https://data.gdeltproject.org/gdeltv2/lastupdate-translation.txt) and [translated master manifest](https://data.gdeltproject.org/gdeltv2/masterfilelist-translation.txt), alongside the cached official GKG 2.1 and Events/Mentions 2.0 codebooks.

The master manifest advertised 140,117,000 bytes. The scanner processed lines incrementally, retained a **47,972,352-byte prefix**, and stopped after finding all six historical GKG targets. It did not load or download the whole manifest. The latest pointer alone was not used to establish historical availability.

All six target ZIPs returned HTTP 200, matched their manifest sizes and MD5s, and were retained with local SHA-256 hashes. Their total compressed size was **50,244,906 bytes**; expanded content was **165,669,320 bytes**.

Results:

- **14,725 GKG records**, with **60 distinct reported source-language code values** and **14,320 distinct typed document identifiers**.
- Every record had an explicit `srclc` value and a translated `-T` record marker.
- Every record’s batch prefix and GKG date matched its requested historical timestamp.
- No exact same-batch typed-URL overlap with the sampled native records; these records do not fill in the native records’ missing labels by a URL join.
- Two records contain multiline final `EXTRASXML` fields. Eight physical continuation lines, including four nonblank fragments, are logged and reassembled into the final field rather than counted as documents. The language/date/identifier fields precede these continuations.

The codebook calls `srclc` the original source-language code and `eng` the translation-engine citation, **not an English-language label**. Reported codes are retained as supplied, including legacy/provider-specific values such as `axe`; 60 code values are not a separately validated census of 60 languages.

Evidence: [stream inventory](../historical/language_recovery/results/stream_inventory.json), [bounded manifest scan](../historical/language_recovery/results/manifest_scan.json), [translated download manifest](../historical/language_recovery/results/translated_download_manifest.json), [quality report](../historical/language_recovery/results/translated_quality.json), [record-level identifiers and URLs](../historical/language_recovery/results/translated_records.jsonl).

### Timestamp meanings

- GKG record-ID prefix: nominal 15-minute update batch. The codebook describes GKG `DATE` as publication-related, but says it is identical across a file; it matches the batch labels here. It is not independent publisher first-publication evidence.
- `MentionTimeDate`: current update batch. `EventTimeDate`: when the referenced event was first recorded by GDELT.
- Events `DATEADDED`: addition to the database; `Day`: coded event date, which can differ from ingestion date.
- S3 `LastModified`: mirror-object metadata, not article publication or historical availability.
- Current publisher publication/modification claims are separately recorded, can conflict with batch labels, and do not establish a publication lead.

## 2. Independent original-page language identification

A frozen, topic-independent URL-hash sample selected **96 distinct URLs across 95 normalized publisher hostnames**: 72 native and 24 translated. Each of the six timestamps contributed 12 native and four translated URLs; at most two URLs were selected per hostname. This is a diagnostic, publisher-capped sample, not a population estimate.

Collection used caching, public-address validation, TLS verification, robots checks, per-host spacing/request limits, timeouts, bounded bodies and no authentication. No browser scripts, source instructions or article links were executed. A transient article-read timeout was retried and recovered; it still produced insufficient text. First-pass results and the technical-retry policy remain available. Robots restrictions, HTTP failures, large error bodies and deferred crawl delays were not bypassed.

Language detection ran locally in an isolated `.language-venv`:

- `langid==1.1.6`; bundled model SHA-256 `e2d675b6d0f511cbb5317b4770c9289754b7338dec97fce660ff09c91b3d95a7`.
- `trafilatura==1.12.2`, precision-oriented body extraction, comments/tables excluded, generic fallback disabled; `numpy==1.26.4`.
- Input: extracted **original publisher body text**, never GKG translated text, entity lists, country, URL or HTML language metadata.
- Minimum 600 characters and 300 alphabetic characters, with article-structure checks. Full-text model ranking and three text-chunk checks are recorded. HTML `lang` and publisher dates are supporting evidence, not classifier inputs.
- Raw model log scores, margins and chunk agreement are **not calibrated probabilities**. These are disclosed diagnostic heuristics, not trained alert thresholds.

### Results after bounded technical recovery

| Selected cohort | URLs | HTTP success | Retrieval failure | Model-scored extracts | Current-language candidates after limited manual overrides |
|---|---:|---:|---:|---:|---:|
| Native | 72 | 37 | 35 / 72 = 48.6% | 29 | 29 |
| Translated | 24 | 12 | 12 / 24 = 50.0% | 7 | 6 |
| Total | 96 | 49 | 47 / 96 = 49.0% | 36 | 35 |

Of the 49 HTTP-success responses, seven yielded no article body, five were too short, and one redirected to a homepage. Automatic checks admitted 34 candidates and flagged two HTML/model disagreements. Manual review rejected one of the 34 as footer-dominated and resolved both disagreements for **current body language only**, leaving 35 diagnostic candidates. Most of those candidates were not manually audited; **35 is not a count of verified historical articles**.

All 29 model-scored native extracts were English. **43 native selections remained unclassified/unusable**, so this does not justify assigning English to the native archive. No substantive non-English body was established in the native subsample.

Manual inspection supported six substantial non-English bodies in the translated subsample: Russian, Arabic, Finnish, Ukrainian, Spanish and French. The Italian model result is excluded as adequate article-body evidence.

[Per-publisher failure rates](../historical/language_recovery/results/failure_rates_by_publisher.csv) and [rates by stream, inferred language and archived metadata language](../historical/language_recovery/results/page_quality_summary.json) retain the denominators. Publisher groups usually have only one selected URL. Retrieval failure rates **by inferred language are unidentifiable** when no text was recovered; they are null, not zero. Rates by archived language can be calculated for the 24 translated selections with prior `srclc` labels, but are very small-sample diagnostics.

### Manual audit and disagreements

The [15-case stratified audit](../historical/language_recovery/results/manual_audit.json) covered every non-English model result, both metadata disagreements, English examples from all three years and rejected cases.

- **MTV Lebanon, page-046:** substantial Arabic body, model `ar`, HTML `en`. Body inspection supports Arabic; no usable structured publication date was recovered.
- **HuffPost Quebec archive, page-079:** French body, model `fr`, HTML `en`. The migrated archive has 2019 publication/modification claims. HTML appears to reflect a template, not the article language.
- **TuttoNapoli, page-061:** the model correctly recognized Italian in an extract dominated by legal/advertising footer text. The actual article/tweet in cached HTML is short. This is an extraction-quality false acceptance, not validated article-language evidence.
- **ZeroHedge, page-033:** soft 404; no article label assigned despite English HTML metadata.
- **Imphal Free Press, page-086:** historical URL became a current homepage; navigation and a 2026 item are not the historical article.

No model/manual language disagreement was found among the **11 substantive bodies reviewed**. This was a purposive, unblinded diagnostic audit—not an accuracy estimate. The observed extraction failure demonstrates why high model scores alone are insufficient.

Full source identifiers, URLs, raw scores, lengths, dates, input hashes and explicit outcomes are in [page evidence CSV](../historical/language_recovery/results/page_language_evidence.csv), [adjudicated records](../historical/language_recovery/results/adjudicated_page_results.json), [model configuration](../historical/language_recovery/results/language_model.json) and [review packets](../historical/language_recovery/results/review_packets/).

### Can current pages represent historical articles?

**Sometimes plausibly, but not automatically.** Stable headlines/URLs, substantive period-consistent text and old publisher claims supported several reviewed identities. Other pages migrated hosts, lost their bodies, became homepages, or claim modifications in 2024/2026. The Russian example’s present publication claim is later than its archive batch label; date-only agreement is not enough for timing claims.

No independent as-of body snapshot was verified. The language results describe content retrieved in September 2026, not guaranteed unchanged 2015–2019 text. Even a plausible historical article identity is not a first-publication timestamp or real-time availability proof.

## 3. Recommended next experiment and cost

### Do not download the full 28-day GKG corpus

For March 1–28, 2015, the cached translated manifest alone lists **30,725,239,896 compressed GKG bytes**; native GKG adds at least **22,744,701,176 listed bytes** in the captured native prefix. This already exceeds the 5 GB ceiling by an order of magnitude. The native scan stopped when the mentions/events grid was complete; its unread final GKG slot is not a confirmed missing object.

No full window was downloaded and no time-bin subsample was substituted as an evaluation corpus.

### Revised metadata-first corpus

A narrower corpus of **distinct event-bearing document identifiers from native and translated mentions, linked to event metadata**, is much smaller:

| March 1–28, 2015 metadata | Listed compressed bytes |
|---|---:|
| Native mentions + events | 1,000,920,359 |
| Translated mentions + events | 538,149,551 |
| Combined | **1,539,069,910 (1.539 GB)** |

This is **not all news**, and mentions rows must never be counted as distinct articles. Native compressed mentions/events were downloaded for one batch and their expanded bytes matched the existing S3 files exactly. The translated mentions sample has the expected 16-column schema and explicit original-language metadata; both event samples have 61 columns and matching update labels.

The native window has all 2,688 expected bins listed. Six translated update bins are missing from the manifest; direct HEAD checks returned **404 for both mentions and events** at each. Keep them unknown, exclude or mark unscorable any baseline/outcome window depending on them, or choose another period using coverage-only criteria before topic selection. Do not fill them with zeros.

Storage: retaining these ZIPs requires about **1.54 GB plus manifests and projections**. Fully expanded TSV storage is roughly **8.61 GB**, extrapolated from one native/translated sample per table—not measured for the full window. Stream parsing avoids retaining that expanded duplicate. Measure projection growth during the pilot before setting a final disk reservation.

Evidence: [corpus transfer estimates](../historical/language_recovery/results/corpus_transfer_estimates.json), [native window inventory](../historical/language_recovery/results/native_metadata_window.json), [verified metadata candidate](../historical/language_recovery/results/metadata_corpus_candidate.json), [missing-object probes](../historical/language_recovery/results/candidate_missing_object_probes.json).

### Required gates before an attention test

1. **Approve the changed corpus definition.** Study event-bearing observed documents, not all GKG/news coverage. Collect all available bins in the declared contiguous window; preserve genuine gaps. Deduplicate by typed document identifier, not mention row or GKG record ID; retain URL/version ambiguity and possible syndication.
2. **Run a historical-language provenance pilot before scaling body collection.** Preselect an all-topic URL cohort across timestamps and publishers, then obtain contemporaneous archived bodies or sufficiently defensible article-version evidence. Begin with at most 200 URLs and an explicit additional page-body transfer ceiling, for example 250 MB. Current-page-only and ambiguous cases remain separate.
3. **Freeze one document eligibility/language-labeling process for the entire cohort.** Apply it to topic matches and non-topic documents alike. An English/non-English comparison needs defensible English labels; do not promote all blank native records to English. A common independently verified body-labeling cohort is the conservative option; archived `srclc` can cross-check it. Do not mix body-fetched topic counts with unfiltered archive denominators.
4. **Define the denominator honestly.** Count all usable, distinct language-labelled documents from that same collection/cohort and bin. Label shares as shares of that observed eligible archive/cohort, not all news. Publish retrieval/label coverage and publisher dependence; insufficient exposure is unknown/unscorable, not zero. This pilot does not supply population denominators.
5. **Only then calibrate and evaluate.** March 1–14 would be calibration and March 15–28 held out if that window is retained. Select topics, volume/domain/sustained-activity thresholds, English-increase definition and horizon using calibration only. Freeze them, inspect story identity, evaluate every held-out alert including failures/unscorable cases, and compare a raw-count baseline. Small samples remain exploratory; batch-timing differences are not publication leads or causality.

The immediate recommendation is the language-provenance/metadata pilot, not fitting a detector or adding a historical alert UI. If comparable English exposure cannot be established within budget, report that specific limitation rather than treating unlabelled records as English or manufacturing a lead.

## 4. Reproduction and verification

Research response-body ledgers total **315,857,196 bytes**: 202,855,221 initially, 100,639,004 for recovery bulk/manifests/package-provenance metadata, and 12,362,971 for publisher requests. Cached software artifacts add **41,092,519 bytes** separately. These are retained response-body/artifact sizes, not HTTP/TLS wire accounting. Both data passes stayed below their declared 250 MB limits; the overall 5 GB limit was not approached. No DOC API, paid infrastructure or runtime LLM was used.

The original Flask `.venv`, application requirements, templates and evidence were preserved. Language dependencies are isolated and exactly pinned in [requirements-language.txt](../research/requirements-language.txt); package hashes/release ages are recorded. `urllib3` emits an import-time LibreSSL compatibility warning on this Python build; it was not used for publisher fetching, and extraction/classification were network-blocked. TLS verification was not disabled.

### Cached analysis — no network required

Run from the repository root:

```sh
.venv/bin/python -m research.language_recovery audit --offline
.venv/bin/python -m research.language_recovery estimate --offline
.venv/bin/python -m research.language_recovery candidate-check --offline
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .language-venv/bin/python -m research.language_pages classify --offline
.language-venv/bin/python -m research.language_pages summarize --offline
.venv/bin/python -m research.language_recovery findings --offline
.language-venv/bin/python -m unittest -v tests.test_language_recovery
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .language-venv/bin/python -m research.verify_language_offline
```

The verification harness blocks socket connections, DNS and urllib HTTP calls, runs cached analysis twice, compares derived bytes, checks raw-cache/ledger immutability and verifies the preserved demo hashes. It does not disable system-wide networking. [Verification record](../historical/language_recovery/results/offline_verification.json).

### Collection commands and dependency restoration

The completed collector is cache-first. Re-running it reuses retained responses; no fresh crawling is needed to reproduce the findings.

```sh
.venv/bin/python -m research.language_recovery inspect
.venv/bin/python -m research.language_recovery manifest
.venv/bin/python -m research.language_recovery download
.venv/bin/python -m research.language_recovery audit --offline
.venv/bin/python -m research.language_recovery plan-pages --offline
.language-venv/bin/python -m research.language_pages fetch
.language-venv/bin/python -m research.language_pages retry-technical
```

To restore the isolated language environment from retained package files:

```sh
python3 -m venv .language-venv
.language-venv/bin/python -m pip install --no-index --find-links historical/language_recovery/packages pip==25.2 setuptools==75.8.0 wheel==0.45.1
.language-venv/bin/python -m pip install --no-index --no-build-isolation --find-links historical/language_recovery/packages -r requirements-language.txt
```

The cached binary wheels target the tested macOS ARM64/Python 3.9 environment. Other platforms need compatible wheels with the same vetted versions. The source collection and full-window sizing commands are in `research/historical_experiment.py` and `research/language_recovery.py`; no command in this milestone downloads the proposed full 28-day corpus.

[Machine-readable findings](../historical/language_recovery/results/findings_summary.json) · [quality figure](../historical/language_recovery/results/language_feasibility.svg)
