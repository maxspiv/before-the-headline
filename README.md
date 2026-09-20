# Before the Headline

**Explore news coverage across sources and languages.**

Before the Headline is a local web app for looking closely at a burst of news coverage. Load a collection of articles about one event, see which pages are actually about it, which ones repeat each other, what each publisher claims about timing, and what is still unclear. It ships with one reviewed collection — coverage of a reported MSC booking suspension at Novorossiysk — and accepts new collections as JSON.

![Investigations home](results/judging/00-home.png)

## Capabilities

- **Investigations home** — lists saved collections with topic, article counts and coverage status; filter the list; import or remove collections.
- **Articles** — cards with publisher, language, where the page was found (GDELT result or elsewhere) and a relevance label; filters for other stories, possible shared reporting and copied text; counts computed by the app.
- **Timeline** — on-story pages by publisher-claimed date. Date-only pages stay date-only.
- **Source details** — cached excerpt with file and line references, URL, every publisher-embedded timestamp with its meaning, GDELT observation vs. capture time, duplication group and notes.
- **Notes** — open questions attached to the collection (conflicting accounts, possible shared upstream reporting, gaps in observation).
- **Import** — JSON collections validated field by field and stored as local files.

![Shipping investigation workspace](results/judging/01-workspace.png)

## Quick start

Python 3.9+ (tested on 3.10). Everything runs from files in this repository; no API keys, database or network access after installation.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py --port 8765
```

Open http://127.0.0.1:8765/. The server binds to loopback only with debug mode off. `GET /healthz` reports the running commit.

Chinese titles need a CJK font (e.g. `fonts-noto-cjk` on Debian/Ubuntu).

## Import format

The home page accepts a JSON file or pasted JSON. The format is documented in the app at `/import/schema`, and `GET /api/import/template` downloads a minimal valid example. Summary:

```jsonc
{
  "schema_version": 1,
  "id": "my-investigation",          // ^[a-z0-9][a-z0-9_-]{2,63}$
  "title": "…", "topic": "…",
  "description": "…",                 // optional, as are the rest below
  "aggregate_context": {"count": 694, "source": "…", "language": "…", "date": "2026-08-31", "meaning": "…"},
  "articles": [                       // 1–500
    {"id": "a-1", "title": "…", "publisher": "…", "language": "English",
     "relevance": "related",         // related | unrelated | uncertain
     "url": "https://…",             // http(s) only; never fetched
     "claimed_timestamp": {"value": "2026-08-31T10:00:00+00:00", "precision": "datetime"},  // or {"value": "2026-08-31", "precision": "date"}
     "duplicate_group": "G1", "duplicate_status": "confirmed",   // confirmed | possible | unassessed
     "excerpt": "…", "include_in_replay": true}
  ],
  "uncertainties": [{"id": "u-1", "title": "…", "detail": "…", "source_ids": ["a-1"]}]
}
```

Validation covers required fields, unique IDs, article references, timestamp precision (`date` = `YYYY-MM-DD`; `datetime` needs a timezone offset), URL scheme, duplicate groups, unknown keys and a 1 MiB body limit. All errors are returned together. Imported labels are shown exactly as supplied; the app does not verify them.

Imported collections are saved as JSON files under `local_investigations/` (override with `SIGNAL_DATA_DIR`) and can be removed from the home page. Bundled collections cannot be removed. `fixtures/synthetic_investigation.json` is a synthetic test fixture and is never listed by default.

## Architecture and data

```
app.py             Flask routes: /, /investigations/<id>, /import/schema, /healthz,
                   /api/investigations[/import|/<id>], /api/investigations/<id>/{evidence,source/<sid>,replay}
demo_data.py       loads the bundled collection from frozen JSON + cached page text; filters and counts
investigations.py  import validation, investigation model, JSON-file store
templates/, static/   Jinja templates, plain CSS and browser JS (no build step, no framework)
fixtures/          synthetic import fixture and invalid examples used by tests
results/msc_validation/  evidence_table.json, timeline_data.json — the bundled collection's reviewed labels
results/raw_candidates.json, evidence_sources.json, results/pages/*.txt — inventory, manifest, cached page text
results/judging/   screenshots, offline walkthrough record, SHA-256 hashes of the evidence files
```

Python computes all filters and counts; the browser only renders. Excerpts are rendered as text, never as publisher HTML, under a `'self'`-only Content Security Policy.

Bundled data comes from the GDELT DOC 2.0 API (one English shipping TimelineVolRaw response — the 694-match aggregate — and one Spanish ArticleList response), pages found outside GDELT results, and publisher pages captured on 19 September 2026. Relevance and copied-text labels were recorded during a manual, agent-assisted review of each cached page and frozen in `results/msc_validation/evidence_table.json`; `test_judging.py` fails if any evidence file changes.

The other Python scripts (`validate_msc.py`, `feasibility.py`, `historical_experiment.py`, `language_recovery.py`, `language_pages.py`, `bounded_recovery.py`, `render_charts.py`, `audit_judging.py`) produced the frozen artifacts and a separate historical data check ([HISTORICAL_FEASIBILITY.md](HISTORICAL_FEASIBILITY.md), [feasibility_report.md](feasibility_report.md)). They need raw caches that are not in the repository and are not required to run the app. [SUBMISSION.md](SUBMISSION.md) has the write-up.

## Tests

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest -v test_demo.py test_investigations.py        # data, API, import
.venv/bin/python -m playwright install chrome                               # if Chrome is missing
SIGNAL_BROWSER_CHANNEL=chrome .venv/bin/python -m unittest -v test_demo_browser.py   # needs server on 8765
SIGNAL_BROWSER_CHANNEL=chrome .venv/bin/python -m unittest -v test_judging.py        # fresh process, network blocked, refreshes screenshots
.venv/bin/python verify_handoff.py                                          # checkout carries only needed files
```

## Limitations

- Cached and user-supplied data only; nothing is polled, fetched or searched live.
- The bundled collection reviews 13 of 32 inventoried pages. It does not explain the 694 aggregate matches, and distinct URLs or languages do not establish independent reporting.
- Timestamps are publisher claims, GDELT observation labels or capture times; none is established as first publication.
- No early-detection performance (lead time, precision, recall, false-alarm rate) has been measured. The historical data check established usable multilingual records only.
- Imported labels and excerpts are trusted as supplied.

## Acknowledgments

Built for HackMIT 2026 (Voloridge "Signal in the Noise" challenge) with [Devin](https://devin.ai), which wrote much of the code, tests and documentation and carried out the agent-assisted page review under human direction. Uses Flask, Jinja, GDELT data, and Playwright with Chrome for browser tests. The app itself uses no LLM or external service at runtime.
