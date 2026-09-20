import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get('SIGNAL_DEMO_URL', 'http://127.0.0.1:8765')
SHIPPING = '/investigations/shipping-msc-2026'
PAYLOAD = '<img data-payload="yes" src="https://bad.invalid/pixel" onerror="window.__signalNoiseXSS=1">'


class DemoBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        channel = os.environ.get('SIGNAL_BROWSER_CHANNEL')
        cls.browser = cls.playwright.chromium.launch(headless=True, **({'channel': channel} if channel else {}))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        (ROOT / 'results').mkdir(exist_ok=True)
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        self.external_requests, self.errors = [], []
        def local_only(route):
            url = urlsplit(route.request.url)
            if url.hostname not in ('127.0.0.1', 'localhost'):
                self.external_requests.append(route.request.url)
                route.abort()
            else:
                route.continue_()
        self.context.route('**/*', local_only)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.external_requests, [], 'Demo attempted an external request without a source-link click')
        self.assertEqual(self.errors, [], 'Browser JavaScript raised errors')

    def open_demo(self):
        self.page.goto(BASE + SHIPPING, wait_until='networkidle')
        expect(self.page.locator('#evidence-cards .evidence-card')).to_have_count(13)
        expect(self.page.get_by_text('Incomplete coverage', exact=True).first).to_be_visible()

    def assert_cards(self, count):
        expect(self.page.locator('#evidence-cards .evidence-card')).to_have_count(count)
        expect(self.page.locator('#result-count')).to_contain_text(str(count))

    def test_complete_filter_flow_and_actual_counts(self):
        self.open_demo()
        self.page.locator('#fold-confirmed').check()
        self.assert_cards(12)
        self.page.locator('#show-unrelated').uncheck()
        self.assert_cards(3)
        self.page.locator('#show-possible').uncheck()
        self.assert_cards(0)
        expect(self.page.get_by_role('button', name='Reset', exact=True).first).to_be_visible()
        self.page.get_by_role('button', name='Reset', exact=True).first.click()
        self.assert_cards(13)
        self.page.locator('#include-uninspected').check()
        self.assert_cards(32)
        self.page.locator('#show-possible').uncheck()
        self.assert_cards(27)
        self.page.locator('#fold-confirmed').check()
        self.assert_cards(26)
        self.page.locator('#show-unrelated').uncheck()
        self.assert_cards(17)
        self.page.get_by_role('button', name='Reset', exact=True).first.click()
        self.assert_cards(13)

    def test_replay_precision_and_source_provenance(self):
        self.open_demo()
        self.page.locator('#show-possible').uncheck()
        self.assert_cards(10)
        expect(self.page.locator('[data-replay-source]')).to_have_count(3)
        self.page.locator('[data-day="2026-09-01"]').click()
        expect(self.page.locator('[data-replay-source]')).to_have_count(2)
        source = self.page.locator('[data-replay-source="shipping_msc_zh"]')
        expect(source).to_contain_text(re.compile('date.only', re.I))
        self.assertNotIn('00:00', source.inner_text())
        source.click()
        dialog = self.page.locator('#source-dialog')
        expect(dialog).to_be_visible()
        expect(self.page.locator('#source-title')).to_contain_text('MSC')
        expect(self.page.locator('#source-excerpt')).to_contain_text('驶往新罗西斯克港')
        expect(dialog).to_contain_text(re.compile('time.*timezone.*unknown', re.I))
        expect(dialog.get_by_text('Source details', exact=True)).to_be_visible()
        link = self.page.locator('#source-external-link')
        self.assertEqual(link.get_attribute('href'), 'https://www.jctrans.com/cn/news/14074/')
        self.assertEqual(link.get_attribute('target'), '_blank')
        self.assertIn('noopener', link.get_attribute('rel'))
        self.assertIn('noreferrer', link.get_attribute('rel'))
        for _ in range(12):
            self.page.keyboard.press('Tab')
            self.assertTrue(self.page.evaluate("document.querySelector('#source-dialog').contains(document.activeElement)"))
        self.page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        expect(source).to_be_focused()
        self.page.locator('[data-day="all"]').click()
        expect(self.page.locator('[data-replay-source]')).to_have_count(3)
        self.assert_cards(10)

    def test_confirmed_copy_can_be_inspected_when_folded(self):
        self.open_demo()
        self.page.locator('#fold-confirmed').check()
        self.assert_cards(12)
        opener = self.page.locator('#evidence-cards [data-source-id="gdelt_iran_laverdad_es"]').first
        opener.click()
        dialog = self.page.locator('#source-dialog')
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('SYND-LARAK-MP-1')
        dialog.locator('[data-source-id="gdelt_iran_diariovasco_es"]').first.click()
        expect(dialog).to_contain_text('Diario Vasco')
        self.page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        expect(opener).to_be_focused()

    def test_source_text_cannot_execute_markup_or_javascript_urls(self):
        self.context.add_init_script('window.__signalNoiseXSS=0')
        def mutate_cards(route):
            response = route.fetch()
            data = response.json()
            data['cards'][0]['title'] = PAYLOAD
            route.fulfill(response=response, json=data)
        def mutate_source(route):
            response = route.fetch()
            data = response.json()
            data['title'] = PAYLOAD
            data['excerpt']['text'] = '<script>window.__signalNoiseXSS=2</script>'
            data['external_url'] = 'javascript:window.__signalNoiseXSS=3'
            data['url'] = data['external_url']
            route.fulfill(response=response, json=data)
        self.context.route('**/api/investigations/*/evidence*', mutate_cards)
        self.context.route('**/api/investigations/*/source/gdelt_panama_es', mutate_source)
        self.open_demo()
        card = self.page.locator('#evidence-cards [data-source-id="gdelt_panama_es"]').first
        expect(self.page.locator('#evidence-cards')).to_contain_text(PAYLOAD)
        card.click()
        expect(self.page.locator('#source-title')).to_have_text(PAYLOAD)
        expect(self.page.locator('#source-excerpt')).to_contain_text('<script>')
        self.assertEqual(self.page.locator('img[data-payload]').count(), 0)
        self.assertEqual(self.page.evaluate('window.__signalNoiseXSS'), 0)
        link = self.page.locator('#source-external-link')
        self.assertIn(link.get_attribute('href'), (None, ''))

    def test_mobile_layout_and_desktop_screenshots(self):
        self.open_demo()
        self.page.screenshot(path=str(ROOT / 'results/signal_noise_desktop.png'), full_page=True)
        self.page.locator('[data-replay-source="shipping_msc_en"]').click()
        expect(self.page.locator('#source-dialog')).to_be_visible()
        expect(self.page.locator('#source-excerpt')).to_contain_text('Novorossiysk')
        self.page.screenshot(path=str(ROOT / 'results/signal_noise_source.png'))
        self.page.keyboard.press('Escape')
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.goto(BASE + SHIPPING, wait_until='networkidle')
        self.assert_cards(13)
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
        self.page.screenshot(path=str(ROOT / 'results/signal_noise_mobile.png'), full_page=True)
        self.page.locator('[data-replay-source="shipping_msc_zh"]').click()
        expect(self.page.locator('#source-dialog')).to_be_visible()
        box = self.page.locator('#source-dialog').bounding_box()
        self.assertLessEqual(box['width'], 390)
        self.assertGreaterEqual(box['x'], 0)
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))

    def test_rapid_filter_changes_use_the_final_state(self):
        self.open_demo()
        self.page.evaluate("""() => {
            for (const [id, checked] of [['show-unrelated',false],['show-possible',false],['show-possible',true],['fold-confirmed',true]]) {
                const input=document.getElementById(id); input.checked=checked; input.dispatchEvent(new Event('change',{bubbles:true}));
            }
        }""")
        self.assert_cards(3)
        self.assertTrue(self.page.locator('#show-possible').is_checked())


class ImportBrowserTests(unittest.TestCase):
    """Import UI tests run against a dedicated server whose SIGNAL_DATA_DIR is a
    temp directory, so imported investigations never land in local_investigations/."""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cls.base = 'http://127.0.0.1:%d' % port
        env = dict(os.environ, SIGNAL_DATA_DIR=cls.tmp.name)
        cls.server = subprocess.Popen(
            [sys.executable, 'app.py', '--port', str(port)], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(cls.base + '/healthz', timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError('import test server did not start')
        cls.playwright = sync_playwright().start()
        channel = os.environ.get('SIGNAL_BROWSER_CHANNEL')
        cls.browser = cls.playwright.chromium.launch(headless=True, **({'channel': channel} if channel else {}))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.tmp.cleanup()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
        self.errors = []
        self.dialogs = []
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.on('dialog', lambda dialog: (self.dialogs.append(dialog.message), dialog.dismiss()))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], 'Browser JavaScript raised errors')
        self.assertEqual(self.dialogs, [], 'A browser dialog (alert) fired')

    def open_home(self):
        self.page.goto(self.base, wait_until='networkidle')
        expect(self.page.locator('.investigation-card')).to_have_count(1)

    def import_via_ui(self, payload):
        self.page.locator('#import-text').fill(payload)
        self.page.get_by_role('button', name='Import', exact=True).click()

    def test_import_synthetic_via_home_ui(self):
        self.open_home()
        fixture = (ROOT / 'fixtures/synthetic_investigation.json').read_text()
        self.import_via_ui(fixture)
        expect(self.page.locator('#import-result a')).to_be_visible()
        expect(self.page.locator('.investigation-card')).to_have_count(2)
        self.page.locator('#investigation-search').fill('synthetic')
        expect(self.page.locator('.investigation-card:not(.hidden)')).to_have_count(1)
        self.page.locator('#investigation-search').fill('nomatch')
        expect(self.page.locator('#search-empty')).to_be_visible()
        self.page.locator('#investigation-search').fill('')
        self.page.locator('#import-result a').click()
        expect(self.page.locator('#evidence-cards .evidence-card')).to_have_count(4)
        self.page.locator('#include-uninspected').check()
        expect(self.page.locator('#evidence-cards .evidence-card')).to_have_count(5)
        self.page.locator('#focus-story').click()
        expect(self.page.locator('#evidence-cards .evidence-card')).to_have_count(2)
        day = self.page.locator('[data-day="2030-01-03"]')
        expect(self.page.locator('[data-day="2030-01-02"]')).to_be_visible()
        expect(day).to_be_visible()
        day.click()
        syn_b = self.page.locator('[data-replay-source="syn-b"]')
        expect(syn_b).to_contain_text('date only; time and timezone unknown')
        self.page.get_by_role('button', name='Reset', exact=True).first.click()
        self.page.locator('#include-uninspected').check()
        self.page.locator('#evidence-cards [data-source-id="syn-e"]').first.click()
        dialog = self.page.locator('#source-dialog')
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('No link available')
        expect(dialog).to_contain_text('No excerpt available.')
        link = self.page.locator('#source-external-link')
        self.assertIn(link.get_attribute('href'), (None, ''))
        self.page.keyboard.press('Escape')
        self.page.goto(self.base, wait_until='networkidle')
        remove = self.page.locator('[data-remove="synthetic-fixture"]')
        expect(remove).to_be_visible()
        self.page.evaluate("window.confirm = () => true")
        remove.click()
        expect(self.page.locator('.investigation-card')).to_have_count(1)

    def test_import_xss_and_javascript_url(self):
        self.open_home()
        bad = {
            'schema_version': 1, 'id': 'xss-test', 'topic': 'test',
            'title': PAYLOAD,
            'articles': [{'id': 'xss-a', 'title': 'x', 'publisher': PAYLOAD,
                          'language': 'english', 'relevance': 'related',
                          'url': 'javascript:alert(1)'}]}
        self.import_via_ui(json.dumps(bad))
        expect(self.page.locator('#import-errors li').first).to_be_visible()
        expect(self.page.locator('#import-errors')).to_contain_text('invalid or unsafe URL scheme')
        bad['articles'][0]['url'] = 'https://example.org/a'
        bad['articles'][0]['excerpt'] = '<script>window.__signalNoiseXSS=2</script>'
        self.context.add_init_script('window.__signalNoiseXSS=0')
        self.import_via_ui(json.dumps(bad))
        expect(self.page.locator('#import-result a')).to_be_visible()
        self.page.locator('#import-result a').click()
        expect(self.page.locator('h1')).to_have_text(PAYLOAD)
        card = self.page.locator('#evidence-cards .evidence-card').first
        card.click()
        expect(self.page.locator('#source-excerpt')).to_contain_text('<script>')
        expect(self.page.locator('#source-meta')).to_contain_text(PAYLOAD)
        self.assertEqual(self.page.locator('img[data-payload]').count(), 0)
        self.assertEqual(self.page.evaluate('window.__signalNoiseXSS'), 0)
        self.assertEqual(self.page.locator('a[href^="javascript:"]').count(), 0)
        self.page.evaluate("window.confirm = () => true")
        res = self.page.evaluate(
            "fetch('/api/investigations/xss-test', {method: 'DELETE'}).then(r => r.status)")
        self.assertEqual(res, 204)


if __name__ == '__main__':
    unittest.main()
