# Decision: GO for a bounded story replay; NO-GO for an attention radar

**Candidate:** the *reported* MSC Ulsan III / Novorossiysk booking suspension. **No UI or successful early-warning claim.**

- [Evidence table](evidence_table.md) — also [CSV](evidence_table.csv) and [JSON](evidence_table.json).
- [One incomplete evidence-only timeline](story_timeline.svg) — [underlying data](timeline_data.json).

## What is established

Three inspected cached publisher pages explicitly connect **MSC + MSC Ulsan III + Novorossiysk + suspension of bookings/services**. Two focus on that suspension; Prensa Latina includes it within a broader roundup. This supports replaying what these pages report, not independently confirming the event or reconstructing all coverage.

| Retained page | Publisher-claimed publication timestamp | Scope / provenance |
|---|---|---|
| [India Shipping News](https://indiashippingnews.com/international-shipping/msc-suspends-russian-port-of-novorossiysks-bookings-after-attack-on-msc-ulsan-iii/) — English | `2026-08-31 09:03:33 +05:30` = **03:33:33 UTC**; claimed modification 05:41:32 UTC | Direct topic focus; upstream reporting independence unverified. |
| [Prensa Latina](https://www.prensa-latina.cu/2026/09/01/navieras-suspenden-operaciones-con-rusia-por-riesgos-en-mar-negro-2/) — Spanish | JSON-LD `2026-09-01 06:02:45 -04:00` = **10:02:45 UTC**; agrees with Open Graph metadata | MSC substory only; explicitly attributes the broader report to Kommersant. |
| [JCtrans](https://www.jctrans.com/cn/news/14074/) — Chinese | **September 1, date only**; time/timezone unresolved | Explicit compilation of Bloomberg and Lloyd’s List; not independent original reporting. |

These are claims in page metadata, **not verified first publication**. All three bodies were captured on September 19. Their GDELT observation times remain unavailable. The timeline uses publisher-claimed calendar dates, leaves unobserved intervals unknown, and does not imply within-day ordering for the Chinese page or any language lead.

**Keep the incident boundaries:** the vessel attack is a precursor, not the booking-suspension announcement itself. English attributes the attack to August 25; Spanish dates the suspension to August 26; Chinese says the precise attack time is unclear. English describes the vessel departing Novorossiysk, Chinese heading toward it. Those conflicts remain unresolved. Other attacks on RMS Team, Yanina, grain/oil facilities, and other carriers’ suspensions in the same articles are not merged into this story. Neither “all Russian ports closed” nor a verified reopening date is supported.

**Duplicates and uncertainty:** exact URLs are deduplicated. The unrelated La Verdad/Diario Vasco Larak reports have matching inspected text and share one duplicate group; they are excluded from the MSC replay. The three retained pages have distinct text, but shared upstream derivation remains possible—do not count them as three independent origins. Blocked bodies, uninspected ArticleList records and the bulk metadata hint remain explicitly uncertain in the table. Carrier customer notices and upstream originals were not verified.

## One bounded recovery pass: completed and closed

I inspected the cached Voloridge README and fetching script. Its default is a 2019 event download; its `--list` path enumerates unsigned S3 objects and sums sizes before downloading. I used equivalent unsigned **ListObjectsV2** requests instead of downloading the default or installing its Python 3.10+/boto3 environment.

- S3 listings for `v2/gkg/2026`, `v2/events/2026` and `v2/mentions/2026` succeeded with no objects and no truncated listings. A positive control for April 15, 2019 listed 96 GKG objects totaling 2,499,519,331 bytes; none of that irrelevant historical payload was downloaded. The provided mirror cannot supply this 2026 story under those prefixes.
- Direct GDELT bulk access worked. HEAD requests estimated the August 26 native/translated pair at **10,559,352 bytes** before GET. A September 18 pair was separately estimated at **10,291,087 bytes** before GET. Both pairs were downloaded and parsed; all sampled rows have the documented 27 columns.
- A single-pair extrapolation gives **about 7.10 GB compressed for seven days / 1,344 files**. This is a planning estimate, not a measured full-week size or coverage guarantee. I kept this diagnostic pass within a **25 MiB response-body budget**, rather than launching a multi-GB corpus crawl.
- Actual pass: **18 sequential network requests, 21,308,790 received body bytes**, including codebook and metadata, from **18:20:36 to 18:25:50 UTC** on September 19—well within 90 minutes. No DOC API calls and no throttling occurred. The fetcher honors numeric/date `Retry-After`, backs off, stops after two persistent throttling/service-failure responses, and enforces a persisted deadline/request/byte ceiling. The pass is closed against further live recovery. Responses, HEAD metadata and hashes are cached.

### September 13 cutoff and comparability

September 14 and 18 bulk objects exist; the downloaded post-cutoff pair contains records with GKG date **20260918000000**. September 19 feed pointers are also available. Thus the earlier DOC endpoint is **not evidence of a GDELT-wide September 13 halt**. The cause of the DOC truncation remains unresolved; these sparse checks do not establish complete coverage of September 14–17 or historical availability at publication time.

GKG provides document identifiers, update-batch IDs and source-language translation metadata. Its codebook distinguishes batch time, the document-date field and translation provenance; blank translation information can include human-translated English inputs. **Do not relabel these as DOC `seendate` or verified original publication language.**

The diagnostic slices supply per-language document inventories **only for those GKG batches**. They do not recover full-window, story-validated numerators and matching denominators. GKG titles/tags are not equivalent to DOC full-text matches. No GKG denominator is combined with DOC counts, and no global denominator substitutes for a language denominator. The three retained URLs were not found in these two sparse batch pairs; that says nothing about their presence elsewhere. An “Ulsan” metadata hint is preserved as uncertain, not accepted as this vessel/story.

## Claims decision

- **Story replay: GO**, limited to these inspected reports, publisher-claimed dates, duplicate filtering and transparent gaps.
- **Attention radar: NO-GO.** Comparable full-window series, validated topic membership, same-corpus language denominators and a complete preceding-observation/held-out evaluation are missing. Alert precision and false-alarm rate are **not measured**, not zero.
- **Reject candidate: not necessary** for the narrow replay. Reject the stronger interpretation that this evidence demonstrates early discovery, first publication, a language lead or independent corroboration.

## Reproduce offline

From the project root:

```sh
python3 validate_msc.py --bulk-audit
python3 validate_msc.py
python3 render_charts.py --msc
python3 -m unittest -v test_msc_validation.py test_feasibility.py
```

Verification passed: **17 tests**, SVG parsing and source-link checks, plus two network-disabled replays with all **14 checked artifacts byte-identical**.

No network is needed. `bounded_recovery.py inventory --offline`, `sample --offline`, `cutoff --offline` and `codebook --offline` replay the cached recovery responses. Live recovery is closed. Inspect `recovery_state.json`, `recovery_inventory.json`, `download_estimate.json`, `post_cutoff_estimate.json`, `bulk_schema_audit.json` and `bulk_recovery_summary.json` for the audit trail. The extracted codebook is `gkg_codebook.txt`; publisher/API/body hashes and timestamp provenance are linked from the evidence JSON.
