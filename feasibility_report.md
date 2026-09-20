# Before the Headline — data feasibility milestone

**Snapshot: 19 September 2026. Decision: NO-GO for a live cross-language radar; UI remains gated.**

The reproducible prototype, response cache, charts and evidence review are delivered. The requested **normalized, comparable timelines for all three topics are not yet available**: repeated HTTP 429 responses prevented eight of nine topic/language aggregate timelines and two of three language denominators from being obtained. Missing responses are not zero coverage. The comparison charts deliberately withhold their lines rather than display misleading comparisons.

There is one measured candidate increase, an independently cross-checked English shipping timeline, and genuine GDELT-linked Spanish article evidence. The final cache audit records **4 successful API responses and 28 HTTP 429 responses across 32 network attempts**; cached replays are not counted as new attempts. No non-English reporting lead, event prediction, or lead time has been established.

## Reproduce without network access

From this directory, using Python 3.9+ (standard library only):

```sh
python3 feasibility.py replay
python3 -m unittest -v test_feasibility.py
```

`replay` reconstructs the request snapshots, analysis, evidence exports, source-page extracts, cache audit and SVG charts from cached response bytes. It never refreshes the network. The report and `results/story_review.json` are the manual review of this frozen capture, not automatically updated conclusions for future live runs.

Verification passed: all **7 unit tests**, Python compilation, XML parsing of all **5 SVGs**, and two offline replays with network access patched to raise an error. All 13 checked machine-readable derived artifacts were byte-identical across those replays.

Online collection is explicit and sequential:

```sh
python3 feasibility.py docs
python3 feasibility.py collect
python3 feasibility.py analyze
python3 feasibility.py evidence
python3 feasibility.py sources
python3 feasibility.py audit
python3 render_charts.py
```

Cached successes **and failures** are reused by default. A bounded retry of selected failed requests is available:

```sh
python3 feasibility.py retry-failed --only denominator_english denominator_spanish semiconductors_english energy_english
```

Each name is a file stem in `results/requests/`. A retry makes at most one new attempt per selected failed request and preserves the older response. Re-run analysis/evidence after new data arrives. `--refresh` explicitly makes new requests; do not use it for frozen replay. Do not run network collectors concurrently. The experiment's dates and topic queries are fixed in `feasibility.py` to avoid a moving-window reproduction.

## Experiment and challenge context

The supplied Voloridge PDF's “Signal in the Noise” challenge rewards originality, technical excellence, insight and execution using public datasets. This milestone tests whether the signal and evidence are defensible before investing in a UI. The PDF text is preserved in `challenge_context.txt`.

- Requested interval: **2026-08-21 00:00:00 through 2026-09-18 00:00:00 UTC**. Analysis uses the half-open daily grid, August 21–September 17, 28 days.
- Languages: English, Spanish and Chinese. These are coverage filters, not country filters or proof of a story's original reporting language.
- Same English query for every language, over GDELT's English-searchable/translated text; append `sourcelang:english`, `sourcelang:spanish` or `sourcelang:chinese`.
- Topic queries were fixed before the multi-topic collection. No smoothing, no article-list-derived population counts, and no synthetic observations in results.

| Topic | Query before the language filter |
|---|---|
| Shipping disruptions | `(shipping OR freight OR "container ship" OR "Red Sea" OR "Suez Canal") (disruption OR attack OR closure OR congestion OR rerouting)` |
| Semiconductor supply chains | `(semiconductor OR semiconductors OR chipmaker OR "chip supply" OR TSMC) (supply OR shortage OR factory OR export OR production)` |
| Energy infrastructure | `("power grid" OR pipeline OR refinery OR "LNG terminal" OR "power plant") (outage OR attack OR shutdown OR disruption OR explosion)` |

### Obtained aggregate data

| Topic | English | Spanish | Chinese |
|---|---|---|---|
| Shipping | HTTP 200, 24 daily bins; separate hourly probe also succeeded | HTTP 429 | HTTP 429 |
| Semiconductors | HTTP 429, including targeted retry | HTTP 429 | HTTP 429 |
| Energy | HTTP 429, including targeted retry | HTTP 429 | HTTP 429 |
| All-topic language denominator | HTTP 429, including targeted retry | HTTP 429, including targeted retry | HTTP 200, 24 daily bins |

All nine topic/language queries were attempted. A failed fetch cannot establish that a topic has no coverage or no candidate increase. The semiconductor and energy time-series feasibility assessments remain blocked, rather than negative findings about those topics.

## API contracts: observed versus documented

| Property | Evidence and status |
|---|---|
| Public access | Unauthenticated requests can succeed, but most returned HTTP 429. Failures persisted with requests spaced beyond the error message's five-second interval, and an article-list request still failed after a three-minute cooldown. Some other requests succeeded. Cause of throttling is not established. Exact attempts, headers and retrieval times are in the cache. |
| Aggregate counts | DOC `TimelineVolRaw` is documented to return distinct matching articles per bin, with a `norm` field. Counts used here come only from this mode. Distinct articles are not distinct stories or independent reporting. |
| Daily resolution | The 28-day request returned `date_resolution: day` and 24 bins, August 21–September 13. Both successful daily responses end on September 13. September 14–17 are absent. September 13 is conservatively excluded from alerts as the latest available, potentially incomplete bin; earlier bins are not certified complete. |
| Hourly resolution and boundaries | The August 29–September 2 probe returned `date_resolution: hour`, 92 bins: 91 inside the 96-hour half-open grid, plus a bin exactly at September 2 00:00Z. Five interior hours are absent. Thus an end boundary must not blindly be assumed exclusive. After excluding the endpoint, sums of returned hourly counts equal daily counts for all four days. This is consistent with omitted zero-match hours, but does not establish monitoring completeness. No missing hour is silently filled with zero. |
| Fifteen-minute resolution | Documentation specifies 15-minute bins for windows shorter than 72 hours, hourly from 72 hours to a week, and daily above a week. The 24-hour timeline probe returned 429, so fifteen-minute **timeline** behavior is not empirically verified. Article `seendate` values in the successful sample are quarter-hour aligned, a separate observation. |
| Timestamp meaning | Returned timeline and `seendate` strings end in `Z`; they are handled as UTC. Bin labels are not event occurrence times. GDELT's master-timestamp policy uses retrieval-completion time for open-web content, whereas older DOC documentation loosely says “publication” dates. Treat `seendate` as a GDELT observation/index-time field, not certified first publication or exact API availability; a DOC-specific latency mapping remains unverified. Publisher creation, modification, event time, GDELT observation and this prototype's fetch time are kept distinct. |
| Language breadth | DOC documentation describes 65 machine-translated languages; the language lookup response is cached. English aggregate data, Chinese language-total data and Spanish article records were actually obtained. Full language breadth, recall and balanced source coverage were not verified. The PDF's “over 100 languages” describes GDELT broadly, not this API contract. |
| Result limits | Documentation says ArticleList defaults to 75 and supports up to 250; `maxrecords` does not limit timeline modes. Requests for 250 and 300 on a broad test both returned 429, so actual cap enforcement was not measured. The successful targeted ArticleList response returned 22 evidence records with a requested cap of 250. **That is a sample size, never an estimate of the matching population—even below the cap.** |
| Historical reach | The original DOC documentation says three months; a later official update describes search back to January 2017 and special restrictions for non-timeline modes. Current historical reach was not verified. Only the explicit recent interval above was tested. |

The challenge's own [GDELT README](https://voloridge-hack-mit-2026.s3.us-east-1.amazonaws.com/src/gdelt/README.md) says its S3 mirror is no longer updated: v2 ends April 16, 2019 and v1 ends September 18, 2019. This is a documented limitation, not an independently enumerated bucket audit. That mirror is not a fallback for this September 2026 live demo. Event dates, `DATEADDED`, event mentions and DOC article counts must not be substituted for one another.

## Normalization: comparisons withheld, not guessed

Let `N(topic, language, day)` be an aggregate matching-article count, `G(day)` the API's `norm`, and `D(language, day)` the aggregate count returned by a language-only query.

- Documented `TimelineVol` percentage: `100 * N / G`, a share of **all monitored coverage**, not that language's news agenda.
- Intended comparison: `10,000 * N / D`, matching articles per 10,000 monitored articles in the **same language and bin**.
- The English shipping response and Chinese language-only response have identical `norm` values on all 24 shared days. Chinese `value` is different from `norm`. This supports the documented global interpretation and rules out treating `norm` as the Chinese language denominator in this sample.
- English and Spanish `D` values were not obtained. The three direct `TimelineVol` consistency probes and `TimelineLang` probe also failed. The pipeline therefore withholds every cross-language normalized series and alert. `TimelineLang` is not silently presumed to have language-specific denominators.
- When data is available, the gate checks daily resolution, positive language totals, `N <= D <= G`, matching `norm` values, cross-language global-normalizer agreement and `TimelineVol = 100*N/G` to the configured rounding tolerance. Missing denominator data remains null, not zero.

**Observed counterexample to raw-count interpretation:** English shipping rose from **218 articles on August 25 to 280 on August 26**, while `100*count/norm` fell from **0.164919% to 0.155702%**. A raw count increase need not be an attention-share increase. Those ratios are not same-language-normalized attention.

## Candidate increase: shipping, not an established early warning

One exploratory raw-count candidate passed the frozen rule:

| Series | Candidate day | Aggregate count | Preceding seven-day mean | Sample SD | Threshold |
|---|---|---:|---:|---:|---:|
| English shipping | August 31 | 694 | 190.571 | 66.156 | 389.039 |

The baseline uses **August 24–30 only**. A raw candidate requires at least 10 articles and a value strictly above `max(2*mean, mean + 3*sample_sd, mean + 10)`. The normalized rule, currently withheld, uses the analogous maximum with a +2-per-10,000 floor and the same minimum numerator count. Both require seven consecutive preceding usable daily observations; missing bins break the baseline. The current observation never enters its baseline. The latest returned bin is excluded. No centered smoothing or future observations are used.

The hourly probe independently sums to **694** for August 31. This checks the aggregate, not its story composition. The inspected example is selected retrospectively; the rule has no calibrated false-positive rate, no weekday/seasonality adjustment and no multiplicity correction. Retrospective GDELT responses are not point-in-time snapshots of what an alerting system could have known then. There is no claimed advance warning or lead interval.

## Linked article evidence and counterexamples

### Actually returned by GDELT

All returned records, URLs, titles, `seendate`, language and source metadata are exported in `results/article_evidence.json`; the original response is `results/requests/evidence_shipping_spanish_20260831.json`. The other five candidate/prior-day language evidence requests failed. I inspected six selected publisher pages from the successful Spanish response, deliberately including relevance and duplication counterexamples rather than claiming a representative precision estimate.

- [Panama Canal reservation changes — Gestión](https://gestion.pe/mundo/internacional/canal-de-panama-flexibiliza-sistema-de-reservas-antes-que-arranque-restriccion-al-transito-noticia/): a genuine maritime story about drought restrictions and bookings, not the Black Sea or East China incident. GDELT `seendate` is August 31 01:45Z; the page displays August 30 20:30 without a timezone in the visible label. Comparing calendar dates alone would be misleading.
- [Larak mine-launcher attack — Negocios](https://www.negocios.com/articulo/geopolitica/eeuu-ataca-larak-iran-preparaba-minas-barcos-comerciales/20260831065711492971.html): another shipping-related story, concerning military action near Hormuz. Its core event is different from a tanker struck near Khasab, even though those narratives share regional context.
- [La Verdad](https://www.laverdad.es/internacional/oriente-proximo/jamenei-reclama-unidad-islamica-israel-20260830224351-ntrc.html) and [Diario Vasco](https://www.diariovasco.com/internacional/oriente-proximo/jamenei-reclama-unidad-islamica-israel-20260830224351-ntrc.html): matching headline, byline, displayed original/update dates and inspected paragraphs. This is strong syndicated-copy evidence, not independent story origination. Their GDELT observation timestamps differ, but that difference is **not** a reporting lead.
- [Quito traffic cameras — El Diario](https://www.eldiario.ec/quito/camaras-con-ia-analizaran-el-transito-y-apoyaran-la-gestion-de-semaforos-en-quito-31082026/) and [Microsoft email outage — Expreso](https://www.expreso.ec/ciencia-y-tecnologia/outlook-no-funciona-no-hay-puedas-recomienda-microsoft-294134.html): the fetched primary stories are not maritime shipping disruptions, despite appearing in this query's GDELT results. These are observed relevance counterexamples. Translation, boilerplate or index-time page differences are possible explanations, not verified root causes.

This mixed Spanish evidence cannot attribute an **English** aggregate increase to any one story. No available data establishes that the language timelines concern the same incident.

### Additional publisher evidence, explicitly not GDELT-confirmed

External web discovery found a useful narrow story across languages:

| Page | Displayed publisher date | Identity assessment |
|---|---|---|
| [India Shipping News, English](https://indiashippingnews.com/international-shipping/msc-suspends-russian-port-of-novorossiysks-bookings-after-attack-on-msc-ulsan-iii/) | August 31 | MSC Ulsan III, drone attack, Novorossiysk booking suspension |
| [Prensa Latina, Spanish](https://www.prensa-latina.cu/2026/09/01/navieras-suspenden-operaciones-con-rusia-por-riesgos-en-mar-negro-2/) | September 1, 06:02; timezone not displayed | Same MSC substory within a broader multi-carrier roundup |
| [JCtrans, Chinese](https://www.jctrans.com/cn/news/14074/) | September 1 | Same entities and operational consequence; explicitly compiles Bloomberg and Lloyd's List |

The core story matches, but the English page says the ship was departing Novorossiysk while the Chinese page says it was heading toward it. The English report attributes an August 25 attack date to an adviser; Spanish dates the suspension to August 26; Chinese says the precise attack time remains unclear. This distinction between event, publication and observation time matters. These selected dates do not demonstrate a non-English lead, and their membership in GDELT has not been confirmed.

Other cached external pages sharpen the feasibility assessment:

- [Tradlinx on Shanghai/Ningbo congestion after Saudel](https://blogs.tradlinx.com/4-31m-teu-is-waiting-to-berth-as-china-ports-recover-from-saudel/) and [gCaptain on a tanker hit near Khasab](https://gcaptain.com/tanker-incident-involving-military-forces-reported-off-oman-amid-u-s-iran-escalation/) both display August 31. Different events can co-occur within “shipping disruptions”; neither page has been established as a contributor to the measured increase.
- [The Asia Business Daily on semiconductor export growth](https://www.asiae.co.kr/en/article/2026091109312288846) displays September 11. Positive trade growth is not a supply disruption, and the English page explicitly discloses AI translation. Language of an edition is not necessarily reporting origin. No semiconductor coverage increase could be measured in this run.
- [OilPrice on the Yaroslavl refinery attack](https://oilprice.com/Latest-Energy-News/World-News/Ukraine-Hits-Refinery-as-Moscow-Prepares-to-Extend-Diesel-Export-Ban.html) displays September 17, 09:30 CDT, inside the requested range but beyond the successful daily responses' tail. Missing API bins do not mean no relevant news existed. Two Reuters pages returned HTTP 401 and were not inspected as full articles; those failures are cached too.

## Recommendation and remaining gate

**Choose shipping disruptions, narrowed to the MSC Ulsan III / Novorossiysk booking-suspension story, for a conditional evidence-led replay demo.** It has accessible multilingual pages, recognizable entities and a concrete operational outcome. Broad shipping also yielded a real aggregate candidate worth inspecting. This is not a statistically supported ranking over semiconductor or energy signals: those timelines were unavailable, and the broad shipping increase has not been attributed to the MSC story.

Do not pitch “Chinese/Spanish news predicted the English headline.” The honest demo is: **detect a coverage candidate, expose conflicting/duplicated evidence, and show why a lead claim is withheld.**

Before this milestone can pass for the intended cross-language radar:

1. Obtain uncapped aggregate numerators and positive, aligned language denominators for all selected topics/languages; pass the normalization checks.
2. Verify current freshness and boundary/completeness behavior, including a successful short-window probe. Persist real polling snapshots before claiming point-in-time alert availability.
3. Retrieve matched GDELT-linked articles around a candidate in multiple languages; validate event identity, translation/syndication provenance and publisher timestamp timezones. Treat unavailable earlier coverage as unknown, not absence.
4. Tighten broad queries using named entities and evaluate held-out relevance and false alerts without tuning thresholds on the demo spike. Do not replace unavailable counts with capped search lengths or event-mention totals.

If DOC access remains unreliable, a separately scoped bulk-data/warehouse approach could compute unique-document counts and language totals. It needs a verified schema, access/budget and completeness audit; the stale challenge S3 mirror is not sufficient. **No UI has been built.**

## Artifacts and provenance

- `feasibility.py`: collection, immutable per-attempt caching, analysis, bounded retries, source extraction, audit and offline replay.
- `test_feasibility.py`: synthetic unit-test fixtures confined to temporary directories; tests for preceding-only baselines, future-value invariance, missing-bin handling, fail-closed denominators and separation of article lists from aggregate counts.
- `results/timeline.csv` / `.json`: aligned daily grid, raw counts where obtained, null unavailable values and explicit states.
- `results/charts/raw_shipping_english.svg`: measured raw timeline, candidate marker, excluded latest bin and gap shading. **Within-language audit only.**
- `results/charts/shipping.svg`, `semiconductors.svg`, `energy.svg`: shared-grid comparison outputs explicitly withheld because normalization is unavailable.
- `results/charts/availability.svg`: missing and unverified cells, not a zero-coverage heat map.
- `results/raw_candidates.json`, `normalization_checks.json`, `coverage_inventory.json`, `api_diagnostics.json`, `audit.json`: machine-readable findings and failed checks.
- `results/article_evidence.json`, `evidence_manifest.json`, `source_manifest.json`, `story_review.json`, `pages/`: linked evidence, sampling/cap warnings, fetch outcomes, manual judgments and extracted page text.
- `cache/<SHA256(request URL)>/<attempt timestamp>.body` and `.json`: fetched API/documentation/article response bytes, URL, HTTP status/headers, retrieval time, errors and SHA-256; previous attempts retained. Redirect responses encountered by the final fetcher are also cached. `results/cache_manifest.json` audits all cached body hashes. Web search was used only to discover external URLs; those pages were then fetched into this cache and kept separate from GDELT evidence.

### Primary contract references, cached locally

- [DOC 2.0 API documentation](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/): modes, counts, normalization, language filters, resolution and result limits.
- [Later DOC historical-search update](https://blog.gdeltproject.org/doc-2-0-updates-1-5-year-searching-and-updated-mobile-interface/): explains why the older three-month wording is not a current verified retention guarantee.
- [GDELT master timestamp policy](https://blog.gdeltproject.org/a-behind-the-scenes-look-at-how-we-think-about-master-file-formats-and-timestamping/): retrieval versus source-claimed timestamps.
- [Language lookup](https://data.gdeltproject.org/api/v2/guides/LOOKUP-LANGUAGES.TXT).
- [Challenge GDELT access notes](https://voloridge-hack-mit-2026.s3.us-east-1.amazonaws.com/src/gdelt/README.md).
