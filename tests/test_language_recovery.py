import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from research import historical_experiment as historical
from research import language_pages as pages
from research import language_recovery as recovery

ROOT = Path(__file__).resolve().parents[1]
CORPUS = 'unit-test-only-corpus'


def observation(url, batch, language, topics=(), record_id=None):
    return {'corpus': CORPUS, 'record_id': record_id or batch + '-1', 'available_batch': batch, 'collection': '1', 'document_id': url, 'domain': 'example.test', 'language': language, 'language_status': 'explicit' if language else 'missing', 'topics': list(topics)}


class HistoricalMeasurementTests(unittest.TestCase):
    def test_missing_language_and_engine_field_are_not_english(self):
        self.assertEqual(historical.original_language(''), (None, 'missing_srclc'))
        self.assertEqual(historical.original_language('eng:GT-SPA 1.0'), (None, 'missing_srclc'))
        self.assertEqual(historical.original_language('srclc:spa;eng:GT-SPA 1.0'), ('spa', 'explicit_srclc'))
        self.assertEqual(historical.original_language('srclc:eng'), ('eng', 'explicit_srclc'))
        self.assertEqual(historical.original_language('srclc:spa;srclc:rus'), (None, 'conflicting_srclc'))

    def test_dedup_uses_documents_not_record_or_mention_rows(self):
        b = '20150301000000'
        rows = [observation('https://example.test/a', b, 'eng', ['topic']), observation('https://example.test/a', b, 'eng', ['topic'], b+'-2'), observation('https://example.test/b', b, 'eng', [], b+'-1')]
        result = historical.deduplicate_as_of(rows, b, CORPUS)
        self.assertEqual(len(result), 2)
        self.assertEqual({r['document_id'] for r in result}, {'https://example.test/a', 'https://example.test/b'})

    def test_language_denominator_is_same_corpus_and_bin(self):
        b = '20150301000000'
        rows = [observation('https://example.test/a', b, 'eng', ['topic']), observation('https://example.test/b', b, 'eng'), observation('https://example.test/c', b, 'spa', ['topic']), observation('https://example.test/d', b, None, ['topic'])]
        en = historical.observed_share(rows, 'topic', 'eng', b, {b}, b, CORPUS)
        es = historical.observed_share(rows, 'topic', 'spa', b, {b}, b, CORPUS)
        self.assertEqual((en['numerator'], en['denominator'], en['share']), (1, 2, 0.5))
        self.assertEqual((es['numerator'], es['denominator'], es['share']), (1, 1, 1.0))
        with self.assertRaises(ValueError): historical.observed_share(rows, 'topic', 'eng', b, {b}, b, 'different-corpus')
        with self.assertRaises(ValueError): historical.observed_share(rows, 'topic', None, b, {b}, b, CORPUS)

    def test_missing_bins_and_unverified_topics_are_not_zero(self):
        b = '20150301000000'
        row = observation('https://example.test/a', b, 'eng')
        self.assertIsNone(historical.observed_share([row], 'topic', 'eng', b, set(), b, CORPUS)['share'])
        self.assertIsNone(historical.observed_share([row], 'topic', 'spa', b, {b}, b, CORPUS)['share'])
        del row['topics']
        self.assertEqual(historical.observed_share([row], 'topic', 'eng', b, {b}, b, CORPUS)['status'], 'topic_membership_unverified')

    def test_future_rows_and_relabels_do_not_change_past_share(self):
        past, future = '20150301000000', '20150301001500'
        row = observation('https://example.test/a', past, 'eng')
        later = observation('https://example.test/a', future, 'spa', ['topic'])
        base = historical.observed_share([row], 'topic', 'eng', past, {past}, past, CORPUS)
        result = historical.observed_share([row, later, observation('https://example.test/b', future, 'eng', ['topic'])], 'topic', 'eng', past, {past, future}, past, CORPUS)
        self.assertEqual(base, result)
        with self.assertRaises(ValueError): historical.observed_share([row, later], 'topic', 'eng', future, {past, future}, past, CORPUS)

    def test_baseline_has_only_preceding_contiguous_bins(self):
        series = {'20150301000000': 1, '20150301001500': 2, '20150301003000': 999, '20150301004500': 99999}
        self.assertEqual(historical.preceding_values(series, '20150301003000', 2), [1, 2])
        del series['20150301001500']
        self.assertIsNone(historical.preceding_values(series, '20150301003000', 2))


class LanguageRecoveryTests(unittest.TestCase):
    def test_multiline_extras_do_not_create_documents_or_shift_language(self):
        import io
        from collections import Counter
        first = [''] * 27
        first[0], first[1], first[25], first[26] = '20150301000000-T1', '20150301000000', 'srclc:spa;eng:engine', '<PAGE_LINKS>start'
        second = list(first)
        second[0] = '20150301000000-T2'
        second[25], second[26] = 'srclc:fra', '<PAGE_LINKS>end</PAGE_LINKS>'
        raw = ('\t'.join(first) + '\n\ncontinued</PAGE_LINKS>\n' + '\t'.join(second) + '\n').encode()
        shapes = Counter()
        records = list(recovery.gkg_records(io.BytesIO(raw), shapes))
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0][2], 2)
        self.assertEqual(records[0][1][25], 'srclc:spa;eng:engine')
        self.assertIn('continued</PAGE_LINKS>', records[0][1][26])
        self.assertEqual(records[1][1][25], 'srclc:fra')

    def test_translated_manifest_and_payload_alignment(self):
        q = json.loads((recovery.OUT / 'translated_quality.json').read_text())
        self.assertEqual(len(q['files']), 6)
        self.assertEqual(q['records'], sum(f['records'] for f in q['files']))
        for file in q['files']:
            self.assertTrue(file['manifest_checksum_matches'])
            self.assertEqual(file['translated_record_markers'], file['records'])
            self.assertEqual(file['record_batch_matches'], file['records'])
            self.assertEqual(file['document_date_matches'], file['records'])
            self.assertEqual(file['blank_translation_info'], 0)
        self.assertTrue(any(code not in ('eng', 'unknown') for code in q['original_language_counts']))

    def test_sample_is_frozen_topic_independent_and_bounded(self):
        plan = json.loads((recovery.OUT / 'url_sample_plan.json').read_text())
        self.assertEqual(plan['planned_urls'], 96)
        self.assertLessEqual(len(plan['records']), 200)
        self.assertEqual(len({r['url'] for r in plan['records']}), 96)
        from collections import Counter
        self.assertLessEqual(max(Counter(r['publisher_host'] for r in plan['records']).values()), 2)
        self.assertEqual({r['batch'] for r in plan['records']}, set(recovery.TARGETS))
        self.assertTrue(all(r['selected'] == r['requested'] for r in plan['strata']))

    def test_public_url_guard(self):
        for value in ('file:///etc/passwd', 'http://127.0.0.1/', 'http://169.254.169.254/', 'http://localhost/', 'https://user:password@example.com/', 'http://example.com:22/'):
            self.assertFalse(pages.valid_public_url(value))
        self.assertTrue(pages.valid_public_url('https://example.com/news'))
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
            with self.assertRaises(OSError): pages.public_connection(('public-looking.example', 80))

    def test_language_input_is_extracted_body_not_metadata(self):
        rows = json.loads((recovery.OUT / 'page_language_results.json').read_text())
        for row in rows:
            if row['model_language'] is None:
                self.assertIsNone(row['raw_model_score'])
                continue
            text = (ROOT / row['text_file']).read_text()[:pages.MAX_MODEL_CHARS]
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), row['model_input_sha256'])
            self.assertGreaterEqual(row['text_chars'], pages.MIN_TEXT_CHARS)
            self.assertIsInstance(row['raw_model_score'], float)
        info = json.loads((recovery.OUT / 'language_model.json').read_text())
        self.assertEqual(info['version'], '1.1.6')
        self.assertIn('NOT calibrated probabilities', info['scores'])

    def test_manual_review_preserves_disagreements_and_rejects_footer(self):
        audit = {r['sample_id']: r for r in json.loads((recovery.OUT / 'manual_audit.json').read_text())['records']}
        self.assertFalse(audit['page-061']['article_body_usable'])
        self.assertFalse(audit['page-033']['article_body_usable'])
        self.assertFalse(audit['page-086']['article_body_usable'])
        for key, language in [('page-046', 'ar'), ('page-079', 'fr')]:
            self.assertTrue(audit[key]['article_body_usable'])
            self.assertEqual(audit[key]['manual_language'], language)
            self.assertFalse(audit[key]['html_language_agrees'])
        summary = json.loads((recovery.OUT / 'page_quality_summary.json').read_text())
        self.assertEqual(summary['by_current_inferred_language']['it']['usable_after_limited_manual_overrides'], 0)
        self.assertIsNone(summary['by_current_inferred_language']['en']['retrieval_failure_fraction'])

    def test_budget_and_demo_preserved(self):
        state = recovery.state()
        used = state['prior_historical_bytes'] + state['received_bytes'] + pages.page_state()['received_bytes']
        self.assertLess(used, recovery.TOTAL_CAP)
        self.assertLess(state['received_bytes'] + pages.page_state()['received_bytes'], recovery.PHASE_CAP)
        historical.preserve_demo()


if __name__ == '__main__':
    unittest.main()
