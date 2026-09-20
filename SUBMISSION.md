# Before the Headline

Explore news coverage across sources and languages.

## Pitch

Before the Headline is a local investigation workspace for exploring news coverage across sources and languages: it helps an investigator separate relevant coverage, repeated reporting and open questions using traceable cached sources, without claiming detection or early warning.

## Problem

A rise in news matches is not necessarily a new story, independent corroboration, or an early signal. Broad queries can mix different incidents, syndicated text, translated editions and unrelated material. Publication labels, platform observation times and retrieval times are also different clocks. A compelling chart can hide those distinctions.

## What was built

A Flask app with a **reusable investigation workspace**: an investigations home lists saved cases, and each opens in the same articles/timeline/notes workspace. The bundled case study — the **reported MSC Ulsan III / Novorossiysk booking suspension** — ships as data:

1. A broad shipping-query aggregate shown as query context, explicitly separate from the reviewed pages.
2. Cached pages browsable with publisher, language, discovery provenance and recorded relevance labels.
3. Filters for other stories and possible shared reporting, plus folding of a confirmed matching-text group.
4. A timeline of the on-story pages by publisher-claimed date; date-only evidence remains date-only.
5. Source panels with excerpts, URLs, timestamp provenance, relevance rationale and notes.

New investigations can be **imported as JSON** (format at `/import/schema`, template at `/api/import/template`, 1 MiB limit, all validation errors reported at once). Imports persist as JSON files under `local_investigations/` (`SIGNAL_DATA_DIR` overrides). Imported labels are marked supplied rather than recorded; imported URLs are stored but never fetched; there is no live search, scraping or detection.

## Data and sample sizes

These are different populations and must not be treated as a funnel:

| Quantity | What it actually means |
|---|---|
| **694** | Matching articles in one cached English shipping-query TimelineVolRaw daily bin, August 31, 2026. An aggregate count, not a count of unique stories; its cause is unestablished. |
| **22** | Returned Spanish article-metadata records in one archived ArticleList response, requested with a cap of 250. |
| **32** | Unique-URL records in the curated evidence inventory: 22 GDELT ArticleList records, 9 externally discovered records and 1 bulk-metadata hint. |
| **13** | Publisher pages whose bodies were reviewed: 6 Spanish GDELT-result pages + 7 external/contextual pages. Not a representative sample of the 694 matches. |
| **3 / 10** | Of those 13 reviewed pages, 3 concern the MSC suspension; 10 concern other stories. |
| **5 English / 7 Spanish / 1 Chinese** | Languages of the 13 reviewed pages. |
| **2 pages / 1 group** | The confirmed copied-text example: La Verdad and Diario Vasco share 22 identical extracted lines in the reviewed spans. |
| **19** | Unreviewed/unavailable inventory records: 16 GDELT metadata-only records, 2 cached publisher HTTP 401 responses and 1 GKG metadata hint. |

## Findings

- The three on-story publisher pages explicitly connect MSC, the named vessel, Novorossiysk and a booking/service suspension — what the pages report, not independent confirmation of the event.
- The reviewed La Verdad/Diario Vasco passages match. Other possible shared reporting, including upstream overlap among the MSC pages, remains unconfirmed.
- Cached English and Spanish publisher metadata supplies explicit offsets; the Chinese page supplies only a date.
- The English account describes departure from Novorossiysk, while the Chinese account describes travel toward it. Attack timing and upstream provenance remain unresolved.

## Methodology

**Review method.** Each reviewed page was opened and read; relevance and copied-text labels were recorded by hand. Counts and folding are computed by the app from those recorded labels.

**Timestamps.** Article dates are publisher-claimed; date-only entries have no time or timezone. GDELT observation times and capture times are shown separately in source details.

**Historical data check.** Independently of the case study, six translated GDELT GKG files (00:00 and 00:15 UTC on 1 March 2015, 2017 and 2019) were verified and parsed: 14,725 records, 60 reported source-language code values, 14,320 distinct document identifiers; manual review confirmed 6 non-English article bodies. The equivalent native-stream sample had every language field blank, so an English denominator was not established. No detector, alert threshold, lead time or false-alarm rate was evaluated. Details: [HISTORICAL_FEASIBILITY.md](HISTORICAL_FEASIBILITY.md).

## Limitations

The reviewed sample is small, curated, incomplete and mixed-provenance. The original carrier notice and upstream source reports were not verified. Distinct URLs and languages do not establish independent reporting. Comparable full-window topic counts and per-language denominators were not recovered. No language lead, lead time, first publication, alert precision, recall or false-alarm rate has been validated.

## Reproducibility

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m unittest test_demo.py test_investigations.py
.venv/bin/python app.py --port 8765          # then open http://127.0.0.1:8765/
```

Browser tests additionally need Chrome (`playwright install chrome`) and run with `SIGNAL_BROWSER_CHANNEL=chrome`.

## AI use

Built with Devin, an AI software engineer, under human-set scope and evidence constraints. No LLM is used by the application at runtime.
