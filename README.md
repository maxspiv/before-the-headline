# Signal / Noise

**An evidence workbench for investigating news spikes across sources and languages.**

Signal / Noise is a local Flask application built for HackMIT 2026 (Voloridge "Signal in the Noise" challenge). It takes one news spike — a reported MSC Ulsan III / Novorossiysk booking suspension — and lets an investigator inspect the cached evidence behind it: which pages are actually about the story, which repeat each other, what each publisher claims about timing, and what remains unresolved.

It runs entirely from files in this repository. No API keys, database, Node build, LLM runtime or network access is needed after dependencies are installed.

- [DEMO.md](DEMO.md) — 90-second presentation script, reset procedure, fallback screenshots
- [SUBMISSION.md](SUBMISSION.md) — submission write-up: problem, approach, findings, limitations
- [HISTORICAL_FEASIBILITY.md](HISTORICAL_FEASIBILITY.md) — the separate historical multilingual-data experiment

## Quick start

Requires Python 3.9+ (tested on 3.10) and, for the browser tests only, Google Chrome.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest -v test_demo.py
.venv/bin/python app.py --port 8765
```

Open **http://127.0.0.1:8765/**. The server binds to loopback only, with debug mode and the reloader disabled. Stop with Ctrl-C.

If the machine lacks a CJK font, the Chinese page title and excerpt render as boxes; install one (e.g. `fonts-noto-cjk` on Debian/Ubuntu) before presenting.

### Devin / remote preview

The app trusts only the `127.0.0.1` and `localhost` Host headers. Run it exactly as above on the remote machine and use a port preview or tunnel that preserves the localhost Host header. Do not bind to `0.0.0.0` or enable debug mode for a preview.

## Tests

```sh
# data + API tests (no browser)
.venv/bin/python -m unittest -v test_demo.py

# browser regressions (needs Chrome and the server running on 8765)
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chrome     # only if Chrome is not already at /opt/google/chrome
SIGNAL_BROWSER_CHANNEL=chrome .venv/bin/python -m unittest -v test_demo_browser.py

# fresh-start offline judging walkthrough (starts its own server on a free port,
# denies outbound server sockets, refreshes results/judging/ screenshots)
SIGNAL_BROWSER_CHANNEL=chrome .venv/bin/python -m unittest -v test_judging.py

# confirm the checkout carries only the files the demo needs
.venv/bin/python verify_handoff.py
```

Latest results on this checkout: `test_demo.py` 10/10, `test_demo_browser.py` 6/6, `test_judging.py` 1/1, `verify_handoff.py` passed with network blocked. The machine-readable record of the fresh-start walkthrough is [results/judging/offline_walkthrough.json](results/judging/offline_walkthrough.json).

## The main journey

1. **Begin investigation** — the opening screen separates the broad-query context (694 English shipping matches in one GDELT TimelineVolRaw daily bin, 31 Aug 2026) from the inspected sample. The two are different populations; the sample does not explain the 694.
2. **Inspect the matches** — 13 inspected pages (6 Spanish GDELT-result pages, 7 externally discovered/contextual pages) with publisher, language, discovery provenance and a recorded relevance label. 3 concern the MSC suspension; 10 concern other stories.
3. **Filter** — hide other stories, hide *possible* shared reporting, or fold the one *confirmed* copied-text group (La Verdad / Diario Vasco, 22 identical extracted lines). Counters always describe cards, not independent stories.
4. **Replay the story** — the three retained MSC pages by publisher-claimed date. Date-only evidence (the Chinese page) stays date-only; no hour or timezone is invented.
5. **Inspect source evidence** — each panel shows the cached excerpt with file and line references, the URL, every publisher-embedded timestamp with its meaning, GDELT observation vs project capture time, and the open uncertainties.
6. **Unresolved** — conflicting vessel direction, attack vs announcement time, possible shared upstream reporting, and incomplete coverage.

An optional collapsed **04 Technical results** panel at the bottom summarises the separate historical experiment. It is not linked from the navigation and is not part of the demo path.

## Architecture

```
app.py            Flask app: /, /healthz, /api/evidence, /api/source/<id>, /api/replay
demo_data.py      loads the frozen JSON artifacts + page texts; deterministic filters/counts
templates/        index.html (single page)
static/           app.css, app.js (plain browser JS, no framework, no build step)
results/msc_validation/   evidence_table.json, timeline_data.json (+ .md/.svg renderings)
results/raw_candidates.json   aggregate context and candidate inventory
results/pages/*.txt       13 cached publisher-page text extractions (excerpt sources)
evidence_sources.json     source manifest for the cached pages
results/judging/          fallback screenshots, walkthrough record, protected-evidence hashes
```

Python computes all filters and counts; the client only renders. Source excerpts are rendered as text, never as publisher HTML. A restrictive Content Security Policy (`'self'` only), HTTP(S)-only external links, loopback binding and disabled debug mode limit the local demo's exposure.

The remaining Python files (`validate_msc.py`, `feasibility.py`, `historical_experiment.py`, `language_recovery.py`, `language_pages.py`, `bounded_recovery.py`, `render_charts.py`, `audit_judging.py`) are the research and audit scripts that produced the frozen artifacts. They need the excluded raw caches (`cache/`, `historical/`, `results/requests/`) and are **not** required to run the demo.

## Data provenance

- **GDELT DOC 2.0 API** — one English shipping-query TimelineVolRaw response (source of the 694 aggregate) and one Spanish ArticleList response (22 metadata records; discovery only).
- **Externally discovered pages** — 9 records found outside GDELT results, including the three retained MSC pages (English, Spanish, Chinese).
- **Publisher pages** — bodies captured 19 September 2026; text extracted and cached under `results/pages/`. Publisher-embedded timestamps are recorded with their meaning and explicit offsets where present.
- **GDELT GKG bulk files** — one metadata hint in the inventory; and, separately, the six translated GKG files used by the historical experiment (not in this checkout).

Relevance and text-sharing judgments are recorded, agent-assisted inspections of this case, frozen in `results/msc_validation/evidence_table.json`. SHA-256 hashes of the 17 evidence fixtures are in `results/judging/protected_evidence_hashes.json`, and the judging test fails if any of them changes.

## Limitations

- **Not live monitoring.** Everything is a frozen cache; the app does not poll or fetch.
- **Not complete coverage.** 13 inspected pages from a 32-record curated inventory. 19 records were never inspected (metadata-only or HTTP 401). The sample is not representative of the 694 aggregate matches.
- **Not first publication.** Publisher-claimed timestamps, GDELT observation labels, GKG record dates and capture times are different clocks. No page is established as first.
- **Not validated early detection.** No cross-language lead, lead time, alert precision, recall or false-alarm rate has been measured. The historical experiment established usable multilingual records, but a comparable English denominator remains unvalidated and no held-out alert evaluation was completed.
- **Not independent confirmation.** Distinct URLs and languages do not establish independent reporting; the three MSC pages may share upstream sources. The carrier notice was not verified.
- Chinese page: date only; the English and Chinese accounts disagree on the vessel's direction. Both are shown; neither is resolved.

## Reset between judges

Escape to close any panel → **Inspect** → **Reset filters** (13 cards) → **Replay** → **All observed pages** (3 pages) → scroll to top. Nothing is persisted client- or server-side; reloading the page also resets everything. Full procedure in [DEMO.md](DEMO.md).

## How Devin contributed

Devin (Cognition's software agent) worked under the user's scope and evidence constraints. It explored the GDELT API contracts and bulk-file formats; wrote and ran the cached feasibility probes and the historical translated-stream verification; inspected the cached publisher pages and recorded the provisional relevance and copied-text classifications; implemented the Flask app, filters and source panels; wrote the data, browser, security and fresh-start offline tests; and prepared the judging audit, walkthrough script and fallback screenshots. In the final pass it set up the environment, fixed CJK rendering for the screenshots, added the technical-results panel, committed the judging artifacts and finalized this documentation.

Devin is a development and analysis assistant here, not a runtime dependency: the app uses no LLM and no external service. The user set the product direction and required the strict separation between observed evidence and unsupported early-warning claims.

## Repository hygiene

`.gitignore` keeps virtual environments, raw HTTP caches, historical bulk data, logs, backups and any credential-like files out of the repository. Only the source, pinned requirements, documentation and the 17 small evidence fixtures plus judging screenshots are tracked.
