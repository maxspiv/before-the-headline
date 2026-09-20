import argparse
import gzip
import hashlib
import html
import ipaddress
import json
import math
import re
import signal
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from research import language_recovery as recovery
from research.historical_experiment import backoff, preserve_demo, save, sha

ROOT = Path(__file__).resolve().parents[1]
BASE = recovery.BASE
OUT = recovery.OUT
PAGES = BASE / 'pages'
LEDGER = BASE / 'page_transfer_ledger.json'
AGENT = 'SignalNoiseLanguageAudit/1.0'
MAX_BODY_BYTES = 1_000_000
MAX_ROBOTS_BYTES = 128_000
MAX_REQUEST_SECONDS = 20
MAX_DOMAIN_REQUESTS = 12
MIN_DOMAIN_SECONDS = 3
MIN_TEXT_CHARS = 600
MIN_ALPHA_CHARS = 300
MAX_MODEL_CHARS = 24000
MIN_MARGIN_PER_ALPHA = 0.02
MIN_CHUNK_AGREEMENT = 0.8
LANGUAGE_ALIASES = {'eng': 'en', 'spa': 'es', 'fra': 'fr', 'deu': 'de', 'zho': 'zh', 'rus': 'ru', 'ara': 'ar', 'por': 'pt', 'ita': 'it', 'nld': 'nl', 'jpn': 'ja', 'kor': 'ko', 'tur': 'tr', 'pol': 'pl', 'ces': 'cs', 'ukr': 'uk', 'ind': 'id', 'vie': 'vi', 'ron': 'ro', 'swe': 'sv', 'fin': 'fi', 'dan': 'da', 'hun': 'hu', 'ell': 'el', 'heb': 'he', 'hin': 'hi', 'tha': 'th', 'fas': 'fa'}


def page_state():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    result = {'received_bytes': 0, 'requests': 0, 'domains': {}, 'started_at_utc': recovery.now()}
    save(LEDGER, result)
    return result


def budget_remaining():
    bulk = recovery.state()
    used = page_state()['received_bytes']
    return min(recovery.PHASE_CAP - bulk['received_bytes'] - used, recovery.TOTAL_CAP - bulk['prior_historical_bytes'] - bulk['received_bytes'] - used)


def host_key(url):
    host = urllib.parse.urlsplit(url).hostname
    return host.lower().removeprefix('www.') if host else ''


def valid_public_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password or p.port not in (None, 80, 443):
            return False
        host = p.hostname.lower()
        if host in ('localhost', 'localhost.localdomain') or host.endswith(('.local', '.localhost', '.internal')):
            return False
        try:
            address = ipaddress.ip_address(host)
            return address.is_global and not address.is_reserved
        except ValueError:
            return '.' in host
    except (ValueError, TypeError):
        return False


def wire_url(url, prefer_https=False):
    if not valid_public_url(url):
        raise ValueError('Unsafe or unsupported public article URL')
    p = urllib.parse.urlsplit(url)
    scheme = 'https' if prefer_https and p.scheme == 'http' else p.scheme
    hostname = p.hostname.encode('idna').decode('ascii')
    if ':' in hostname:
        hostname = '[' + hostname + ']'
    port = p.port
    if prefer_https and p.scheme == 'http' and port == 80:
        port = None
    netloc = hostname + (':' + str(port) if port else '')
    return urllib.parse.urlunsplit((scheme, netloc, urllib.parse.quote(p.path or '/', safe="/%:@;~!$&'()*+,=-._"), urllib.parse.quote(p.query, safe="=&%:@;~!$'()*+,-._/?"), ''))


@contextmanager
def deadline(seconds):
    def expired(signum, frame):
        raise TimeoutError('Bounded request deadline exceeded')
    old = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def public_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
    host, port = address
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    if not infos or any(not ipaddress.ip_address(info[4][0]).is_global or ipaddress.ip_address(info[4][0]).is_reserved for info in infos):
        raise OSError('Blocked non-public resolved address')
    end = time.monotonic() + (12 if timeout is socket._GLOBAL_DEFAULT_TIMEOUT or timeout is None else min(float(timeout), 12))
    last = None
    for family, socktype, proto, _, sockaddr in sorted(infos, key=lambda x: x[0] != socket.AF_INET):
        sock = socket.socket(family, socktype, proto)
        try:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Connect deadline exceeded')
            sock.settimeout(remaining)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last = exc
            sock.close()
    raise last or OSError('No public address connected')


def fetch_http(url, role='article', offline=False, refresh=False, body_cap=MAX_BODY_BYTES):
    url = wire_url(url)
    folder = PAGES / hashlib.sha256(url.encode()).hexdigest()
    previous = sorted(folder.glob('*.json'))
    if previous and not refresh:
        meta = json.loads(previous[-1].read_text())
        path = folder / meta['body_file']
        assert sha(path) == meta['sha256']
        return meta, path
    if offline:
        raise RuntimeError('Page response not cached: ' + url)
    proxies = {k: v for k, v in urllib.request.getproxies().items() if k in ('http', 'https', 'all')}
    if proxies:
        raise RuntimeError('Proxy configuration needs review before public-URL fetching; no proxy bypass attempted')
    for attempt in range(2):
        state = page_state()
        host = host_key(url)
        domain = state['domains'].get(host, {'requests': 0, 'last_finished': 0, 'not_before': 0})
        if domain['requests'] >= MAX_DOMAIN_REQUESTS:
            return {'status': None, 'error': 'per_domain_request_limit', 'url': url}, None
        wait = max(MIN_DOMAIN_SECONDS - (time.time() - domain['last_finished']), domain['not_before'] - time.time(), 0)
        if wait > 60:
            return {'status': None, 'error': 'deferred_domain_retry_after', 'url': url}, None
        time.sleep(wait)
        cap = min(MAX_ROBOTS_BYTES if role == 'robots' else body_cap, budget_remaining())
        if cap <= 0:
            return {'status': None, 'error': 'transfer_budget_exhausted', 'url': url}, None
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%S%fZ')
        path = folder / (stamp + '.body')
        status, headers, error, body, complete = None, {}, None, b'', False
        started = recovery.now()
        response = None
        chunks, size = [], 0
        try:
            try:
                import certifi
                context = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                context = ssl.create_default_context()
            opener = urllib.request.build_opener(recovery.NoRedirect, urllib.request.HTTPSHandler(context=context))
            req = urllib.request.Request(url, headers={'User-Agent': AGENT, 'Accept': 'text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1', 'Accept-Encoding': 'identity'})
            with deadline(MAX_REQUEST_SECONDS), patch('socket.create_connection', public_connection):
                try:
                    response = opener.open(req, timeout=12)
                except urllib.error.HTTPError as exc:
                    response, error = exc, 'http_' + str(exc.code)
                status = response.code
                headers = {k: ('[redacted]' if k.lower() in ('set-cookie', 'authorization', 'proxy-authorization') else v) for k, v in response.headers.items()}
                length = response.headers.get('Content-Length')
                actual_cap = min(cap, 64000) if status != 200 else cap
                if length and int(length) > actual_cap:
                    error = 'response_body_too_large'
                else:
                    chunks, size = [], 0
                    while size < actual_cap:
                        chunk = response.read(min(65536, actual_cap - size))
                        if not chunk:
                            complete = True
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                    body = b''.join(chunks)
                    if length and size == int(length):
                        complete = True
                    if not complete:
                        error = 'response_body_truncated'
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            error = type(exc).__name__ + ': ' + str(exc)
            body = b''.join(chunks)
        finally:
            if response is not None:
                response.close()
        path.write_bytes(body)
        meta = {'url': url, 'role': role, 'status': status, 'headers': headers, 'error': error, 'complete': complete, 'requested_at_utc': started, 'retrieved_at_utc': recovery.now(), 'received_bytes': len(body), 'partial_body_preservation': True, 'request_body_cap': cap, 'body_file': path.name, 'raw_path': str(path.relative_to(ROOT)), 'sha256': sha(path)}
        save(folder / (stamp + '.json'), meta)
        domain['requests'] += 1
        domain['last_finished'] = time.time()
        if status in (429, 503):
            delay = backoff(next((v for k, v in headers.items() if k.lower() == 'retry-after'), None))
            domain['not_before'] = time.time() + delay
        state['domains'][host] = domain
        state['requests'] += 1
        state['received_bytes'] += len(body)
        save(LEDGER, state)
        if status not in (429, 503, 502, 504) or attempt == 1:
            return meta, path
        delay = max(30, domain.get('not_before', 0) - time.time())
        if delay > 60:
            return meta, path
        time.sleep(delay)


def header(meta, name):
    return next((value for key, value in meta.get('headers', {}).items() if key.lower() == name.lower()), None)


def robots_allowed(url, offline=False):
    p = urllib.parse.urlsplit(url)
    robots_url = urllib.parse.urlunsplit((p.scheme, p.netloc, '/robots.txt', '', ''))
    trace = []
    for _ in range(4):
        meta, path = fetch_http(robots_url, 'robots', offline)
        trace.append(meta)
        location = header(meta, 'location')
        if meta['status'] in (301, 302, 303, 307, 308) and location:
            robots_url = wire_url(urllib.parse.urljoin(robots_url, location))
            continue
        if meta['status'] in (404, 410):
            return True, 'robots_not_found', trace
        if meta['status'] != 200 or not path or meta.get('error') or not meta.get('complete'):
            return False, 'robots_unavailable_or_denied', trace
        raw = path.read_bytes()
        if (header(meta, 'content-encoding') or '').lower() == 'gzip':
            try:
                with gzip.GzipFile(fileobj=__import__('io').BytesIO(raw)) as stream:
                    raw = stream.read(1_000_001)
                if len(raw) > 1_000_000:
                    return False, 'robots_decoded_size_limit', trace
            except (OSError, EOFError):
                return False, 'robots_decoding_failure', trace
        elif header(meta, 'content-encoding') not in (None, '', 'identity'):
            return False, 'robots_encoding_unsupported', trace
        text = raw.decode('utf-8', errors='replace')
        if ('html' in (header(meta, 'content-type') or '').lower() or '<html' in text[:1000].lower()) and not re.search(r'^\s*user-agent\s*:', text, re.M | re.I):
            return False, 'robots_not_usable_text', trace
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        if not parser.can_fetch(AGENT, url):
            return False, 'robots_disallowed', trace
        crawl_delay = parser.crawl_delay(AGENT) or parser.crawl_delay('*') or 0
        rate = parser.request_rate(AGENT) or parser.request_rate('*')
        if rate and rate.requests > 0:
            crawl_delay = max(crawl_delay, rate.seconds / rate.requests)
        if crawl_delay:
            if crawl_delay > 60:
                return False, 'robots_crawl_delay_exceeds_bound', trace
            state = page_state()
            key = host_key(url)
            if key in state['domains']:
                state['domains'][key]['not_before'] = max(state['domains'][key].get('not_before', 0), time.time() + crawl_delay)
                save(LEDGER, state)
        return True, 'robots_allowed', trace
    return False, 'robots_redirect_limit', trace


def fetch_pages(offline=False):
    preserve_demo()
    plan = json.loads((OUT / 'url_sample_plan.json').read_text())
    if plan['planned_urls'] > 200:
        raise ValueError('URL cap exceeded')
    outcomes = []
    path = OUT / 'page_fetch_manifest.json'
    existing = {r['sample_id']: r for r in json.loads(path.read_text())} if path.exists() else {}
    for row in plan['records']:
        if row['sample_id'] in existing:
            outcomes.append(existing[row['sample_id']])
            continue
        current = wire_url(row['url'], prefer_https=True)
        result = {**row, 'starting_url': current, 'scheme_upgrade': current != row['url'] and urllib.parse.urlsplit(row['url']).scheme == 'http', 'redirects': [], 'robots_trace': [], 'outcome': None, 'response': None, 'final_url': current}
        for hop in range(5):
            try:
                allowed, reason, trace = robots_allowed(current, offline)
            except (OSError, ValueError) as exc:
                result['outcome'] = 'unsafe_or_unresolvable_robots_url'
                result['error'] = str(exc)
                break
            result['robots_trace'].extend(trace)
            if not allowed:
                if hop == 0 and result['scheme_upgrade'] and trace and trace[-1]['status'] is None:
                    current = wire_url(row['url'])
                    result['redirects'].append({'from': result['starting_url'], 'to': current, 'reason': 'original_http_fallback_after_https_transport_failure; no TLS validation disabled'})
                    continue
                result['outcome'] = reason
                break
            meta, body = fetch_http(current, 'article', offline)
            result['response'] = meta
            result['final_url'] = current
            location = header(meta, 'location')
            if meta['status'] in (301, 302, 303, 307, 308) and location:
                target = urllib.parse.urljoin(current, location)
                if not valid_public_url(target):
                    result['outcome'] = 'unsafe_redirect'
                    break
                result['redirects'].append({'from': current, 'to': target, 'status': meta['status']})
                current = wire_url(target)
                continue
            if meta['status'] == 200 and meta.get('complete') and not meta.get('error'):
                result['outcome'] = 'http_success'
            else:
                result['outcome'] = meta.get('error') or 'http_failure'
            break
        if result['outcome'] is None:
            result['outcome'] = 'article_redirect_limit'
        outcomes.append(result)
        save(path, outcomes)
        print(row['sample_id'], row['stream'], row['publisher_host'], result['outcome'], flush=True)
    preserve_demo()


def retry_technical(offline=False):
    manifest_path = OUT / 'page_fetch_manifest.json'
    rows = json.loads(manifest_path.read_text())
    originals = OUT / 'page_fetch_manifest.initial.json'
    if not originals.exists():
        save(originals, rows)
    for name in ('page_language_results', 'page_quality_summary'):
        source = OUT / (name + '.json')
        target = OUT / (name + '.initial.json')
        if source.exists() and not target.exists():
            target.write_bytes(source.read_bytes())
    policy = {'reason': 'One uniform technical-recovery pass for successful-status oversized/truncated bodies and article-read timeouts; not selected by topic or language result.', 'article_body_cap_bytes': 2_000_000, 'attempts_per_affected_url': 1, 'robots_and_per_host_limits_preserved': True, 'no_retry_of_http_403_or_robots_disallow': True}
    save(OUT / 'technical_retry_policy.json', policy)
    for row in rows:
        if row.get('technical_retry_completed'):
            continue
        previous = row.get('response') or {}
        error = str(previous.get('error', ''))
        eligible = (previous.get('status') == 200 and error in ('response_body_too_large', 'response_body_truncated')) or (previous.get('status') in (None, 200) and ('timed out' in error.lower() or 'deadline' in error.lower()) and previous.get('role') == 'article')
        if not eligible:
            continue
        if offline:
            raise RuntimeError('Technical recovery for this sample has not been cached')
        row['initial_outcome'] = row['outcome']
        row['initial_response'] = previous
        current = previous['url']
        for hop in range(4):
            allowed, reason, trace = robots_allowed(current, offline)
            row['robots_trace'].extend(trace)
            if not allowed:
                row['outcome'] = reason
                break
            meta, path = fetch_http(current, offline=offline, refresh=True, body_cap=2_000_000)
            row['response'], row['final_url'] = meta, current
            location = header(meta, 'location')
            if meta['status'] in (301, 302, 303, 307, 308) and location:
                target = urllib.parse.urljoin(current, location)
                if not valid_public_url(target):
                    row['outcome'] = 'unsafe_redirect'
                    break
                row['redirects'].append({'from': current, 'to': target, 'status': meta['status'], 'phase': 'technical_retry'})
                current = wire_url(target)
                continue
            row['outcome'] = 'http_success' if meta['status'] == 200 and meta.get('complete') and not meta.get('error') else meta.get('error') or 'http_failure'
            break
        else:
            row['outcome'] = 'technical_retry_redirect_limit'
        row['technical_retry_completed'] = True
        save(manifest_path, rows)
        print('retry', row['sample_id'], row['initial_outcome'], '->', row['outcome'], flush=True)


class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title, self.in_title, self.html_lang, self.og_locale = [], False, None, None
        self.meta_dates, self.article_tag, self.og_article, self.scripts = [], False, False, []
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'html': self.html_lang = attrs.get('lang') or attrs.get('xml:lang')
        if tag == 'article': self.article_tag = True
        if tag == 'title': self.in_title = True
        if tag == 'meta':
            name = attrs.get('property') or attrs.get('name') or attrs.get('itemprop') or ''
            value = attrs.get('content')
            if name.lower() == 'og:locale': self.og_locale = value
            if name.lower() == 'og:type' and value == 'article': self.og_article = True
            if name.lower() in ('article:published_time', 'article:modified_time', 'datepublished', 'datemodified', 'pubdate', 'publishdate'):
                self.meta_dates.append({'field': name, 'value': value, 'source': 'HTML meta'})
        if tag == 'script' and attrs.get('type') == 'application/ld+json': self.script = []

    def handle_data(self, data):
        if self.in_title: self.title.append(data)
        if self.script is not None: self.script.append(data)

    def handle_endtag(self, tag):
        if tag == 'title': self.in_title = False
        if tag == 'script' and self.script is not None:
            self.scripts.append(''.join(self.script))
            self.script = None


def schema_articles(obj):
    found = []
    if isinstance(obj, dict):
        kinds = obj.get('@type', [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if isinstance(kinds, list) and any(k in ('Article', 'NewsArticle', 'BlogPosting', 'ReportageNewsArticle') for k in kinds):
            found.append(obj)
        for value in obj.values(): found.extend(schema_articles(value))
    elif isinstance(obj, list):
        for value in obj: found.extend(schema_articles(value))
    return found


def html_language(value):
    if value and re.fullmatch(r'[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*', value):
        primary = re.split('[-_]', value.lower())[0]
        return None if primary in ('und', 'unk', 'zxx') else LANGUAGE_ALIASES.get(primary, primary)
    return None


def historical_status(row, dates, final_url):
    batch_day = datetime.strptime(row['batch'][:8], '%Y%m%d').date()
    published, modified = [], []
    for item in dates:
        try:
            value = datetime.fromisoformat(re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', item['value'].replace('Z', '+00:00'))).date()
        except (AttributeError, ValueError, TypeError):
            continue
        (modified if 'modif' in item['field'].lower() else published).append(value)
    if urllib.parse.urlsplit(final_url).path.strip('/') == '' and urllib.parse.urlsplit(row['url']).path.strip('/'):
        return 'redirected_to_homepage_not_historical_article'
    if published and min((d - batch_day).days for d in published) > 2:
        return 'publisher_claimed_date_after_historical_bin'
    if modified and max((d - batch_day).days for d in modified) > 2:
        return 'historical_identity_possible_but_modified_later'
    if host_key(final_url) != row['publisher_host']:
        return 'changed_host_historical_identity_unverified'
    if published and any((d - batch_day).days <= 2 for d in published):
        return 'plausible_historical_article_current_snapshot_not_asof_proof'
    return 'historical_identity_unverified_no_usable_publisher_date'


def classify_pages():
    import importlib.metadata
    import trafilatura
    from trafilatura.utils import decode_file
    from langid.langid import LanguageIdentifier, model
    identifier = LanguageIdentifier.from_modelstring(model, norm_probs=False)
    payload = model.encode() if isinstance(model, str) else model
    model_info = {'model': 'langid packaged statistical language-ID model', 'version': importlib.metadata.version('langid'), 'model_sha256': hashlib.sha256(payload).hexdigest(), 'numpy_version': importlib.metadata.version('numpy'), 'extractor': 'trafilatura', 'extractor_version': importlib.metadata.version('trafilatura'), 'scores': 'Unnormalized raw model log scores, NOT calibrated probabilities; margins and chunk agreement are diagnostic heuristics.', 'policy': {'min_text_chars': MIN_TEXT_CHARS, 'min_alpha_chars': MIN_ALPHA_CHARS, 'max_model_chars': MAX_MODEL_CHARS, 'min_margin_per_alpha': MIN_MARGIN_PER_ALPHA, 'min_chunk_agreement': MIN_CHUNK_AGREEMENT, 'input': 'Current original publisher article-body text only. No GKG entity/theme lists, translations, country or URL used as classifier input.'}}
    save(OUT / 'language_model.json', model_info)
    rows = json.loads((OUT / 'page_fetch_manifest.json').read_text())
    classified = []
    textdir = BASE / 'texts'
    textdir.mkdir(parents=True, exist_ok=True)
    errors = ('page not found', 'access denied', 'just a moment', 'verify you are human', 'error 404', '404 not found', 'seite nicht gefunden', 'página no encontrada', 'page introuvable', 'security check', 'consent required')
    for row in rows:
        result = {**row, 'language_status': None, 'model_language': None, 'raw_model_score': None, 'raw_top_two_margin': None, 'raw_margin_per_alpha': None, 'html_lang': None, 'og_locale': None, 'text_chars': None, 'alphabetic_chars': None, 'text_file': None, 'model_input_sha256': None, 'publisher_dates': [], 'historical_status': 'not_assessable_without_body'}
        if row['outcome'] != 'http_success':
            result['language_status'] = 'inaccessible_or_not_fetched'
            classified.append(result)
            continue
        meta = row['response']
        raw = (ROOT / meta['raw_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == meta['sha256']
        if (header(meta, 'content-type') or '').split(';')[0].lower() not in ('text/html', 'application/xhtml+xml'):
            result['language_status'] = 'unsupported_content_type'
            classified.append(result)
            continue
        if (header(meta, 'content-encoding') or '').lower() == 'gzip':
            try:
                with gzip.GzipFile(fileobj=__import__('io').BytesIO(raw)) as stream:
                    decoded = stream.read(5_000_001)
                if len(decoded) > 5_000_000:
                    raise ValueError('Decoded page safety cap')
                raw = decoded
            except (OSError, ValueError, EOFError):
                result['language_status'] = 'content_decoding_failed'
                classified.append(result)
                continue
        parser = PageMetadata()
        parser.feed(decode_file(raw))
        title = ''.join(parser.title).strip()
        articles = []
        for script in parser.scripts:
            try:
                articles.extend(schema_articles(json.loads(script)))
            except (ValueError, RecursionError):
                pass
        primary = [a for a in articles if str(a.get('url', '')).rstrip('/') == row['final_url'].rstrip('/') or str(a.get('@id', '')).split('#')[0].rstrip('/') == row['final_url'].rstrip('/')]
        if not primary and len(articles) == 1:
            primary = articles
        dates = list(parser.meta_dates)
        for article in primary:
            for field in ('datePublished', 'dateModified'):
                if isinstance(article.get(field), str): dates.append({'field': field, 'value': article[field], 'source': 'primary Article JSON-LD'})
        result.update(page_title=title, html_lang=parser.html_lang, og_locale=parser.og_locale, publisher_dates=dates, historical_status=historical_status(row, dates, row['final_url']))
        if any(word in title.casefold() for word in errors):
            result['language_status'] = 'error_or_consent_screen_suspected'
            classified.append(result)
            continue
        if result['historical_status'] == 'redirected_to_homepage_not_historical_article':
            result['language_status'] = 'navigation_redirect_rejected'
            classified.append(result)
            continue
        try:
            text = trafilatura.extract(raw, url=row['final_url'], output_format='txt', include_comments=False, include_tables=False, favor_precision=True, no_fallback=True)
        except Exception as exc:
            result['extractor_error'] = type(exc).__name__ + ': ' + str(exc)
            text = None
        method = 'trafilatura_precision_no_fallback'
        if not text:
            bodies = [a['articleBody'] for a in primary if isinstance(a.get('articleBody'), str)]
            if len(bodies) == 1:
                from feasibility import PageText
                cleaner = PageText()
                cleaner.feed(bodies[0])
                text = '\n'.join(cleaner.parts)
                method = 'primary_jsonld_articleBody'
        if not text:
            result['language_status'] = 'no_article_body_extracted'
            classified.append(result)
            continue
        text = text.strip()
        alpha = sum(c.isalpha() for c in text)
        textpath = textdir / (row['sample_id'] + '-' + meta['sha256'][:12] + '.txt')
        textpath.write_text(text)
        result.update(text_file=str(textpath.relative_to(ROOT)), text_chars=len(text), alphabetic_chars=alpha, extraction_method=method, text_sha256=sha(textpath))
        if len(text) < MIN_TEXT_CHARS or alpha < MIN_ALPHA_CHARS:
            result['language_status'] = 'short_text_rejected'
            classified.append(result)
            continue
        paragraphs = [p for p in text.splitlines() if sum(c.isalpha() for c in p) >= 80]
        sentences = len(re.findall(r'[.!?。！？]', text))
        structure = parser.article_tag or parser.og_article or bool(primary)
        if not structure or (sentences < 3 and len(paragraphs) < 3):
            result['language_status'] = 'article_structure_unverified_or_navigation_suspected'
            classified.append(result)
            continue
        if text.count('\ufffd') > max(2, len(text) * 0.005):
            result['language_status'] = 'encoding_ambiguity'
            classified.append(result)
            continue
        model_text = text[:MAX_MODEL_CHARS]
        ranking = identifier.rank(model_text)
        top = ranking[0][0]
        score, margin = float(ranking[0][1]), float(ranking[0][1] - ranking[1][1])
        weighted, chunks = Counter(), []
        width = min(1200, max(200, len(model_text) // 3))
        starts = sorted({0, max(0, (len(model_text) - width) // 2), max(0, len(model_text) - width)})
        for start in starts:
            chunk = model_text[start:start+width]
            letters = sum(c.isalpha() for c in chunk)
            if letters < 100: continue
            label, rawscore = identifier.classify(chunk)
            weighted[label] += letters
            chunks.append({'offset': start, 'chars': len(chunk), 'alphabetic_chars': letters, 'language': label, 'raw_score': float(rawscore)})
        agreement = weighted[top] / sum(weighted.values()) if weighted else 0
        html_code = html_language(parser.html_lang)
        metadata_code = LANGUAGE_ALIASES.get(row['metadata_language'])
        result.update(model_language=top, raw_model_score=score, raw_top_two_margin=margin, raw_margin_per_alpha=margin / max(1, sum(c.isalpha() for c in model_text)), model_top_five=[{'language': label, 'raw_score': float(value)} for label, value in ranking[:5]], chunk_results=chunks, chunk_agreement_fraction=agreement, html_language_primary=html_code, html_model_disagreement=bool(html_code and html_code != top), metadata_language_two_letter=metadata_code, archive_metadata_model_disagreement=(metadata_code != top) if metadata_code else None, model_input_sha256=hashlib.sha256(model_text.encode()).hexdigest(), model_input_chars=len(model_text), model_input_truncated=len(text) > MAX_MODEL_CHARS)
        if not math.isfinite(score) or not math.isfinite(margin):
            result['language_status'] = 'invalid_model_score'
        elif agreement < MIN_CHUNK_AGREEMENT:
            result['language_status'] = 'ambiguous_or_multilingual_chunk_disagreement'
        elif result['raw_margin_per_alpha'] < MIN_MARGIN_PER_ALPHA:
            result['language_status'] = 'ambiguous_low_model_margin'
        elif result['html_model_disagreement']:
            result['language_status'] = 'ambiguous_html_model_disagreement'
        else:
            result['language_status'] = 'usable_current_body_language_not_historical_proof'
        classified.append(result)
    save(OUT / 'page_language_results.json', classified)
    print(json.dumps({'statuses': dict(Counter(r['language_status'] for r in classified)), 'model_labels': dict(Counter(r['model_language'] or 'unavailable' for r in classified))}, indent=2))


def summarize():
    rows = json.loads((OUT / 'page_language_results.json').read_text())
    audit_path = OUT / 'manual_audit.json'
    audited = json.loads(audit_path.read_text())['records'] if audit_path.exists() else []
    manual = {r['sample_id']: r for r in audited}
    def usable(row):
        review = manual.get(row['sample_id'], {})
        if review.get('article_body_usable') is False:
            return False
        return row['language_status'] == 'usable_current_body_language_not_historical_proof' or review.get('resolve_current_language_ambiguity', False)
    def stats(group):
        n = len(group)
        retrieved = sum(r['outcome'] == 'http_success' for r in group)
        scored = sum(r['model_language'] is not None for r in group)
        automatic = sum(r['language_status'] == 'usable_current_body_language_not_historical_proof' for r in group)
        reviewed = sum(usable(r) for r in group)
        return {'selected_urls': n, 'http_successes': retrieved, 'retrieval_failures': n - retrieved, 'retrieval_failure_fraction': (n - retrieved) / n if n else None, 'model_scored_bodies': scored, 'automatic_usable_current_language': automatic, 'not_automatically_usable': n - automatic, 'usable_after_limited_manual_overrides': reviewed, 'not_usable_after_limited_manual_overrides': n - reviewed, 'overall_not_usable_fraction': (n - reviewed) / n if n else None, 'language_status_counts': dict(Counter(r['language_status'] for r in group))}
    publishers = {host: stats([r for r in rows if r['publisher_host'] == host]) for host in sorted({r['publisher_host'] for r in rows})}
    streams = {stream: stats([r for r in rows if r['stream'] == stream]) for stream in sorted({r['stream'] for r in rows})}
    inferred = {}
    for code in sorted({r['model_language'] for r in rows if r['model_language']}):
        group = [r for r in rows if r['model_language'] == code]
        inferred[code] = {'model_scored_bodies': len(group), 'automatic_usable_bodies': sum(r['language_status'] == 'usable_current_body_language_not_historical_proof' for r in group), 'ambiguous_bodies': sum(r['language_status'].startswith('ambiguous') for r in group), 'usable_after_limited_manual_overrides': sum(usable(r) for r in group), 'manual_body_rejections': sum(manual.get(r['sample_id'], {}).get('article_body_usable') is False for r in group), 'retrieval_failure_fraction': None, 'limitation': 'Conditional on a model-scored body. Retrieval failures have no inferred language; assigning them to this language would be unjustified.'}
    metadata_languages = {code: stats([r for r in rows if r['metadata_language'] == code]) for code in sorted({r['metadata_language'] for r in rows if r['metadata_language']})}
    summary = {'overall': stats(rows), 'by_stream': streams, 'by_publisher_hostname': publishers, 'by_current_inferred_language': inferred, 'by_archive_metadata_language_translated_subsample_only': metadata_languages, 'historical_representation': dict(Counter(r['historical_status'] for r in rows)), 'native_current_model_labels': dict(Counter(r['model_language'] or 'unavailable' for r in rows if r['stream'] == 'native_s3')), 'translated_current_model_labels': dict(Counter(r['model_language'] or 'unavailable' for r in rows if r['stream'] == 'official_translated')), 'html_model_disagreements': [r['sample_id'] for r in rows if r.get('html_model_disagreement')], 'archive_metadata_model_disagreements': [r['sample_id'] for r in rows if r.get('archive_metadata_model_disagreement')], 'rates_scope': 'Descriptive rates in this deterministic, publisher-capped, topic-independent pilot; not population estimates. Publisher host is not independent media ownership.', 'language_scores_are_calibrated_probabilities': False}
    summary['manual_audit'] = {'reviewed_records': len(audited), 'valid_substantive_bodies_reviewed': sum(r['article_body_usable'] for r in audited), 'language_disagreements_on_valid_reviewed_bodies': [r['sample_id'] for r in audited if r['article_body_usable'] and r['model_language_agrees'] is False], 'model_input_quality_false_acceptances': [r['sample_id'] for r in audited if not r['article_body_usable'] and next(x for x in rows if x['sample_id'] == r['sample_id'])['language_status'] == 'usable_current_body_language_not_historical_proof'], 'html_disagreements_resolved_for_current_body_only': [r['sample_id'] for r in audited if r.get('resolve_current_language_ambiguity')], 'not_an_accuracy_estimate': True}
    save(OUT / 'page_quality_summary.json', summary)
    adjudicated = [{**row, 'manual_review': manual.get(row['sample_id']), 'accepted_current_language_diagnostic': usable(row), 'historical_language_verified': False, 'historical_counts_eligible': False, 'evidence_scope': 'Current retrieved article body; manual overrides do not prove historical content availability.'} for row in rows]
    save(OUT / 'adjudicated_page_results.json', adjudicated)
    import csv
    with (OUT / 'failure_rates_by_publisher.csv').open('w', newline='') as handle:
        fields = ['publisher_hostname', 'selected_urls', 'http_successes', 'retrieval_failures', 'retrieval_failure_fraction', 'model_scored_bodies', 'automatic_usable_current_language', 'usable_after_limited_manual_overrides', 'overall_not_usable_fraction']
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for host, values in publishers.items():
            writer.writerow({'publisher_hostname': host, **{k: values[k] for k in fields if k != 'publisher_hostname'}})
    with (OUT / 'page_language_evidence.csv').open('w', newline='') as handle:
        fields = ['sample_id', 'stream', 'batch', 'publisher_host', 'url', 'final_url', 'outcome', 'metadata_language', 'html_lang', 'model_language', 'raw_model_score', 'text_chars', 'language_status', 'accepted_current_language_diagnostic', 'historical_status', 'historical_language_verified', 'text_file']
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in adjudicated:
            values = {key: row.get(key) for key in fields}
            writer.writerow({key: ("'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value) for key, value in values.items()})
    review = []
    selected = set()
    def add(row, reason):
        if row['sample_id'] not in selected:
            selected.add(row['sample_id'])
            review.append({'sample_id': row['sample_id'], 'selection_reason': reason})
    for row in rows:
        if row['model_language'] and row['model_language'] != 'en': add(row, 'all non-English model labels')
        if row['language_status'].startswith('ambiguous') or row.get('archive_metadata_model_disagreement'): add(row, 'all classifier/metadata ambiguity or disagreement')
    for year in ('2015', '2017', '2019'):
        group = [r for r in rows if r['batch'].startswith(year) and r['stream'] == 'native_s3' and r['model_language'] == 'en']
        if group: add(min(group, key=lambda r: r['selection_hash']), 'English native sample from ' + year)
    for status in sorted({r['historical_status'] for r in rows if r['model_language']}):
        group = [r for r in rows if r['historical_status'] == status and r['model_language']]
        if group: add(min(group, key=lambda r: r['selection_hash']), 'historical-representativeness stratum: ' + status)
    for status in ('short_text_rejected', 'no_article_body_extracted', 'navigation_redirect_rejected', 'error_or_consent_screen_suspected', 'article_structure_unverified_or_navigation_suspected'):
        group = [r for r in rows if r['language_status'] == status]
        if group: add(min(group, key=lambda r: r['selection_hash']), 'rejection stratum: ' + status)
    save(OUT / 'manual_review_plan.json', {'sampling': 'Purposive stratified diagnostic audit: all non-English and disagreement cases, plus temporal/representativeness/rejection strata. Not an accuracy-estimation sample.', 'records': review})
    index = []
    for row in rows:
        index.append({k: row.get(k) for k in ('sample_id', 'stream', 'batch', 'publisher_host', 'url', 'final_url', 'outcome', 'page_title', 'metadata_language', 'html_lang', 'model_language', 'raw_model_score', 'raw_margin_per_alpha', 'chunk_agreement_fraction', 'text_chars', 'language_status', 'historical_status', 'text_file', 'publisher_dates')})
    save(OUT / 'review_index.json', index)
    print(json.dumps({'overall': summary['overall'], 'by_stream': streams, 'native_current_model_labels': summary['native_current_model_labels'], 'translated_current_model_labels': summary['translated_current_model_labels'], 'html_model_disagreements': summary['html_model_disagreements'], 'manual_review_size': len(review), 'manual_review_ids': [r['sample_id'] for r in review]}, indent=2))


def review_packet():
    rows = {r['sample_id']: r for r in json.loads((OUT / 'page_language_results.json').read_text())}
    plan = json.loads((OUT / 'manual_review_plan.json').read_text())
    directory = OUT / 'review_packets'
    directory.mkdir(parents=True, exist_ok=True)
    for item in plan['records']:
        row = rows[item['sample_id']]
        fields = {key: row.get(key) for key in ('sample_id', 'stream', 'batch', 'url', 'final_url', 'page_title', 'metadata_language', 'html_lang', 'model_language', 'raw_model_score', 'raw_margin_per_alpha', 'chunk_agreement_fraction', 'language_status', 'historical_status', 'publisher_dates', 'text_file')}
        if row.get('text_file'):
            text = (ROOT / row['text_file']).read_text()
            kind = 'EXTRACTED BODY (the full text is in text_file)'
        elif row.get('response', {}).get('raw_path'):
            from feasibility import PageText
            cleaner = PageText()
            cleaner.feed((ROOT / row['response']['raw_path']).read_bytes().decode('utf-8', errors='replace'))
            text = '\n'.join(cleaner.parts)
            kind = 'RAW PAGE VISIBLE-TEXT PREVIEW, NOT A VALIDATED ARTICLE BODY; not used as classifier input'
        else:
            text, kind = '(no body)', 'UNAVAILABLE'
        (directory / (row['sample_id'] + '.txt')).write_text(json.dumps(fields, indent=2, ensure_ascii=False) + '\n\n' + kind + '\n\n' + text[:7000] + ('\n[Preview truncated; inspect full cached text if needed.]' if len(text) > 7000 else '') + '\n')
    print('Wrote', len(plan['records']), 'manual review packets to', directory)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('fetch', 'classify', 'summarize', 'retry-technical', 'review-packet'))
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.command == 'fetch':
        fetch_pages(args.offline)
    elif args.command == 'summarize':
        summarize()
    elif args.command == 'retry-technical':
        retry_technical(args.offline)
    elif args.command == 'review-packet':
        review_packet()
    else:
        with patch('socket.socket.connect', side_effect=AssertionError('Classification must run offline')), patch('socket.getaddrinfo', side_effect=AssertionError('Classification must run offline')):
            classify_pages()
    preserve_demo()


if __name__ == '__main__':
    main()
