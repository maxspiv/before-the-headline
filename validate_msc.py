import argparse
import csv
import hashlib
import html
import json
import io
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from feasibility import save_json

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'results' / 'msc_validation'
CORE = ('shipping_msc_en', 'shipping_msc_es', 'shipping_msc_zh')
REVIEWS = {
    'shipping_msc_en': ('India Shipping News', 'direct_suspension', 'Retain: MSC stops new bookings to/from Novorossiysk after the MSC Ulsan III attack. Other passages discuss RMS Team, Yanina, grain/oil terminals and other carriers; those are separate incidents, not additional suspension observations.', 'MSC-EN', 'Upstream independence unknown; adviser attribution for attack date does not independently verify the suspension.', 'August 31, 2026'),
    'shipping_msc_es': ('Prensa Latina', 'roundup_suspension', 'Retain the MSC substory only: suspension on August 26 after the named vessel attack. Akkon/RMS Team, FESCO/Yanina, Arkas and other vessels/carriers in this roundup are distinct incidents.', 'MSC-ES', 'Kommersant explicitly attributed; overlap with other upstream reporting is unknown.', 'September 1, 2026, 06:02'),
    'shipping_msc_zh': ('JCtrans', 'direct_suspension', 'Retain: MSC suspends new Novorossiysk bookings after the named vessel attack, not all Russian ports. Compiled secondary account; precise attack time explicitly uncertain. Direction of vessel travel conflicts with English account.', 'MSC-ZH', 'Explicitly compiles Bloomberg and Lloyd’s List; not independent confirmation of original reporting.', '2026-09-01; timezone/time unavailable'),
    'shipping_typhoon_en': ('Tradlinx', 'different_story_verified', 'Exclude: Saudel/Shanghai/Ningbo congestion, not MSC Novorossiysk booking suspension.', 'OTHER-SAUDEL', 'No duplicate established in this inspected set.', 'August 31, 2026'),
    'shipping_hormuz_en': ('gCaptain', 'different_story_verified', 'Exclude: tanker near Khasab/Hormuz and separate Indian Ocean encounter, not the named MSC booking suspension.', 'OTHER-HORMUZ-TANKER', 'No duplicate established in this inspected set.', 'August 31, 2026'),
    'semiconductors_exports_en': ('The Asia Business Daily', 'different_story_verified', 'Exclude: Korean semiconductor export growth; publisher discloses AI translation.', 'OTHER-CHIP-EXPORTS', 'Translated edition; upstream-language independence not established.', 'September 11, 2026, 09:31 KST'),
    'energy_yaroslavl_oilprice_en': ('OilPrice.com', 'different_story_verified', 'Exclude: Yaroslavl refinery attack, not Novorossiysk shipping suspension.', 'OTHER-YAROSLAVL', 'No duplicate established from accessible text; Reuters pages were blocked.', 'September 17, 2026, 09:30 CDT'),
    'energy_ryazan_en': ('Reuters', 'uncertain_unavailable_body', 'HTTP 401: only metadata/title/URL available; title suggests Ryazan, not the MSC suspension. Do not treat as a verified full-text classification.', 'UNKNOWN-REUTERS-RYAZAN', 'Not assessed.', 'Unknown from cached publisher body'),
    'energy_yaroslavl_en': ('Reuters', 'uncertain_unavailable_body', 'HTTP 401: only metadata/title/URL available; title suggests Yaroslavl. Do not assert duplicate equivalence with OilPrice.', 'UNKNOWN-REUTERS-YAROSLAVL', 'Not assessed.', 'Unknown from cached publisher body'),
    'gdelt_panama_es': ('Gestión', 'different_story_verified', 'Exclude: Panama Canal drought and reservation policy, not Novorossiysk.', 'OTHER-PANAMA', 'EFE-attributed; no matching copy verified in inspected set.', 'August 30, 2026, 20:30'),
    'gdelt_larak_es': ('Negocios', 'different_story_verified', 'Exclude: attack on Larak mine launchers; related to regional shipping risk, not MSC suspension.', 'OTHER-LARAK-NEGOCIOS', 'Same broad incident as M.P. copies, but no textual duplicate established.', 'August 31, 2026, 06:57'),
    'gdelt_iran_laverdad_es': ('La Verdad', 'different_story_verified', 'Exclude from MSC replay. Matching inspected headline, byline, dates and paragraphs with Diario Vasco: one syndicated-text group, not two independent origins.', 'SYND-LARAK-MP-1', 'Verified matching inspected text with Diario Vasco; collapse group for any story display.', 'Original August 30, 2026; updated August 31, 07:51'),
    'gdelt_iran_diariovasco_es': ('Diario Vasco', 'different_story_verified', 'Exclude from MSC replay; duplicate/syndicated text with La Verdad.', 'SYND-LARAK-MP-1', 'Verified matching inspected text with La Verdad; collapse group for any story display.', 'Original August 30, 2026; updated August 31, 07:51'),
    'gdelt_quito_es': ('El Diario', 'different_story_verified', 'Exclude: Quito traffic-camera installation. The broad shipping-query match is not evidence of maritime relevance; matching mechanism unknown.', 'OTHER-QUITO', 'No duplicate established.', 'August 31, 2026, 15:37'),
    'gdelt_outlook_es': ('Expreso', 'different_story_verified', 'Exclude: Microsoft email outage, not maritime suspension; matching mechanism unknown.', 'OTHER-OUTLOOK', 'No duplicate established.', 'Created/updated August 31, 2026, 16:23'),
}


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values, self.scripts, self.title_parts = [], [], []
        self.ld = None
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'title':
            self.in_title = True
        if tag == 'meta':
            name = attrs.get('property') or attrs.get('name') or attrs.get('itemprop') or ''
            if name.lower() in ('article:published_time', 'article:modified_time', 'datepublished', 'datemodified'):
                self.values.append({'field': name, 'value': attrs.get('content'), 'location': 'HTML meta'})
        if tag == 'script' and attrs.get('type') == 'application/ld+json':
            self.ld = []

    def handle_data(self, value):
        if self.in_title:
            self.title_parts.append(value)
        if self.ld is not None:
            self.ld.append(value)

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        if tag == 'script' and self.ld is not None:
            self.scripts.append(''.join(self.ld))
            self.ld = None


def utc(value):
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.astimezone(timezone.utc).isoformat() if date.tzinfo else None
    except (TypeError, ValueError, AttributeError):
        return None


def primary_identity(identity, url):
    if isinstance(identity, str):
        return identity.split('#', 1)[0].rstrip('/') == url.rstrip('/')
    if isinstance(identity, dict):
        return any(primary_identity(identity.get(key), url) for key in ('@id', 'url'))
    return False


def json_dates(value, url, path='$'):
    found = []
    if isinstance(value, dict):
        identities = [value.get('@id', ''), value.get('url', ''), value.get('mainEntityOfPage', '')]
        scoped = any(primary_identity(identity, url) for identity in identities)
        if scoped:
            for key in ('datePublished', 'dateModified'):
                if isinstance(value.get(key), str):
                    found.append({'field': key, 'value': value[key], 'location': 'primary-page JSON-LD ' + path})
        for key, child in value.items():
            found.extend(json_dates(child, url, path + '.' + key))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            found.extend(json_dates(child, url, path + '[' + str(i) + ']'))
    return found


def cached_body(source):
    for path in sorted((ROOT / 'cache' / source['cache_key']).glob('*.json')):
        meta = json.loads(path.read_text())
        if meta['sha256'] == source['response_sha256']:
            body = (path.parent / meta['body_file']).read_bytes()
            if hashlib.sha256(body).hexdigest() != source['response_sha256']:
                raise ValueError('Publisher body cache corruption')
            return body, str(path.relative_to(ROOT))
    raise ValueError('Pinned publisher response not found: ' + source['id'])


def clean_cell(value):
    value = str(value)
    return "'" + value if value.startswith(('=', '+', '-', '@')) else value


def md(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def build():
    manifest = json.loads((ROOT / 'results/source_manifest.json').read_text())
    articles = json.loads((ROOT / 'results/article_evidence.json').read_text())
    indexed = {a['url']: a for a in articles}
    rows = []
    for source in manifest:
        sid = source['id']
        publisher, relevance, notes, group, syndication, visible = REVIEWS[sid]
        body, cache_metadata = cached_body(source)
        parser = Metadata()
        parser.feed(body.decode('utf-8', errors='replace'))
        values = parser.values
        for script in parser.scripts:
            try:
                values.extend(json_dates(json.loads(script), source['url']))
            except ValueError:
                pass
        for value in values:
            value['utc_if_explicit_offset'] = utc(value['value'])
            value['meaning'] = 'publisher-claimed modification, not first publication' if 'modif' in value['field'].lower() else 'publisher-claimed publication, not verified first appearance'
        article = indexed.get(source['url'], {})
        fallback_title = {'energy_ryazan_en': "Russia's Ryazan oil refinery has stopped operations after Sept 6 drone attack, sources say", 'energy_yaroslavl_en': "Russia's Yaroslavl refinery shuts processing after a drone attack, sources say"}
        title = article.get('title') or (''.join(parser.title_parts).strip() if source['status'] == 200 else fallback_title[sid])
        rows.append({'id': sid, 'title': title, 'url': source['url'], 'publisher': publisher, 'language': source['language'], 'origin': source['origin'], 'retrieved_at_utc': source['retrieved_at_utc'], 'retrieval_meaning': 'prototype capture time; page contents at this time, not a historical as-of observation', 'publisher_visible_timestamp': visible, 'publisher_embedded_timestamps': values, 'gdelt_seendate': article.get('seendate'), 'gdelt_seendate_meaning': 'GDELT observation/index timestamp; not certified publication or exact availability' if article else 'unavailable: publisher page was externally discovered; GDELT membership not established', 'relevance': relevance, 'scope_notes': notes, 'duplicate_group': group, 'syndication_assessment': syndication, 'independent_reporting_verified': False, 'include_in_story_replay': sid in CORE, 'publisher_body_status': source['status'], 'body_sha256': source['response_sha256'], 'cache_metadata': cache_metadata, 'text_file': source['text_file']})
    seen = {r['url'] for r in rows}
    for i, article in enumerate(articles):
        if article['url'] in seen:
            continue
        seen.add(article['url'])
        possible = article['domain'] in ('elnortedecastilla.es', 'lavozdegalicia.es')
        rows.append({'id': 'gdelt_metadata_' + str(i + 1).zfill(2), 'title': article['title'], 'url': article['url'], 'publisher': article['domain'], 'language': article['language'].lower(), 'origin': 'GDELT_artlist_metadata_only', 'retrieved_at_utc': json.loads((ROOT / 'results/requests' / (article['request_name'] + '.json')).read_text())['retrieved_at_utc'], 'retrieval_meaning': 'prototype capture time of GDELT metadata, not publisher body', 'publisher_visible_timestamp': 'unavailable; publisher body not inspected', 'publisher_embedded_timestamps': [], 'gdelt_seendate': article['seendate'], 'gdelt_seendate_meaning': 'GDELT observation/index timestamp; not certified publication or exact availability', 'relevance': 'uncertain_metadata_only', 'scope_notes': 'Not verified as this specific booking suspension. Title-level evidence alone is insufficient; exclude from replay without claiming absent relevance.', 'duplicate_group': 'POSSIBLE-SYND-LARAK-MP-1' if possible else 'UNASSESSED-' + str(i + 1), 'syndication_assessment': 'Similar headline to inspected Larak copies; possible syndication only, not collapsed as a verified duplicate.' if possible else 'Not assessed from full text.', 'independent_reporting_verified': False, 'include_in_story_replay': False, 'publisher_body_status': None, 'body_sha256': None, 'cache_metadata': 'results/requests/' + article['request_name'] + '.json', 'text_file': None})
    hint_path = OUT / 'bulk_candidate_hints.json'
    hints = json.loads(hint_path.read_text()) if hint_path.exists() else []
    for i, hint in enumerate(hints):
        if hint['document_url'] in seen:
            continue
        seen.add(hint['document_url'])
        rows.append({'id': 'bulk_hint_' + str(i + 1).zfill(2), 'title': hint['page_title_if_present'] or '(title unavailable in GKG record)', 'url': hint['document_url'], 'publisher': hint['publisher_domain'], 'language': hint['source_language'] + ' (GKG source-language code)', 'origin': 'GKG_metadata_hint_only', 'retrieved_at_utc': hint['retrieved_at_utc'], 'retrieval_meaning': 'prototype download of bulk metadata; no publisher body observed', 'publisher_visible_timestamp': 'unavailable; publisher body not inspected', 'publisher_embedded_timestamps': [], 'gdelt_seendate': None, 'gdelt_seendate_meaning': 'DOC seendate unavailable; GKG record date must not be relabeled as DOC seendate', 'gkg_document_date': hint['gkg_document_date'], 'gkg_record_id': hint['record_id'], 'relevance': 'uncertain_bulk_metadata', 'scope_notes': 'Only a metadata token match (' + ', '.join(hint['matched_strings']) + '); the title suggests a different subject, but no publisher body was inspected. Not a verified booking-suspension observation.', 'duplicate_group': 'UNASSESSED-BULK-' + str(i + 1), 'syndication_assessment': 'Not assessed.', 'independent_reporting_verified': False, 'include_in_story_replay': False, 'publisher_body_status': None, 'body_sha256': None, 'bulk_response_sha256': hint['bulk_response_sha256'], 'cache_metadata': hint['cache_metadata'], 'text_file': None})
    event_claims = {
        'shipping_msc_en': 'August 25 attack date attributed to EOS Risk Group adviser; date of event, not publication or observation. English account says departing Novorossiysk.',
        'shipping_msc_es': 'August 26 MSC suspension date in Kommersant-attributed roundup; retrospective event claim, not an article observed on that date.',
        'shipping_msc_zh': 'Exact attack time explicitly unclear; describes vessel heading toward Novorossiysk, conflicting with English account. No precise suspension time independently established.',
    }
    for row in rows:
        row.setdefault('gkg_document_date', None)
        row.setdefault('gkg_record_id', None)
        row['reported_event_timestamps_and_uncertainty'] = event_claims.get(row['id'], 'No target-event timestamp established from this evidence.')
    save_json(OUT / 'evidence_table.json', rows)
    columns = ('id', 'title', 'url', 'publisher', 'language', 'publisher_visible_timestamp', 'publisher_embedded_timestamps', 'reported_event_timestamps_and_uncertainty', 'gkg_document_date', 'gkg_record_id', 'gdelt_seendate', 'gdelt_seendate_meaning', 'retrieved_at_utc', 'retrieval_meaning', 'origin', 'relevance', 'duplicate_group', 'syndication_assessment', 'scope_notes', 'include_in_story_replay', 'publisher_body_status', 'body_sha256', 'cache_metadata')
    with (OUT / 'evidence_table.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: clean_cell(json.dumps(row[k], ensure_ascii=False) if isinstance(row[k], list) else row[k] if row[k] is not None else 'unavailable') for k in columns})
    lines = ['# MSC Ulsan III / Novorossiysk: evidence table', '', 'A purposive evidence inventory, not an article census. Inclusion requires an inspected statement connecting MSC, the named vessel attack, Novorossiysk and suspension of bookings/services. Direct_suspension denotes topical focus, not primary or independent reporting. Other incidents at the same port or involving other vessels are not merged. Uninspected records remain uncertain.', '', 'Publisher timestamps below are claims in the cached page, not verified first publication. GDELT observation time is distinct; capture time is when this prototype fetched the evidence. Shared upstream reporting remains possible for all three retained pages.', '', '| ID / title and URL | Publisher / language | Available timestamps and meanings | Relevance and incident boundary | Duplicate / syndication group |', '|---|---|---|---|---|']
    for row in rows:
        dates = ['Visible publisher label: ' + row['publisher_visible_timestamp']]
        dedup = set()
        for value in row['publisher_embedded_timestamps']:
            item = (value['field'], value['value'])
            if item in dedup:
                continue
            dedup.add(item)
            dates.append(value['field'] + ': ' + str(value['value']) + ' [UTC ' + str(value['utc_if_explicit_offset'] or 'unresolved') + ']')
        if row['gkg_document_date']:
            dates.append('GKG document-date field: ' + row['gkg_document_date'] + '; update record ID: ' + row['gkg_record_id'] + '; not verified publisher time or DOC seendate')
        dates.extend(['GDELT observation: ' + str(row['gdelt_seendate'] or 'unavailable'), 'Prototype capture: ' + row['retrieved_at_utc'], 'Event claim (not publication): ' + row['reported_event_timestamps_and_uncertainty']])
        lines.append('| ' + ' | '.join((md(row['id']) + ': [' + md(row['title']) + '](' + row['url'] + ')', md(row['publisher'] + ' / ' + row['language']), '<br>'.join(md(d) for d in dates), md(row['relevance'] + ': ' + row['scope_notes']), md(row['duplicate_group'] + ': ' + row['syndication_assessment']))) + ' |')
    (OUT / 'evidence_table.md').write_text('\n'.join(lines) + '\n')
    events = []
    for sid in CORE:
        row = next(r for r in rows if r['id'] == sid)
        claimed = next((v for v in row['publisher_embedded_timestamps'] if v['field'].lower() in ('article:published_time', 'datepublished')), None)
        events.append({'id': sid, 'display_date': '2026-08-31' if sid == 'shipping_msc_en' else '2026-09-01', 'publisher': row['publisher'], 'language': row['language'], 'title': row['title'], 'url': row['url'], 'publisher_publication_utc_claim': claimed['utc_if_explicit_offset'] if claimed else None, 'precision': 'publisher metadata with explicit offset' if claimed else 'date only; timezone and time unknown', 'retrieved_at_utc': row['retrieved_at_utc'], 'gdelt_seendate': None, 'duplicate_group': row['duplicate_group'], 'syndication': row['syndication_assessment'], 'role': row['relevance']})
    timeline = {'mode': 'incomplete_evidence_only', 'title': 'Reported MSC Ulsan III / Novorossiysk booking suspension', 'start_date': '2026-08-25', 'end_date': '2026-09-19', 'date_axis_meaning': 'Publisher-claimed calendar dates of selected cached pages, not real-time observation or first publication. Date-only Chinese page is not ordered within September 1.', 'empty_interval_meaning': 'Unavailable/unsearched observations, not zero coverage', 'doc_cutoff_date': '2026-09-13', 'doc_cutoff_scope': 'Earlier broad-query DOC daily responses only, not an MSC story cutoff or a demonstrated GDELT-wide outage', 'events': events, 'context': ['Spanish roundup attributes suspension to August 26; this is a retrospective event claim, not an observed article from that day.', 'English page attributes attack to August 25; Chinese page says exact attack time unclear. Vessel travel direction conflicts.', 'All three pages were actually captured September 19. Carrier notice and upstream originals were not verified.', 'Exact URLs deduplicated. No verified duplicate among the retained pages; shared upstream derivation remains possible. No independent-source count is claimed.'], 'lead_time': None, 'normalized_comparison': None, 'decision': 'story_replay'}
    save_json(OUT / 'timeline_data.json', timeline)
    print('Wrote evidence table and frozen evidence-only timeline data to', OUT)


def hint_terms(text):
    return sorted({term.casefold() for term in re.findall(r'\bulsan\b|\bnovorossiysk\b|новороссийск|新罗西斯克', text, flags=re.I)})


def audit_bulk():
    sources = json.loads((OUT / 'bulk_samples.json').read_text()) + json.loads((OUT / 'bulk_post_cutoff.json').read_text())
    core_urls = {s['url'] for s in json.loads((ROOT / 'results/source_manifest.json').read_text()) if s['id'] in CORE}
    output, hints = [], []
    for source in sources:
        meta = source['response']
        if meta['status'] != 200 or meta['truncated']:
            raise ValueError('Incomplete bulk sample response')
        body_path = ROOT / 'cache/recovery_msc' / meta['cache_key'] / meta['body_file']
        body = body_path.read_bytes()
        if hashlib.sha256(body).hexdigest() != meta['sha256']:
            raise ValueError('Bulk cache hash mismatch')
        shapes, collections, dates, groups, exact = Counter(), Counter(), set(), defaultdict(set), []
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            expanded = sum(member.file_size for member in archive.infolist())
            if expanded > 160 * 1024 * 1024:
                raise ValueError('Expanded ZIP exceeds diagnostic memory/work budget')
            for member in archive.infolist():
                if member.is_dir():
                    continue
                with archive.open(member) as handle:
                    for raw in handle:
                        fields = raw.decode('utf-8', errors='replace').rstrip('\r\n').split('\t')
                        shapes[len(fields)] += 1
                        if len(fields) != 27:
                            continue
                        collections[fields[2]] += 1
                        dates.add(fields[1])
                        translation = dict(item.strip().split(':', 1) for item in fields[25].split(';') if ':' in item)
                        lang = translation.get('srclc', 'english_input_or_human_translation' if '-T' not in fields[0] and not fields[25] else 'unknown')
                        if fields[2] == '1':
                            groups[(fields[0].split('-')[0], lang)].add(fields[4])
                        if fields[4] in core_urls:
                            exact.append({'url': fields[4], 'record_id': fields[0], 'gkg_document_date': fields[1], 'language_field': fields[25]})
                        matched = hint_terms(raw.decode('utf-8', errors='replace'))
                        if matched:
                            title = re.search(r'<PAGE_TITLE>(.*?)</PAGE_TITLE>', fields[26], flags=re.S | re.I)
                            hints.append({'bulk_url': meta['url'], 'record_id': fields[0], 'gkg_document_date': fields[1], 'document_url': fields[4], 'publisher_domain': fields[3], 'source_language': lang, 'retrieved_at_utc': meta['completed_at_utc'], 'cache_metadata': str(body_path.with_suffix('.json').relative_to(ROOT)), 'bulk_response_sha256': meta['sha256'], 'matched_strings': matched, 'page_title_if_present': html.unescape(title.group(1)) if title else None, 'classification': 'unverified_metadata_hint_not_a_booking_suspension_observation', 'limitation': 'A vessel/city/port string in extracted metadata is not sufficient event identity; no original publisher body inspected in recovery.'})
            names = archive.namelist()
        row = {'url': meta['url'], 'compressed_bytes': len(body), 'expanded_bytes': expanded, 'members': names, 'column_count_histogram': dict(shapes), 'source_collection_histogram': dict(collections), 'gkg_document_date_values': sorted(dates), 'slice_unique_web_urls_by_batch_language_NOT_DOC_denominators': [{'batch': batch, 'language': lang, 'unique_urls': len(urls)} for (batch, lang), urls in sorted(groups.items())], 'exact_core_url_matches': exact, 'response_sha256': meta['sha256'], 'limitation': 'Only one scheduled batch for this stream/date. Neither a complete day nor the DOC corpus. No topic numerator or normalized series is inferred.'}
        output.append(row)
    save_json(OUT / 'bulk_schema_audit.json', output)
    save_json(OUT / 'bulk_candidate_hints.json', hints)
    summary = {'files': len(output), 'downloaded_zip_bytes': sum(r['compressed_bytes'] for r in output), 'all_rows_have_27_columns': all(set(r['column_count_histogram']) == {27} for r in output), 'core_exact_url_matches_in_selected_slices': sum(len(r['exact_core_url_matches']) for r in output), 'zero_matches_do_not_mean_absent_coverage': True, 'post_cutoff_record_dates': sorted({d for r in output if '/20260918' in r['url'] for d in r['gkg_document_date_values']}), 'candidate_hint_records_not_story_counts': len(hints), 'normalized_series_recovered': False, 'reason': 'Sparse diagnostic batches and metadata hints cannot reconstruct full-window, story-validated numerators and matching language denominators; GKG cannot be mixed with DOC counts.'}
    save_json(OUT / 'bulk_recovery_summary.json', summary)
    print(json.dumps(summary, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bulk-audit', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.bulk_audit:
        audit_bulk()
    else:
        build()


if __name__ == '__main__':
    main()
