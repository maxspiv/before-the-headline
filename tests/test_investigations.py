import json
import tempfile
import unittest
from pathlib import Path

from before_the_headline.app import create_app
from before_the_headline.demo_data import filter_records, load_dataset, replay_view
from before_the_headline.investigations import (InvestigationImportError, Store,
                            build_investigation, import_template,
                            validate_import)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'fixtures'


def load_fixture(name='synthetic_investigation.json'):
    return json.loads((FIXTURES / name).read_text())


def errors_of(name):
    try:
        validate_import(load_fixture('invalid/' + name))
    except InvestigationImportError as exc:
        return exc.errors
    raise AssertionError(name + ' unexpectedly validated')


class ValidationTests(unittest.TestCase):
    def test_invalid_fixtures_emit_expected_errors(self):
        expectations = {
            'bad_scheme.json': 'articles[0].url: invalid or unsafe URL scheme; only http(s) URLs are accepted',
            'dup_ids.json': "articles[1].id: duplicate article id 'a-1'",
            'missing_required.json': 'title: required',
            'bad_timestamp.json': 'articles[0].claimed_timestamp.value: date precision requires YYYY-MM-DD exactly',
            'replay_without_timestamp.json': 'articles[0].include_in_replay: true requires claimed_timestamp',
            'unknown_field.json': 'surprise: unknown field',
            'confirmed_singleton.json': 'articles[0].duplicate_status: "confirmed" requires at least 2 articles sharing duplicate_group',
            'bad_uncertainty_ref.json': "uncertainties[0].source_ids: unknown article id 'ghost-id'",
        }
        for fixture, expected in expectations.items():
            with self.subTest(fixture=fixture):
                errors = errors_of(fixture)
                self.assertTrue(any(expected in e for e in errors),
                                '%r not in %r' % (expected, errors))

    def test_all_errors_are_collected(self):
        errors = errors_of('bad_timestamp.json')
        self.assertIn('articles[1].claimed_timestamp.value: datetime precision '
                      'requires an explicit timezone offset', errors)

    def test_missing_required_collects_everything(self):
        errors = errors_of('missing_required.json')
        self.assertIn('title: required', errors)
        self.assertIn('topic: required', errors)

    def test_template_always_validates(self):
        normalized = validate_import(import_template())
        self.assertEqual(normalized['id'], 'my-investigation')

    def test_synthetic_counts_and_replay(self):
        inv = build_investigation(
            validate_import(load_fixture()), 'imported', None)
        self.assertEqual(inv['summary']['inventory_total'], 5)
        self.assertEqual(inv['summary']['inspected_total'], 4)
        self.assertIsNone(inv['aggregate_context'])
        view = filter_records(inv, include_unrelated=False)
        self.assertEqual(view['counts']['visible_count'], 2)
        view = filter_records(inv, fold_confirmed=True)
        self.assertEqual(view['counts']['visible_count'], 3)
        replay = replay_view(inv)
        self.assertEqual(replay['visible_count'], 2)
        self.assertEqual(replay['days'], ['2030-01-02', '2030-01-03'])
        first = replay['events'][0]
        self.assertEqual(first['claim_label'],
                         '2030-01-02 10:00:00 UTC · publisher claim')
        second = replay['events'][1]
        self.assertEqual(second['claim_label'],
                         '2030-01-03 · date only; time and timezone unknown')
        self.assertTrue(all(r['classification_source'] == 'supplied'
                            for r in inv['records']))
        self.assertTrue(all(r['classification_source'] == 'recorded'
                            for r in load_dataset()['records']))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = Store(self.dir, bundled=[load_dataset()])

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_persist_delete(self):
        inv = self.store.import_json((FIXTURES / 'synthetic_investigation.json').read_bytes())
        self.assertEqual(inv['id'], 'synthetic-fixture')
        self.assertTrue((self.dir / 'synthetic-fixture.json').exists())
        fresh = Store(self.dir, bundled=[load_dataset()])
        self.assertEqual(fresh.get('synthetic-fixture')['summary']['inventory_total'], 5)
        self.assertEqual([s['id'] for s in fresh.list()],
                         ['shipping-msc-2026', 'synthetic-fixture'])
        fresh.delete('synthetic-fixture')
        self.assertFalse((self.dir / 'synthetic-fixture.json').exists())
        with self.assertRaises(KeyError):
            fresh.get('synthetic-fixture')

    def test_bundled_delete_and_missing_delete(self):
        with self.assertRaises(PermissionError):
            self.store.delete('shipping-msc-2026')
        with self.assertRaises(KeyError):
            self.store.delete('no-such-id')

    def test_id_collision_rejected(self):
        self.store.import_json((FIXTURES / 'synthetic_investigation.json').read_text())
        with self.assertRaises(InvestigationImportError) as ctx:
            self.store.import_json((FIXTURES / 'synthetic_investigation.json').read_text())
        self.assertIn('id: already exists; remove it first or choose another id',
                      ctx.exception.errors)

    def test_invalid_stored_file_is_skipped(self):
        (self.dir / 'broken.json').write_text('{"schema_version": 99}')
        store = Store(self.dir, bundled=[load_dataset()])
        self.assertEqual([s['id'] for s in store.list()], ['shipping-msc-2026'])


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(data_dir=Path(cls.tmp.name))
        cls.app.testing = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_import_endpoints_and_isolation(self):
        before = self.client.get('/api/investigations/shipping-msc-2026/evidence').json['counts']
        res = self.client.post('/api/investigations/import',
                               data=(FIXTURES / 'synthetic_investigation.json').read_bytes(),
                               content_type='application/json')
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json['id'], 'synthetic-fixture')
        listing = self.client.get('/api/investigations').json
        self.assertEqual([i['id'] for i in listing['investigations']],
                         ['shipping-msc-2026', 'synthetic-fixture'])
        self.assertEqual(listing['persistence']['mode'], 'local-json-files')
        imported = self.client.get('/api/investigations/synthetic-fixture/evidence').json
        self.assertEqual(imported['counts']['visible_count'], 4)
        self.assertEqual(imported['investigation']['id'], 'synthetic-fixture')
        replay = self.client.get('/api/investigations/synthetic-fixture/replay').json
        self.assertEqual(replay['days'], ['2030-01-02', '2030-01-03'])
        after = self.client.get('/api/investigations/shipping-msc-2026/evidence').json['counts']
        self.assertEqual(before, after)
        res = self.client.delete('/api/investigations/synthetic-fixture')
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self.client.delete('/api/investigations/synthetic-fixture').status_code, 404)
        self.assertEqual(self.client.get('/api/investigations/synthetic-fixture/evidence').status_code, 404)

    def test_import_rejects_invalid_and_wrong_content_type(self):
        res = self.client.post('/api/investigations/import',
                               data=(FIXTURES / 'invalid/dup_ids.json').read_bytes(),
                               content_type='application/json')
        self.assertEqual(res.status_code, 400)
        self.assertTrue(res.json['errors'])
        res = self.client.post('/api/investigations/import',
                               data='{}', content_type='text/plain')
        self.assertEqual(res.status_code, 400)

    def test_delete_bundled_is_forbidden(self):
        res = self.client.delete('/api/investigations/shipping-msc-2026')
        self.assertEqual(res.status_code, 403)
        self.assertTrue(res.json['error'])

    def test_oversize_body_rejected(self):
        res = self.client.post('/api/investigations/import',
                               data=b' ' * (1_048_576 + 1),
                               content_type='application/json')
        self.assertEqual(res.status_code, 413)

    def test_schema_page_documents_every_field(self):
        from before_the_headline.investigations import ARTICLE_KEYS, TOP_LEVEL_KEYS
        html = self.client.get('/import/schema').get_data(as_text=True)
        for key in TOP_LEVEL_KEYS | ARTICLE_KEYS:
            self.assertIn('<code>%s</code>' % key, html, key)

    def test_template_endpoint_serves_attachment(self):
        res = self.client.get('/api/import/template')
        self.assertEqual(res.status_code, 200)
        self.assertIn('before-the-headline-investigation-template.json',
                      res.headers['Content-Disposition'])
        validate_import(json.loads(res.get_data()))

    def test_legacy_routes_removed(self):
        for path in ('/api/evidence', '/api/replay', '/api/source/x'):
            self.assertEqual(self.client.get(path).status_code, 404)


if __name__ == '__main__':
    unittest.main()
