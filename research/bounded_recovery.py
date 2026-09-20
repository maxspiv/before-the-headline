import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from research.feasibility import NoRedirect, save_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results' / 'msc_validation'
CACHE = ROOT / 'cache' / 'recovery_msc'
STATE = OUT / 'recovery_state.json'
SESSION_DEADLINE = datetime.fromisoformat('2026-09-19T19:48:00+00:00')
MAX_REQUESTS = 24
MAX_BYTES = 25 * 1024 * 1024
DATA = 'https://data.gdeltproject.org/gdeltv2/'
S3 = 'https://gdelt-open-data.s3.us-east-1.amazonaws.com/'


def now():
    return datetime.now(timezone.utc)


def retry_delay(value, attempt=0, reference=None):
    reference = reference or now()
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            seconds = (dt - reference).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = 0
    return max(30 * (2 ** attempt), seconds)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    state = {'started_at_utc': now().isoformat(), 'deadline_utc': min(now() + timedelta(minutes=90), SESSION_DEADLINE).isoformat(), 'requests': 0, 'received_body_bytes': 0, 'last_request_finished_epoch': 0, 'closed': False, 'stop_reason': None, 'max_requests': MAX_REQUESTS, 'max_body_bytes': MAX_BYTES}
    save_json(STATE, state)
    return state


def stop(state, reason):
    state.update(closed=True, stop_reason=reason, finished_at_utc=now().isoformat())
    save_json(STATE, state)
    raise RuntimeError(reason)


def request(url, method='GET', offline=False, max_bytes=2 * 1024 * 1024, redirects=0):
    if urllib.parse.urlparse(url).netloc == 'api.gdeltproject.org':
        raise ValueError('DOC API requests are prohibited in this recovery pass')
    key = hashlib.sha256((method + ' ' + url).encode()).hexdigest()
    folder = CACHE / key
    attempts = sorted(folder.glob('*.json'))
    if attempts:
        meta = json.loads(attempts[-1].read_text())
        body = (folder / meta['body_file']).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta['sha256']:
            raise ValueError('Cache hash mismatch: ' + url)
    elif offline:
        raise RuntimeError('Recovery response not cached: ' + method + ' ' + url)
    else:
        state = load_state()
        for attempt in range(2):
            if state['closed']:
                raise RuntimeError('Recovery pass is closed: ' + str(state['stop_reason']))
            deadline = datetime.fromisoformat(state['deadline_utc'])
            wait = max(0, 6 - (time.time() - state['last_request_finished_epoch']))
            if state['requests'] >= MAX_REQUESTS or now() + timedelta(seconds=wait + 1) >= deadline:
                stop(state, 'Request or 90-minute time budget exhausted')
            if state['received_body_bytes'] >= MAX_BYTES:
                stop(state, 'Byte budget exhausted')
            time.sleep(wait)
            cap = min(max_bytes, MAX_BYTES - state['received_body_bytes'])
            started = now().isoformat()
            state['requests'] += 1
            save_json(STATE, state)
            status, headers, body, error, truncated = None, {}, b'', None, False
            response = None
            try:
                req = urllib.request.Request(url, method=method, headers={'User-Agent': 'BeforeTheHeadline-BoundedEvidence/0.2', 'Accept-Encoding': 'identity'})
                timeout = max(0.1, min(30, (deadline - now()).total_seconds()))
                try:
                    response = urllib.request.build_opener(NoRedirect).open(req, timeout=timeout)
                except urllib.error.HTTPError as exc:
                    response = exc
                    error = str(exc)
                status, headers = response.code, dict(response.headers)
                claimed = response.headers.get('Content-Length')
                if method != 'HEAD' and claimed and int(claimed) > cap:
                    truncated, error = True, 'Body not fetched: Content-Length exceeds remaining byte cap'
                elif method != 'HEAD':
                    chunks, size = [], 0
                    while True:
                        if now() >= deadline:
                            truncated, error = True, 'Deadline reached during body read'
                            break
                        block = response.read(min(65536, cap - size + 1))
                        if not block:
                            break
                        chunks.append(block)
                        size += len(block)
                        if size > cap:
                            truncated, error = True, 'Body exceeds byte cap'
                            break
                    body = b''.join(chunks)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                error = str(exc)
            finally:
                if response:
                    response.close()
            folder.mkdir(parents=True, exist_ok=True)
            name = now().strftime('%Y%m%dT%H%M%S%fZ')
            (folder / (name + '.body')).write_bytes(body)
            meta = {'url': url, 'method': method, 'status': status, 'headers': headers, 'requested_at_utc': started, 'completed_at_utc': now().isoformat(), 'body_file': name + '.body', 'body_bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'error': error, 'truncated': truncated, 'cache_key': key}
            save_json(folder / (name + '.json'), meta)
            state['received_body_bytes'] += len(body)
            state['last_request_finished_epoch'] = time.time()
            save_json(STATE, state)
            print(method, status, len(body), url, flush=True)
            if truncated:
                stop(state, error)
            if status not in (429, 503, 502, 504):
                break
            if attempt == 1:
                stop(state, 'Persistent throttling or service failure after two sequential attempts')
            header = next((v for k, v in headers.items() if k.lower() == 'retry-after'), None)
            delay = retry_delay(header, attempt)
            if delay + 1 >= (deadline - now()).total_seconds():
                stop(state, 'Retry-After/backoff would exceed recovery deadline')
            time.sleep(delay)
    location = next((v for k, v in meta['headers'].items() if k.lower() == 'location'), None)
    if meta['status'] in (301, 302, 303, 307, 308) and location:
        if redirects >= 4:
            raise RuntimeError('Redirect limit reached')
        return request(urllib.parse.urljoin(url, location), method, offline, max_bytes, redirects + 1)
    return meta, body


def inventory(offline=False):
    rows = []
    for prefix in ('v2/gkg/2026', 'v2/events/2026', 'v2/mentions/2026', 'v2/gkg/20190415'):
        url = S3 + '?' + urllib.parse.urlencode({'list-type': 2, 'prefix': prefix, 'max-keys': 1000})
        meta, body = request(url, offline=offline)
        row = {'kind': 's3_listing', 'prefix': prefix, 'response': meta, 'object_count': None, 'listed_bytes': None}
        if meta['status'] == 200:
            root = ET.fromstring(body)
            ns = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
            items = root.findall('s:Contents', ns)
            row.update(object_count=len(items), listed_bytes=sum(int(item.findtext('s:Size', namespaces=ns)) for item in items), truncated=root.findtext('s:IsTruncated', namespaces=ns), first_key=items[0].findtext('s:Key', namespaces=ns) if items else None, last_key=items[-1].findtext('s:Key', namespaces=ns) if items else None)
        rows.append(row)
        save_json(OUT / 'recovery_inventory.json', rows)
    for suffix in ('lastupdate.txt', 'lastupdate-translation.txt'):
        meta, body = request(DATA + suffix, offline=offline)
        rows.append({'kind': 'live_feed_pointer', 'response': meta, 'lines': body.decode('utf-8', errors='replace').splitlines() if meta['status'] == 200 else None})
        save_json(OUT / 'recovery_inventory.json', rows)
    for date in ('20260826000000', '20260914000000', '20260918000000'):
        for suffix in ('gkg.csv.zip', 'translation.gkg.csv.zip'):
            url = DATA + date + '.' + suffix
            meta, _ = request(url, method='HEAD', offline=offline)
            length = next((v for k, v in meta['headers'].items() if k.lower() == 'content-length'), None)
            rows.append({'kind': 'archive_size_probe', 'date': date, 'stream': suffix, 'response': meta, 'compressed_bytes': int(length) if meta['status'] == 200 and length and length.isdigit() else None})
            save_json(OUT / 'recovery_inventory.json', rows)
    selected = [r for r in rows if r['kind'] == 'archive_size_probe' and r['date'] == '20260826000000']
    known = len(selected) == 2 and all(r['compressed_bytes'] is not None for r in selected)
    pair_bytes = sum(r['compressed_bytes'] for r in selected) if known else None
    estimate = {'sample_urls': [r['response']['url'] for r in selected], 'sample_compressed_bytes': pair_bytes, 'sample_description': 'One scheduled 15-minute GKG pair at 2026-08-26 00:00 UTC; not representative or complete coverage', 'seven_day_extrapolated_bytes': pair_bytes * 96 * 7 if known else None, 'extrapolation_caveat': 'Illustrative only: one-bin size scaled to 7 days and both streams; not a measured daily total or completeness estimate', 'full_window_download_authorized': False, 'payload_download_before_estimate': False}
    save_json(OUT / 'download_estimate.json', estimate)
    print(json.dumps(estimate, indent=2))


def sample(offline=False):
    estimate = json.loads((OUT / 'download_estimate.json').read_text())
    size = estimate['sample_compressed_bytes']
    if size is None or size > 20 * 1024 * 1024:
        raise RuntimeError('No bounded size estimate for the sample; payload fetch withheld')
    state = load_state()
    if not offline and state['received_body_bytes'] + size > MAX_BYTES:
        raise RuntimeError('Sample would exceed remaining pass byte budget')
    outputs = []
    for url in estimate['sample_urls']:
        meta, body = request(url, offline=offline, max_bytes=20 * 1024 * 1024)
        outputs.append({'response': meta, 'expected_scope': 'diagnostic 15-minute slice only; no population counts or normalized timeline'})
        save_json(OUT / 'bulk_samples.json', outputs)


def cutoff(offline=False):
    rows = json.loads((OUT / 'recovery_inventory.json').read_text())
    selected = [r for r in rows if r['kind'] == 'archive_size_probe' and r['date'] == '20260918000000']
    if len(selected) != 2 or any(r['compressed_bytes'] is None for r in selected):
        raise RuntimeError('Post-cutoff pair has no complete size estimate')
    size = sum(r['compressed_bytes'] for r in selected)
    estimate = {'sample_compressed_bytes': size, 'urls': [r['response']['url'] for r in selected], 'scope': 'Post-September-13 diagnostic pair only; does not establish corpus-wide completeness'}
    save_json(OUT / 'post_cutoff_estimate.json', estimate)
    print('Post-cutoff estimate before fetching:', size, 'compressed bytes', flush=True)
    state = load_state()
    if not offline and state['received_body_bytes'] + size > MAX_BYTES:
        raise RuntimeError('Post-cutoff pair exceeds remaining byte budget')
    outputs = []
    for url in estimate['urls']:
        meta, body = request(url, offline=offline, max_bytes=20 * 1024 * 1024)
        outputs.append({'response': meta, 'expected_scope': estimate['scope']})
        save_json(OUT / 'bulk_post_cutoff.json', outputs)


def codebook(offline=False):
    url = 'https://data.gdeltproject.org/documentation/GDELT-Global_Knowledge_Graph_Codebook-V2.1.pdf'
    head, _ = request(url, method='HEAD', offline=offline)
    size = next((int(v) for k, v in head['headers'].items() if k.lower() == 'content-length' and v.isdigit()), None)
    save_json(OUT / 'codebook_estimate.json', {'response': head, 'estimated_bytes': size})
    if head['status'] != 200 or size is None or size > 5 * 1024 * 1024:
        raise RuntimeError('Codebook size/access gate did not pass')
    meta, _ = request(url, offline=offline, max_bytes=5 * 1024 * 1024)
    save_json(OUT / 'codebook_response.json', meta)


def close_pass():
    state = load_state()
    if not state['closed']:
        state.update(closed=True, finished_at_utc=now().isoformat(), stop_reason='Bounded pass completed; further live recovery disabled')
        save_json(STATE, state)
    print(json.dumps(state, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('inventory', 'sample', 'cutoff', 'codebook', 'close'))
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.phase == 'inventory':
        inventory(args.offline)
    elif args.phase == 'sample':
        sample(args.offline)
    elif args.phase == 'cutoff':
        cutoff(args.offline)
    elif args.phase == 'codebook':
        codebook(args.offline)
    else:
        close_pass()


if __name__ == '__main__':
    main()
