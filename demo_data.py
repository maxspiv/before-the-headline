import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
INSPECTED_CLASSES = {'direct_suspension', 'roundup_suspension', 'different_story_verified'}
POSSIBLE_SHARED_IDS = {'shipping_msc_en', 'shipping_msc_es', 'shipping_msc_zh'}
EXCERPTS = {
    'shipping_msc_en': ('shipping_msc_en.txt', 45, 54),
    'shipping_msc_es': ('shipping_msc_es.txt', 29, 30),
    'shipping_msc_zh': ('shipping_msc_zh.txt', 269, 269),
    'shipping_typhoon_en': ('shipping_typhoon_en.txt', 133, 167),
    'shipping_hormuz_en': ('shipping_hormuz_en.txt', 29, 38),
    'semiconductors_exports_en': ('semiconductors_exports_en.txt', 58, 73),
    'energy_yaroslavl_oilprice_en': ('energy_yaroslavl_oilprice_en.txt', 404, 425),
    'gdelt_panama_es': ('gdelt_panama_es.txt', 73, 93),
    'gdelt_larak_es': ('gdelt_larak_es.txt', 70, 95),
    'gdelt_iran_laverdad_es': ('gdelt_iran_laverdad_es.txt', 167, 181),
    'gdelt_iran_diariovasco_es': ('gdelt_iran_diariovasco_es.txt', 137, 151),
    'gdelt_quito_es': ('gdelt_quito_es.txt', 50, 69),
    'gdelt_outlook_es': ('gdelt_outlook_es.txt', 52, 78),
}
EXTRA_RANGES = {'shipping_msc_es': [(35, 35)], 'shipping_msc_zh': [(283, 284)]}
ORDER = ['gdelt_panama_es', 'gdelt_larak_es', 'gdelt_iran_laverdad_es', 'gdelt_iran_diariovasco_es', 'gdelt_quito_es', 'gdelt_outlook_es', 'shipping_msc_en', 'shipping_msc_es', 'shipping_msc_zh']
UNCERTAINTIES = [
    {'id': 'direction', 'title': 'One vessel. Conflicting directions.', 'detail': 'India Shipping News describes departure from Novorossiysk toward Tekirdag. JCtrans describes a vessel heading toward Novorossiysk. The core suspension story matches; the route detail remains unresolved.', 'source_ids': ['shipping_msc_en', 'shipping_msc_zh']},
    {'id': 'timing', 'title': 'Attack time is not announcement time.', 'detail': 'The English page attributes an August 25 attack date to an adviser. The Spanish roundup dates the suspension to August 26. The Chinese page says the precise attack time is unclear. None is a verified first-publication time.', 'source_ids': ['shipping_msc_en', 'shipping_msc_es', 'shipping_msc_zh']},
    {'id': 'sharing', 'title': 'Three pages, not three proven origins.', 'detail': 'JCtrans compiles Bloomberg and Lloyd’s List; Prensa Latina cites Kommersant. Shared upstream reporting remains possible. The carrier notice and upstream originals were not verified.', 'source_ids': ['shipping_msc_zh', 'shipping_msc_es']},
    {'id': 'gaps', 'title': 'A blank interval is not silence.', 'detail': 'All three pages were captured September 19. Their GDELT observation times remain unavailable. Prior broad DOC timelines end September 13, but later bulk records exist. Neither complete coverage nor the cause of the DOC cutoff is established.', 'source_ids': ['shipping_msc_en', 'shipping_msc_es', 'shipping_msc_zh']},
]


def safe_external_url(value):
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password else None
    except (TypeError, ValueError, AttributeError):
        return None


def inspected(row):
    return row.get('publisher_body_status') == 200 and bool(row.get('text_file')) and row.get('relevance') in INSPECTED_CLASSES


def claim_label(event):
    value = event.get('publisher_publication_utc_claim')
    if value is None:
        return event['display_date'] + ' · date only; time and timezone unknown'
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('A UTC publication claim must have an explicit offset')
    return value.replace('T', ' ').replace('+00:00', ' UTC') + ' · publisher claim'


def load_dataset(root=ROOT):
    root = Path(root)
    paths = {'evidence': root / 'results/msc_validation/evidence_table.json', 'timeline': root / 'results/msc_validation/timeline_data.json', 'aggregate': root / 'results/raw_candidates.json'}
    blobs = {key: path.read_bytes() for key, path in paths.items()}
    evidence, timeline, candidates = (json.loads(blobs[key]) for key in ('evidence', 'timeline', 'aggregate'))
    if len({r['id'] for r in evidence}) != len(evidence):
        raise ValueError('Evidence IDs must be unique')
    event_map = {event['id']: event for event in timeline['events']}
    retained = {row['id'] for row in evidence if row['include_in_story_replay']}
    if retained != set(event_map):
        raise ValueError('Evidence and replay artifacts disagree on retained pages')
    aggregate = [r for r in candidates if (r['topic'], r['language'], r['date']) == ('shipping', 'english', '20260831T000000Z')]
    if len(aggregate) != 1:
        raise ValueError('Expected the frozen English shipping aggregate context')
    duplicate_sizes = Counter(r['duplicate_group'] for r in evidence if r['duplicate_group'].startswith('SYND-'))
    cards = []
    for row in evidence:
        is_inspected = inspected(row)
        related = row['relevance'] in ('direct_suspension', 'roundup_suspension')
        category = 'related' if related else 'unrelated' if row['relevance'] == 'different_story_verified' else 'uncertain'
        confirmed = row['duplicate_group'].startswith('SYND-') and duplicate_sizes[row['duplicate_group']] > 1
        possible = row['id'] in POSSIBLE_SHARED_IDS or row['duplicate_group'].startswith('POSSIBLE-SYND-')
        excerpt = None
        if is_inspected and row['id'] in EXCERPTS:
            filename, start, end = EXCERPTS[row['id']]
            expected = 'results/pages/' + filename
            if row['text_file'] != expected:
                raise ValueError('Excerpt path differs from the reviewed source mapping')
            source = root / expected
            content = source.read_text().splitlines()
            ranges = [(start, end)] + EXTRA_RANGES.get(row['id'], [])
            if len(content) < max(b for a, b in ranges):
                raise ValueError('Reviewed excerpt range is unavailable: ' + expected)
            selected = '\n[…]\n'.join('\n'.join(content[a - 1:b]) for a, b in ranges)
            text = selected[:900]
            if len(selected) > 900 and ' ' in text[-80:]:
                text = text.rsplit(' ', 1)[0]
            excerpt = {'text': text, 'truncated': len(text) < len(selected), 'source_file': expected, 'line_start': start, 'line_end': ranges[-1][1], 'line_ranges': ranges, 'range_label': ', '.join(str(a) if a == b else str(a) + '–' + str(b) for a, b in ranges), 'text_file_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'meaning': 'Selected passages from the locally cached publisher page. […] marks omitted lines; this is not a generated summary.'}
        event = event_map.get(row['id'])
        origin_label = 'GDELT result' if row['origin'].startswith('GDELT_artlist') else 'Bulk metadata only' if row['origin'] == 'GKG_metadata_hint_only' else 'External discovery'
        cards.append({
            **row, 'inspected': is_inspected, 'category': category,
            'category_label': {'related': 'Matches MSC story', 'unrelated': 'Other story / not MSC', 'uncertain': 'Unresolved / uninspected'}[category],
            'origin_label': origin_label, 'confirmed_duplicate': confirmed, 'possible_shared': possible,
            'duplicate_status': 'confirmed' if confirmed else 'possible' if possible else 'unassessed',
            'confirmed_group_size': duplicate_sizes[row['duplicate_group']] if confirmed else None,
            'excerpt': excerpt, 'external_url': safe_external_url(row['url']),
            'claim_label': claim_label(event) if event else row['publisher_visible_timestamp'],
            'date_only': bool(event and event['publisher_publication_utc_claim'] is None),
            'uncertainty_ids': [u['id'] for u in UNCERTAINTIES if row['id'] in u['source_ids']],
        })
    ranks = {value: i for i, value in enumerate(ORDER)}
    cards.sort(key=lambda row: (ranks.get(row['id'], len(ORDER)), next(i for i, original in enumerate(evidence) if original['id'] == row['id'])))
    reviewed = [r for r in cards if r['inspected']]
    summary = {'inventory_total': len(cards), 'inspected_total': len(reviewed), 'uninspected_total': len(cards) - len(reviewed), 'related_inspected': sum(r['category'] == 'related' for r in reviewed), 'unrelated_inspected': sum(r['category'] == 'unrelated' for r in reviewed), 'confirmed_duplicate_pages': sum(r['confirmed_duplicate'] for r in reviewed), 'confirmed_duplicate_groups': len({r['duplicate_group'] for r in reviewed if r['confirmed_duplicate']}), 'possible_shared_inspected': sum(r['possible_shared'] for r in reviewed), 'inspected_gdelt_pages': sum(r['origin_label'] == 'GDELT result' for r in reviewed), 'inspected_external_pages': sum(r['origin_label'] == 'External discovery' for r in reviewed)}
    labels = {row['id']: {'id': row['id'], 'publisher': row['publisher'], 'language': row['language']} for row in cards}
    uncertainties = [{**u, 'sources': [labels[sid] for sid in u['source_ids']]} for u in UNCERTAINTIES]
    return {'records': cards, 'by_id': {r['id']: r for r in cards}, 'timeline': timeline, 'uncertainties': uncertainties, 'summary': summary, 'artifact_hashes': {key: hashlib.sha256(body).hexdigest() for key, body in blobs.items()}, 'aggregate_context': {'count': aggregate[0]['article_count'], 'date': '2026-08-31', 'language': 'English', 'source': 'GDELT TimelineVolRaw aggregate', 'meaning': 'Separate context only. This curated inspected set is not a representative sample of these matches. The retained pages do not explain or attribute the ' + str(aggregate[0]['article_count']) + '-match spike.'}, 'scope_notice': 'The reviewed set mixes GDELT results and externally discovered contextual pages, including other-topic checks. “Other story” means unrelated to this MSC suspension, not necessarily an invalid shipping-query match.'}


def filter_records(dataset, include_unrelated=True, include_possible=True, fold_confirmed=False, include_uninspected=False):
    cohort = [r for r in dataset['records'] if include_uninspected or r['inspected']]
    eligible = [r for r in cohort if (include_unrelated or r['category'] != 'unrelated') and (include_possible or not r['possible_shared'])]
    displayed, groups, folded = [], set(), []
    for row in eligible:
        if fold_confirmed and row['confirmed_duplicate']:
            if row['duplicate_group'] in groups:
                folded.append(row['id'])
                continue
            groups.add(row['duplicate_group'])
        displayed.append(row)
    cards = [{key: row[key] for key in ('id', 'title', 'publisher', 'language', 'category', 'category_label', 'origin_label', 'inspected', 'confirmed_duplicate', 'possible_shared', 'duplicate_status', 'duplicate_group', 'confirmed_group_size', 'claim_label', 'date_only', 'scope_notes', 'include_in_story_replay')} for row in displayed]
    counts = {'cohort_count': len(cohort), 'visible_count': len(displayed), 'visible_inspected': sum(r['inspected'] for r in displayed), 'visible_uninspected': sum(not r['inspected'] for r in displayed), 'filtered_out_count': len(cohort) - len(eligible), 'folded_copy_count': len(folded), 'related_visible': sum(r['category'] == 'related' for r in displayed), 'unrelated_visible': sum(r['category'] == 'unrelated' for r in displayed), 'uncertain_visible': sum(r['category'] == 'uncertain' for r in displayed)}
    return {'cards': cards, 'counts': counts, 'folded_ids': folded, 'summary': dataset['summary'], 'aggregate_context': dataset['aggregate_context'], 'scope_notice': dataset['scope_notice'], 'uncertainties': dataset['uncertainties'], 'artifact_hashes': dataset['artifact_hashes'], 'filters': {'include_unrelated': include_unrelated, 'include_possible': include_possible, 'fold_confirmed': fold_confirmed, 'include_uninspected': include_uninspected}}


def source_detail(dataset, source_id):
    if source_id not in dataset['by_id']:
        raise KeyError(source_id)
    row = dataset['by_id'][source_id]
    members = [r for r in dataset['records'] if r['id'] != source_id and r['confirmed_duplicate'] and row['confirmed_duplicate'] and r['duplicate_group'] == row['duplicate_group']]
    return {**row, 'confirmed_group_members': [{'id': r['id'], 'title': r['title'], 'publisher': r['publisher']} for r in members], 'uncertainties': [u for u in dataset['uncertainties'] if u['id'] in row['uncertainty_ids']], 'replay_event': next((e for e in dataset['timeline']['events'] if e['id'] == source_id), None)}


def replay_view(dataset, day='all'):
    days = sorted({event['display_date'] for event in dataset['timeline']['events']})
    if day != 'all' and day not in days:
        raise ValueError('Unknown publisher-claimed date')
    events = [{**e, 'claim_label': dataset['by_id'][e['id']]['claim_label'], 'date_only': e['publisher_publication_utc_claim'] is None} for e in dataset['timeline']['events'] if day == 'all' or e['display_date'] == day]
    return {'events': events, 'days': days, 'selected_day': day, 'visible_count': len(events), 'total_count': len(dataset['timeline']['events']), 'context': dataset['timeline']['context'], 'date_axis_meaning': dataset['timeline']['date_axis_meaning'], 'empty_interval_meaning': dataset['timeline']['empty_interval_meaning'], 'filter_scope': 'Replay always uses the retained story pages. Evidence-browser filters do not change this set.', 'lead_time': None, 'normalized_comparison': None}
