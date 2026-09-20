import contextlib
import hashlib
import json
import multiprocessing
import os
import queue
import re
import socket
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/screenshots'


def serve_fresh(port, events):
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    allowed = {'127.0.0.1', 'localhost', '::1', b'127.0.0.1', b'localhost', b'::1', None}

    def check(host):
        if host not in allowed:
            events.put({'kind': 'blocked_server_egress', 'destination': repr(host)})
            raise OSError('External network disabled for judging verification')

    def connect(sock, address):
        if sock.family != socket.AF_UNIX:
            check(address[0])
        return real_connect(sock, address)

    def connect_ex(sock, address):
        if sock.family != socket.AF_UNIX:
            check(address[0])
        return real_connect_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        check(host)
        return real_getaddrinfo(host, *args, **kwargs)

    (ROOT / 'results').mkdir(exist_ok=True)
    with (ROOT / 'results/fresh_start_server.log').open('w') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        with patch.object(socket.socket, 'connect', connect), patch.object(socket.socket, 'connect_ex', connect_ex), patch.object(socket, 'getaddrinfo', getaddrinfo):
            from app import main
            sys.argv = ['app.py', '--port', str(port)]
            events.put({'kind': 'fresh_process', 'pid': os.getpid(), 'entrypoint': 'app.main --port ' + str(port)})
            main()


class JudgingOfflineWalkthrough(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.base = 'http://127.0.0.1:' + str(cls.port)
        ctx = multiprocessing.get_context('spawn')
        cls.events = ctx.Queue()
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ['SIGNAL_DATA_DIR'] = cls.tmp.name
        cls.server = ctx.Process(target=serve_fresh, args=(cls.port, cls.events), daemon=True)
        cls.server.start()
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(cls.base + '/healthz', timeout=1) as response:
                    assert json.load(response)['mode'] == 'cached-offline'
                break
            except (urllib.error.URLError, TimeoutError, OSError):
                if not cls.server.is_alive() or time.monotonic() >= deadline:
                    cls.server.terminate()
                    cls.server.join(timeout=5)
                    raise RuntimeError('Fresh offline server failed; see results/fresh_start_server.log')
                time.sleep(0.1)
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel=os.environ.get('SIGNAL_BROWSER_CHANNEL', 'chrome'), headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.terminate()
        cls.server.join(timeout=5)
        cls.tmp.cleanup()
        os.environ.pop('SIGNAL_DATA_DIR', None)

    def test_exact_90_second_demo_path_and_reset(self):
        external, errors, captures = [], [], []
        context = self.browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce', service_workers='block')
        def route_local_only(route):
            uri = urlsplit(route.request.url)
            if uri.hostname == '127.0.0.1' and uri.port == self.port:
                route.continue_()
            else:
                external.append(route.request.url)
                route.abort()
        context.route('**/*', route_local_only)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))

        def capture(filename, caption):
            path = OUT / filename
            page.screenshot(path=str(path))
            captures.append({'file': filename, 'caption': caption, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})

        def cards(n):
            expect(page.locator('#evidence-cards .evidence-card')).to_have_count(n)
            expect(page.locator('#result-count')).to_contain_text('Showing ' + str(n) + ' of 13 articles')

        try:
            page.goto(self.base, wait_until='networkidle')
            expect(page.locator('.investigation-card')).to_have_count(1)
            expect(page.locator('.investigation-card')).to_contain_text('Reported MSC Ulsan III / Novorossiysk booking suspension')
            expect(page.locator('.investigation-card')).to_contain_text('Bundled')
            expect(page.get_by_label(re.compile('Filter investigations', re.I))).to_be_visible()
            expect(page.locator('#import-text')).to_be_visible()
            capture('00-home.png', 'Investigations home: bundled shipping case, filter box and JSON import panel.')
            page.locator('.investigation-card').get_by_role('link', name='Open').click()
            cards(13)
            expect(page.locator('h1')).to_have_text('Reported MSC Ulsan III / Novorossiysk booking suspension')
            expect(page.locator('.hero-copy')).to_contain_text('Coverage of a reported MSC booking suspension')
            expect(page.locator('#aggregate-block')).to_contain_text('694')
            expect(page.locator('#aggregate-block')).to_contain_text('do not explain')
            expect(page.locator('#aggregate-block')).to_contain_text('English')
            expect(page.locator('#aggregate-block')).to_contain_text('2026-08-31')
            expect(page.get_by_text('Incomplete coverage', exact=True).first).to_be_visible()
            capture('01-workspace.png', 'Workspace hero: query context shows 694 aggregate matches kept separate from the reviewed pages.')

            page.get_by_role('link', name='Articles', exact=True).click()
            cards(13)
            expect(page.locator('#summary-counters')).to_contain_text('13')
            capture('02-articles.png', '13 reviewed pages: 3 on story, 10 other; 6 GDELT-result pages and 7 found elsewhere.')

            page.locator('#fold-confirmed').check()
            cards(12)
            expect(page.locator('#result-count')).to_contain_text('1 folded')
            capture('03-fold-copied-text.png', 'One matching-text group contains two pages; folding merges one card.')
            page.locator('#show-unrelated').uncheck()
            cards(3)
            page.locator('#show-possible').uncheck()
            cards(0)
            expect(page.locator('#evidence-cards')).to_contain_text('No articles match these filters.')
            page.locator('#show-possible').check()
            cards(3)
            capture('04-on-story.png', 'On-story filter applied: three pages, each marked with possible shared-reporting uncertainty.')

            page.get_by_role('link', name='Timeline', exact=True).click()
            expect(page.locator('[data-replay-source]')).to_have_count(3)
            expect(page.locator('#replay-scope')).to_contain_text('regardless of filters')
            capture('05-timeline.png', 'Publisher-claimed dates; the Chinese page is date-only.')
            page.locator('[data-day="2026-09-01"]').click()
            expect(page.locator('[data-replay-source]')).to_have_count(2)
            chinese = page.locator('[data-replay-source="shipping_msc_zh"]')
            expect(chinese).to_contain_text('date only; time and timezone unknown')
            self.assertNotIn('00:00', chinese.inner_text())
            chinese.click()
            expect(page.locator('#source-dialog')).to_be_visible()
            expect(page.locator('#source-claim')).to_contain_text('time and timezone unknown')
            expect(page.locator('#source-excerpt')).to_contain_text('暂停接受')
            expect(page.locator('#source-excerpt')).to_contain_text('驶往新罗西斯克港')
            expect(page.locator('#source-external-link')).to_have_attribute('href', 'https://www.jctrans.com/cn/news/14074/')
            capture('06-source-details.png', 'Cached source excerpt, date-only precision, provenance and direction notes; no external link opened.')
            page.keyboard.press('Escape')
            expect(page.locator('#source-dialog')).not_to_be_visible()
            page.get_by_role('link', name='Notes', exact=True).click()
            expect(page.locator('#uncertainty-cards')).to_contain_text('Conflicting directions')
            expect(page.locator('#uncertainty-cards')).to_contain_text('Shared upstream reporting remains possible')
            capture('07-notes.png', 'Notes: route conflict, attack-time uncertainty, possible common upstream reporting and incomplete coverage.')

            page.get_by_role('button', name='Reset', exact=True).first.click()
            cards(13)
            page.locator('[data-day="all"]').click()
            expect(page.locator('[data-replay-source]')).to_have_count(3)
            page.evaluate('window.scrollTo(0,0)')
            self.assertTrue(page.locator('#show-unrelated').is_checked())
            self.assertTrue(page.locator('#show-possible').is_checked())
            self.assertFalse(page.locator('#fold-confirmed').is_checked())
            self.assertFalse(page.locator('#include-uninspected').is_checked())

            page.locator('#evidence-cards [data-source-id="semiconductors_exports_en"]').click()
            expect(page.locator('#source-title')).to_contain_text('Semiconductor')
            page.locator('#timestamp-details summary').click()
            expect(page.locator('#source-timestamps')).to_contain_text('2026-09-11T09:31:22+0900')
            expect(page.locator('#source-timestamps')).to_contain_text('Not supplied in cached artifact')
            self.assertNotIn('no explicit offset', page.locator('#source-timestamps').inner_text())
            page.keyboard.press('Escape')
            expect(page.locator('#source-dialog')).not_to_be_visible()
            page.goto(self.base, wait_until='networkidle')
            page.locator('#import-text').fill((ROOT / 'fixtures/synthetic_investigation.json').read_text())
            page.get_by_role('button', name='Import', exact=True).click()
            expect(page.locator('.investigation-card')).to_have_count(2)
            expect(page.locator('.investigation-card').last).to_contain_text('Synthetic fixture')
            expect(page.locator('.investigation-card').last).to_contain_text('Imported')
            capture('08-import.png', 'Investigation imported via JSON paste: second card shows Imported badge; removal restores a clean list.')
            page.locator('#import-result a').click()
            page.locator('#include-uninspected').check()
            expect(page.locator('#evidence-cards .evidence-card')).to_have_count(5)
            status = page.evaluate(
                "fetch('/api/investigations/synthetic-fixture', {method: 'DELETE'}).then(r => r.status)")
            self.assertEqual(status, 204)
            page.goto(self.base, wait_until='networkidle')
            expect(page.locator('.investigation-card')).to_have_count(1)
            page.evaluate('window.scrollTo(0,0)')
            self.assertEqual(external, [])
            self.assertEqual(errors, [])
            server_events = []
            while True:
                try:
                    server_events.append(self.events.get(timeout=0.1))
                except queue.Empty:
                    break
            self.assertTrue(any(e['kind'] == 'fresh_process' for e in server_events))
            self.assertFalse(any(e['kind'] == 'blocked_server_egress' for e in server_events))
            frozen = json.loads((OUT / 'protected_evidence_hashes.json').read_text())
            self.assertTrue(all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha for path, sha in frozen.items()))
            report = {'status': 'passed', 'fresh_cli_start': True, 'entrypoint': 'app.main --port <unused loopback port>', 'isolated_browser_context': True, 'offline_definition': 'Server outbound sockets/DNS denied; browser requests restricted to this loopback origin. System-wide networking was not changed.', 'browser_external_attempts': external, 'javascript_errors': errors, 'server_events': server_events, 'walkthrough_card_counts': [13, 12, 3, 0, 3], 'replay_counts': [3, 2, 3], 'import_flow_verified': True, 'reset_verified': True, 'explicit_offset_missing_conversion_label_verified': True, 'protected_evidence_unchanged': True, 'screenshots': captures}
            (OUT / 'offline_walkthrough.json').write_text(json.dumps(report, indent=2) + '\n')
            (OUT / 'screenshots.md').write_text('# Screenshots\n\nScreenshots captured by test_judging.py from a fresh offline run.\n\n' + '\n\n'.join('## ' + item['file'] + '\n\n' + item['caption'] + '\n\n![' + item['caption'] + '](' + item['file'] + ')' for item in captures) + '\n')
        finally:
            context.close()


if __name__ == '__main__':
    unittest.main()
