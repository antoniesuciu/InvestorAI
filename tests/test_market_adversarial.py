"""One end-to-end adversarial lifecycle, with invented prices and real process exits."""

from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from investor_ai.market_data import MarketDataError, split_price_return
from investor_ai.market_store import JsonFileSource, MarketStore, collect_market

REPO = Path(__file__).resolve().parents[1]
DAYS = ['2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05', '2024-01-08',
        '2024-01-09', '2024-01-10', '2024-01-11', '2024-01-12', '2024-01-16']
PRICES = ['120', '122', '61', '62', '63', '21', '22', '44', '45', '46']
TIMES = [f'2025-02-{day:02}T00:00:00+00:00' for day in range(1, 8)]

# The child dies during a real filesystem operation, bypassing finally blocks.
CRASH = r'''
import os, sys
from pathlib import Path
def offline(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo'}:
        raise AssertionError('Child network access forbidden')
sys.addaudithook(offline)
import investor_ai.storage as storage
import investor_ai.market_store as market
root, source, stage, observed = sys.argv[1:]
original = storage.write_bytes
def crash(path, body):
    if (stage == 'raw' and path.parent.name == 'raw') or (stage == 'pointer' and path.name == 'latest.json'):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name('.' + path.name + '.adversarial.tmp')
        with temporary.open('wb') as handle:
            handle.write(body[:max(1, len(body) // 2)])
            handle.flush()
        os._exit(73)
    return original(path, body)
storage.write_bytes = crash
market.write_bytes = crash
market.utc_now = lambda: observed
market.collect_market(market.JsonFileSource(source), market.MarketStore(root),
                      sleep=lambda _: None, cache_hours=0, overlap_days=3)
raise AssertionError('Crash injection did not execute')
'''


def invented_bundle(*, corrected=False):
    bars = []
    for day, close in zip(DAYS, PRICES):
        close = '125' if corrected and day == DAYS[0] else close
        amount = Decimal(close)
        bars.append({'date': day, 'open': close, 'high': str(amount + 1), 'low': str(amount - 1),
                     'close': close, 'volume': '1000', 'published_at': day + 'T22:00:00Z'})
    actions = [
        {'id': 'FORWARD-1', 'kind': 'split', 'ex_date': '2024-01-04', 'ratio': '2'},
        {'id': 'CASH-1', 'kind': 'dividend', 'ex_date': '2024-01-05', 'amount': '0.75' if corrected else '0.5',
         'currency': 'USD', 'payment_date': '2024-01-10'},
        {'id': 'FORWARD-2', 'kind': 'split', 'ex_date': '2024-01-09', 'ratio': '2.5' if corrected else '3'},
        {'id': 'CASH-2', 'kind': 'dividend', 'ex_date': '2024-01-10', 'amount': '0.25', 'currency': 'USD'},
        {'id': 'REVERSE', 'kind': 'split', 'ex_date': '2024-01-11', 'ratio': '0.5'},
        {'id': 'CASH-3', 'kind': 'dividend', 'ex_date': '2024-01-12', 'amount': '0.1', 'currency': 'USD'},
    ]
    return {'schema_version': 1, 'synthetic': True, 'provider': 'synthetic', 'feed': 'synthetic',
            'instrument': {'id': 'ADVERSARIAL-XNAS', 'symbol': 'ADVERSARIAL', 'mic': 'XNAS',
                           'currency': 'USD', 'timezone': 'America/New_York', 'cik': '0000123456'},
            'adjustment': 'raw', 'adjustment_as_of': None,
            'calendar': {'start': DAYS[0], 'end': DAYS[-1],
                         'sessions': [{'date': day, 'open_at': day + 'T14:30:00Z', 'close_at': day + 'T21:00:00Z'} for day in DAYS]},
            'pages': [{'bars': bars[:5], 'actions': actions[:2]}, {'bars': bars[5:], 'actions': actions[2:]}]}


class MarketAdversarialIntegrationTests(unittest.TestCase):
    def test_incremental_financial_history_survives_corrections_and_process_death(self):
        with tempfile.TemporaryDirectory(prefix='investor-adversarial-') as folder, \
                patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')), \
                patch('socket.socket.connect', side_effect=AssertionError('Connections forbidden')), \
                patch('socket.socket.connect_ex', side_effect=AssertionError('Connections forbidden')):
            self._exercise(Path(folder))

    def _exercise(self, folder):
        root, source_file = folder / 'store', folder / 'source.json'
        store = MarketStore(root)
        saved = []

        def source(start, end, *, corrected=False, complete=True):
            data = invented_bundle(corrected=corrected)
            if not complete:
                data['pages'][1]['actions'] = [event for event in data['pages'][1]['actions'] if event['id'] != 'CASH-3']
            data['coverage'] = {'start': start, 'end': end}
            data['action_coverage'] = {**data['coverage'], 'complete': complete}
            source_file.write_text(json.dumps(data))
            return JsonFileSource(source_file)

        def collect(start, end, at, *, corrected=False, complete=True, refresh=False, cache_hours=0):
            adapter = source(start, end, corrected=corrected, complete=complete)
            with patch('investor_ai.market_store.utc_now', return_value=at):
                result = collect_market(adapter, MarketStore(root), refresh=refresh, cache_hours=cache_hours,
                                        overlap_days=3, sleep=lambda _: None)
            self.assertEqual(result['status'], 'cached' if cache_hours else 'success')
            report = MarketStore(root).inspect('ADVERSARIAL', as_of=at)
            if not cache_hours:
                saved.append((at, deepcopy(report['snapshot']), deepcopy(store.load(report['snapshot']))))
            return report

        def check_return(report, start, end, expected):
            result = split_price_return(report['bars'], report['actions'], start=start, end=end,
                                        as_of=report['as_of'], action_coverage=report['action_coverage'])
            # Exact hand-worked rational references below are independent of
            # provider parsing, revision selection and the production formula.
            self.assertLess(abs(Fraction(Decimal(result['value'])) - expected), Fraction(1, 10 ** 32))
            self.assertTrue(any('not total return' in text for text in result['limitations']))
            for row in result['inputs']:
                self.assertLessEqual(row['observed_at'], report['as_of'])
                store._read(row['source'])
            return result

        initial = collect(DAYS[0], '2024-01-05', TIMES[0])
        check_return(initial, DAYS[0], '2024-01-05', Fraction(1, 30))  # $120 -> 2 shares * $62 = $124.
        original_files = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*.json')
                          if p.name not in {'latest.json', 'status.json'}}

        second = collect('2024-01-05', '2024-01-10', TIMES[1])
        check_return(second, DAYS[0], '2024-01-10', Fraction(1, 10))  # $120 -> 6 * $22 = $132.
        self.assertTrue(second['quality']['actions_complete'])

        incomplete = collect('2024-01-10', '2024-01-12', TIMES[2], complete=False)
        self.assertFalse(incomplete['quality']['actions_complete'])
        self.assertNotIn('CASH-3', {event['id'] for event in incomplete['actions']})
        with self.assertRaises(MarketDataError):
            check_return(incomplete, DAYS[0], '2024-01-12', Fraction(1, 8))
        check_return(incomplete, DAYS[0], '2024-01-05', Fraction(1, 30))

        repaired = collect('2024-01-09', '2024-01-12', TIMES[3])
        check_return(repaired, DAYS[0], '2024-01-12', Fraction(1, 8))  # $120 -> 3 * $45 = $135.
        self.assertTrue(repaired['quality']['actions_complete'])
        self.assertEqual(len(repaired['actions']), 6)  # Three splits and three dividends.
        self.assertEqual(next(r for r in repaired['actions'] if r['id'] == 'CASH-3')['observed_at'], TIMES[3])

        corrected = collect(DAYS[0], '2024-01-12', TIMES[4], corrected=True, refresh=True)
        check_return(corrected, DAYS[0], '2024-01-12', Fraction(-1, 10))  # $125 -> 2.5 * $45 = $112.50.
        check_return(corrected, DAYS[0], '2024-01-05', Fraction(-1, 125))  # $125 -> 2 * $62 = $124.
        self.assertEqual(next(r for r in corrected['actions'] if r['id'] == 'CASH-1')['amount'], '0.75')
        check_return(MarketStore(root).inspect('ADVERSARIAL', as_of=TIMES[1]), DAYS[0], '2024-01-10', Fraction(1, 10))

        # The next update first dies during raw publication, then dies with a
        # complete orphan snapshot immediately before updating the latest pointer.
        source('2024-01-11', DAYS[-1], corrected=True)
        prior_id = corrected['snapshot']['snapshot_id']
        for stage in ('raw', 'pointer'):
            child = subprocess.run([sys.executable, '-c', CRASH, str(root), str(source_file), stage, TIMES[5]],
                                   cwd=REPO, env={'PYTHONPATH': str(REPO / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'},
                                   capture_output=True, text=True, timeout=20)
            self.assertEqual(child.returncode, 73, child.stderr)
            restarted = MarketStore(root).inspect('ADVERSARIAL', as_of=TIMES[5])
            self.assertEqual(restarted['snapshot']['snapshot_id'], prior_id)
            self.assertEqual(restarted['coverage']['end'], '2024-01-12')
            check_return(restarted, DAYS[0], '2024-01-12', Fraction(-1, 10))
        self.assertGreaterEqual(len(list(root.rglob('*.adversarial.tmp'))), 2)

        final = collect('2024-01-11', DAYS[-1], TIMES[6], corrected=True)
        self.assertEqual(final['snapshot']['previous_snapshot_id'], prior_id)
        check_return(final, DAYS[0], DAYS[-1], Fraction(-2, 25))  # $125 -> 2.5 * $46 = $115.
        self.assertTrue(final['quality']['actions_complete'])
        self.assertEqual(final['quality']['missing_sessions'], [])
        self.assertGreater(final['stored_bar_versions'], len(final['bars']))
        self.assertGreater(final['stored_action_versions'], len(final['actions']))

        cached = collect('2024-01-11', DAYS[-1], '2025-02-07T01:00:00+00:00', corrected=True, cache_hours=24)
        self.assertEqual(cached['snapshot'], final['snapshot'])
        for at, manifest, original_data in saved:
            self.assertEqual(MarketStore(root).load(manifest), original_data)
            self.assertEqual(MarketStore(root).inspect('ADVERSARIAL', as_of=at)['snapshot'], manifest)
        for path, digest in original_files.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        for window in final['action_coverage']['windows']:
            for reference in window['evidence']['sources']:
                MarketStore(root)._read(reference)
        with self.assertRaises(MarketDataError):
            MarketStore(root).inspect('ADVERSARIAL', as_of='2024-12-31')
        # Integrity failures must be explicit, including during a cache lookup.
        normalized = root / final['snapshot']['data']['path']
        original = normalized.read_bytes()
        try:
            normalized.write_bytes(original[:len(original) // 2])
            with self.assertRaisesRegex(MarketDataError, 'checksum'):
                MarketStore(root).inspect('ADVERSARIAL', as_of=TIMES[6])
            with self.assertRaisesRegex(MarketDataError, 'checksum'):
                collect('2024-01-11', DAYS[-1], '2025-02-07T01:00:00+00:00', corrected=True, cache_hours=24)
        finally:
            normalized.write_bytes(original)
        check_return(MarketStore(root).inspect('ADVERSARIAL', as_of=TIMES[6]), DAYS[0], DAYS[-1], Fraction(-2, 25))
        self.validation_summary = {'synthetic': True, 'network_access': 'blocked in parent and children',
                                   'published_updates': len(saved), 'abrupt_process_exits': 2,
                                   'historical_snapshots_rechecked': len(saved), 'financial_reference': 'exact Fraction hand calculations',
                                   'final_price_return': '-0.08', 'initial_historical_return': '1/30',
                                   'live_validation': 'not performed; live integration remains disabled'}
