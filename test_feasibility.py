import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import feasibility as f


class AnalyticalContractTests(unittest.TestCase):
    def fixture(self, target, days=10):
        dates = f.expected_dates()[:days]
        for lang in f.LANGUAGES:
            denominator = [{'date': d, 'value': 100, 'norm': 1000} for d in dates]
            counts = [{'date': d, 'value': 40 if i == 8 else 5, 'norm': 1000} for i, d in enumerate(dates)]
            def store(name, ps):
                f.save_json(target / 'requests' / (name + '.json'), {'status': 200, 'data': {'query_details': {'date_resolution': 'day'}, 'timeline': [{'series': 'synthetic unit-test fixture', 'data': ps}]}})
            store('denominator_' + lang, denominator)
            for topic in f.TOPICS:
                store(topic + '_' + lang, counts)
            store('percent_shipping_' + lang, [{'date': p['date'], 'value': p['value'] / 10} for p in counts])

    def run_analysis(self, folder):
        with patch.object(f, 'OUT', folder), contextlib.redirect_stdout(io.StringIO()):
            f.analyze()
        return json.loads((folder / 'timeline.json').read_text())

    def test_trailing_baseline_and_guardrails(self):
        result = f.trailing_alert([10] * 7, 25, 10)
        self.assertEqual(result['baseline_mean_per_10k'], 10)
        self.assertEqual(result['threshold_per_10k'], 20)
        self.assertTrue(result['candidate'])
        self.assertFalse(f.trailing_alert([10] * 7, 25, 9)['candidate'])
        self.assertIsNone(f.trailing_alert([10] * 6, 25, 10))
        self.assertIsNone(f.trailing_alert([10] * 6 + [None], 25, 10))
        self.assertIsNone(f.trailing_alert([10] * 7, None, 10))
        self.assertFalse(f.trailing_alert([0] * 7, 0, 0)['candidate'])

    def test_normalization_and_preceding_only_dates(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            self.fixture(folder)
            rows = self.run_analysis(folder)
            first = rows[0]
            self.assertEqual(first['per_10k_language_articles'], 500)
            alerts = json.loads((folder / 'candidates.json').read_text())
            self.assertEqual(len(alerts), 9)
            for alert in alerts:
                self.assertEqual(alert['date'], f.expected_dates()[8])
                self.assertEqual(alert['baseline_dates'], f.expected_dates()[1:8])
                self.assertTrue(all(d < alert['date'] for d in alert['baseline_dates']))
            self.assertEqual(sum(r['state'] == 'excluded_latest_bin' for r in rows), 9)
            self.assertEqual(sum(r['state'] == 'missing' for r in rows), 18 * 9)
            self.assertTrue(all(r['per_10k_language_articles'] is None for r in rows if r['state'] == 'missing'))

    def test_future_numerators_do_not_change_earlier_baselines(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            self.fixture(folder, days=14)
            before = self.run_analysis(folder)
            for topic in f.TOPICS:
                for lang in f.LANGUAGES:
                    path = folder / 'requests' / (topic + '_' + lang + '.json')
                    value = json.loads(path.read_text())
                    for point in value['data']['timeline'][0]['data'][10:]:
                        point['value'] = 90
                    f.save_json(path, value)
            for lang in f.LANGUAGES:
                path = folder / 'requests' / ('percent_shipping_' + lang + '.json')
                value = json.loads(path.read_text())
                for point in value['data']['timeline'][0]['data'][10:]:
                    point['value'] = 9
                f.save_json(path, value)
            after = self.run_analysis(folder)
            self.assertEqual([r for r in before if r['date'] < f.expected_dates()[10]], [r for r in after if r['date'] < f.expected_dates()[10]])

    def test_missing_denominator_withholds_comparisons(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            self.fixture(folder)
            f.save_json(folder / 'requests' / 'denominator_chinese.json', {'status': 429, 'data': None})
            rows = self.run_analysis(folder)
            self.assertTrue(all(r['per_10k_language_articles'] is None for r in rows))
            self.assertEqual(json.loads((folder / 'candidates.json').read_text()), [])
            self.assertTrue(any(r['article_count'] is not None for r in rows))

    def test_missing_bin_breaks_baseline(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            self.fixture(folder)
            for topic in f.TOPICS:
                for lang in f.LANGUAGES:
                    path = folder / 'requests' / (topic + '_' + lang + '.json')
                    value = json.loads(path.read_text())
                    del value['data']['timeline'][0]['data'][4]
                    f.save_json(path, value)
            self.run_analysis(folder)
            self.assertEqual(json.loads((folder / 'candidates.json').read_text()), [])
            self.assertEqual(json.loads((folder / 'raw_candidates.json').read_text()), [])

    def test_article_list_cannot_become_a_count_timeline(self):
        self.assertEqual(f.points({'status': 200, 'data': {'articles': [{'url': 'https://example.org'}] * 250}}), {})
        with self.assertRaises(ValueError):
            f.points({'status': 200, 'data': {'timeline': [{'data': [{'date': 'x', 'value': 1}, {'date': 'x', 'value': 2}]}]}})

    def test_zero_language_denominator_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            self.fixture(folder)
            path = folder / 'requests' / 'denominator_english.json'
            value = json.loads(path.read_text())
            value['data']['timeline'][0]['data'][0]['value'] = 0
            f.save_json(path, value)
            rows = self.run_analysis(folder)
            self.assertTrue(all(r['per_10k_language_articles'] is None for r in rows))


if __name__ == '__main__':
    unittest.main()
