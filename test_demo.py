import copy
import itertools
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from demo_data import EXCERPTS, claim_label, filter_records, load_dataset, replay_view, safe_external_url, source_detail

ROOT = Path(__file__).resolve().parent


class DemoDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset()

    def test_counts_and_membership_match_frozen_artifacts(self):
        rows = json.loads((ROOT / 'results/msc_validation/evidence_table.json').read_text())
        view = filter_records(self.data)
        self.assertEqual(self.data['summary']['inventory_total'], len(rows))
        self.assertEqual(self.data['summary']['inventory_total'], 32)
        self.assertEqual({c['id'] for c in view['cards']}, set(EXCERPTS))
        self.assertEqual(view['counts']['visible_count'], 13)
        self.assertEqual(view['summary']['related_inspected'], 3)
        self.assertEqual(view['summary']['unrelated_inspected'], 10)
        self.assertEqual(view['summary']['uninspected_total'], 19)
        self.assertEqual(view['summary']['inspected_gdelt_pages'], 6)
        self.assertEqual(view['summary']['inspected_external_pages'], 7)
        self.assertEqual(view['aggregate_context']['count'], 694)
        self.assertIn('do not explain', view['aggregate_context']['meaning'])
        self.assertNotEqual(view['counts']['cohort_count'], view['aggregate_context']['count'])

    def test_all_filter_combinations_have_exact_counts(self):
        base_counts = {(False, True, True): 13, (False, False, True): 3, (False, True, False): 10, (False, False, False): 0,
                       (True, True, True): 32, (True, False, True): 22, (True, True, False): 27, (True, False, False): 17}
        before = json.dumps(self.data, sort_keys=True, ensure_ascii=False)
        for inventory, unrelated, possible, fold in itertools.product((False, True), repeat=4):
            with self.subTest(inventory=inventory, unrelated=unrelated, possible=possible, fold=fold):
                view = filter_records(self.data, unrelated, possible, fold, inventory)
                counts = view['counts']
                folded = 1 if fold and unrelated else 0
                self.assertEqual(counts['visible_count'], base_counts[(inventory, unrelated, possible)] - folded)
                self.assertEqual(counts['folded_copy_count'], folded)
                self.assertEqual(counts['cohort_count'], 32 if inventory else 13)
                self.assertEqual(counts['visible_count'] + counts['filtered_out_count'] + folded, counts['cohort_count'])
                self.assertEqual(counts['related_visible'] + counts['unrelated_visible'] + counts['uncertain_visible'], counts['visible_count'])
                self.assertEqual(counts['visible_inspected'] + counts['visible_uninspected'], counts['visible_count'])
                if not unrelated:
                    self.assertTrue(all(c['category'] != 'unrelated' for c in view['cards']))
                if not possible:
                    self.assertTrue(all(not c['possible_shared'] for c in view['cards']))
        self.assertEqual(json.dumps(self.data, sort_keys=True, ensure_ascii=False), before)

    def test_confirmed_and_possible_sharing_are_not_conflated(self):
        confirmed = {r['id'] for r in self.data['records'] if r['confirmed_duplicate']}
        self.assertEqual(confirmed, {'gdelt_iran_laverdad_es', 'gdelt_iran_diariovasco_es'})
        self.assertEqual(self.data['summary']['confirmed_duplicate_groups'], 1)
        core = {'shipping_msc_en', 'shipping_msc_es', 'shipping_msc_zh'}
        self.assertTrue(all(self.data['by_id'][sid]['possible_shared'] for sid in core))
        self.assertFalse(any(self.data['by_id'][sid]['confirmed_duplicate'] for sid in core))
        view = filter_records(self.data, fold_confirmed=True)
        self.assertEqual(view['folded_ids'], ['gdelt_iran_diariovasco_es'])
        detail = source_detail(self.data, 'gdelt_iran_laverdad_es')
        self.assertEqual([r['id'] for r in detail['confirmed_group_members']], ['gdelt_iran_diariovasco_es'])

    def test_source_excerpts_support_retained_story_and_conflict(self):
        for row in self.data['records']:
            with self.subTest(source=row['id']):
                if row['inspected']:
                    self.assertIsNotNone(row['excerpt'])
                    self.assertLessEqual(len(row['excerpt']['text']), 900)
                    self.assertTrue(row['excerpt']['text_file_sha256'])
                else:
                    self.assertIsNone(row['excerpt'])
        english = source_detail(self.data, 'shipping_msc_en')['excerpt']['text']
        spanish = source_detail(self.data, 'shipping_msc_es')['excerpt']['text']
        chinese = source_detail(self.data, 'shipping_msc_zh')['excerpt']['text']
        self.assertIn('from Novorossiysk to Tekirdağ', english)
        self.assertIn('MSC Ulsan III', spanish)
        self.assertIn('Kommersant', spanish)
        self.assertIn('暂停接受', chinese)
        self.assertIn('驶往新罗西斯克港', chinese)
        self.assertIn('准确时间', chinese)
        self.assertIn('[…]', chinese)

    def test_replay_preserves_precision_and_is_independent_of_card_filters(self):
        artifact = json.loads((ROOT / 'results/msc_validation/timeline_data.json').read_text())
        replay = replay_view(self.data)
        self.assertEqual({e['id'] for e in replay['events']}, {e['id'] for e in artifact['events']})
        self.assertEqual(replay['visible_count'], 3)
        self.assertEqual(replay_view(self.data, '2026-09-01')['visible_count'], 2)
        self.assertEqual(replay_view(self.data, '2026-08-31')['visible_count'], 1)
        chinese = next(e for e in replay['events'] if e['id'] == 'shipping_msc_zh')
        self.assertIsNone(chinese['publisher_publication_utc_claim'])
        self.assertTrue(chinese['date_only'])
        self.assertIn('time and timezone unknown', chinese['claim_label'])
        self.assertNotIn('00:00', chinese['claim_label'])
        self.assertEqual(filter_records(self.data, False, False)['counts']['visible_count'], 0)
        self.assertEqual(replay_view(self.data)['visible_count'], 3)
        self.assertIsNone(replay['lead_time'])
        self.assertIsNone(replay['normalized_comparison'])
        with self.assertRaises(ValueError):
            replay_view(self.data, '2026-09-13')
        with self.assertRaises(ValueError):
            claim_label({'publisher_publication_utc_claim': '2026-09-01T10:02:45', 'display_date': '2026-09-01'})

    def test_safe_url_validation(self):
        for value in ('javascript:alert(1)', 'data:text/html,<script>', '//evil.test', 'https://user:pass@example.test/', None, 'http://[invalid'):
            self.assertIsNone(safe_external_url(value))
        self.assertEqual(safe_external_url('https://example.test/article'), 'https://example.test/article')


class DemoWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app
        with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('No network allowed')):
            cls.app = create_app()
            cls.app.testing = True
            cls.client = cls.app.test_client()

    def test_html_assets_and_api_are_local(self):
        for path in ('/', '/static/app.css', '/static/app.js', '/api/investigations/shipping-msc-2026/evidence', '/api/investigations/shipping-msc-2026/replay', '/healthz'):
            with self.subTest(path=path):
                response = self.client.get(path)
                try:
                    self.assertEqual(response.status_code, 200)
                finally:
                    response.close()
        root = self.client.get('/').get_data(as_text=True)
        self.assertIn('Signal', root)
        self.assertIn('Incomplete observed coverage', root)
        self.assertNotIn('https://fonts.', root)
        self.assertNotIn('unpkg.com', root)
        self.assertNotIn('cdn.jsdelivr', root)
        self.assertEqual(self.client.get('/api/investigations/shipping-msc-2026/evidence').json['counts']['visible_count'], 13)
        self.assertEqual(self.client.get('/api/investigations/shipping-msc-2026/evidence?unrelated=0').json['counts']['visible_count'], 3)
        self.assertEqual(self.client.get('/api/investigations/shipping-msc-2026/evidence?fold=1').json['counts']['visible_count'], 12)
        self.assertEqual(self.client.get('/api/investigations/shipping-msc-2026/evidence?inventory=1').json['counts']['visible_count'], 32)

    def test_invalid_filters_unknown_sources_and_file_routes_fail_closed(self):
        for path in ('/api/investigations/shipping-msc-2026/evidence?possible=false', '/api/investigations/shipping-msc-2026/evidence?fold=9', '/api/investigations/shipping-msc-2026/evidence?unrelated=1&unrelated=0', '/api/investigations/shipping-msc-2026/replay?day=2026-09-13', '/api/investigations/shipping-msc-2026/replay?day=all&day=all'):
            self.assertEqual(self.client.get(path).status_code, 400)
        for path in ('/api/investigations/shipping-msc-2026/source/missing', '/cache/', '/results/msc_validation/evidence_table.json', '/static/../demo_data.py', '/api/source/../../demo_data.py'):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get('/', headers={'Host': 'untrusted.example'}).status_code, 400)

    def test_security_headers_and_source_panel_data(self):
        response = self.client.get('/api/investigations/shipping-msc-2026/source/shipping_msc_zh')
        self.assertEqual(response.status_code, 200)
        csp = response.headers['Content-Security-Policy']
        self.assertIn("script-src 'self'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertNotIn('unsafe-inline', csp)
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
        data = response.json
        self.assertTrue(data['date_only'])
        self.assertIsNone(data['gdelt_seendate'])
        self.assertEqual(data['replay_event']['publisher_publication_utc_claim'], None)
        self.assertTrue(data['uncertainties'])
        self.assertIn('驶往', data['excerpt']['text'])

    def test_untrusted_source_strings_stay_json_not_template_code(self):
        import tempfile
        from app import create_app
        from investigations import Store
        dataset = copy.deepcopy(load_dataset())
        source = dataset['by_id']['shipping_msc_en']
        source['title'] = '<img src=x onerror="alert(1)">{{7*7}}'
        source['excerpt']['text'] = '<script>alert(1)</script>'
        with tempfile.TemporaryDirectory() as data_dir:
            store = Store(Path(data_dir), bundled=[dataset])
            client = create_app(store=store).test_client()
            result = client.get('/api/investigations/shipping-msc-2026/source/shipping_msc_en')
        self.assertEqual(result.mimetype, 'application/json')
        self.assertEqual(result.json['title'], source['title'])
        self.assertEqual(result.json['excerpt']['text'], source['excerpt']['text'])
        self.assertNotIn(source['title'], client.get('/').get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
