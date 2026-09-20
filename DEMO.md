# Signal / Noise — HackMIT judging guide

An evidence-inspection prototype using frozen cached sources. **Early warning is unvalidated future work.**

## Fresh checkout / Devin Cloud handoff

The Git checkout includes the Python source, pinned dependencies, local web assets, documentation, and the 3 JSON + 13 publisher-text fixtures needed by the offline Flask demo. Raw HTTP caches, historical bulk data, publisher crawl caches, virtual environments, logs, screenshots and local backups are intentionally not transferred. Original fixture text and source-line references are preserved; no HTTP headers or credentials are required by the demo.

Run from the repository root in Devin Cloud using Python 3.9 or later:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest -v test_demo.py
.venv/bin/python app.py --port 8765
```

The app then runs offline at `http://127.0.0.1:8765/`. Use a browser inside the Cloud machine, or an authenticated port-forward/reverse proxy that preserves the localhost Host header. The app deliberately remains loopback-only with its trusted-host checks; no public binding or security relaxation is needed for this handoff. Dependencies require internet on the first installation, but the demo itself does not.

Historical collection/evaluation scripts and reports are included for continued development, but their large caches are not. Full research replay/audit tests and the old screenshot links require those excluded artifacts; they are not the portable-demo acceptance command. Do not run network collectors merely to start the demo. The isolated language-analysis environment and its optional dependencies are separate from the Flask runtime.

## Start the prepared demo

From the project root:

```sh
.venv/bin/python app.py
```

Open **http://127.0.0.1:8765/**. Keep the terminal running; stop with Ctrl-C. The server binds only to loopback and disables debugging/reload.

If that port is already serving the demo, use the existing instance. For a separate fresh instance without disturbing it:

```sh
.venv/bin/python app.py --port 8766
```

Then use **http://127.0.0.1:8766/**. The prepared environment needs no installation or network access. Do not run data collectors or recovery scripts for judging. The app serves local CSS, scripts, fonts available on the machine, JSON and source excerpts. Only explicitly opening an external publisher link requires internet; **do not click those links during the offline walkthrough**.

For a genuinely fresh checkout, dependencies must have been installed before going offline:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Keep the provided `results/` artifacts and page-text files with the app. No Node build, API keys, cloud services or LLM runtime is needed.

## Reset between judges

1. Press **Escape** if a source panel is open.
2. Use **Inspect** in the header, then **Reset filters**. Confirm **13 cards**, other stories and possible sharing checked, copied-text folding and uninspected inventory unchecked.
3. Use **Replay**, then **All observed pages**. Confirm **3 retained pages**. Reset filters affects the evidence browser only; this separate step resets the replay.
4. Scroll to the opening screen. The **Begin investigation** action should be visible. Do not use the browser Back button as a reset procedure.

This exact reset is covered by the fresh-start judging test. The app does not persist user selections in local storage or a server session.

## Final 90-second walkthrough

Use the short narration below, not every detail in the source panel. The sample is curated; never draw a funnel from 694 to 13 to 3.

| Time | Action | Narration |
|---|---|---|
| **0:00–0:15** | Show the opening screen and separate aggregate context. | “Signal / Noise helps inspect a news spike. GDELT counted 694 English shipping matches on this day. That is separate from our inspected sample—we have not established the spike’s cause.” |
| **0:15–0:30** | Click **Begin investigation**. Point to the sample counters and source-provenance labels. | “Here are 13 inspected pages: six Spanish GDELT-result pages and seven external or contextual pages. Three concern the MSC suspension; ten concern other stories. The sample is curated, not representative.” |
| **0:30–0:47** | Turn on **Fold confirmed copied text**: 13 → 12 cards. Turn off **Show other stories**: 3 cards. Briefly turn off **Show possible shared reporting**: 0 cards; restore it: 3. | “These two pages contain matching passages. Folding shows twelve cards. Hiding other stories leaves three MSC pages. Possible upstream sharing is not confirmed copying. Excluding it hides all three, so we keep that uncertainty visible.” |
| **0:47–1:00** | Use **Replay**. Show all 3 retained pages, then select **1 Sep** to show its 2 dated pages. | “Replay uses publisher-claimed dates, not first publication. The Chinese page has a date but no verified hour or timezone. We don’t invent one.” |
| **1:00–1:18** | Open **JCtrans**. Point to the date-only label, cached excerpt and URL without opening the external link. | “This panel links each claim to a cached excerpt and its source. Note the Chinese account’s direction: toward Novorossiysk. The English account describes departure. We surface the conflict rather than resolve it without evidence.” |
| **1:18–1:30** | Press Escape and use **Unresolved**. Finish on the four uncertainty cards. | “Attack timing, shared sources and coverage gaps remain unresolved. This is a working evidence-inspection prototype. Early warning needs comparable data and held-out false-alarm testing; it is unvalidated future work.” |

### If asked how copied text was established

Open La Verdad’s source panel and its linked Diario Vasco copy. The audit found **22 identical extracted lines** in the inspected headline/byline/date/body spans. This supports one matching-text group, not an assertion of copying direction, identical complete HTML, or independent reporting.

### If asked why the sample was not expanded

The cache-only audit screened all **19 remaining inventory candidates**. Sixteen have only GDELT metadata, two have cached publisher HTTP 401 responses, and one is a GKG metadata hint. None has an additional successful cached publisher body to inspect. Another matching-headline pair is documented as possible metadata-level sharing only. No pages were promoted and no synthetic examples added.

## Presentation fallback

[Open the screenshot sequence](results/judging/screenshots.md). These are actual viewport captures from the fresh offline demo path:

1. [Opening / aggregate context](results/judging/01-opening.png)
2. [13-page inspected sample](results/judging/02-inspected-sample.png)
3. [Confirmed copied-text fold / 12 cards](results/judging/03-confirmed-copy-fold.png)
4. [3 related pages, possible sharing retained](results/judging/04-related-pages.png)
5. [Publisher-claimed story timeline](results/judging/05-story-timeline.png)
6. [Cached source evidence and date-only precision](results/judging/06-source-evidence.png)
7. [Unresolved details](results/judging/07-unresolved.png)

If the browser demo is unavailable, advance through these images while giving the same narration. Do not present the screenshots as live news or a complete historical coverage record.

## Facts to keep straight

- **694** is one cached English aggregate daily count, not a capped search-list length, story count or the sample’s size.
- **13 inspected pages = 3 MSC-related + 10 other stories**. Language labels are 5 English, 7 Spanish and 1 Chinese. The 6 inspected GDELT-result pages are Spanish; the 7 other pages were externally discovered/contextual.
- **32 inventory records** include **19 uninspected/unavailable records**. Optional inventory mode does not promote those records to inspected evidence.
- Confirmed copied text and possible shared reporting are different. Folding removes cards, not proven independent origins. All three retained MSC pages may share upstream reporting.
- Relevance means relevance to this MSC suspension. An “other story” can still be a legitimate broad shipping-query match. Unknown records are not automatically classified as unrelated.
- The replay remains the three retained pages even when evidence-browser filters change. Within September 1, date-only evidence is not ordered by an invented hour.
- All three publisher bodies were captured September 19. Publisher claims, GDELT observation labels, GKG record metadata and capture time are different clocks. No article is established as “first.”
- The DOC September 13 endpoint is not a proven GDELT-wide halt: later bulk records exist, but completeness and the DOC-specific cause remain unresolved.

## Verification and audit

**17 tests passed**: 10 data/API tests, 6 browser regressions and the fresh-start offline judging-path test. The fresh run used a new application process via the real CLI entry point and a new browser context. Outbound server connections/DNS and non-local browser requests were denied; loopback HTTP was allowed. No external requests, blocked server attempts or JavaScript errors were recorded. System-wide network settings were not changed.

Run the exact fresh-start judging path using the already installed Chrome:

```sh
.venv/bin/python audit_judging.py
SIGNAL_BROWSER_CHANNEL=chrome .venv/bin/python -m unittest -v test_judging.py
```

The judging test starts and stops its own server on an unused loopback port, without stopping an existing demo. It verifies the reset procedure, refreshes the seven fallback images, and writes [offline verification](results/judging/offline_walkthrough.json).

For all demo regressions, start the regular server separately, then:

```sh
SIGNAL_BROWSER_CHANNEL=chrome .venv/bin/python -m unittest -v test_demo.py test_demo_browser.py test_judging.py
```

The prepared environment already has the test dependencies and uses installed Chrome. Do not install packages or browsers during offline judging. Test dependency pins are in `requirements-dev.txt`.

- [Submission draft](SUBMISSION.md)
- [Claim and additional-candidate audit](results/judging/claim_audit.md)
- [Machine-readable claim audit](results/judging/claim_audit.json)
- [Protected evidence hashes](results/judging/protected_evidence_hashes.json)
- [Pre-judging app backup](results/judging/pre_judging_demo.zip)

The backup preserves the previous working app code, styles, tests and guide. Cached evidence and frozen classifications were not changed. This preparation altered presentation wording and corrected an unsupported timestamp fallback label; it did not reopen collection or add new product features.

## Remaining issues to disclose

No blocking issue was found on the tested Chrome desktop/mobile paths. The scientific limits remain: a small mixed-provenance sample, incomplete coverage, unverified upstream originals and carrier notice, contradictory route/timing details, no matched normalization denominators, and no measured alert or false-alarm performance.

One non-story record has a raw `+0900` timestamp with no UTC conversion in its legacy artifact. The panel preserves that raw value and correctly says the cached conversion was not supplied. This does not affect the MSC date-only replay. External publisher links require internet; Safari and other browser engines were not verified in this judging run.
