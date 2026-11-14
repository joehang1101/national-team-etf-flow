import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import refresh
import fetch
import compute
import render


class PipelineTests(unittest.TestCase):
    def test_nav_json_is_parsed_without_javascript_and_in_beijing_time(self):
        text = 'var Data_netWorthTrend = [{"x":1794499200000,"y":1.25,"equityReturn":0,"unitMoney":""}]; other();'
        values = fetch.parse_nav_text(text)
        self.assertEqual(values, {'20261113': 1.25})
        with self.assertRaises(ValueError):
            fetch.parse_nav_text('<html>access denied</html>')

    def test_offline_partial_data_renders_without_network_or_cache_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shares = {'shares': {'20261112': {'510300': 100, '159919': 100},
                                 '20261113': {'510300': 110, '159919': 120}},
                      'trade_days': {'days': ['20261112', '20261113', '20261116']}}
            nav = {'510300': {'20261112': 1, '20261113': 1.2}, '159919': {'20261112': 1}}
            for name, obj in [('shares.json', shares), ('nav.json', nav), ('klines.json', {})]:
                (root / name).write_text(json.dumps(obj), encoding='utf-8')
            original = (root / 'shares.json').read_bytes()
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(refresh, 'SHARES_CACHE', str(root / 'shares.json')))
                stack.enter_context(patch.object(refresh, 'NAV_CACHE', str(root / 'nav.json')))
                stack.enter_context(patch.object(render, 'REPORT', str(root / 'report.html')))
                
                for module in (refresh, compute, fetch):
                    stack.enter_context(patch.object(module, 'TARGETS', [('510300', 'test SH', 'sh'), ('159919', 'test SZ', 'sz')]))
                stack.enter_context(patch.object(fetch, 'KLINE_CACHE', str(root / 'klines.json')))
                stack.enter_context(patch.object(refresh.requests.sessions.Session, 'request', side_effect=AssertionError('offline network call')))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                payload = refresh.main(offline=True)
                refresh.render_html(payload)
            self.assertEqual(payload['quality']['latest_complete'], 1)
            self.assertEqual(payload['quality']['missing_latest'], ['159919'])
            sz = next(x for x in payload['etfs'] if x['c'] == '159919')
            self.assertIsNone(sz['today'])
            self.assertIsNone(sz['series']['2026-11-13'])
            self.assertIsNone(payload['date_total']['20261112'])
            html = (root / 'report.html').read_text(encoding='utf-8')
            self.assertIn('1/2 只可计算', html)
            self.assertIn('2026-11-13', html)
            self.assertNotIn('<script src=', html)
            from bs4 import BeautifulSoup
            for name in ['index.html','flows.html','research.html','quality.html']:
                page=root/name
                self.assertTrue(page.exists(),name)
                soup=BeautifulSoup(page.read_text(encoding='utf-8'),'html.parser')
                self.assertEqual(len(soup.select('nav.nav a')),4)
                self.assertEqual(len(soup.select('nav.nav a[aria-current="page"]')),1)
                for link in soup.select('nav.nav a'):
                    self.assertTrue((root/link['href']).exists())
            self.assertNotIn('id="signals"',html)
            self.assertNotIn('id="klnBox"',html)
            self.assertIn('klnBox',(root/'flows.html').read_text(encoding='utf-8'))
            self.assertIn('id="signals"',(root/'research.html').read_text(encoding='utf-8'))
            self.assertEqual((root / 'shares.json').read_bytes(), original)

    def test_strict_failure_preserves_existing_homepage_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'index.html').write_text('previous report', encoding='utf-8')
            payload = {'quality': {'is_stale': True, 'latest_complete': 1, 'target_count': 2}}
            with patch.object(refresh, 'OUT_DIR', directory), patch.object(refresh, 'main', return_value=payload), \
                 patch('sys.argv', ['refresh.py', '--require-fresh']), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(refresh.cli(), 1)
            self.assertEqual((root / 'index.html').read_text(), 'previous report')
            self.assertFalse((root / '.refresh.lock').exists())


if __name__ == '__main__':
    unittest.main()
