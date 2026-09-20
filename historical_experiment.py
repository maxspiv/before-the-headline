import argparse
import csv
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT / 'historical'
OUT = BASE / 'results'
HTTP = BASE / 'raw'
S3 = 'https://gdelt-open-data.s3.us-east-1.amazonaws.com/'
INITIAL_CAP = 250_000_000
TOTAL_CAP = 5_000_000_000
DATES = ('20150301', '20170301', '20190301')
SAMPLE_TIMES = ('000000', '001500')
TABLES = {'gkg': 'gkg', 'mentions': 'mentions', 'events': 'export'}
LEDGER = BASE / 'transfer_ledger.json'


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def sha(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def ledger():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    state = {'started_at_utc': utcnow(), 'received_bytes': 0, 'requests': 0, 'last_finished': 0, 'initial_cap_bytes': INITIAL_CAP, 'total_cap_bytes': TOTAL_CAP, 'active_cap_bytes': INITIAL_CAP, 'network_closed': False}
    save(LEDGER, state)
    return state


def backoff(value, reference=None):
    reference = reference or datetime.now(timezone.utc)
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
            delay = (parsed - reference).total_seconds()
        except (TypeError, ValueError, OverflowError):
            delay = 0
    return max(30, delay)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def allowed_url(url):
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == 'https' and not parsed.username and not parsed.password and (
        (parsed.netloc == 'gdelt-open-data.s3.us-east-1.amazonaws.com' and (parsed.path == '/' or parsed.path.startswith('/v2/'))) or
        (parsed.netloc == 'data.gdeltproject.org' and parsed.path.startswith('/documentation/')))


def request(url, offline=False, method='GET', expected_size=None, ceiling=4_000_000):
    if not allowed_url(url):
        raise ValueError('Only anonymous historical S3 and official codebooks are allowed: ' + url)
    key = hashlib.sha256((method + ' ' + url).encode()).hexdigest()
    folder = HTTP / key
    existing = sorted(folder.glob('*.json'))
    if existing:
        meta = json.loads(existing[-1].read_text())
        path = folder / meta['body_file']
        if sha(path) != meta['sha256']:
            raise ValueError('Cached response hash mismatch: ' + str(path))
        if meta['status'] != 200 or not meta['complete'] or meta['error']:
            raise RuntimeError('Cached failure is unavailable data, not an empty result: ' + url)
        if expected_size is not None and meta['received_bytes'] != expected_size:
            raise ValueError('Cached object size mismatch')
        return meta, path
    if offline:
        raise RuntimeError('Missing cached response: ' + url)
    state = ledger()
    for attempt in range(2):
        if state['network_closed']:
            raise RuntimeError('Historical network pass is closed: ' + str(state.get('stop_reason')))
        cap = min(ceiling, state['active_cap_bytes'] - state['received_bytes'])
        if cap <= 0 or (expected_size is not None and expected_size > cap):
            raise RuntimeError('Transfer budget would be exceeded before requesting object')
        time.sleep(max(0, 1 - (time.time() - state['last_finished'])))
        folder.mkdir(parents=True, exist_ok=True)
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = folder / (name + '.body')
        started = utcnow()
        state['requests'] += 1
        save(LEDGER, state)
        status, headers, error, size, complete = None, {}, None, 0, False
        response = None
        try:
            req = urllib.request.Request(url, method=method, headers={'User-Agent': 'SignalNoise-HistoricalFeasibility/1.0', 'Accept-Encoding': 'identity'})
            try:
                response = urllib.request.build_opener(NoRedirect).open(req, timeout=60)
            except urllib.error.HTTPError as exc:
                response = exc
                error = str(exc)
            status, headers = response.code, dict(response.headers)
            length = response.headers.get('Content-Length')
            if method == 'GET' and length and int(length) > cap:
                raise ValueError('Content-Length exceeds request/remaining transfer cap')
            if status == 200 and expected_size is not None and length and int(length) != expected_size:
                raise ValueError('Object changed since size listing')
            with path.open('wb') as handle:
                if method != 'HEAD':
                    while size < cap:
                        chunk = response.read(min(65536, cap - size))
                        if not chunk:
                            complete = True
                            break
                        handle.write(chunk)
                        size += len(chunk)
                    if length and size == int(length):
                        complete = True
                else:
                    complete = True
            if expected_size is not None and status == 200 and size != expected_size:
                raise ValueError('Downloaded object is incomplete')
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            error = str(exc)
        finally:
            if response is not None:
                response.close()
            if not path.exists():
                path.write_bytes(b'')
        meta = {'url': url, 'method': method, 'status': status, 'headers': headers, 'requested_at_utc': started, 'completed_at_utc': utcnow(), 'received_bytes': size, 'complete': complete, 'error': error, 'body_file': path.name, 'sha256': sha(path), 'raw_path': str(path.relative_to(ROOT))}
        save(folder / (name + '.json'), meta)
        state['received_bytes'] += size
        state['last_finished'] = time.time()
        save(LEDGER, state)
        print(method, status, size, url, flush=True)
        if status in (429, 503, 502, 504):
            if attempt == 1:
                state.update(network_closed=True, stop_reason='Persistent throttling/service failure')
                save(LEDGER, state)
                raise RuntimeError(state['stop_reason'])
            retry = next((v for k, v in headers.items() if k.lower() == 'retry-after'), None)
            delay = backoff(retry)
            if delay > 300:
                state.update(network_closed=True, stop_reason='Retry-After exceeds bounded automatic retry wait; no early retry attempted')
                save(LEDGER, state)
                raise RuntimeError(state['stop_reason'])
            time.sleep(delay)
            continue
        if status != 200 or not complete or error:
            raise RuntimeError('Response cannot be used: ' + json.dumps(meta))
        return meta, path


def list_objects(prefix, offline=False, delimiter=None):
    objects, prefixes, pages = [], [], []
    token = None
    while True:
        params = {'list-type': 2, 'prefix': prefix, 'max-keys': 1000}
        if delimiter:
            params['delimiter'] = delimiter
        if token:
            params['continuation-token'] = token
        meta, path = request(S3 + '?' + urllib.parse.urlencode(params), offline)
        body = path.read_bytes()
        if b'<!DOCTYPE' in body or b'<!ENTITY' in body:
            raise ValueError('Unexpected XML entity declaration')
        doc = ET.fromstring(body)
        ns = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
        for node in doc.findall('s:Contents', ns):
            objects.append({'key': node.findtext('s:Key', namespaces=ns), 'size': int(node.findtext('s:Size', namespaces=ns)), 'etag': node.findtext('s:ETag', namespaces=ns), 'last_modified': node.findtext('s:LastModified', namespaces=ns)})
        prefixes.extend(node.findtext('s:Prefix', namespaces=ns) for node in doc.findall('s:CommonPrefixes', ns))
        pages.append(meta)
        truncated = doc.findtext('s:IsTruncated', namespaces=ns)
        if truncated == 'false':
            break
        token = doc.findtext('s:NextContinuationToken', namespaces=ns)
        if truncated != 'true' or not token or len(pages) >= 12:
            raise ValueError('Cannot establish complete object listing')
    return {'prefix': prefix, 'objects': objects, 'common_prefixes': prefixes, 'pages': pages, 'complete_listing': True}


def expected_bins(day):
    date = datetime.strptime(day, '%Y%m%d')
    return [(date + timedelta(minutes=15*i)).strftime('%Y%m%d%H%M%S') for i in range(96)]


def object_bin(key):
    found = re.fullmatch(r'(\d{14})(?:\.translation)?\.(?:gkg|mentions|export)\.csv', key.rsplit('/', 1)[-1], re.I)
    return found.group(1) if found else None


def preserve_demo():
    paths = ['app.py', 'demo_data.py', 'templates/index.html', 'static/app.js', 'static/app.css', 'requirements.txt', 'requirements-dev.txt', 'DEMO.md', 'SUBMISSION.md', 'results/msc_validation/evidence_table.json', 'results/msc_validation/timeline_data.json', 'results/raw_candidates.json']
    paths += [str(p.relative_to(ROOT)) for p in sorted((ROOT / 'results/pages').glob('*.txt'))]
    hashes = {name: sha(ROOT / name) for name in paths}
    path = BASE / 'preserved_demo_hashes.json'
    if path.exists():
        assert json.loads(path.read_text()) == hashes, 'Existing demo changed during historical experiment'
    else:
        save(path, hashes)


def documents(offline=False):
    docdir = BASE / 'docs'
    docdir.mkdir(parents=True, exist_ok=True)
    reused = {
        'sponsor_README.md': ROOT / 'cache/d64c629a3280123800675098c72711052f15444c542b800e4803ff1dd536479a/20260919T174325639740Z.body',
        'sponsor_fetch.py': ROOT / 'cache/bf1b229104bfb4c2139fdf5d8e00247af53a14db98c18f23023669dc0a262452/20260919T174325754006Z.body',
        'gkg_codebook.txt': ROOT / 'results/msc_validation/gkg_codebook.txt',
    }
    manifest = []
    for name, source in reused.items():
        (docdir / name).write_bytes(source.read_bytes())
        manifest.append({'file': str((docdir / name).relative_to(ROOT)), 'reused_from': str(source.relative_to(ROOT)), 'sha256': sha(source), 'new_download_bytes': 0})
    old = json.loads((ROOT / 'results/msc_validation/codebook_response.json').read_text())
    pdf = ROOT / 'cache/recovery_msc' / old['cache_key'] / old['body_file']
    assert sha(pdf) == old['sha256']
    (docdir / 'GKG-Codebook-V2.1.pdf').write_bytes(pdf.read_bytes())
    manifest.append({'file': str((docdir / 'GKG-Codebook-V2.1.pdf').relative_to(ROOT)), 'reused_from': str(pdf.relative_to(ROOT)), 'url': old['url'], 'sha256': old['sha256'], 'new_download_bytes': 0})
    url = 'https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf'
    head, _ = request(url, offline, method='HEAD')
    size = next((int(v) for k, v in head['headers'].items() if k.lower() == 'content-length' and v.isdigit()), None)
    if size is None or size > 5_000_000:
        raise RuntimeError('Event codebook size is unknown or too large')
    save(OUT / 'event_codebook_size.json', {'estimated_bytes': size, 'head': head})
    meta, path = request(url, offline, expected_size=size, ceiling=5_000_000)
    from pypdf import PdfReader
    reader = PdfReader(path)
    (docdir / 'events_mentions_codebook.txt').write_text('\n'.join('=== Page ' + str(i+1) + ' ===\n' + (page.extract_text() or '') for i, page in enumerate(reader.pages)))
    manifest.append({'file': str(path.relative_to(ROOT)), 'text_file': 'historical/docs/events_mentions_codebook.txt', 'url': url, 'sha256': meta['sha256'], 'downloaded_bytes': meta['received_bytes']})
    save(OUT / 'documentation_manifest.json', manifest)


def plan(offline=False):
    preserve_demo()
    OUT.mkdir(parents=True, exist_ok=True)
    layout = list_objects('v2/', offline, delimiter='/')
    save(OUT / 'layout.json', layout)
    listings, sample = [], []
    for day in DATES:
        for table in TABLES:
            listing = list_objects('v2/' + table + '/' + day, offline)
            listing.update(table=table, day=day)
            listing['total_bytes'] = sum(o['size'] for o in listing['objects'])
            listing['observed_bins'] = sorted({object_bin(o['key']) for o in listing['objects'] if object_bin(o['key'])})
            listing['missing_expected_bins'] = sorted(set(expected_bins(day)) - set(listing['observed_bins']))
            listing['unrecognized_keys'] = [o['key'] for o in listing['objects'] if object_bin(o['key']) is None]
            listing['translation_filename_keys'] = [o['key'] for o in listing['objects'] if '.translation.' in o['key'].lower()]
            listings.append(listing)
            for obj in listing['objects']:
                if object_bin(obj['key']) in {day + t for t in SAMPLE_TIMES}:
                    sample.append({**obj, 'table': table, 'batch': object_bin(obj['key']), 'selection_reason': 'Predeclared first two consecutive UTC bins on March 1 in 2015, 2017 and 2019; diagnostic only, not evaluation time sampling'})
            save(OUT / 'listings.json', listings)
    estimate = {'sample_objects': sample, 'object_count': len(sample), 'listed_payload_bytes': sum(o['size'] for o in sample), 'initial_cap_bytes': INITIAL_CAP, 'total_cap_bytes': TOTAL_CAP, 'storage_note': 'Objects are plain CSV/TSV per sponsor; retain one raw copy plus compact derived projections. Allow extra local space for derived tables and metadata; no full-corpus download yet.', 'complete_days_listed': [{'day': l['day'], 'table': l['table'], 'objects': len(l['objects']), 'bytes': l['total_bytes'], 'missing_expected_bins': l['missing_expected_bins']} for l in listings], 'full_window_download_authorized': False}
    save(OUT / 'sample_plan.json', estimate)
    print(json.dumps({k: v for k, v in estimate.items() if k != 'sample_objects'}, indent=2))


def download_sample(offline=False):
    proposal = json.loads((OUT / 'sample_plan.json').read_text())
    state = ledger()
    remaining = 0
    for obj in proposal['sample_objects']:
        url = S3 + urllib.parse.quote(obj['key'], safe='/')
        key = hashlib.sha256(('GET ' + url).encode()).hexdigest()
        if not list((HTTP / key).glob('*.json')):
            remaining += obj['size']
    if not offline and state['received_bytes'] + remaining > INITIAL_CAP:
        raise RuntimeError('Whole proposed feasibility sample exceeds the initial 250 MB cap; revise the explicit plan before downloading')
    manifest = []
    for obj in proposal['sample_objects']:
        meta, path = request(S3 + urllib.parse.quote(obj['key'], safe='/'), offline, expected_size=obj['size'], ceiling=INITIAL_CAP)
        manifest.append({**obj, 'response': meta, 'raw_path': str(path.relative_to(ROOT))})
        save(OUT / 'download_manifest.json', manifest)
    preserve_demo()


def original_language(value):
    codes = {part.split(':', 1)[1].strip().lower() for part in value.split(';') if ':' in part and part.split(':', 1)[0].strip().lower() == 'srclc'}
    if not codes:
        return None, 'missing_srclc'
    if len(codes) != 1:
        return None, 'conflicting_srclc'
    code = next(iter(codes))
    if not re.fullmatch('[a-z]{3}', code) or code in {'und', 'unk', 'xxx', 'zxx'}:
        return None, 'unusable_srclc'
    return code, 'explicit_srclc'


def valid_stamp(value):
    if not re.fullmatch(r'\d{14}', value):
        return False
    try:
        date = datetime.strptime(value, '%Y%m%d%H%M%S')
        return date.minute % 15 == 0 and date.second == 0
    except ValueError:
        return False


def web_domain(collection, identifier):
    if collection != '1':
        return None
    try:
        url = urllib.parse.urlsplit(identifier)
        return url.hostname.lower() if url.scheme in ('http', 'https') and url.hostname and not url.username and not url.password else None
    except ValueError:
        return None


def deduplicate_as_of(records, cutoff, corpus):
    selected = defaultdict(list)
    for row in records:
        if row['corpus'] != corpus:
            raise ValueError('Mixed corpora cannot share a denominator')
        if row['available_batch'] is not None and row['available_batch'] <= cutoff and row['domain']:
            selected[(row['collection'], row['document_id'])].append(row)
    output = []
    for key, group in selected.items():
        earliest = min(r['available_batch'] for r in group)
        first = [r for r in group if r['available_batch'] == earliest]
        representative = dict(min(first, key=lambda r: r['record_id']))
        languages = {r['language'] for r in first}
        if len(languages) != 1:
            representative['language'] = None
            representative['language_status'] = 'ambiguous_first_batch'
        output.append(representative)
    return sorted(output, key=lambda r: (r['available_batch'], r['collection'], r['document_id']))


def observed_share(records, topic, language, batch, available_bins, as_of, corpus):
    if not language or language in ('unknown', 'und'):
        raise ValueError('An explicit usable language is required')
    if batch > as_of:
        raise ValueError('Future observations are unavailable')
    if batch not in available_bins:
        return {'numerator': None, 'denominator': None, 'share': None, 'status': 'missing_bin'}
    docs = [r for r in deduplicate_as_of(records, as_of, corpus) if r['available_batch'] == batch and r['language'] == language]
    if not docs:
        return {'numerator': None, 'denominator': 0, 'share': None, 'status': 'no_usable_language_exposure'}
    if any('topics' not in r for r in docs):
        return {'numerator': None, 'denominator': len(docs), 'share': None, 'status': 'topic_membership_unverified'}
    numerator = sum(topic in r['topics'] for r in docs)
    return {'numerator': numerator, 'denominator': len(docs), 'share': numerator / len(docs), 'status': 'observed_archive_share'}


def preceding_values(series, at, steps, minutes=15):
    date = datetime.strptime(at, '%Y%m%d%H%M%S')
    bins = [(date - timedelta(minutes=minutes*i)).strftime('%Y%m%d%H%M%S') for i in range(steps, 0, -1)]
    values = [series.get(key) for key in bins]
    return values if all(v is not None for v in values) else None


def jsonl(path, records):
    with path.open('w') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')


def raw_lines(path):
    with path.open('rb') as handle:
        yield from enumerate(handle, 1)


def analyze():
    preserve_demo()
    manifest = json.loads((OUT / 'download_manifest.json').read_text())
    listings = json.loads((OUT / 'listings.json').read_text())
    gkg, mentions, events, file_reports = [], [], [], []
    for obj in manifest:
        path = ROOT / obj['raw_path']
        response = obj['response']
        if response['status'] != 200 or not response['complete'] or response['error'] or path.stat().st_size != obj['size'] or sha(path) != response['sha256']:
            raise ValueError('Incomplete or changed raw file: ' + obj['key'])
        table, batch = obj['table'], obj['batch']
        expected_columns = {'gkg': 27, 'mentions': 16, 'events': 61}[table]
        shapes, languages, lang_statuses, timestamp_checks = Counter(), Counter(), Counter(), Counter()
        valid_rows = invalid_utf8 = translated = nonempty_translation = 0
        for number, raw in raw_lines(path):
            try:
                line = raw.decode('utf-8')
            except UnicodeDecodeError:
                invalid_utf8 += 1
                continue
            fields = line.rstrip('\r\n').split('\t')
            shapes[len(fields)] += 1
            if len(fields) != expected_columns:
                continue
            valid_rows += 1
            provenance = {'object_key': obj['key'], 'row_number': number, 'file_batch': batch}
            if table == 'gkg':
                language, status = original_language(fields[25])
                marker = bool(re.fullmatch(r'\d{14}-T\d+', fields[0]))
                record_batch = fields[0].split('-', 1)[0]
                valid_record_time = valid_stamp(record_batch) and record_batch == batch
                domain = web_domain(fields[2], fields[4])
                row = {**provenance, 'corpus': 'gdelt-open-data/v2/gkg', 'record_id': fields[0], 'available_batch': record_batch if valid_record_time else None, 'gkg_document_date': fields[1], 'collection': fields[2], 'document_id': fields[4], 'domain': domain, 'language': language, 'language_status': status, 'translation_info': fields[25], 'translated_id_marker': marker}
                gkg.append(row)
                translated += marker
                nonempty_translation += bool(fields[25])
                languages[language or 'unknown'] += 1
                lang_statuses[status] += 1
                timestamp_checks['record_batch_matches_object'] += valid_record_time
                timestamp_checks['document_date_matches_batch'] += fields[1] == batch
            elif table == 'mentions':
                language, status = original_language(fields[14])
                row = {**provenance, 'event_id': fields[0], 'event_time': fields[1], 'mention_time': fields[2], 'collection': fields[3], 'document_id': fields[5], 'domain': web_domain(fields[3], fields[5]), 'language': language, 'language_status': status, 'translation_info': fields[14], 'confidence': fields[11]}
                mentions.append(row)
                nonempty_translation += bool(fields[14])
                languages[language or 'unknown'] += 1
                lang_statuses[status] += 1
                timestamp_checks['mention_time_matches_object'] += fields[2] == batch
                timestamp_checks['valid_mention_timestamp'] += valid_stamp(fields[2])
                timestamp_checks['event_time_after_mention_time'] += valid_stamp(fields[1]) and valid_stamp(fields[2]) and fields[1] > fields[2]
            else:
                events.append({**provenance, 'event_id': fields[0], 'event_day': fields[1], 'date_added': fields[59], 'source_url': fields[60]})
                timestamp_checks['date_added_matches_object'] += fields[59] == batch
                timestamp_checks['event_day_differs_from_ingestion_day'] += fields[1] != fields[59][:8]
        file_reports.append({'key': obj['key'], 'table': table, 'batch': batch, 'bytes': obj['size'], 's3_last_modified': obj['last_modified'], 'columns_expected': expected_columns, 'column_histogram': dict(shapes), 'valid_rows': valid_rows, 'invalid_utf8_rows': invalid_utf8, 'wrong_shape_rows': sum(v for k, v in shapes.items() if k != expected_columns), 'original_language_records': dict(languages), 'language_statuses': dict(lang_statuses), 'translation_info_nonempty': nonempty_translation, 'translated_id_markers': translated if table == 'gkg' else None, 'timestamp_checks': dict(timestamp_checks), 'sha256': response['sha256']})
    jsonl(OUT / 'gkg_observations.jsonl', gkg)
    jsonl(OUT / 'mentions_projection.jsonl', mentions)
    jsonl(OUT / 'events_projection.jsonl', events)
    max_batch = max(r['file_batch'] for r in gkg)
    distinct = deduplicate_as_of(gkg, max_batch, 'gdelt-open-data/v2/gkg')
    jsonl(OUT / 'distinct_documents.jsonl', distinct)
    by_url = defaultdict(list)
    for row in gkg:
        if row['domain']:
            by_url[(row['collection'], row['document_id'])].append(row)
    by_record_id = defaultdict(list)
    for row in gkg:
        by_record_id[row['record_id']].append(row)
    collisions = [{'record_id': rid, 'documents': [{'object_key': r['object_key'], 'row_number': r['row_number'], 'collection': r['collection'], 'document_id': r['document_id']} for r in group]} for rid, group in sorted(by_record_id.items()) if len({(r['collection'], r['document_id']) for r in group}) > 1]
    jsonl(OUT / 'record_id_collisions.jsonl', collisions)
    event_map = {r['event_id']: r for r in events}
    join_rows = []
    for mention in mentions:
        if not mention['domain']:
            continue
        candidates = by_url.get((mention['collection'], mention['document_id']), [])
        prior = [r for r in candidates if r['available_batch'] and r['available_batch'] <= mention['mention_time']]
        same = [r for r in candidates if r['available_batch'] == mention['mention_time']]
        event = event_map.get(mention['event_id'])
        join_rows.append({'mention_object': mention['object_key'], 'mention_row': mention['row_number'], 'event_id': mention['event_id'], 'document_url': mention['document_id'], 'mention_time': mention['mention_time'], 'gkg_match_any_sample': bool(candidates), 'gkg_match_as_of': bool(prior), 'gkg_match_same_batch': bool(same), 'gkg_rows_as_of': [{'record_id': r['record_id'], 'object_key': r['object_key'], 'row_number': r['row_number']} for r in prior], 'event_found_in_sample': event is not None, 'event_dateadded_matches_eventtimedate': event['date_added'] == mention['event_time'] if event else None})
    jsonl(OUT / 'mention_gkg_links.jsonl', join_rows)
    missing = [{'table': listing['table'], 'date': listing['day'], 'expected_bins': 96, 'listed_object_count': len(listing['objects']), 'missing_object_bins': listing['missing_expected_bins'], 'translation_filename_count': len(listing['translation_filename_keys']), 'content_audited_bins': sorted({f['batch'] for f in file_reports if f['table'] == listing['table'] and f['batch'].startswith(listing['day'])}), 'content_not_audited_bins': sorted(set(expected_bins(listing['day'])) - {f['batch'] for f in file_reports if f['table'] == listing['table']})} for listing in listings]
    save(OUT / 'missing_bins.json', missing)
    language_counts = Counter(r['language'] or 'unknown' for r in gkg)
    mention_languages = Counter(r['language'] or 'unknown' for r in mentions)
    duplicates_within_bin = len([r for r in gkg if r['domain']]) - len({(r['file_batch'], r['collection'], r['document_id']) for r in gkg if r['domain']})
    corpus_languages_by_bin = defaultdict(Counter)
    for row in distinct:
        corpus_languages_by_bin[row['available_batch']][row['language'] or 'unknown'] += 1
    english_bins = {b for b, counts in corpus_languages_by_bin.items() if counts['eng'] > 0}
    nonenglish_bins = {b for b, counts in corpus_languages_by_bin.items() if any(k not in ('eng', 'unknown') and v > 0 for k, v in counts.items())}
    paired = any(day+'000000' in english_bins & nonenglish_bins and day+'001500' in english_bins & nonenglish_bins for day in DATES)
    failures = []
    if not any(k != 'unknown' for k in language_counts):
        failures.append('No explicit usable original-language codes in sampled GKG records; blank values are unknown, not English')
    if not any(k not in ('eng', 'unknown') for k in language_counts):
        failures.append('Non-English original-language coverage not established in sampled GKG')
    if not english_bins:
        failures.append('No explicitly labeled English comparator/denominator established')
    if not paired:
        failures.append('No consecutive sampled GKG bins with both explicit English and non-English document exposure')
    if any(r['wrong_shape_rows'] or r['invalid_utf8_rows'] for r in file_reports):
        failures.append('Parsing quality requires further investigation')
    quality = {'sampling_scope': 'Two consecutive bins on each of three predeclared dates. Diagnostic only; not a 28-day corpus or representative language sample.', 'source_bucket': 's3://gdelt-open-data', 'region': 'us-east-1', 'anonymous': True, 'payload_bytes': sum(f['bytes'] for f in file_reports), 'total_transfer_bytes': ledger()['received_bytes'], 'initial_cap_bytes': INITIAL_CAP, 'file_reports': file_reports, 'gkg_rows': len(gkg), 'gkg_language_counts': dict(language_counts), 'gkg_translated_id_markers': sum(r['translated_id_marker'] for r in gkg), 'gkg_nonempty_translation_info': sum(bool(r['translation_info']) for r in gkg), 'gkg_distinct_record_ids': len({r['record_id'] for r in gkg}), 'gkg_record_id_collision_groups': len(collisions), 'gkg_record_id_repeated_rows': len(gkg) - len(by_record_id), 'gkg_record_id_warning': 'Record identifiers are not unique in this sample. Use object key + row for provenance, and typed document identifier for document deduplication.' if collisions else 'No record-ID collisions observed in this diagnostic sample; document IDs still define document counts.', 'gkg_source_collection_counts': dict(Counter(r['collection'] for r in gkg)), 'gkg_invalid_web_url_rows': sum(r['collection'] == '1' and not r['domain'] for r in gkg), 'gkg_web_rows': sum(bool(r['domain']) for r in gkg), 'gkg_distinct_web_document_ids': len(distinct), 'gkg_within_bin_duplicate_rows': duplicates_within_bin, 'gkg_cross_bin_repeated_document_ids': sum(len({r['available_batch'] for r in group}) > 1 for group in by_url.values()), 'document_language_conflicts': sum(len({r['language'] for r in group}) > 1 for group in by_url.values()), 'mentions_rows': len(mentions), 'mentions_source_collection_counts': dict(Counter(r['collection'] for r in mentions)), 'mentions_invalid_web_url_rows': sum(r['collection'] == '1' and not r['domain'] for r in mentions), 'mentions_language_counts': dict(mention_languages), 'mentions_nonempty_translation_info': sum(bool(r['translation_info']) for r in mentions), 'mentions_distinct_web_document_ids': len({(r['collection'], r['document_id']) for r in mentions if r['domain']}), 'events_rows': len(events), 'events_distinct_ids': len(event_map), 'event_days_differing_from_dateadded_day': sum(r['event_day'] != r['date_added'][:8] for r in events), 'join': {'web_mention_rows': len(join_rows), 'gkg_url_match_any_selected_slice_rows': sum(r['gkg_match_any_sample'] for r in join_rows), 'gkg_url_match_as_of_rows': sum(r['gkg_match_as_of'] for r in join_rows), 'gkg_url_match_same_batch_rows': sum(r['gkg_match_same_batch'] for r in join_rows), 'future_only_gkg_matches': sum(r['gkg_match_any_sample'] and not r['gkg_match_as_of'] for r in join_rows), 'event_found_rows': sum(r['event_found_in_sample'] for r in join_rows), 'matched_event_timestamp_disagreements': sum(r['event_dateadded_matches_eventtimedate'] is False for r in join_rows), 'limitation': 'Many-to-many URL/event mapping, exact typed-URL join only. Unmatched rows may require files outside these diagnostic slices; no inference of absent coverage.'}, 'feasibility_passed': not failures, 'gate_failures': failures, 'missing_bins_report': 'missing_bins.json', 'language_rule': 'Only explicit srclc values. No inference from country, domain, T-marker absence, or missing translation metadata.', 'timestamp_rule': 'Use update-batch labels for observed-archive sequencing, never event date or publisher-lead claims. S3 last-modified is mirror-object metadata, not historical news availability.', 'dedup_rule': 'Exact (source collection, document identifier) at the earliest observed update batch as of the cutoff. Same-URL revisions cannot be separated; different-URL syndication remains possible.'}
    save(OUT / 'quality_report.json', quality)
    if failures:
        state = ledger()
        if not state['network_closed']:
            state.update(network_closed=True, stop_reason='Milestone 1 comparison feasibility failed; no corpus expansion or evaluation', closed_at_utc=utcnow())
            save(LEDGER, state)
    status = {'status': 'not_run', 'reason': '; '.join(failures) if failures else 'Feasibility passed; complete-corpus size and calibration/evaluation gates still required', 'milestone_1_passed': not failures, 'corpus_28_days_built': False, 'calibration_topics_selected': False, 'thresholds_frozen': False, 'held_out_period_evaluated': False, 'alert_count': None, 'fraction_followed_by_english_increase': None, 'observed_lead_times': None, 'false_alarms': None, 'unscorable_alerts': None, 'raw_count_baseline_comparison': None, 'interpretation': 'Not evaluated is not zero alerts, no false alarms, or evidence against the hypothesis.'}
    save(OUT / 'evaluation_status.json', status)
    with (OUT / 'alert_results.csv').open('w', newline='') as handle:
        csv.writer(handle).writerow(['alert_id', 'topic', 'source_language', 'available_batch', 'outcome', 'english_increase_batch', 'observed_batch_lead_minutes', 'source_document_identifiers', 'source_urls', 'unscorable_reason'])
    preserve_demo()
    print(json.dumps({k: v for k, v in quality.items() if k != 'file_reports'}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('docs', 'plan', 'sample', 'analyze', 'evaluate'))
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if args.command == 'docs':
        documents(args.offline)
    elif args.command == 'plan':
        plan(args.offline)
    elif args.command == 'sample':
        download_sample(args.offline)
    elif args.command == 'analyze':
        analyze()
    else:
        status = json.loads((OUT / 'evaluation_status.json').read_text())
        print(json.dumps(status, indent=2))
        if status['status'] == 'not_run':
            print('Evaluation intentionally not run; no performance or lead-time result is available.')


if __name__ == '__main__':
    main()
