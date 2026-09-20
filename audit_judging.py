import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from demo_data import EXTRA_RANGES, EXCERPTS, load_dataset
from feasibility import PageText
from validate_msc import Metadata, json_dates

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'results/judging'
SUPPORT = {
    'shipping_msc_en': ['MSC Ulsan III', 'Novorossiysk', 'bookings', 'Aug. 25'],
    'shipping_msc_es': ['MSC Ulsan III', '26 de agosto', 'Kommersant'],
    'shipping_msc_zh': ['MSC ULSAN III', '暂停接受', '驶往新罗西斯克港', '准确时间'],
    'shipping_typhoon_en': ['Shanghai', 'Ningbo', 'Saudel'],
    'shipping_hormuz_en': ['Khasab', 'tanker'],
    'semiconductors_exports_en': ['semiconductor exports', 'Korea Customs Service', 'AI translation'],
    'energy_yaroslavl_oilprice_en': ['Yaroslavl', 'refinery'],
    'gdelt_panama_es': ['Canal de Panamá', 'sequía'],
    'gdelt_larak_es': ['Larak', 'minas'],
    'gdelt_iran_laverdad_es': ['Larak', 'Guardia Revolucionaria'],
    'gdelt_iran_diariovasco_es': ['Larak', 'Guardia Revolucionaria'],
    'gdelt_quito_es': ['899 cámaras', 'Quito', 'semáforos'],
    'gdelt_outlook_es': ['Exchange Online', 'Microsoft'],
}
CANDIDATE_NOTES = {
    'energy_ryazan_en': 'Cached HTTP 401, not an article body. Discovery title suggests a Ryazan refinery incident; full-text classification remains unavailable.',
    'energy_yaroslavl_en': 'Cached HTTP 401. Cannot establish full-text equivalence with the inspected OilPrice report or relevance to the MSC suspension.',
    'gdelt_metadata_02': 'Title describes the Saler wildfire scar. No publisher body; do not promote a title-based exclusion to verified relevance.',
    'gdelt_metadata_06': 'Headline matches the inspected Iran/Jordan copied-text group. Possible shared reporting only: publisher body is absent.',
    'gdelt_metadata_07': 'Potentially useful broad-shipping context about Arctic routes. No body or explicit MSC suspension evidence in the cached title.',
    'gdelt_metadata_08': 'Similar Iran/Jordan headline to inspected copies. Preserve possible syndication, not confirmed copied text.',
    'gdelt_metadata_09': 'Title concerns a Maduro prison photograph. Only metadata was inspected; no verified booking-suspension observation.',
    'gdelt_metadata_10': 'Title concerns Pentagon assessment of the Iran war. Related regional context is not evidence of the MSC event.',
    'gdelt_metadata_11': 'Exact headline match with gdelt_metadata_14 suggests another possible shared-reporting pair. Both bodies are absent; no confirmed duplicate group is added.',
    'gdelt_metadata_12': 'Title concerns electricity disruption in Táchira. Full text is unavailable; no specific MSC evidence established.',
    'gdelt_metadata_13': 'Potentially useful supply-chain context about inventory strategy. No publisher body to inspect or establish MSC event membership.',
    'gdelt_metadata_14': 'Exact headline match with gdelt_metadata_11. Possible metadata-level pair only; do not fold these as confirmed copies.',
    'gdelt_metadata_15': 'Title concerns Larak strikes and retaliation in Jordan. Cannot merge regional shipping risk with the specific MSC booking suspension.',
    'gdelt_metadata_16': 'Title concerns a reconnaissance aircraft over the Baltic. Body not cached; retain uninspected status.',
    'gdelt_metadata_17': 'Title concerns a Nepal flood/landslide advisory. Body not cached; retain uninspected status.',
    'gdelt_metadata_18': 'Potentially useful Hormuz commentary, not verified MSC-event evidence. Publisher body is absent.',
    'gdelt_metadata_20': 'Title is an Édouard Balladur obituary. No publisher body; no verified MSC relevance or duplication assessment.',
    'gdelt_metadata_22': 'Another Maduro/prison headline, but not enough to confirm duplication with gdelt_metadata_09. Both remain metadata-only.',
    'bulk_hint_01': 'GKG title concerns Korean chip-company/university recruitment; an Ulsan token is not unique-vessel identity. No publisher body or verified MSC-event membership.',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def canonical(url):
    p = urlsplit(url)
    return (p.hostname, p.path.rstrip('/'), p.query)


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    app_files = ['app.py', 'demo_data.py', 'templates/index.html', 'static/app.js', 'static/app.css', 'requirements.txt', 'requirements-dev.txt', 'DEMO.md', 'test_demo.py', 'test_demo_browser.py']
    backup = OUT / 'pre_judging_demo.zip'
    if not backup.exists():
        with zipfile.ZipFile(backup, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in app_files:
                archive.write(ROOT / name, name)
    dataset = load_dataset()
    evidence = json.loads((ROOT / 'results/msc_validation/evidence_table.json').read_text())
    source_manifest = json.loads((ROOT / 'results/source_manifest.json').read_text())
    sources = {s['id']: s for s in source_manifest}
    cache = []
    for path in sorted((ROOT / 'cache').rglob('*.json')):
        meta = json.loads(path.read_text())
        if 'body_file' not in meta or 'url' not in meta:
            continue
        body_path = path.parent / meta['body_file']
        assert digest(body_path) == meta['sha256'], str(body_path)
        cache.append({'metadata_file': str(path.relative_to(ROOT)), 'url': meta['url'], 'final_url': meta.get('final_url', meta['url']), 'status': meta['status'], 'method': meta.get('method', 'GET'), 'body_file': str(body_path.relative_to(ROOT)), 'sha256': meta['sha256']})
    inspected = [r for r in dataset['records'] if r['inspected']]
    assert {r['id'] for r in inspected} == set(SUPPORT)
    audit_rows, timestamp_caveats = [], []
    api_articles = {a['url']: a for a in json.loads((ROOT / 'results/article_evidence.json').read_text())}
    for row in inspected:
        source = sources[row['id']]
        meta = json.loads((ROOT / row['cache_metadata']).read_text())
        body_path = (ROOT / row['cache_metadata']).parent / meta['body_file']
        body = body_path.read_bytes()
        assert hashlib.sha256(body).hexdigest() == row['body_sha256'] == source['response_sha256']
        extractor = PageText()
        extractor.feed(body.decode('utf-8', errors='replace'))
        extracted = '\n'.join(extractor.parts) + '\n'
        text_path = ROOT / row['text_file']
        assert extracted == text_path.read_text(), row['id']
        normalized = ' '.join(extracted.split()).casefold()
        assert all(term.casefold() in normalized for term in SUPPORT[row['id']]), row['id']
        parser = Metadata()
        parser.feed(body.decode('utf-8', errors='replace'))
        values = list(parser.values)
        for script in parser.scripts:
            try:
                values.extend(json_dates(json.loads(script), row['url']))
            except ValueError:
                pass
        found = {(v['field'], v['value'], v['location']) for v in values}
        for value in row['publisher_embedded_timestamps']:
            assert (value['field'], value['value'], value['location']) in found
            normalized_date = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', value['value'].replace('Z', '+00:00'))
            parsed = datetime.fromisoformat(normalized_date)
            utc = parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
            if value['utc_if_explicit_offset'] is not None:
                assert utc == value['utc_if_explicit_offset']
            elif utc is not None:
                timestamp_caveats.append({'id': row['id'], 'value': value['value'], 'field': value['field'], 'utc_recoverable_from_raw_value': utc, 'artifact_utc': None, 'required_ui_wording': 'Not supplied in cached artifact; do not assert that an explicit offset is absent.'})
        if row['url'] in api_articles:
            article = api_articles[row['url']]
            assert row['title'] == article['title']
            assert row['language'] == article['language'].lower()
            assert row['gdelt_seendate'] == article['seendate']
            title_provenance = 'cached GDELT ArticleList record'
        else:
            assert row['title'] == ''.join(parser.title_parts).strip()
            title_provenance = 'cached publisher HTML title'
        filename, a, b = EXCERPTS[row['id']]
        lines = extracted.splitlines()
        ranges = [(a, b)] + EXTRA_RANGES.get(row['id'], [])
        selected = '\n[…]\n'.join('\n'.join(lines[x-1:y]) for x, y in ranges)
        shown = row['excerpt']['text']
        assert selected.startswith(shown)
        assert row['excerpt']['truncated'] == (len(shown) < len(selected))
        audit_rows.append({'id': row['id'], 'title': row['title'], 'url': row['url'], 'publisher': row['publisher'], 'language': row['language'], 'title_provenance': title_provenance, 'relevance': row['relevance'], 'rationale': row['scope_notes'], 'support_terms': SUPPORT[row['id']], 'duplicate_group': row['duplicate_group'], 'confirmed_copied_text': row['confirmed_duplicate'], 'possible_shared_reporting': row['possible_shared'], 'body_hash_verified': True, 'extracted_text_matches_body': True, 'excerpt_prefix_and_omissions_verified': True, 'timestamp_fields_verified': len(row['publisher_embedded_timestamps']), 'capture_matches_cache_metadata': row['retrieved_at_utc'] == meta['completed_at_utc'], 'source_text': row['text_file']})
    a = (ROOT / 'results/pages/gdelt_iran_laverdad_es.txt').read_text().splitlines()[166:188]
    b = (ROOT / 'results/pages/gdelt_iran_diariovasco_es.txt').read_text().splitlines()[136:158]
    assert a == b
    raw = json.loads((ROOT / 'results/requests/shipping_english.json').read_text())
    daily = {p['date']: p for p in raw['data']['timeline'][0]['data']}
    assert daily['20260831T000000Z']['value'] == dataset['aggregate_context']['count'] == 694
    assert len(daily) == 24 and max(daily) == '20260913T000000Z'
    unknown = [r for r in evidence if r['id'] not in SUPPORT]
    assert {r['id'] for r in unknown} == set(CANDIDATE_NOTES)
    candidates = []
    for row in unknown:
        urls = {canonical(row['url'])}
        if row['url'] in api_articles and api_articles[row['url']].get('url_mobile'):
            urls.add(canonical(api_articles[row['url']]['url_mobile']))
        matches = [m for m in cache if m['method'] == 'GET' and (canonical(m['url']) in urls or canonical(m['final_url']) in urls)]
        bodies = [m for m in matches if m['status'] == 200]
        candidates.append({'id': row['id'], 'title': row['title'], 'url': row['url'], 'current_classification': row['relevance'], 'review': CANDIDATE_NOTES[row['id']], 'matching_publisher_cache_statuses': [m['status'] for m in matches], 'successful_publisher_body_available': bool(bodies), 'promoted_to_inspected': False, 'metadata_provenance': row['cache_metadata']})
    assert not any(r['successful_publisher_body_available'] for r in candidates)
    protected = set(p['body_file'] for p in cache) | set(p['metadata_file'] for p in cache)
    protected.update(r['text_file'] for r in evidence if r['text_file'])
    protected.update(['results/msc_validation/evidence_table.json', 'results/msc_validation/timeline_data.json', 'results/raw_candidates.json', 'results/requests/shipping_english.json', 'results/article_evidence.json'])
    frozen = {name: digest(ROOT / name) for name in sorted(protected)}
    baseline = OUT / 'protected_evidence_hashes.json'
    if baseline.exists():
        assert json.loads(baseline.read_text()) == frozen, 'Cached evidence changed during judging preparation'
    else:
        save('protected_evidence_hashes.json', frozen)
    result = {'summary': dataset['summary'], 'inspected_language_counts': dict(Counter(r['language'] for r in inspected)), 'aggregate_count': 694, 'aggregate_date': '2026-08-31', 'aggregate_language': 'english', 'aggregate_source': 'cached TimelineVolRaw, not search-list length', 'daily_bins_returned': len(daily), 'daily_last_bin': max(daily), 'copied_text_evidence': {'sources': ['gdelt_iran_laverdad_es', 'gdelt_iran_diariovasco_es'], 'equal_lines_in_reviewed_spans': len(a), 'ranges': ['167–188', '137–158'], 'meaning': 'Matching cached passages, not an assertion about who copied whom or every byte of both documents'}, 'additional_candidates_reviewed': len(candidates), 'additional_inspected_pages_added': 0, 'expansion_decision': 'No expansion: remaining candidate records lack successful cached publisher bodies. Metadata classifications remain uncertain; no synthetic examples or new fetches.', 'scope': 'Audit checks provenance and faithful representation of publisher claims, not the truth of publishers’ reporting.', 'network_requests': 0, 'timestamp_caveats': timestamp_caveats, 'inspected_rows': audit_rows, 'candidate_reviews': candidates, 'cached_response_records_checked': len(cache), 'protected_evidence_files': len(frozen)}
    save('claim_audit.json', result)
    lines = ['# Judging claim audit — cache only', '', 'No new publisher or GDELT requests. Original cached evidence and classification artifacts are unchanged. This audit verifies provenance and faithful representation of publisher claims, not their independent factual truth.', '', '## Visible claim families', '', '| Claim | Evidence / disposition |', '|---|---|', '| 694 matches | Cached English shipping TimelineVolRaw bin for 2026-08-31. Separate from the inspected set; attribution to MSC is unestablished. |', '| 13 inspected / 3 related / 10 other | Counts and membership checked against the frozen evidence table and reviewed bodies. “Other” is relative to MSC, not necessarily an invalid broad shipping match. |', '| 6 GDELT / 7 external | The 6 inspected GDELT-result pages are Spanish; the 694 aggregate is English. These populations must not be conflated. |', '| 32 inventory / 19 uninspected | 16 additional GDELT metadata records, 2 cached HTTP 401 publisher failures, and 1 GKG metadata hint. |', '| Confirmed copied text | Two pages have 22 identical extracted lines in reviewed spans. One group; folding removes one card, not one independent story. |', '| Possible shared reporting | Three MSC pages have unverified upstream independence; two other inventory headlines carry possible-syndication flags. No promotion to confirmed duplicates. |', '| Excerpts | All 13 inspected cached HTML hashes and their text extraction verified; displayed excerpts are source prefixes with explicit omissions/truncation. |', '| Timestamps | Every raw embedded timestamp checked against cached HTML/JSON-LD; supplied UTC conversions independently checked. Legacy artifacts omit some parseable +0900 conversions, so the UI must say “Not supplied in cached artifact,” not “no explicit offset.” Chinese remains date-only. Event dates, platform labels and prototype capture time stay separate. |', '| Unresolved details | English/Chinese route conflict; adviser-attributed attack date versus uncertain Chinese timing; secondary citations and unavailable original carrier notice; incomplete DOC intervals with later bulk data. |', '| Headline/product claims | Evidence-inspection prototype and story replay only. No validated warning, language lead, earliest publication, precision/recall or false-alarm result. |', '', '## Additional cached candidate review', '', 'All remaining 19 records were screened. No successful publisher body exists in the response cache (including mobile-URL aliases) for any of them; no inspected-set expansion is defensible. The matching-headline pair below is documented as a possible metadata-level relation only; frozen UI classifications and counts are preserved.', '', '| Candidate | Available evidence and classification |', '|---|---|']
    for row in candidates:
        label = (row['id'] + ': ' + row['title']).replace('|', '\\|')
        lines.append('| [' + label + '](' + row['url'] + ') | ' + row['review'].replace('|', '\\|') + ' Remains `' + row['current_classification'] + '`. |')
    lines.extend(['', '## Inspected-source audit trail', '', '| Source | Classification | Body / excerpt / timestamp audit |', '|---|---|---|'])
    for row in audit_rows:
        lines.append('| ' + row['id'] + ' | ' + row['relevance'] + ' | Cached body hash and extracted text match; source excerpt and ' + str(row['timestamp_fields_verified']) + ' embedded timestamp fields verified. |')
    (OUT / 'claim_audit.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('inspected_rows', 'candidate_reviews')}, indent=2))


if __name__ == '__main__':
    run()
