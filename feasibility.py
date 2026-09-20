#!/usr/bin/env python3
import argparse
import csv
import hashlib
import html
import io
import json
import math
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / 'cache'
OUT = ROOT / 'results'
API = 'https://api.gdeltproject.org/api/v2/doc/doc'
DOCS = 'https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/'
LANGUAGES = ('english', 'spanish', 'chinese')
TOPICS = {
    'shipping': '(shipping OR freight OR "container ship" OR "Red Sea" OR "Suez Canal") (disruption OR attack OR closure OR congestion OR rerouting)',
    'semiconductors': '(semiconductor OR semiconductors OR chipmaker OR "chip supply" OR TSMC) (supply OR shortage OR factory OR export OR production)',
    'energy': '("power grid" OR pipeline OR refinery OR "LNG terminal" OR "power plant") (outage OR attack OR shutdown OR disruption OR explosion)',
}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def follow_redirect(meta, body, offline, refresh, depth):
    location = next((v for k, v in meta['headers'].items() if k.lower() == 'location'), None)
    if meta['status'] in (301, 302, 303, 307, 308) and location:
        if depth >= 8:
            raise RuntimeError('Too many redirects: ' + meta['url'])
        return fetch(urllib.parse.urljoin(meta['url'], location), offline, refresh, depth + 1)
    return meta, body


def fetch(url, offline=False, refresh=False, depth=0):
    CACHE.mkdir(exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    folder = CACHE / key
    folder.mkdir(exist_ok=True)
    previous = sorted(folder.glob('*.json'))
    if previous and not refresh:
        meta = json.loads(previous[-1].read_text())
        body = (folder / meta['body_file']).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta['sha256']:
            raise ValueError('Corrupt response cache: ' + url)
        return follow_redirect({**meta, 'from_cache': True}, body, offline, refresh, depth)
    if offline:
        raise RuntimeError('Not cached: ' + url)
    if urllib.parse.urlparse(url).netloc == 'api.gdeltproject.org':
        time.sleep(6)
    started = stamp()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'BeforeTheHeadline-Feasibility/0.1 (research prototype)', 'Accept-Encoding': 'identity'})
        with urllib.request.build_opener(NoRedirect).open(req, timeout=65) as response:
            body = response.read()
            status = response.status
            headers = dict(response.headers)
            final_url = response.url
        error = None
    except urllib.error.HTTPError as exc:
        body, status, headers, final_url, error = exc.read(), exc.code, dict(exc.headers), exc.url, str(exc)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        body, status, headers, final_url, error = b'', None, {}, url, str(exc)
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    (folder / (name + '.body')).write_bytes(body)
    meta = {'url': url, 'final_url': final_url, 'requested_at_utc': started, 'completed_at_utc': stamp(), 'status': status, 'headers': headers, 'error': error, 'body_file': name + '.body', 'sha256': hashlib.sha256(body).hexdigest()}
    save_json(folder / (name + '.json'), meta)
    return follow_redirect({**meta, 'from_cache': False}, body, offline, refresh, depth)


def api(query, mode, start, end, offline=False, refresh=False, **extra):
    params = {'query': query, 'mode': mode, 'format': 'json', 'startdatetime': start, 'enddatetime': end, **extra}
    url = API + '?' + urllib.parse.urlencode(params)
    meta, body = fetch(url, offline, refresh)
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        value = None
    return {'request': params, 'cache_key': hashlib.sha256(url.encode()).hexdigest(), 'status': meta['status'], 'error': meta['error'], 'retrieved_at_utc': meta['completed_at_utc'], 'from_cache': meta['from_cache'], 'data': value, 'body_preview': body[:500].decode('utf-8', errors='replace') if value is None else None}


START = '20260821000000'
END = '20260918000000'


def collect(offline=False, refresh=False):
    plan = []
    for language in LANGUAGES:
        plan.append(('denominator_' + language, 'sourcelang:' + language, 'timelinevolraw', START, END, {}))
        for topic, query in TOPICS.items():
            plan.append((topic + '_' + language, query + ' sourcelang:' + language, 'timelinevolraw', START, END, {}))
    for language in LANGUAGES:
        plan.append(('percent_shipping_' + language, TOPICS['shipping'] + ' sourcelang:' + language, 'timelinevol', START, END, {}))
    plan.extend([
        ('languages_shipping', TOPICS['shipping'], 'timelinelang', START, END, {}),
        ('resolution_24h', TOPICS['shipping'] + ' sourcelang:english', 'timelinevolraw', '20260831000000', '20260901000000', {}),
        ('resolution_4d', TOPICS['shipping'] + ' sourcelang:english', 'timelinevolraw', '20260829000000', '20260902000000', {}),
        ('article_cap_250', 'sourcelang:english', 'artlist', '20260831000000', '20260901000000', {'maxrecords': 250, 'sort': 'datedesc'}),
        ('article_cap_300', 'sourcelang:english', 'artlist', '20260831000000', '20260901000000', {'maxrecords': 300, 'sort': 'datedesc'}),
    ])
    for name, query, mode, start, end, extra in plan:
        result = api(query, mode, start, end, offline, refresh, **extra)
        save_json(OUT / 'requests' / (name + '.json'), result)
        data = result.get('data') or {}
        print(name, result['status'], data.get('query_details'), len(data.get('articles', [])), result.get('body_preview'), flush=True)
        if result['status'] == 429 and not offline and not result['from_cache']:
            time.sleep(15)


def retry_failed(names=None):
    paths = sorted((OUT / 'requests').glob('*.json'))
    if names and set(names) - {p.stem for p in paths}:
        raise ValueError('Unknown request names: ' + str(set(names) - {p.stem for p in paths}))
    for path in paths:
        if names and path.stem not in names:
            continue
        prior = json.loads(path.read_text())
        if prior.get('status') == 200 and prior.get('data') is not None:
            continue
        params = dict(prior['request'])
        query, mode = params.pop('query'), params.pop('mode')
        start, end = params.pop('startdatetime'), params.pop('enddatetime')
        params.pop('format')
        result = api(query, mode, start, end, refresh=True, **params)
        save_json(path, result)
        print(path.stem, result['status'], result.get('body_preview'), flush=True)
        if result['status'] == 429:
            time.sleep(15)


def load_request(name):
    path = OUT / 'requests' / (name + '.json')
    return json.loads(path.read_text()) if path.exists() else {'status': None, 'data': None, 'error': 'not collected'}


def points(response):
    if response.get('status') != 200 or not isinstance(response.get('data'), dict):
        return {}
    series = response['data'].get('timeline', [])
    if len(series) != 1:
        return {}
    data = series[0].get('data', [])
    if len({p['date'] for p in data}) != len(data):
        raise ValueError('Duplicate timeline bins')
    return {p['date']: p for p in data}


def parse_date(value):
    return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)


def expected_dates():
    start = datetime.strptime(START, '%Y%m%d%H%M%S')
    end = datetime.strptime(END, '%Y%m%d%H%M%S')
    return [(start + timedelta(days=i)).strftime('%Y%m%dT%H%M%SZ') for i in range((end - start).days)]


def trailing_alert(history, current, count):
    if len(history) != 7 or current is None or any(v is None for v in history):
        return None
    mean = statistics.mean(history)
    sd = statistics.stdev(history)
    threshold = max(2 * mean, mean + 3 * sd, mean + 2)
    return {'baseline_mean_per_10k': mean, 'baseline_sd_per_10k': sd, 'threshold_per_10k': threshold, 'fold': current / mean if mean else None, 'candidate': count >= 10 and current > threshold}


def analyze():
    dates = expected_dates()
    denominators = {lang: points(load_request('denominator_' + lang)) for lang in LANGUAGES}
    raw = {(topic, lang): points(load_request(topic + '_' + lang)) for topic in TOPICS for lang in LANGUAGES}
    checks = []
    for lang in LANGUAGES:
        pct = points(load_request('percent_shipping_' + lang))
        shared = set(pct) & set(raw[('shipping', lang)])
        errors = [abs(pct[d]['value'] - 100 * raw[('shipping', lang)][d]['value'] / raw[('shipping', lang)][d]['norm']) for d in shared if raw[('shipping', lang)][d].get('norm', 0) > 0]
        checks.append({'check': 'timelinevol_equals_100_count_over_norm', 'language': lang, 'bins': len(errors), 'max_absolute_percentage_point_error': max(errors) if errors else None, 'passed': bool(errors) and max(errors) <= 0.00001})
    common = set.intersection(*(set(denominators[lang]) for lang in LANGUAGES))
    global_norm_equal = bool(common) and all(len({denominators[lang][d].get('norm') for lang in LANGUAGES}) == 1 for d in common)
    checks.append({'check': 'norm_identical_across_language_only_queries', 'bins': len(common), 'passed': global_norm_equal})
    language_checks = {}
    for lang in LANGUAGES:
        dp = denominators[lang]
        ds = sorted(dp)
        failures = []
        if not ds:
            failures.append('missing_language_denominator')
        if (load_request('denominator_' + lang).get('data') or {}).get('query_details', {}).get('date_resolution') != 'day':
            failures.append('denominator_not_daily')
        for d, p in dp.items():
            if not isinstance(p.get('value'), (int, float)) or not isinstance(p.get('norm'), (int, float)) or not (0 < p['value'] <= p['norm']):
                failures.append('invalid_language_total:' + d)
        for topic in TOPICS:
            tp = raw[(topic, lang)]
            if not tp:
                failures.append('missing_numerator:' + topic)
            if (load_request(topic + '_' + lang).get('data') or {}).get('query_details', {}).get('date_resolution') != 'day':
                failures.append('numerator_not_daily:' + topic)
            for d, p in tp.items():
                if d not in dp or p.get('norm') != dp[d].get('norm') or not (0 <= p.get('value', -1) <= dp[d]['value']):
                    failures.append('numerator_denominator_mismatch:' + topic + ':' + d)
        language_checks[lang] = {'passed': not failures and global_norm_equal and all(c['passed'] for c in checks if c.get('language') == lang), 'failures': failures}
    normalization_verified = all(v['passed'] for v in language_checks.values())
    checks.append({'check': 'language_denominator_checks', 'details': language_checks, 'passed': normalization_verified})
    inventory = {}
    for topic in TOPICS:
        for lang in LANGUAGES:
            name = topic + '_' + lang
            ps = raw[(topic, lang)]
            inventory[name] = {'status': load_request(name)['status'], 'observed_bins': len(ps), 'first': min(ps) if ps else None, 'last': max(ps) if ps else None, 'missing_expected_bins': [d for d in dates if d not in ps], 'outside_requested_grid': sorted(set(ps) - set(dates))}
    latest = {key: max(ps) if ps else None for key, ps in raw.items()}
    rows, alerts, raw_alerts = [], [], []
    for topic in TOPICS:
        for lang in LANGUAGES:
            history, raw_history = [], []
            for i, d in enumerate(dates):
                p = raw[(topic, lang)].get(d)
                den = denominators[lang].get(d)
                state = 'observed'
                if p is None:
                    state = 'missing'
                elif den is None or not normalization_verified:
                    state = 'unverified_denominator'
                elif d == latest[(topic, lang)] or d == max(denominators[lang]):
                    state = 'excluded_latest_bin'
                share = 10000 * p['value'] / den['value'] if p and den and normalization_verified and den['value'] > 0 else None
                usable = share if state == 'observed' else None
                a = trailing_alert(history[-7:], usable, p['value'] if p else 0)
                row = {'topic': topic, 'language': lang, 'date': d, 'state': state, 'article_count': p['value'] if p else None, 'language_total': den['value'] if den else None, 'global_norm': p.get('norm') if p else None, 'per_10k_language_articles': share, 'baseline_mean_per_10k': a['baseline_mean_per_10k'] if a else None, 'threshold_per_10k': a['threshold_per_10k'] if a else None, 'candidate': bool(a and a['candidate'])}
                row['raw_state'] = 'missing' if p is None else ('excluded_latest_bin' if d == latest[(topic, lang)] else 'observed')
                row['raw_candidate'] = False
                rows.append(row)
                if a and a['candidate']:
                    alerts.append({**row, **a, 'baseline_dates': dates[i-7:i]})
                history.append(usable)
                raw_current = p['value'] if p and d != latest[(topic, lang)] else None
                preceding = raw_history[-7:]
                if len(preceding) == 7 and all(v is not None for v in preceding) and raw_current is not None:
                    mu, sd = statistics.mean(preceding), statistics.stdev(preceding)
                    threshold = max(2 * mu, mu + 3 * sd, mu + 10)
                    if raw_current >= 10 and raw_current > threshold:
                        row['raw_candidate'] = True
                        raw_alerts.append({'topic': topic, 'language': lang, 'date': d, 'article_count': raw_current, 'baseline_mean_count': mu, 'baseline_sd_count': sd, 'threshold_count': threshold, 'baseline_dates': dates[i-7:i], 'ratio_to_threshold': raw_current / threshold, 'interpretation': 'within-language aggregate-count increase only; not normalized and not cross-language comparable'})
                raw_history.append(raw_current)
    save_json(OUT / 'raw_candidates.json', raw_alerts)
    save_json(OUT / 'normalization_checks.json', checks)
    save_json(OUT / 'coverage_inventory.json', inventory)
    save_json(OUT / 'timeline.json', rows)
    save_json(OUT / 'candidates.json', alerts)
    with (OUT / 'timeline.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    diagnostics = {}
    for name in ('resolution_24h', 'resolution_4d'):
        response = load_request(name)
        ps = points(response)
        sorted_dates = sorted(ps)
        deltas = sorted({(parse_date(b)-parse_date(a)).total_seconds() / 60 for a, b in zip(sorted_dates, sorted_dates[1:])})
        diagnostics[name] = {'status': response['status'], 'query_details': (response.get('data') or {}).get('query_details'), 'bins': len(ps), 'adjacent_deltas_minutes': deltas, 'first': sorted_dates[0] if ps else None, 'last': sorted_dates[-1] if ps else None, 'aggregate_count_sum_not_unique_window_count': sum(p['value'] for p in ps.values()) if ps else None}
    for name in ('article_cap_250', 'article_cap_300'):
        response = load_request(name)
        articles = (response.get('data') or {}).get('articles')
        diagnostics[name] = {'status': response['status'], 'returned_evidence_records_not_population_count': len(articles) if articles is not None else None, 'body_preview': response.get('body_preview')}
    lang_response = load_request('languages_shipping')
    diagnostics['timelinelang'] = {'status': lang_response['status'], 'series_labels': [s.get('series') for s in (lang_response.get('data') or {}).get('timeline', [])], 'used_for_normalization': False}
    save_json(OUT / 'api_diagnostics.json', diagnostics)
    selected = []
    for topic in TOPICS:
        candidates = [a for a in alerts if a['topic'] == topic]
        if candidates:
            best = max(candidates, key=lambda a: a['per_10k_language_articles'] / a['threshold_per_10k'])
            selected.append({'topic': topic, 'date': best['date'], 'trigger_language': best['language'], 'reason': 'largest ratio to frozen exploratory alert threshold'})
        elif not normalization_verified:
            candidates = [a for a in raw_alerts if a['topic'] == topic]
            if candidates:
                best = max(candidates, key=lambda a: a['ratio_to_threshold'])
                selected.append({'topic': topic, 'date': best['date'], 'trigger_language': best['language'], 'reason': 'raw-count-only fallback; normalization unavailable; no language ranking'})
    save_json(OUT / 'evidence_plan.json', selected)
    print(json.dumps({'normalization_verified': normalization_verified, 'checks': checks, 'candidate_count': len(alerts), 'evidence_plan': selected, 'diagnostics': diagnostics}, indent=2))


def evidence(offline=False, refresh=False):
    plan = json.loads((OUT / 'evidence_plan.json').read_text())
    manifest, article_rows = [], []
    for entry in plan:
        date = parse_date(entry['date'])
        for lang in LANGUAGES:
            for offset in (-1, 0):
                day = date + timedelta(days=offset)
                start = day.strftime('%Y%m%d%H%M%S')
                end = (day + timedelta(days=1)).strftime('%Y%m%d%H%M%S')
                name = 'evidence_' + entry['topic'] + '_' + lang + '_' + start[:8]
                r = api(TOPICS[entry['topic']] + ' sourcelang:' + lang, 'artlist', start, end, offline, refresh, maxrecords=250, sort='dateasc')
                save_json(OUT / 'requests' / (name + '.json'), r)
                articles = (r.get('data') or {}).get('articles', [])
                article_rows.extend({'request_name': name, 'topic': entry['topic'], 'requested_language': lang, 'request_cache_key': r['cache_key'], 'sample_not_census': True, **article} for article in articles)
                manifest.append({'name': name, 'topic': entry['topic'], 'language': lang, 'date': start[:8], 'selection': 'earliest-seen capped evidence, not a census', 'status': r['status'], 'returned_records': len(articles), 'at_requested_cap': len(articles) >= 250, 'cache_key': r['cache_key']})
                print(name, r['status'], len(articles), flush=True)
                if r['status'] == 429 and not offline and not r['from_cache']:
                    time.sleep(15)
    save_json(OUT / 'evidence_manifest.json', manifest)
    save_json(OUT / 'article_evidence.json', article_rows)


def audit():
    cached = []
    for path in sorted(CACHE.glob('*/*.json')):
        meta = json.loads(path.read_text())
        body = (path.parent / meta['body_file']).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta['sha256']:
            raise ValueError('Cache integrity failure: ' + str(path))
        cached.append({'metadata': str(path.relative_to(ROOT)), 'url': meta['url'], 'status': meta['status'], 'retrieved_at_utc': meta['completed_at_utc'], 'bytes': len(body), 'sha256': meta['sha256']})
    save_json(OUT / 'cache_manifest.json', cached)
    daily = points(load_request('shipping_english'))
    chinese_total = points(load_request('denominator_chinese'))
    common = sorted(set(daily) & set(chinese_total))
    hourly = points(load_request('resolution_4d'))
    begin, end = parse_date('20260829T000000Z'), parse_date('20260902T000000Z')
    grid = [(begin + timedelta(hours=i)).strftime('%Y%m%dT%H%M%SZ') for i in range(96)]
    hour_by_day = {}
    for d, p in hourly.items():
        if begin <= parse_date(d) < end:
            day = d[:8] + 'T000000Z'
            hour_by_day[day] = hour_by_day.get(day, 0) + p['value']
    counterexample = []
    for d in ('20260825T000000Z', '20260826T000000Z'):
        if d in daily:
            p = daily[d]
            counterexample.append({'date': d, 'count': p['value'], 'norm': p['norm'], 'count_over_norm_percent': 100 * p['value'] / p['norm']})
    result = {
        'api_attempts_by_status': {str(status): sum(r['status'] == status and urllib.parse.urlparse(r['url']).netloc == 'api.gdeltproject.org' for r in cached) for status in {r['status'] for r in cached if urllib.parse.urlparse(r['url']).netloc == 'api.gdeltproject.org'}},
        'partial_norm_check': {'bins': len(common), 'english_shipping_norm_equals_chinese_all_articles_norm': bool(common) and all(daily[d]['norm'] == chinese_total[d]['norm'] for d in common), 'does_not_verify_english_or_spanish_language_totals': True},
        'hourly_expected_half_open_bins': len(grid),
        'hourly_missing_half_open_bins': [d for d in grid if d not in hourly],
        'hourly_outside_half_open_grid': sorted(set(hourly) - set(grid)),
        'hourly_sums_vs_daily': [{'date': d, 'sum_returned_hourly_counts': n, 'daily_count': daily.get(d, {}).get('value'), 'equal': daily.get(d, {}).get('value') == n} for d, n in sorted(hour_by_day.items())],
        'count_vs_norm_counterexample': counterexample,
        'lead_time': None,
    }
    save_json(OUT / 'audit.json', result)
    print(json.dumps(result, indent=2))


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(' '.join(data.split()))


def sources(offline=False, refresh=False):
    plan = json.loads((ROOT / 'evidence_sources.json').read_text())
    manifest = []
    for source in plan:
        meta, body = fetch(source['url'], offline, refresh)
        parser = PageText()
        parser.feed(body.decode('utf-8', errors='replace'))
        text_path = OUT / 'pages' / (source['id'] + '.txt')
        text_path.parent.mkdir(exist_ok=True)
        text_path.write_text('\n'.join(parser.parts) + '\n')
        manifest.append({**source, 'status': meta['status'], 'cache_key': hashlib.sha256(source['url'].encode()).hexdigest(), 'response_sha256': meta['sha256'], 'retrieved_at_utc': meta['completed_at_utc'], 'final_url': meta['final_url'], 'text_file': str(text_path.relative_to(ROOT))})
        print(source['id'], meta['status'], text_path, flush=True)
    save_json(OUT / 'source_manifest.json', manifest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['docs', 'probe', 'collect', 'analyze', 'evidence', 'retry-failed', 'sources', 'audit', 'replay'])
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--only', nargs='*', help='Named request snapshots to retry with retry-failed')
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    if args.command == 'replay':
        if args.refresh:
            parser.error('replay is offline and cannot refresh')
        collect(offline=True)
        analyze()
        evidence(offline=True)
        sources(offline=True)
        audit()
        import render_charts
        render_charts.main()
    if args.command == 'docs':
        for url in (DOCS, 'https://blog.gdeltproject.org/doc-2-0-updates-1-5-year-searching-and-updated-mobile-interface/', 'https://blog.gdeltproject.org/a-behind-the-scenes-look-at-how-we-think-about-master-file-formats-and-timestamping/', 'https://data.gdeltproject.org/api/v2/guides/LOOKUP-LANGUAGES.TXT', 'https://voloridge-hack-mit-2026.s3.us-east-1.amazonaws.com/index.html'):
            meta, body = fetch(url, args.offline, args.refresh)
            print(json.dumps({'url': url, 'status': meta['status'], 'bytes': len(body), 'cache_key': hashlib.sha256(url.encode()).hexdigest()}), flush=True)
    if args.command == 'collect':
        collect(args.offline, args.refresh)
    if args.command == 'sources':
        sources(args.offline, args.refresh)
    if args.command == 'audit':
        audit()
    if args.command == 'retry-failed':
        if args.offline:
            parser.error('retry-failed requires network access')
        retry_failed(args.only)
    if args.command == 'analyze':
        analyze()
    if args.command == 'evidence':
        evidence(args.offline, args.refresh)
    if args.command == 'probe':
        result = api('sourcelang:english', 'timelinevolraw', '20260917000000', '20260918000000', args.offline, args.refresh)
        save_json(OUT / 'initial_probe.json', result)
        print(json.dumps(result, ensure_ascii=False)[:12000], flush=True)


if __name__ == '__main__':
    main()
