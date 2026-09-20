import contextlib
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from research import bounded_recovery as recovery
from research import validate_msc as validation


class EvidenceTests(unittest.TestCase):
    def test_offset_conversion_does_not_invent_timezones(self):
        self.assertEqual(validation.utc('2026-08-31T09:03:33+05:30'), '2026-08-31T03:33:33+00:00')
        self.assertEqual(validation.utc('2026-09-01T06:02:45-04:00'), '2026-09-01T10:02:45+00:00')
        self.assertIsNone(validation.utc('2026-09-01'))
        self.assertIsNone(validation.utc('2026-09-01T06:02:45'))
        self.assertIsNone(validation.utc(None))

    def test_primary_jsonld_does_not_take_unrelated_article_date(self):
        data = {'@graph': [
            {'@id': 'https://example.test/story/#article', 'datePublished': '2026-09-01T10:00:00Z'},
            {'@id': 'https://example.test/other/#article', 'datePublished': '1999-01-01T00:00:00Z'},
            {'@id': 'https://example.test/story-copy/#article', 'datePublished': '1998-01-01T00:00:00Z'}]}
        rows = validation.json_dates(data, 'https://example.test/story/')
        self.assertEqual([r['value'] for r in rows], ['2026-09-01T10:00:00Z'])

    def test_cached_evidence_contract_and_uncertainty(self):
        with tempfile.TemporaryDirectory() as name, patch.object(validation, 'OUT', Path(name)), contextlib.redirect_stdout(io.StringIO()):
            (Path(name) / 'bulk_candidate_hints.json').write_text((validation.ROOT / 'results/msc_validation/bulk_candidate_hints.json').read_text())
            validation.build()
            rows = json.loads((Path(name) / 'evidence_table.json').read_text())
            timeline = json.loads((Path(name) / 'timeline_data.json').read_text())
            self.assertEqual(len(rows), len({r['url'] for r in rows}))
            included = {r['id'] for r in rows if r['include_in_story_replay']}
            self.assertEqual(included, set(validation.CORE))
            self.assertTrue(all(not r['include_in_story_replay'] for r in rows if r['relevance'].startswith('uncertain')))
            self.assertTrue(all(not r['independent_reporting_verified'] for r in rows))
            copies = [r for r in rows if r['id'] in ('gdelt_iran_laverdad_es', 'gdelt_iran_diariovasco_es')]
            self.assertEqual(len({r['duplicate_group'] for r in copies}), 1)
            self.assertTrue(all(not r['include_in_story_replay'] for r in copies))
            self.assertEqual({e['id'] for e in timeline['events']}, included)
            self.assertIsNone(timeline['lead_time'])
            self.assertIsNone(timeline['normalized_comparison'])
            self.assertTrue(all(e['gdelt_seendate'] is None for e in timeline['events']))
            chinese = next(e for e in timeline['events'] if e['language'] == 'chinese')
            self.assertIsNone(chinese['publisher_publication_utc_claim'])
            self.assertIn('not zero', timeline['empty_interval_meaning'])
            self.assertEqual(timeline['decision'], 'story_replay')

    def test_entity_hint_word_boundaries(self):
        self.assertEqual(validation.hint_terms('impulsan impulsando'), [])
        self.assertEqual(validation.hint_terms('MSC ULSAN III near Novorossiysk; Ulsan'), ['novorossiysk', 'ulsan'])

    def test_csv_formula_prefix_guard(self):
        self.assertEqual(validation.clean_cell('=1+1'), "'=1+1")
        self.assertEqual(validation.clean_cell('MSC'), 'MSC')


class RecoveryTests(unittest.TestCase):
    def test_retry_after_seconds_and_http_date(self):
        reference = datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(recovery.retry_delay('120', reference=reference), 120)
        self.assertEqual(recovery.retry_delay('Sat, 19 Sep 2026 18:03:00 GMT', reference=reference), 180)
        self.assertEqual(recovery.retry_delay('bad', reference=reference), 30)
        self.assertEqual(recovery.retry_delay(None, attempt=1, reference=reference), 60)
        self.assertEqual(recovery.retry_delay('-1', reference=reference), 30)

    def test_doc_api_prohibited(self):
        with self.assertRaises(ValueError):
            recovery.request('https://api.gdeltproject.org/api/v2/doc/doc?query=test')

    def state(self, folder, seconds=600):
        state = {'started_at_utc': recovery.now().isoformat(), 'deadline_utc': (recovery.now() + timedelta(seconds=seconds)).isoformat(), 'requests': 0, 'received_body_bytes': 0, 'last_request_finished_epoch': 0, 'closed': False, 'stop_reason': None}
        (folder / 'state.json').write_text(json.dumps(state))

    def test_persistent_throttling_stops_after_two_attempts_and_caches_both(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            self.state(folder)
            opener = Mock()
            opener.open.side_effect = [urllib.error.HTTPError('https://example.test/data', 429, 'throttled', {'Retry-After': '120'}, io.BytesIO(b'rate limited')) for _ in range(2)]
            with patch.object(recovery, 'STATE', folder / 'state.json'), patch.object(recovery, 'CACHE', folder / 'cache'), patch('urllib.request.build_opener', return_value=opener), patch.object(recovery.time, 'sleep') as sleep, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'Persistent throttling'):
                    recovery.request('https://example.test/data')
            state = json.loads((folder / 'state.json').read_text())
            self.assertEqual(opener.open.call_count, 2)
            self.assertEqual(state['requests'], 2)
            self.assertTrue(state['closed'])
            self.assertEqual(len(list((folder / 'cache').glob('*/*.json'))), 2)
            sleep.assert_any_call(120)

    def test_retry_after_cannot_exceed_deadline(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            self.state(folder, seconds=15)
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError('https://example.test/data', 429, 'throttled', {'Retry-After': '600'}, io.BytesIO(b'rate limited'))
            with patch.object(recovery, 'STATE', folder / 'state.json'), patch.object(recovery, 'CACHE', folder / 'cache'), patch('urllib.request.build_opener', return_value=opener), patch.object(recovery.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'exceed recovery deadline'):
                    recovery.request('https://example.test/data')
            self.assertEqual(opener.open.call_count, 1)
            self.assertTrue(json.loads((folder / 'state.json').read_text())['closed'])

    def test_closed_pass_never_starts_new_request(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name)
            self.state(folder)
            state = json.loads((folder / 'state.json').read_text())
            state['closed'] = True
            (folder / 'state.json').write_text(json.dumps(state))
            with patch.object(recovery, 'STATE', folder / 'state.json'), patch.object(recovery, 'CACHE', folder / 'cache'), patch('urllib.request.build_opener') as opener:
                with self.assertRaisesRegex(RuntimeError, 'closed'):
                    recovery.request('https://example.test/data')
                opener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
