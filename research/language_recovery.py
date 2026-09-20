import argparse
import gzip
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import tarfile
from email.parser import Parser
from datetime import datetime, timedelta, timezone
from collections import Counter
from pathlib import Path

from research.historical_experiment import backoff, original_language, preserve_demo, save, sha

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'historical/language_recovery'
OUT = BASE / 'results'
RAW = BASE / 'raw'
STATE = BASE / 'transfer_ledger.json'
PHASE_CAP = 250_000_000
TOTAL_CAP = 5_000_000_000
MANIFEST_SCAN_CAP = 64_000_000
MANIFEST_DECODED_CAP = 160_000_000
GDELT = 'https://data.gdeltproject.org/gdeltv2/'
TARGETS = tuple(day + clock for day in ('20150301', '20170301', '20190301') for clock in ('000000', '001500'))


def now():
    return datetime.now(timezone.utc).isoformat()


def state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    prior = json.loads((ROOT / 'historical/transfer_ledger.json').read_text())['received_bytes']
    value = {'started_at_utc': now(), 'prior_historical_bytes': prior, 'received_bytes': 0, 'requests': 0, 'phase_cap_bytes': PHASE_CAP, 'overall_cap_bytes': TOTAL_CAP, 'last_finished_epoch': 0}
    save(STATE, value)
    return value


def remaining():
    s = state()
    pages = BASE / 'page_transfer_ledger.json'
    page_bytes = json.loads(pages.read_text())['received_bytes'] if pages.exists() else 0
    return min(PHASE_CAP - s['received_bytes'] - page_bytes, TOTAL_CAP - s['prior_historical_bytes'] - s['received_bytes'] - page_bytes)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, method='GET', offline=False, limit=2_000_000):
    key = hashlib.sha256((method + ' ' + url).encode()).hexdigest()
    folder = RAW / key
    files = sorted(folder.glob('*.json'))
    if files:
        meta = json.loads(files[-1].read_text())
        body = folder / meta['body_file']
        assert sha(body) == meta['sha256']
        return meta, body
    if offline:
        raise RuntimeError('Uncached request: ' + method + ' ' + url)
    if not (url.startswith(GDELT) or url.startswith('https://pypi.org/pypi/')):
        raise ValueError('Only the official bulk stream and package provenance metadata are allowed here')
    for attempt in range(2):
        s = state()
        cap = min(limit, remaining())
        if cap <= 0:
            raise RuntimeError('Recovery transfer budget exhausted')
        time.sleep(max(0, 3 - (time.time() - s['last_finished_epoch'])))
        folder.mkdir(parents=True, exist_ok=True)
        stem = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = folder / (stem + '.body')
        started = now()
        status, headers, error, body = None, {}, None, b''
        response = None
        try:
            req = urllib.request.Request(url, method=method, headers={'User-Agent': 'SignalNoise-LanguageFeasibility/1.0', 'Accept-Encoding': 'identity'})
            try:
                response = urllib.request.build_opener(NoRedirect).open(req, timeout=45)
            except urllib.error.HTTPError as exc:
                response, error = exc, str(exc)
            status, headers = response.code, dict(response.headers)
            length = response.headers.get('Content-Length')
            if method != 'HEAD':
                if length and int(length) > cap:
                    error = 'Body withheld: advertised size exceeds bounded request cap'
                else:
                    body = response.read(cap)
                    if length and len(body) != int(length):
                        error = 'Incomplete body'
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            error = str(exc)
        finally:
            if response is not None:
                response.close()
        path.write_bytes(body)
        meta = {'url': url, 'method': method, 'requested_at_utc': started, 'completed_at_utc': now(), 'status': status, 'headers': headers, 'received_bytes': len(body), 'error': error, 'body_file': path.name, 'sha256': sha(path), 'raw_path': str(path.relative_to(ROOT))}
        save(folder / (stem + '.json'), meta)
        s.update(received_bytes=s['received_bytes'] + len(body), requests=s['requests'] + 1, last_finished_epoch=time.time())
        save(STATE, s)
        print(method, status, len(body), url, flush=True)
        if status not in (429, 503, 502, 504) or attempt == 1:
            return meta, path
        delay = backoff(next((v for k, v in headers.items() if k.lower() == 'retry-after'), None))
        if delay > 300:
            raise RuntimeError('Retry-After too long for automatic retry; no early retry made')
        time.sleep(delay)


def inspect(offline=False):
    preserve_demo()
    results = []
    for suffix in ('lastupdate-translation.txt', 'masterfilelist-translation.txt'):
        meta, _ = request(GDELT + suffix, method='HEAD', offline=offline)
        results.append({'kind': 'manifest_head', 'response': meta})
    meta, path = request(GDELT + 'lastupdate-translation.txt', offline=offline)
    results.append({'kind': 'latest_pointer', 'response': meta, 'text': path.read_text(errors='replace') if meta['status'] == 200 else None})
    for batch in TARGETS:
        url = GDELT + batch + '.translation.gkg.csv.zip'
        meta, _ = request(url, method='HEAD', offline=offline)
        length = next((v for k, v in meta['headers'].items() if k.lower() == 'content-length'), None)
        results.append({'kind': 'historical_translated_object', 'batch': batch, 'url': url, 'head_status': meta['status'], 'compressed_bytes': int(length) if meta['status'] == 200 and length and length.isdigit() else None, 'response': meta, 'interpretation': 'HEAD availability only; contents remain unverified until downloaded'})
        save(OUT / 'stream_inventory.json', results)
    print(json.dumps([{'kind': r['kind'], 'batch': r.get('batch'), 'status': r['response']['status'], 'bytes': r.get('compressed_bytes')} for r in results], indent=2))


class CappedTee(io.RawIOBase):
    def __init__(self, response, handle, limit):
        self.response, self.handle, self.limit, self.count = response, handle, limit, 0
        self.exhausted = False

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.count >= self.limit:
            self.exhausted = True
            return 0
        data = self.response.read(min(len(buffer), self.limit - self.count))
        buffer[:len(data)] = data
        self.handle.write(data)
        self.count += len(data)
        return len(data)


def stream_manifest(offline=False):
    report_path = OUT / 'manifest_scan.json'
    if report_path.exists():
        report = json.loads(report_path.read_text())
        assert sha(ROOT / report['raw_path']) == report['sha256']
        print(json.dumps(report, indent=2))
        return
    if offline:
        raise RuntimeError('Master manifest scan has not been cached')
    url = GDELT + 'masterfilelist-translation.txt'
    s = state()
    cap = min(MANIFEST_SCAN_CAP, remaining())
    if cap <= 0:
        raise RuntimeError('No transfer budget for manifest scan')
    time.sleep(max(0, 3 - (time.time() - s['last_finished_epoch'])))
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / 'masterfilelist-translation.scan.body'
    req = urllib.request.Request(url, headers={'User-Agent': 'SignalNoise-LanguageFeasibility/1.0', 'Accept-Encoding': 'gzip'})
    started = now()
    matched, lines, decoded, stop_reason = [], 0, 0, 'end_of_response'
    response = urllib.request.build_opener(NoRedirect).open(req, timeout=45)
    headers = dict(response.headers)
    with response, path.open('wb') as handle:
        tee = CappedTee(response, handle, cap)
        buffered = io.BufferedReader(tee, buffer_size=65536)
        stream = gzip.GzipFile(fileobj=buffered) if response.headers.get('Content-Encoding', '').lower() == 'gzip' else buffered
        try:
            while True:
                line = stream.readline(8192)
                if not line:
                    stop_reason = 'transfer_cap' if tee.exhausted else 'end_of_response'
                    break
                decoded += len(line)
                lines += 1
                text = line.decode('utf-8', errors='replace').strip()
                parts = text.split()
                if len(parts) == 3 and parts[0].isdigit():
                    found = re.fullmatch(r'https?://data\.gdeltproject\.org/gdeltv2/(\d{14})\.translation\.gkg\.csv\.zip', parts[2])
                    if found and found.group(1) in TARGETS:
                        matched.append({'batch': found.group(1), 'compressed_bytes': int(parts[0]), 'md5': parts[1], 'url': parts[2], 'manifest_line': lines})
                if {r['batch'] for r in matched} == set(TARGETS):
                    stop_reason = 'all_six_targets_found'
                    break
                if decoded >= MANIFEST_DECODED_CAP:
                    stop_reason = 'decoded_scan_cap'
                    break
        except (EOFError, OSError, TimeoutError) as exc:
            stop_reason = 'bounded_or_interrupted_stream: ' + str(exc)
        transferred = tee.count
    s.update(received_bytes=s['received_bytes'] + transferred, requests=s['requests'] + 1, last_finished_epoch=time.time())
    save(STATE, s)
    report = {'url': url, 'requested_at_utc': started, 'completed_at_utc': now(), 'headers': headers, 'transfer_limit_bytes': cap, 'transferred_bytes': transferred, 'decoded_bytes_scanned': decoded, 'lines_scanned': lines, 'stop_reason': stop_reason, 'matches': matched, 'targets_not_observed_in_scanned_portion': sorted(set(TARGETS) - {r['batch'] for r in matched}), 'missing_interpretation': 'Not found in a bounded partial scan is not proof that an archive object is absent. Inspect the separate HEAD/download outcomes.', 'raw_path': str(path.relative_to(ROOT)), 'sha256': sha(path), 'response_cache_is_partial': stop_reason != 'end_of_response'}
    save(report_path, report)
    print(json.dumps(report, indent=2))


def download_translated(offline=False):
    inventory = json.loads((OUT / 'stream_inventory.json').read_text())
    candidates = [r for r in inventory if r['kind'] == 'historical_translated_object']
    estimate = sum(r['compressed_bytes'] or 0 for r in candidates)
    save(OUT / 'translated_download_plan.json', {'files': [{'batch': r['batch'], 'url': r['url'], 'compressed_bytes': r['compressed_bytes'], 'head_status': r['head_status']} for r in candidates], 'advertised_payload_bytes': estimate, 'storage': 'Retain ZIP files and compact projections; stream decompression without persisting another uncompressed raw copy.', 'prior_native_historical_bytes': state()['prior_historical_bytes'], 'phase_cap_bytes': PHASE_CAP, 'overall_cap_bytes': TOTAL_CAP})
    if estimate > remaining() and not offline:
        raise RuntimeError('Whole translated sample exceeds remaining recovery budget')
    manifest = []
    for item in candidates:
        if item['head_status'] != 200 or item['compressed_bytes'] is None:
            manifest.append({**item, 'status': 'unavailable_or_unknown', 'interpretation': 'Unavailable object, not zero records'})
        else:
            meta, path = request(item['url'], offline=offline, limit=item['compressed_bytes'])
            manifest.append({'batch': item['batch'], 'url': item['url'], 'expected_bytes': item['compressed_bytes'], 'response': meta, 'raw_path': str(path.relative_to(ROOT)), 'status': 'downloaded' if meta['status'] == 200 and meta['error'] is None and path.stat().st_size == item['compressed_bytes'] else 'failed'})
        save(OUT / 'translated_download_manifest.json', manifest)


def gkg_records(handle, shapes=None):
    pending, start, continuations = None, None, 0
    for number, raw in enumerate(handle, 1):
        text = raw.decode('utf-8', errors='replace').rstrip('\r\n')
        fields = text.split('\t')
        if shapes is not None:
            shapes[len(fields)] += 1
        if len(fields) == 27 and re.fullmatch(r'\d{14}-T\d+', fields[0]):
            if pending is not None:
                yield start, pending, continuations
            start, pending, continuations = number, fields, 0
        elif pending is not None and '\t' not in text:
            pending[26] += '\n' + text
            continuations += 1
        elif text:
            raise ValueError('Unrecognized GKG record boundary at physical line ' + str(number))
    if pending is not None:
        yield start, pending, continuations


def audit_translated():
    manifest = json.loads((OUT / 'translated_download_manifest.json').read_text())
    scan = json.loads((OUT / 'manifest_scan.json').read_text()) if (OUT / 'manifest_scan.json').exists() else {'matches': []}
    manifest_entries = {r['batch']: r for r in scan['matches']}
    native = [json.loads(line) for line in (ROOT / 'historical/results/gkg_observations.jsonl').open()]
    native_keys = {(r['file_batch'], r['collection'], r['document_id']) for r in native}
    rows, reports = [], []
    for item in manifest:
        if item['status'] != 'downloaded':
            reports.append({'batch': item['batch'], 'status': 'unavailable', 'records': None})
            continue
        path = ROOT / item['raw_path']
        assert sha(path) == item['response']['sha256']
        body = path.read_bytes()
        md5 = hashlib.md5(body).hexdigest()
        entry = manifest_entries.get(item['batch'])
        checksums_match = md5 == entry['md5'] and len(body) == entry['compressed_bytes'] if entry else None
        counts, shapes = Counter(), Counter()
        batch_matches = translated_markers = date_matches = blank = n = continued_records = continuation_lines = 0
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            expanded = sum(member.file_size for member in archive.infolist())
            if expanded > 200_000_000:
                raise RuntimeError('ZIP expanded-size safety cap exceeded')
            for member in archive.infolist():
                if member.is_dir():
                    continue
                with archive.open(member) as handle:
                    for line_number, fields, continued in gkg_records(handle, shapes):
                        continuation_lines += continued
                        continued_records += bool(continued)
                        n += 1
                        language, language_status = original_language(fields[25])
                        counts[language or 'unknown'] += 1
                        blank += not bool(fields[25])
                        batch_matches += fields[0].split('-')[0] == item['batch']
                        date_matches += fields[1] == item['batch']
                        translated_markers += bool(re.fullmatch(r'\d{14}-T\d+', fields[0]))
                        title = re.search(r'<PAGE_TITLE>(.*?)</PAGE_TITLE>', fields[26], re.S | re.I)
                        rows.append({'stream': 'official_translated', 'batch': item['batch'], 'record_id': fields[0], 'document_date': fields[1], 'collection': fields[2], 'publisher_domain': fields[3], 'document_url': fields[4], 'language_metadata': language, 'language_metadata_status': language_status, 'translation_info': fields[25], 'historical_title_if_present': title.group(1) if title else None, 'source_zip': str(path.relative_to(ROOT)), 'source_member': member.filename, 'source_row': line_number})
        reports.append({'batch': item['batch'], 'status': 'parsed', 'compressed_bytes': len(body), 'expanded_bytes': expanded, 'records': n, 'column_histogram': dict(shapes), 'multiline_extras_records': continued_records, 'extras_continuation_lines': continuation_lines, 'original_language_counts': dict(counts), 'blank_translation_info': blank, 'translated_record_markers': translated_markers, 'record_batch_matches': batch_matches, 'document_date_matches': date_matches, 'md5': md5, 'manifest_checksum_matches': checksums_match, 'url': item['url']})
    with (OUT / 'translated_records.jsonl').open('w') as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')
    summary = {'files': reports, 'records': len(rows), 'distinct_typed_document_ids': len({(r['collection'], r['document_url']) for r in rows}), 'original_language_counts': dict(Counter(r['language_metadata'] or 'unknown' for r in rows)), 'native_translated_exact_url_overlap_same_batch': len({(r['batch'], r['collection'], r['document_url']) for r in rows} & native_keys), 'missing_or_unverified_batches': [r['batch'] for r in reports if r['status'] != 'parsed'], 'meaning': 'Explicit multilingual metadata in the separate translated stream, not evidence that the S3 native mirror had those labels. Batch timestamps are not publisher lead times.', 'native_language_provenance': 'Unresolved unless separately verified; blank native metadata is not relabeled English.'}
    save(OUT / 'translated_quality.json', summary)
    preserve_demo()
    print(json.dumps(summary, indent=2))


def plan_pages():
    seed = 'signal-noise-language-audit-v1'
    pools = {}
    native_path = ROOT / 'historical/results/gkg_observations.jsonl'
    translated_path = OUT / 'translated_records.jsonl'
    with native_path.open() as handle:
        native = [json.loads(line) for line in handle]
    with translated_path.open() as handle:
        translated = [json.loads(line) for line in handle]
    for row in native:
        if row['collection'] == '1' and row['domain']:
            pools.setdefault((row['file_batch'], 'native_s3'), []).append({'url': row['document_id'], 'batch': row['file_batch'], 'stream': 'native_s3', 'metadata_language': row['language'], 'record_id': row['record_id'], 'source_object': row['object_key'], 'source_row': row['row_number'], 'historical_title': None})
    for row in translated:
        if row['collection'] == '1':
            pools.setdefault((row['batch'], 'official_translated'), []).append({'url': row['document_url'], 'batch': row['batch'], 'stream': 'official_translated', 'metadata_language': row['language_metadata'], 'record_id': row['record_id'], 'source_object': row['source_zip'], 'source_row': row['source_row'], 'historical_title': row['historical_title_if_present']})
    domain_counts, seen, selected, strata = Counter(), set(), [], []
    for batch in TARGETS:
        for stream, quota in (('native_s3', 12), ('official_translated', 4)):
            candidates = sorted(pools.get((batch, stream), []), key=lambda r: hashlib.sha256((seed + '|' + r['url']).encode()).hexdigest())
            chosen = 0
            for row in candidates:
                try:
                    url = urllib.parse.urlsplit(row['url'])
                    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.port not in (None, 80, 443):
                        continue
                    domain = url.hostname.lower().removeprefix('www.')
                    identity = urllib.parse.urlunsplit((url.scheme, url.netloc.lower(), url.path, url.query, ''))
                except ValueError:
                    continue
                if identity in seen or domain_counts[domain] >= 2:
                    continue
                seen.add(identity)
                domain_counts[domain] += 1
                selected.append({**row, 'sample_id': 'page-' + str(len(selected) + 1).zfill(3), 'publisher_host': domain, 'selection_hash': hashlib.sha256((seed + '|' + row['url']).encode()).hexdigest()})
                chosen += 1
                if chosen == quota:
                    break
            strata.append({'batch': batch, 'stream': stream, 'requested': quota, 'selected': chosen})
    if len(selected) > 200:
        raise RuntimeError('URL sample cap exceeded')
    plan = {'seed': seed, 'sampling': 'Topic-independent deterministic URL hashes; 12 native and 4 translated URLs per historical batch, at most 2 selected URLs per normalized publisher hostname. Not population-representative; no topic terms used.', 'maximum_selected_urls': 200, 'planned_urls': len(selected), 'publisher_hosts': len(domain_counts), 'per_publisher_selection_limit': 2, 'strata': strata, 'source_hashes': {'native': sha(native_path), 'translated': sha(translated_path)}, 'records': selected}
    save(OUT / 'url_sample_plan.json', plan)
    print(json.dumps({k: v for k, v in plan.items() if k != 'records'}, indent=2))


def cached_window_estimates():
    scan = json.loads((OUT / 'manifest_scan.json').read_text())
    path = ROOT / scan['raw_path']
    assert sha(path) == scan['sha256']
    windows = [('20150301', '20150329'), ('20170301', '20170329')]
    objects = {}
    with path.open('rb') as raw:
        stream = gzip.GzipFile(fileobj=raw) if scan['headers'].get('Content-Encoding', '').lower() == 'gzip' else raw
        for line in stream:
            parts = line.decode('utf-8', errors='replace').split()
            if len(parts) != 3 or not parts[0].isdigit(): continue
            match = re.fullmatch(r'https?://data\.gdeltproject\.org/gdeltv2/(\d{14})\.translation\.(gkg|mentions|export)\.csv\.zip', parts[2], re.I)
            if match and any(start <= match.group(1)[:8] < end for start, end in windows):
                objects[(match.group(1), match.group(2).lower())] = {'batch': match.group(1), 'table': match.group(2).lower(), 'bytes': int(parts[0]), 'md5': parts[1], 'url': parts[2]}
    quality = json.loads((OUT / 'translated_quality.json').read_text())
    ratio = sum(f['expanded_bytes'] for f in quality['files'] if f['status'] == 'parsed') / sum(f['compressed_bytes'] for f in quality['files'] if f['status'] == 'parsed')
    results = []
    for start, end in windows:
        begin = datetime.strptime(start, '%Y%m%d')
        expected = {(begin + timedelta(minutes=15*i)).strftime('%Y%m%d%H%M%S') for i in range(28*96)}
        tables = {}
        for table in ('gkg', 'mentions', 'export'):
            entries = [item for (batch, kind), item in objects.items() if kind == table and batch in expected]
            missing = sorted(expected - {r['batch'] for r in entries})
            total = sum(r['bytes'] for r in entries)
            tables[table] = {'listed_objects': len(entries), 'expected_objects': len(expected), 'missing_manifest_entries': missing, 'listed_compressed_bytes': total, 'complete_nominal_grid_in_cached_manifest': not missing, 'estimated_expanded_bytes_using_sample_ratio': round(total * ratio) if table == 'gkg' else None, 'size_interpretation': 'Sum of named files in the cached manifest, not newly downloaded or content-verified; missing entries are unknown, not zero.'}
        results.append({'start_inclusive': start, 'end_exclusive': end, 'calibration_days_if_future_experiment': [start, (begin+timedelta(days=13)).strftime('%Y%m%d')], 'held_out_days_if_future_experiment': [(begin+timedelta(days=14)).strftime('%Y%m%d'), (begin+timedelta(days=27)).strftime('%Y%m%d')], 'translated_tables': tables, 'translated_gkg_alone_exceeds_5gb': tables['gkg']['listed_compressed_bytes'] > TOTAL_CAP, 'translated_mentions_plus_events_bytes': tables['mentions']['listed_compressed_bytes'] + tables['export']['listed_compressed_bytes'], 'native_side_not_in_this_estimate': True, 'storage_warning': 'Keep ZIPs plus projections; fully expanded GKG storage estimate is extrapolated from six files, not measured for this full window.'})
    save(OUT / 'corpus_transfer_estimates.json', {'source_manifest_sha256': scan['sha256'], 'sample_gkg_expansion_ratio': ratio, 'windows': results, 'downloaded_full_window': False, 'missing_intervals_policy': 'Unavailable/unverified intervals remain unknown; no time-bin subsampling plan is silently substituted.'})
    print(json.dumps(results, indent=2))


def native_metadata_estimate(offline=False):
    result_path = OUT / 'native_metadata_window.json'
    if result_path.exists():
        print(json.dumps(json.loads(result_path.read_text()), indent=2))
        return
    if offline:
        raise RuntimeError('Native metadata manifest prefix is not cached')
    url = GDELT + 'masterfilelist.txt'
    head, _ = request(url, method='HEAD')
    if head['status'] != 200:
        raise RuntimeError('Native master manifest unavailable')
    start = datetime(2015, 3, 1)
    expected = {(start + timedelta(minutes=15*i)).strftime('%Y%m%d%H%M%S') for i in range(28*96)}
    wanted = {(batch, table) for batch in expected for table in ('mentions', 'export')}
    objects, lines, decoded = {}, 0, 0
    s = state()
    time.sleep(max(0, 3 - (time.time() - s['last_finished_epoch'])))
    path = RAW / 'masterfilelist-native-201503.scan.body'
    response = urllib.request.build_opener(NoRedirect).open(urllib.request.Request(url, headers={'User-Agent': 'SignalNoise-LanguageFeasibility/1.0', 'Accept-Encoding': 'identity'}), timeout=45)
    headers = dict(response.headers)
    stop = 'end_of_response'
    with response, path.open('wb') as handle:
        tee = CappedTee(response, handle, min(5_000_000, remaining()))
        stream = io.BufferedReader(tee, buffer_size=65536)
        for line in stream:
            lines += 1
            decoded += len(line)
            parts = line.decode('utf-8', errors='replace').split()
            if len(parts) == 3 and parts[0].isdigit():
                found = re.fullmatch(r'https?://data\.gdeltproject\.org/gdeltv2/(\d{14})\.(gkg|mentions|export)\.csv\.zip', parts[2], re.I)
                if found and found.group(1) in expected:
                    objects[(found.group(1), found.group(2).lower())] = {'batch': found.group(1), 'table': found.group(2).lower(), 'bytes': int(parts[0]), 'md5': parts[1], 'url': parts[2].replace('http://', 'https://', 1)}
            if wanted <= set(objects):
                stop = 'all_native_mentions_and_events_window_slots_found'
                break
        if tee.exhausted: stop = 'bounded_prefix_cap'
        transferred = tee.count
    s.update(received_bytes=s['received_bytes'] + transferred, requests=s['requests'] + 1, last_finished_epoch=time.time())
    save(STATE, s)
    tables = {}
    for table in ('gkg', 'mentions', 'export'):
        entries = [v for (batch, kind), v in objects.items() if kind == table]
        tables[table] = {'listed_objects': len(entries), 'expected_objects': len(expected), 'listed_compressed_bytes': sum(r['bytes'] for r in entries), 'missing_manifest_entries': sorted(expected - {r['batch'] for r in entries})}
    results = {'start_inclusive': '20150301', 'end_exclusive': '20150329', 'source_url': url, 'head': head, 'headers': headers, 'lines_scanned': lines, 'transferred_bytes': transferred, 'stop_reason': stop, 'raw_path': str(path.relative_to(ROOT)), 'sha256': sha(path), 'tables': tables, 'native_mentions_plus_events_bytes': tables['mentions']['listed_compressed_bytes'] + tables['export']['listed_compressed_bytes'], 'full_corpus_downloaded': False, 'missing_entries_are_unknown_not_zero': True, 'objects': list(objects.values())}
    save(result_path, results)
    print(json.dumps({k: v for k, v in results.items() if k not in ('objects', 'head', 'headers')}, indent=2))


def verify_metadata_candidate(offline=False):
    native = json.loads((OUT / 'native_metadata_window.json').read_text())
    translated = json.loads((OUT / 'corpus_transfer_estimates.json').read_text())['windows'][0]
    missing = translated['translated_tables']['mentions']['missing_manifest_entries']
    probes = []
    for batch in missing:
        for suffix in ('mentions.CSV.zip', 'export.CSV.zip'):
            url = GDELT + batch + '.translation.' + suffix
            meta, _ = request(url, method='HEAD', offline=offline)
            probes.append({'batch': batch, 'table': suffix.split('.')[0], 'url': url, 'status': meta['status'], 'size_if_present': next((int(v) for k, v in meta['headers'].items() if k.lower() == 'content-length' and v.isdigit()), None), 'meaning': 'HEAD result at retrieval time; missing is unknown coverage, never zero news.'})
            save(OUT / 'candidate_missing_object_probes.json', probes)
    manifest_source = json.loads((OUT / 'manifest_scan.json').read_text())
    translated_entries = {}
    with (ROOT / manifest_source['raw_path']).open('rb') as handle:
        for line in handle:
            parts = line.decode('utf-8', errors='replace').split()
            if len(parts) == 3 and parts[0].isdigit() and re.search(r'/20150301000000\.translation\.(mentions|export)\.CSV\.zip$', parts[2], re.I):
                translated_entries[parts[2].split('.translation.')[1].split('.')[0].lower()] = {'url': parts[2].replace('http://', 'https://', 1), 'bytes': int(parts[0]), 'md5': parts[1]}
    samples = []
    old = json.loads((ROOT / 'historical/results/download_manifest.json').read_text())
    for table in ('mentions', 'export'):
        native_item = next(r for r in native['objects'] if r['batch'] == '20150301000000' and r['table'] == table)
        for stream, item in (('native_official', native_item), ('translated_official', translated_entries[table])):
            meta, path = request(item['url'], offline=offline, limit=item['bytes'])
            report = {'stream': stream, 'table': table, 'url': item['url'], 'expected_compressed_bytes': item['bytes'], 'response': meta}
            if meta['status'] == 200 and meta['error'] is None:
                body = path.read_bytes()
                if hashlib.md5(body).hexdigest() != item['md5']:
                    raise ValueError('Metadata sample checksum mismatch')
                with zipfile.ZipFile(io.BytesIO(body)) as archive:
                    member = archive.infolist()[0]
                    content = archive.read(member)
                fields_list = [line.decode('utf-8', errors='replace').split('\t') for line in content.splitlines() if line]
                languages = Counter()
                if table == 'mentions':
                    for fields in fields_list:
                        if len(fields) == 16:
                            languages[original_language(fields[14])[0] or 'unknown'] += 1
                report.update(expanded_bytes=len(content), rows=len(fields_list), column_histogram=dict(Counter(len(f) for f in fields_list)), language_counts=dict(languages), batch_timestamp_matches=sum((f[2] if table == 'mentions' else f[59]) == '20150301000000' for f in fields_list if len(f) == (16 if table == 'mentions' else 61)), expanded_sha256=hashlib.sha256(content).hexdigest())
                if stream == 'native_official':
                    original = next(r for r in old if r['batch'] == '20150301000000' and r['table'] == ('events' if table == 'export' else table))
                    report['matches_s3_raw_exactly'] = report['expanded_sha256'] == original['response']['sha256']
            samples.append(report)
            save(OUT / 'metadata_candidate_samples.json', samples)
    summary = {'window_start': '20150301', 'window_end_exclusive': '20150329', 'native_mentions_events_compressed_bytes': native['native_mentions_plus_events_bytes'], 'translated_mentions_events_listed_compressed_bytes': translated['translated_mentions_plus_events_bytes'], 'combined_listed_compressed_bytes': native['native_mentions_plus_events_bytes'] + translated['translated_mentions_plus_events_bytes'], 'unlisted_translated_bin_count': len(missing), 'missing_probes': probes, 'sample_checks': samples, 'full_window_downloaded': False, 'corpus_redefinition': 'Distinct event-bearing document identifiers from both mention streams, NOT mentions rows as articles and NOT all news. Language provenance and missing intervals still require gates before alerts.'}
    save(OUT / 'metadata_corpus_candidate.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ('missing_probes', 'sample_checks')}, indent=2))


def final_findings():
    quality = json.loads((OUT / 'translated_quality.json').read_text())
    page_quality = json.loads((OUT / 'page_quality_summary.json').read_text())
    native = json.loads((OUT / 'native_metadata_window.json').read_text())
    translated = json.loads((OUT / 'corpus_transfer_estimates.json').read_text())['windows'][0]
    candidate = json.loads((OUT / 'metadata_corpus_candidate.json').read_text())
    samples = json.loads((OUT / 'metadata_candidate_samples.json').read_text())
    expanded_estimate = 0
    for item in samples:
        table = item['table']
        source = native['tables'] if item['stream'] == 'native_official' else translated['translated_tables']
        expanded_estimate += source[table]['listed_compressed_bytes'] * item['expanded_bytes'] / item['expected_compressed_bytes']
    packages = json.loads((OUT / 'package_provenance.json').read_text())
    pages_state = json.loads((BASE / 'page_transfer_ledger.json').read_text())
    current = state()
    malformed = []
    for item in json.loads((OUT / 'translated_download_manifest.json').read_text()):
        if item['status'] != 'downloaded': continue
        with zipfile.ZipFile(ROOT / item['raw_path']) as archive:
            for member in archive.infolist():
                if member.is_dir(): continue
                with archive.open(member) as handle:
                    for number, raw in enumerate(handle, 1):
                        line = raw.rstrip(b'\r\n')
                        if len(line.split(b'\t')) != 27:
                            malformed.append({'batch': item['batch'], 'member': member.filename, 'line': number, 'blank': not bool(line.strip()), 'byte_length': len(line), 'preview': line[:160].decode('utf-8', errors='replace')})
    save(OUT / 'translated_nonrecord_lines.json', malformed)
    result = {'translated_gkg_records': quality['records'], 'translated_language_code_values': len(quality['original_language_counts']), 'translated_distinct_document_identifiers': quality['distinct_typed_document_ids'], 'translated_gkg_zip_bytes': sum(f['compressed_bytes'] for f in quality['files']), 'translated_gkg_expanded_bytes': sum(f['expanded_bytes'] for f in quality['files']), 'non_27_column_lines': len(malformed), 'non_27_column_blank_lines': sum(r['blank'] for r in malformed), 'page_quality': {k: page_quality[k] for k in ('overall', 'by_stream', 'historical_representation', 'manual_audit')}, 'native_plus_translated_metadata_window_bytes': candidate['combined_listed_compressed_bytes'], 'metadata_window_full_expanded_bytes_estimate': round(expanded_estimate), 'expanded_estimate_method': 'Separate native/translated mentions/events ratios measured on one batch each, applied to manifest-listed window sizes. Not a measured full-window size.', 'initial_historical_recorded_body_bytes': current['prior_historical_bytes'], 'recovery_bulk_metadata_recorded_body_bytes': current['received_bytes'], 'publisher_recorded_body_bytes': pages_state['received_bytes'], 'historical_recorded_response_body_bytes_total': current['prior_historical_bytes'] + current['received_bytes'] + pages_state['received_bytes'], 'cached_package_artifact_bytes': sum(p['bytes'] for p in packages), 'transfer_note': 'Response-body ledgers exclude HTTP/TLS overhead and software artifact transport retries. Package artifact sizes are stated separately; totals are far below the 5 GB ceiling.', 'decision': 'GO for a revised bounded language-provenance/metadata corpus pilot; NO-GO for a validated historical English-lead claim at this stage.', 'hypothesis_evaluated': False, 'alert_count': None, 'successful_alert_fraction': None, 'lead_time_distribution': None, 'full_28_day_corpus_downloaded': False, 'existing_demo_changed': False}
    save(OUT / 'findings_summary.json', result)
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540"><rect width="960" height="540" fill="#f5f3eb"/><g font-family="sans-serif" fill="#173d31"><text x="48" y="58" font-size="29">Historical language feasibility</text><text x="48" y="91" font-size="16">Diagnostic samples, not an attention-lead evaluation</text>'''
    metrics = [('Selected historical URLs', 96, '#173d31'), ('HTTP-success responses', page_quality['overall']['http_successes'], '#41765f'), ('Model-scored extracts', page_quality['overall']['model_scored_bodies'], '#719a80'), ('Current-language candidates after review', page_quality['overall']['usable_after_limited_manual_overrides'], '#99b78d')]
    for i, (label, count, color) in enumerate(metrics):
        y = 132 + i*67
        svg += '<text x="48" y="' + str(y+18) + '" font-size="15">' + label + '</text><rect x="410" y="' + str(y) + '" width="' + str(count/96*420) + '" height="29" fill="' + color + '"/><text x="850" y="' + str(y+21) + '" font-size="19">' + str(count) + '</text>'
    svg += '<text x="48" y="440" font-size="16">' + str(quality['records']) + ' translated GKG records; ' + str(len(quality['original_language_counts'])) + ' explicit source-code values.</text><text x="48" y="473" font-size="15">Manual audit: 2 HTML-language conflicts resolved; 1 boilerplate false acceptance rejected.</text><text x="48" y="505" font-size="14">Current-language candidates are not verified historical bodies. No alert or lead-time result.</text></g></svg>'
    (OUT / 'language_feasibility.svg').write_text(svg)
    print(json.dumps(result, indent=2))


def environment_plan(offline=False):
    pairs = [('pip', '25.2'), ('setuptools', '75.8.0'), ('wheel', '0.45.1'), ('numpy', '1.26.4'), ('langid', '1.1.6'), ('trafilatura', '1.12.2')]
    rows = []
    for package, version in pairs:
        meta, path = request('https://pypi.org/pypi/' + package + '/' + version + '/json', offline=offline)
        if meta['status'] != 200:
            raise RuntimeError('Pinned dependency unavailable: ' + package + ' ' + version)
        data = json.loads(path.read_bytes())
        dates = [datetime.fromisoformat(item['upload_time_iso_8601'].replace('Z', '+00:00')) for item in data['urls']]
        cutoff = datetime(2026, 9, 12, tzinfo=timezone.utc)
        if not dates or max(dates) > cutoff:
            raise RuntimeError('Dependency artifact is too new or undated')
        rows.append({'name': package, 'version': version, 'requires_python': data['info']['requires_python'], 'latest_artifact_upload': max(dates).isoformat(), 'metadata': str(path.relative_to(ROOT))})
    save(OUT / 'environment_plan.json', rows)
    print(json.dumps(rows, indent=2))


def validate_packages(offline=False):
    rows = []
    for path in sorted((BASE / 'packages').iterdir()):
        if path.suffix == '.whl':
            with zipfile.ZipFile(path) as archive:
                name = next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA') and n.count('/') == 1)
                info = Parser().parsestr(archive.read(name).decode())
        elif path.name.endswith('.tar.gz'):
            with tarfile.open(path) as archive:
                candidates = sorted((m for m in archive.getmembers() if m.name.endswith('PKG-INFO')), key=lambda m: m.name.count('/'))
                info = Parser().parsestr(archive.extractfile(candidates[0]).read().decode())
        else:
            continue
        package, version = info['Name'], info['Version']
        meta, body = request('https://pypi.org/pypi/' + package + '/' + version + '/json', offline=offline)
        if meta['status'] != 200:
            raise RuntimeError('Dependency metadata unavailable: ' + package)
        data = json.loads(body.read_bytes())
        artifact = next((a for a in data['urls'] if a['filename'] == path.name), None)
        if artifact is None or artifact['digests']['sha256'] != sha(path):
            raise RuntimeError('Package artifact provenance mismatch: ' + path.name)
        date = datetime.fromisoformat(artifact['upload_time_iso_8601'].replace('Z', '+00:00'))
        row = {'name': package, 'version': version, 'filename': path.name, 'bytes': path.stat().st_size, 'sha256': sha(path), 'uploaded_at_utc': date.isoformat(), 'older_than_seven_days': date <= datetime(2026, 9, 12, tzinfo=timezone.utc)}
        rows.append(row)
        save(OUT / 'package_provenance.json', rows)
        if not row['older_than_seven_days']:
            raise RuntimeError('Downloaded dependency is too new; do not install: ' + path.name)
    runtime = [r for r in rows if r['name'].lower() not in ('pip', 'setuptools', 'wheel')]
    target = ROOT / 'research' / 'requirements-language.txt'
    target.write_text('\n'.join(sorted(r['name'] + '==' + r['version'] for r in runtime)) + '\n')
    print(json.dumps({'verified_packages': len(rows), 'cached_package_bytes': sum(r['bytes'] for r in rows), 'requirements': str(target)}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('inspect', 'manifest', 'download', 'audit', 'environment-plan', 'plan-pages', 'validate-packages', 'estimate', 'native-estimate', 'candidate-check', 'findings'))
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.command == 'inspect':
        inspect(args.offline)
    elif args.command == 'manifest':
        stream_manifest(args.offline)
    elif args.command == 'download':
        download_translated(args.offline)
    elif args.command == 'environment-plan':
        environment_plan(args.offline)
    elif args.command == 'plan-pages':
        plan_pages()
    elif args.command == 'validate-packages':
        validate_packages(args.offline)
    elif args.command == 'estimate':
        cached_window_estimates()
    elif args.command == 'native-estimate':
        native_metadata_estimate(args.offline)
    elif args.command == 'candidate-check':
        verify_metadata_candidate(args.offline)
    elif args.command == 'findings':
        final_findings()
    else:
        audit_translated()


if __name__ == '__main__':
    main()
