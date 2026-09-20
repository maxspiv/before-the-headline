import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{2,63}$')
LANGUAGE_RE = re.compile(r'^[a-z]+$')
MAX_BODY_BYTES = 1_048_576
LIMITS = {
    'articles_min': 1, 'articles_max': 500, 'uncertainties_max': 50,
    'title': 200, 'topic': 80, 'description': 1000, 'story_label': 60,
    'publisher': 120, 'language': 40, 'url': 2048, 'excerpt': 2000,
    'long_text': 1000, 'context_items': 10, 'context_item': 300,
}
DEFAULT_DATA_DIR = ROOT / 'local_investigations'

TOP_LEVEL_KEYS = {'schema_version', 'id', 'title', 'topic', 'description',
                  'story_label', 'coverage_status', 'kicker',
                  'aggregate_context', 'articles', 'uncertainties',
                  'timeline_notes'}
AGGREGATE_KEYS = {'count', 'date', 'language', 'source', 'meaning'}
ARTICLE_KEYS = {'id', 'title', 'publisher', 'language', 'url', 'origin_label',
                'relevance', 'inspected', 'duplicate_group', 'duplicate_status',
                'claimed_timestamp', 'publisher_visible_timestamp', 'excerpt',
                'scope_notes', 'syndication_assessment',
                'reported_event_timestamps_and_uncertainty', 'include_in_replay'}
TIMESTAMP_KEYS = {'value', 'precision'}
UNCERTAINTY_KEYS = {'id', 'title', 'detail', 'source_ids'}
TIMELINE_NOTE_KEYS = {'context', 'date_axis_meaning', 'empty_interval_meaning'}

RELEVANCES = ('related', 'unrelated', 'uncertain')
DUPLICATE_STATUSES = ('confirmed', 'possible', 'unassessed')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

DEFAULT_DATE_AXIS_MEANING = (
    'Publisher-claimed calendar dates supplied with the import, '
    'not observation or first publication.')
DEFAULT_EMPTY_INTERVAL_MEANING = (
    'unavailable/unsearched observations, not zero coverage')
IMPORT_EXCERPT_MEANING = (
    'Excerpt text supplied by the import file; not extracted or verified '
    'by this app.')
IMPORTED_SCOPE_NOTICE = (
    'This investigation was imported from a user-supplied JSON file. '
    'Relevance labels and excerpts are exactly as supplied; nothing was '
    'inspected or verified by this app.')


class InvestigationImportError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('; '.join(self.errors))


def _is_str(value):
    return isinstance(value, str)


def _check_len(errors, path, value, limit):
    if _is_str(value) and len(value) > limit:
        errors.append('%s: exceeds %d characters' % (path, limit))
        return False
    return True


def _required_str(errors, name, obj, key, limit, required=True):
    value = obj.get(key)
    if value is None:
        if required:
            errors.append('%s: required' % name)
        return None
    if not _is_str(value) or not value.strip():
        errors.append('%s: must be a non-empty string' % name)
        return None
    _check_len(errors, name, value, limit)
    return value


def _unknown_keys(errors, prefix, obj, allowed):
    for key in obj:
        if key not in allowed:
            errors.append('%s%s: unknown field'
                          % ((prefix + '.') if prefix else '', key))


def _validate_slug(errors, path, value, label='id'):
    if not _is_str(value) or not ID_RE.match(value):
        errors.append(
            '%s: %s must match %s (lowercase slug, 3-64 chars)'
            % (path, label, ID_RE.pattern))
        return False
    return True


def _validate_claimed_timestamp(errors, path, raw):
    if not isinstance(raw, dict):
        errors.append('%s: must be an object' % path)
        return None
    _unknown_keys(errors, path, raw, TIMESTAMP_KEYS)
    value = raw.get('value')
    precision = raw.get('precision')
    if value is None:
        errors.append('%s.value: required' % path)
    elif not _is_str(value):
        errors.append('%s.value: must be a string' % path)
        value = None
    if precision not in ('date', 'datetime'):
        errors.append('%s.precision: must be "date" or "datetime"' % path)
        return None
    if value is None:
        return None
    if precision == 'date':
        if not DATE_RE.match(value):
            errors.append('%s.value: date precision requires YYYY-MM-DD '
                          'exactly' % path)
            return None
        try:
            datetime.strptime(value, '%Y-%m-%d')
        except ValueError:
            errors.append('%s.value: not a real calendar date' % path)
            return None
        return {'value': value, 'precision': 'date'}
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        errors.append('%s.value: not a valid ISO 8601 datetime' % path)
        return None
    if parsed.tzinfo is None:
        errors.append('%s.value: datetime precision requires an explicit '
                      'timezone offset' % path)
        return None
    return {'value': value, 'precision': 'datetime', 'utc': parsed.astimezone(timezone.utc)}


def _validate_aggregate_context(errors, raw):
    if not isinstance(raw, dict):
        errors.append('aggregate_context: must be an object')
        return None
    _unknown_keys(errors, 'aggregate_context', raw, AGGREGATE_KEYS)
    ok = True
    count = raw.get('count')
    if count is None:
        errors.append('aggregate_context.count: required')
        ok = False
    elif not isinstance(count, int) or isinstance(count, bool) or count < 0:
        errors.append('aggregate_context.count: must be an integer >= 0')
        ok = False
    date = raw.get('date')
    if date is None:
        errors.append('aggregate_context.date: required')
        ok = False
    elif not _is_str(date) or not DATE_RE.match(date):
        errors.append('aggregate_context.date: must match YYYY-MM-DD')
        ok = False
    for key in ('language', 'source', 'meaning'):
        _required_str(errors, 'aggregate_context.' + key, raw, key,
                      LIMITS['long_text'])
        if raw.get(key) is None:
            ok = False
    if not ok:
        return None
    return {'count': count, 'date': date, 'language': raw['language'],
            'source': raw['source'], 'meaning': raw['meaning']}


def _validate_articles(errors, raw_articles):
    if not isinstance(raw_articles, list):
        errors.append('articles: must be a list')
        return None
    if not (LIMITS['articles_min'] <= len(raw_articles) <= LIMITS['articles_max']):
        errors.append('articles: must contain %d..%d entries'
                      % (LIMITS['articles_min'], LIMITS['articles_max']))
        return None
    articles = []
    seen_ids = set()
    groups = {}
    for i, raw in enumerate(raw_articles):
        path = 'articles[%d]' % i
        if not isinstance(raw, dict):
            errors.append('%s: must be an object' % path)
            continue
        _unknown_keys(errors, path, raw, ARTICLE_KEYS)
        article = {}
        aid = raw.get('id')
        if aid is None:
            errors.append('%s.id: required' % path)
        elif _validate_slug(errors, path + '.id', aid):
            if aid in seen_ids:
                errors.append('%s.id: duplicate article id %r' % (path, aid))
            else:
                seen_ids.add(aid)
            article['id'] = aid
        for key, limit in (('title', LIMITS['title']),
                           ('publisher', LIMITS['publisher']),
                           ('language', LIMITS['language'])):
            value = _required_str(errors, path + '.' + key, raw, key, limit)
            if value is not None:
                article[key] = value
        if 'language' in article and not LANGUAGE_RE.match(article['language']):
            errors.append('%s.language: must be a lowercase word like "english"'
                          % path)
        url = raw.get('url')
        if url is not None:
            if not _is_str(url):
                errors.append('%s.url: must be a string' % path)
            else:
                _check_len(errors, path + '.url', url, LIMITS['url'])
                from demo_data import safe_external_url
                if safe_external_url(url) is None:
                    errors.append('%s.url: invalid or unsafe URL scheme; '
                                  'only http(s) URLs are accepted' % path)
                else:
                    article['url'] = url
        for key, default, limit in (
                ('origin_label', 'Supplied by import', LIMITS['publisher']),
                ('publisher_visible_timestamp', 'Not supplied',
                 LIMITS['long_text']),
                ('scope_notes', None, LIMITS['long_text']),
                ('syndication_assessment', 'Not assessed.', LIMITS['long_text']),
                ('reported_event_timestamps_and_uncertainty', None,
                 LIMITS['long_text'])):
            value = raw.get(key)
            if value is not None:
                if not _is_str(value):
                    errors.append('%s.%s: must be a string' % (path, key))
                    continue
                _check_len(errors, path + '.' + key, value, limit)
            article[key] = value if value is not None else default
        relevance = raw.get('relevance')
        if relevance is None:
            errors.append('%s.relevance: required' % path)
        elif relevance not in RELEVANCES:
            errors.append('%s.relevance: must be one of %s'
                          % (path, ', '.join(RELEVANCES)))
        else:
            article['relevance'] = relevance
        inspected = raw.get('inspected', True)
        if not isinstance(inspected, bool):
            errors.append('%s.inspected: must be true or false' % path)
            inspected = True
        article['inspected'] = inspected
        group = raw.get('duplicate_group')
        if group is not None and not _is_str(group):
            errors.append('%s.duplicate_group: must be a string' % path)
            group = None
        status = raw.get('duplicate_status', 'unassessed')
        if status not in DUPLICATE_STATUSES:
            errors.append('%s.duplicate_status: must be one of %s'
                          % (path, ', '.join(DUPLICATE_STATUSES)))
            status = 'unassessed'
        article['duplicate_status'] = status
        article['duplicate_group'] = group or ('UNASSESSED-' + str(aid))
        groups.setdefault(article['duplicate_group'], []).append((path, status))
        claim = raw.get('claimed_timestamp')
        if claim is not None:
            article['claimed_timestamp'] = _validate_claimed_timestamp(
                errors, path + '.claimed_timestamp', claim)
        else:
            article['claimed_timestamp'] = None
        excerpt = raw.get('excerpt')
        if excerpt is not None:
            if not _is_str(excerpt):
                errors.append('%s.excerpt: must be a string' % path)
            else:
                _check_len(errors, path + '.excerpt', excerpt,
                           LIMITS['excerpt'])
                article['excerpt'] = excerpt
        replay = raw.get('include_in_replay', False)
        if not isinstance(replay, bool):
            errors.append('%s.include_in_replay: must be true or false' % path)
            replay = False
        if replay and article['claimed_timestamp'] is None and claim is None:
            errors.append('%s.include_in_replay: true requires '
                          'claimed_timestamp' % path)
        elif replay and claim is not None and article['claimed_timestamp'] is None:
            replay = False
        article['include_in_replay'] = replay
        articles.append(article)
    for group, members in groups.items():
        if any(status == 'confirmed' for _, status in members):
            if len(members) < 2:
                for member_path, status in members:
                    if status == 'confirmed':
                        errors.append('%s.duplicate_status: "confirmed" requires '
                                      'at least 2 articles sharing duplicate_group %r'
                                      % (member_path, group))
            elif not all(status == 'confirmed' for _, status in members):
                errors.append('articles: duplicate_group %r mixes "confirmed" '
                              'with other statuses; all members must be confirmed'
                              % group)
    return articles


def _validate_uncertainties(errors, raw_list, article_ids):
    if raw_list is None:
        return []
    if not isinstance(raw_list, list):
        errors.append('uncertainties: must be a list')
        return []
    if len(raw_list) > LIMITS['uncertainties_max']:
        errors.append('uncertainties: at most %d entries'
                      % LIMITS['uncertainties_max'])
        return []
    uncertainties = []
    seen = set()
    for i, raw in enumerate(raw_list):
        path = 'uncertainties[%d]' % i
        if not isinstance(raw, dict):
            errors.append('%s: must be an object' % path)
            continue
        _unknown_keys(errors, path, raw, UNCERTAINTY_KEYS)
        uid = raw.get('id')
        if uid is None:
            errors.append('%s.id: required' % path)
        elif _validate_slug(errors, path + '.id', uid):
            if uid in seen:
                errors.append('%s.id: duplicate uncertainty id %r' % (path, uid))
            seen.add(uid)
        title = _required_str(errors, path + '.title', raw, 'title',
                              LIMITS['title'])
        detail = _required_str(errors, path + '.detail', raw, 'detail',
                               LIMITS['long_text'])
        source_ids = raw.get('source_ids')
        valid_ids = []
        if not isinstance(source_ids, list) or not source_ids:
            errors.append('%s.source_ids: must be a non-empty list of article '
                          'ids' % path)
        else:
            for sid in source_ids:
                if sid not in article_ids:
                    errors.append('%s.source_ids: unknown article id %r'
                                  % (path, sid))
                else:
                    valid_ids.append(sid)
        uncertainties.append({'id': uid, 'title': title, 'detail': detail,
                              'source_ids': valid_ids})
    return uncertainties


def _validate_timeline_notes(errors, raw):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        errors.append('timeline_notes: must be an object')
        return {}
    _unknown_keys(errors, 'timeline_notes', raw, TIMELINE_NOTE_KEYS)
    notes = {}
    context = raw.get('context')
    if context is not None:
        if not isinstance(context, list):
            errors.append('timeline_notes.context: must be a list of strings')
        elif len(context) > LIMITS['context_items']:
            errors.append('timeline_notes.context: at most %d items'
                          % LIMITS['context_items'])
        else:
            items = []
            for i, item in enumerate(context):
                if not _is_str(item):
                    errors.append('timeline_notes.context[%d]: must be a '
                                  'string' % i)
                else:
                    _check_len(errors, 'timeline_notes.context[%d]' % i,
                               item, LIMITS['context_item'])
                    items.append(item)
            notes['context'] = items
    for key in ('date_axis_meaning', 'empty_interval_meaning'):
        value = raw.get(key)
        if value is not None:
            if not _is_str(value):
                errors.append('timeline_notes.%s: must be a string' % key)
            else:
                _check_len(errors, 'timeline_notes.' + key, value,
                           LIMITS['long_text'])
                notes[key] = value
    return notes


def validate_import(obj):
    errors = []
    if not isinstance(obj, dict):
        raise InvestigationImportError(['body: must be a JSON object'])
    _unknown_keys(errors, '', obj, TOP_LEVEL_KEYS)
    if obj.get('schema_version') is None:
        errors.append('schema_version: required')
    elif obj['schema_version'] != SCHEMA_VERSION:
        errors.append('schema_version: must equal %d' % SCHEMA_VERSION)
    if obj.get('id') is None:
        errors.append('id: required')
    else:
        _validate_slug(errors, 'id', obj['id'])
    title = _required_str(errors, 'title', obj, 'title', LIMITS['title'])
    topic = _required_str(errors, 'topic', obj, 'topic', LIMITS['topic'])
    description = _required_str(errors, 'description', obj, 'description',
                                LIMITS['description'], required=False)
    story_label = _required_str(errors, 'story_label', obj, 'story_label',
                                LIMITS['story_label'], required=False)
    coverage = _required_str(errors, 'coverage_status', obj, 'coverage_status',
                             LIMITS['title'], required=False)
    kicker = _required_str(errors, 'kicker', obj, 'kicker',
                           LIMITS['story_label'], required=False)
    aggregate = None
    if 'aggregate_context' in obj and obj['aggregate_context'] is not None:
        aggregate = _validate_aggregate_context(errors,
                                                obj['aggregate_context'])
    articles = _validate_articles(errors, obj.get('articles')) or []
    article_ids = {a.get('id') for a in articles}
    uncertainties = _validate_uncertainties(
        errors, obj.get('uncertainties'), article_ids)
    notes = _validate_timeline_notes(errors, obj.get('timeline_notes'))
    if errors:
        raise InvestigationImportError(errors)
    return {
        'schema_version': SCHEMA_VERSION,
        'id': obj['id'], 'title': title, 'topic': topic,
        'description': description,
        'story_label': story_label or 'the story',
        'coverage_status': coverage or 'Incomplete observed coverage',
        'kicker': kicker or 'Imported investigation',
        'aggregate_context': aggregate,
        'articles': articles,
        'uncertainties': uncertainties,
        'timeline_notes': notes,
    }


def _truncate_excerpt(text, limit=900):
    text = text[:limit + 1]
    truncated = len(text) > limit
    text = text[:limit]
    if truncated and ' ' in text[-80:]:
        text = text.rsplit(' ', 1)[0]
    return text, truncated


def build_investigation(normalized, origin, imported_at,
                        artifact_hashes=None, scope_notice=None):
    story_label = normalized['story_label']
    group_sizes = {}
    for article in normalized['articles']:
        group_sizes[article['duplicate_group']] = \
            group_sizes.get(article['duplicate_group'], 0) + 1
    records = []
    for article in normalized['articles']:
        relevance = article['relevance']
        category_label = {
            'related': 'Matches ' + story_label,
            'unrelated': 'Other story',
            'uncertain': 'Unresolved / uninspected',
        }[relevance]
        confirmed = article['duplicate_status'] == 'confirmed'
        possible = article['duplicate_status'] == 'possible'
        claim = article['claimed_timestamp']
        if claim is None:
            claim_label_value = article['publisher_visible_timestamp']
            date_only = False
            display_date = None
            utc_claim = None
        elif claim['precision'] == 'date':
            claim_label_value = claim['value'] + \
                ' · date only; time and timezone unknown'
            date_only = True
            display_date = claim['value']
            utc_claim = None
        else:
            utc = claim['utc']
            claim_label_value = \
                utc.strftime('%Y-%m-%d %H:%M:%S') + ' UTC · publisher claim'
            date_only = False
            display_date = utc.strftime('%Y-%m-%d')
            utc_claim = utc.isoformat()
        excerpt = None
        if article.get('excerpt'):
            text, truncated = _truncate_excerpt(article['excerpt'])
            excerpt = {'text': text, 'truncated': truncated,
                       'source_file': None, 'range_label': None,
                       'meaning': IMPORT_EXCERPT_MEANING}
        from demo_data import safe_external_url
        url = article.get('url')
        records.append({
            'id': article['id'],
            'title': article['title'],
            'publisher': article['publisher'],
            'language': article['language'],
            'url': url,
            'external_url': safe_external_url(url) if url else None,
            'origin_label': article['origin_label'],
            'relevance': relevance,
            'category': relevance,
            'category_label': category_label,
            'inspected': article['inspected'],
            'duplicate_group': article['duplicate_group'],
            'duplicate_status': article['duplicate_status'],
            'confirmed_duplicate': confirmed,
            'possible_shared': possible,
            'confirmed_group_size':
                group_sizes[article['duplicate_group']] if confirmed else None,
            'claim_label': claim_label_value,
            'date_only': date_only,
            'publisher_visible_timestamp':
                article['publisher_visible_timestamp'],
            'excerpt': excerpt,
            'scope_notes': article['scope_notes'],
            'syndication_assessment': article['syndication_assessment'],
            'reported_event_timestamps_and_uncertainty':
                article['reported_event_timestamps_and_uncertainty'],
            'include_in_story_replay': article['include_in_replay'],
            'uncertainty_ids': [],
            'classification_source': 'supplied',
            'publisher_embedded_timestamps': [],
            'gdelt_seendate': None, 'gdelt_seendate_meaning': None,
            'retrieved_at_utc': None, 'retrieval_meaning': None,
            'gkg_document_date': None, 'gkg_record_id': None,
            '_display_date': display_date,
            '_utc_claim': utc_claim,
        })
    uncertainties = []
    for u in normalized['uncertainties']:
        sources = [{'id': sid,
                    'publisher': next(a['publisher'] for a in normalized['articles'] if a['id'] == sid),
                    'language': next(a['language'] for a in normalized['articles'] if a['id'] == sid)}
                   for sid in u['source_ids']]
        uncertainties.append({'id': u['id'], 'title': u['title'],
                              'detail': u['detail'], 'sources': sources})
        for record in records:
            if record['id'] in u['source_ids']:
                record['uncertainty_ids'].append(u['id'])
    replay_articles = [r for r in records if r['include_in_story_replay']]
    notes = normalized['timeline_notes']
    timeline = None
    if replay_articles:
        events = [{'id': r['id'], 'display_date': r['_display_date'],
                   'publisher_publication_utc_claim': r['_utc_claim'],
                   'publisher': r['publisher'], 'title': r['title']}
                  for r in replay_articles]
        events.sort(key=lambda e: (e['display_date'], e['id']))
        timeline = {
            'events': events,
            'context': notes.get('context', []),
            'date_axis_meaning': notes.get('date_axis_meaning',
                                         DEFAULT_DATE_AXIS_MEANING),
            'empty_interval_meaning': notes.get('empty_interval_meaning',
                                                DEFAULT_EMPTY_INTERVAL_MEANING),
        }
    for record in records:
        del record['_display_date']
        del record['_utc_claim']
    reviewed = [r for r in records if r['inspected']]
    summary = {
        'inventory_total': len(records),
        'inspected_total': len(reviewed),
        'uninspected_total': len(records) - len(reviewed),
        'related_inspected': sum(r['category'] == 'related' for r in reviewed),
        'unrelated_inspected': sum(r['category'] == 'unrelated' for r in reviewed),
        'confirmed_duplicate_pages': sum(r['confirmed_duplicate'] for r in reviewed),
        'confirmed_duplicate_groups': len({r['duplicate_group'] for r in reviewed if r['confirmed_duplicate']}),
        'possible_shared_inspected': sum(r['possible_shared'] for r in reviewed),
        'inspected_gdelt_pages': sum(r['origin_label'] == 'GDELT result' for r in reviewed),
        'inspected_external_pages': sum(r['origin_label'] != 'GDELT result' for r in reviewed),
    }
    return {
        'id': normalized['id'],
        'title': normalized['title'],
        'topic': normalized['topic'],
        'description': normalized.get('description'),
        'kicker': normalized.get('kicker'),
        'story_label': story_label,
        'coverage_status': normalized['coverage_status'],
        'origin': origin,
        'imported_at': imported_at,
        'classification_note': (
            'Relevance and copied-text labels are recorded, agent-assisted '
            'inspections from this project. Counts and folding are computed '
            'by the app.' if origin == 'bundled' else
            'Relevance and copied-text labels were supplied with the import '
            'file and are not verified by this app. Counts and folding are '
            'computed by the app.'),
        'persistence_note': (
            'Bundled with the app; cannot be removed.'
            if origin == 'bundled' else
            'Stored as a JSON file in this app\'s local data directory.'),
        'records': records,
        'by_id': {r['id']: r for r in records},
        'timeline': timeline,
        'uncertainties': uncertainties,
        'summary': summary,
        'artifact_hashes': artifact_hashes or {},
        'aggregate_context': normalized['aggregate_context'],
        'scope_notice': scope_notice or IMPORTED_SCOPE_NOTICE,
    }


def _summary(investigation):
    summary = investigation['summary']
    timeline = investigation.get('timeline')
    return {
        'id': investigation['id'],
        'title': investigation['title'],
        'topic': investigation['topic'],
        'coverage_status': investigation['coverage_status'],
        'origin': investigation['origin'],
        'imported_at': investigation['imported_at'],
        'article_count': summary['inventory_total'],
        'inspected_count': summary['inspected_total'],
        'related_count': summary['related_inspected'],
        'has_aggregate_context': investigation['aggregate_context'] is not None,
        'has_timeline': timeline is not None,
        'replay_count': len(timeline['events']) if timeline else 0,
    }


class Store:
    def __init__(self, data_dir, bundled):
        self.data_dir = Path(data_dir)
        self._bundled = {inv['id']: inv for inv in bundled}
        self._imported = {}
        if self.data_dir.is_dir():
            for path in sorted(self.data_dir.glob('*.json')):
                try:
                    obj = json.loads(path.read_text())
                    normalized = validate_import(obj)
                except (ValueError, OSError) as exc:
                    log.warning('Skipping invalid stored investigation %s: %s',
                                path, exc)
                    continue
                inv = build_investigation(
                    normalized, 'imported',
                    datetime.fromtimestamp(
                        path.stat().st_mtime, timezone.utc).isoformat(),
                    artifact_hashes={
                        'import_file': hashlib.sha256(
                            path.read_bytes()).hexdigest()})
                self._imported[inv['id']] = inv

    def list(self):
        imported = sorted(self._imported.values(),
                          key=lambda i: i['imported_at'] or '')
        return [_summary(i) for i in self._bundled.values()] + \
            [_summary(i) for i in imported]

    def get(self, investigation_id):
        if investigation_id in self._bundled:
            return self._bundled[investigation_id]
        if investigation_id in self._imported:
            return self._imported[investigation_id]
        raise KeyError(investigation_id)

    def import_json(self, text_or_bytes):
        if isinstance(text_or_bytes, bytes):
            text_or_bytes = text_or_bytes.decode('utf-8', 'replace')
        try:
            obj = json.loads(text_or_bytes)
        except ValueError as exc:
            raise InvestigationImportError(
                ['body: invalid JSON (%s)' % exc])
        normalized = validate_import(obj)
        if normalized['id'] in self._bundled or \
                normalized['id'] in self._imported:
            raise InvestigationImportError(
                ['id: already exists; remove it first or choose another id'])
        self.data_dir.mkdir(parents=True, exist_ok=True)
        body = (json.dumps(obj, indent=2, ensure_ascii=False,
                           sort_keys=True) + '\n').encode('utf-8')
        path = self.data_dir / (normalized['id'] + '.json')
        path.write_bytes(body)
        imported_at = datetime.now(timezone.utc).isoformat()
        inv = build_investigation(
            normalized, 'imported', imported_at,
            artifact_hashes={'import_file': hashlib.sha256(body).hexdigest()})
        self._imported[inv['id']] = inv
        return inv

    def delete(self, investigation_id):
        if investigation_id in self._bundled:
            raise PermissionError(
                'bundled investigations cannot be deleted')
        if investigation_id not in self._imported:
            raise KeyError(investigation_id)
        del self._imported[investigation_id]
        path = self.data_dir / (investigation_id + '.json')
        if path.exists():
            path.unlink()


def import_template():
    return {
        'schema_version': 1,
        'id': 'my-investigation',
        'title': 'My investigation title',
        'topic': 'Topic · Region',
        'description': 'What this investigation examines.',
        'story_label': 'the story',
        'coverage_status': 'Incomplete observed coverage',
        'articles': [
            {
                'id': 'example-a',
                'title': 'Example article title',
                'publisher': 'Example Publisher',
                'language': 'english',
                'url': 'https://example.org/article-a',
                'relevance': 'related',
                'claimed_timestamp': {'value': '2030-01-02T10:00:00+00:00',
                                      'precision': 'datetime'},
                'include_in_replay': True,
            },
            {
                'id': 'example-b',
                'title': 'Another example article',
                'publisher': 'Example Publisher',
                'language': 'english',
                'url': 'https://example.org/article-b',
                'relevance': 'unrelated',
                'claimed_timestamp': {'value': '2030-01-03',
                                      'precision': 'date'},
                'include_in_replay': True,
            },
        ],
        'uncertainties': [
            {'id': 'example-uncertainty',
             'title': 'Example open question',
             'detail': 'What remains unresolved and why.',
             'source_ids': ['example-a', 'example-b']},
        ],
    }
