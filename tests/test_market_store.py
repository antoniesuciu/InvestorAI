"""Storage/collection integration tests using authored synthetic evidence only."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from investor_ai.cli import main
from investor_ai.market_data import MarketDataError
from investor_ai.market_store import JsonFileSource, MarketStore, RetryableSourceError, collect_market
from investor_ai.sec import SecClient
from investor_ai.storage import write_json

FIXTURE = Path(__file__).parent / 'fixtures' / 'market_data.json'
EARLY = '2025-01-01T00:00:00+00:00'
LATE = '2025-02-01T00:00:00+00:00'


class MarketStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MarketStore(self.root / 'store')
        self.source = JsonFileSource(FIXTURE)
        self.data = json.loads(FIXTURE.read_text())

    def source_for(self, data):
        path = self.root / 'source.json'
        path.write_text(json.dumps(data))
        return JsonFileSource(path)

    def collect(self, source=None, at=EARLY, **kwargs):
        with patch('investor_ai.market_store.utc_now', return_value=at):
            return collect_market(source or self.source, self.store, **{'sleep': lambda _: None, **kwargs})

    def test_value_reverting_to_an_older_number_is_a_new_observation(self):
        self.collect()
        changed = deepcopy(self.data)
        changed['pages'][1]['bars'][0]['close'] = '54'
        self.collect(self.source_for(changed), at=LATE)
        self.collect(self.source_for(self.data), at='2025-03-01T00:00:00+00:00')
        latest = self.store.inspect('SYNTH', as_of='2025-03-01')
        self.assertEqual(latest['bars'][-1]['close'], '53')
        self.assertEqual(latest['bars'][-1]['observed_at'], '2025-03-01T00:00:00+00:00')
        self.assertEqual(self.store.inspect('SYNTH', as_of='2025-02-15')['bars'][-1]['close'], '54')

    def test_incremental_price_window_cannot_claim_unfetched_action_history(self):
        first = deepcopy(self.data)
        first['action_coverage']['complete'] = False
        for page in first['pages']:
            page['actions'] = []
        self.collect(self.source_for(first))
        self.collect(self.source_for(self.data), at=LATE, overlap_days=0)
        latest = self.store.inspect('SYNTH', as_of=LATE)
        self.assertEqual(latest['action_coverage']['start'], '2024-12-02')
        self.assertFalse(latest['quality']['actions_complete'])
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(['--data-dir', str(self.store.root), 'market', 'inspect', 'SYNTH']), 0)
        self.assertIn('whole-series complete coverage: False', output.getvalue())

    def test_action_reversion_is_a_new_occurrence(self):
        self.collect()
        changed = deepcopy(self.data)
        changed['pages'][0]['actions'][0]['ratio'] = '3'
        self.collect(self.source_for(changed), at=LATE)
        self.collect(self.source_for(self.data), at='2025-03-01T00:00:00+00:00')
        report = self.store.inspect('SYNTH', as_of='2025-03-01')
        split = next(r for r in report['actions'] if r['kind'] == 'split')
        self.assertEqual(split['ratio'], '2')
        self.assertEqual(report['stored_action_versions'], 4)

    def test_removed_calendar_session_or_action_fails_closed(self):
        first = self.collect()
        for kind in ('calendar', 'actions'):
            changed = deepcopy(self.data)
            if kind == 'calendar':
                changed['calendar']['sessions'].pop(1)
                changed['pages'][0]['bars'].pop()
            else:
                changed['pages'][0]['actions'] = []
            with self.subTest(kind=kind), self.assertRaisesRegex(MarketDataError, 'reconciliation'):
                self.collect(self.source_for(changed), at=LATE, refresh=True)
        self.assertEqual(self.store.latest(first['dataset_key'])['snapshot_id'], first['snapshot_id'])

    def test_paginated_collection_raw_replay_and_strictly_offline_inspection(self):
        result = self.collect()
        self.assertEqual(result['requests'], 2)
        with patch.object(SecClient, 'get', side_effect=AssertionError('network forbidden')):
            report = self.store.inspect('SYNTH', as_of=EARLY)
        self.assertEqual(len(report['bars']), 3)
        self.assertEqual(len(report['actions']), 2)
        self.assertEqual(report['bars'][-1]['volume'], '500')
        for row in report['bars']:
            raw = json.loads(self.store._read(row['source']))
            self.assertIn(row['date'], {b['date'] for b in raw['bars']})
        self.assertEqual(report['availability_policy'], 'observed_by_cutoff')
        self.assertTrue(report['synthetic'])

    def test_cache_hits_verify_every_source_and_keep_original_data_age(self):
        first = self.collect()
        with patch.object(self.source, 'fetch_page', side_effect=AssertionError('must be cached')):
            second = self.collect(at='2025-01-01T01:00:00+00:00')
        self.assertEqual(second['status'], 'cached')
        self.assertEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertEqual(self.store.latest(first['dataset_key'])['retrieved_at'], EARLY)
        manifest = self.store.latest(first['dataset_key'])
        (self.store.root / manifest['sources'][0]['path']).write_text('corrupt')
        with self.assertRaisesRegex(MarketDataError, 'checksum'):
            self.collect()

    def test_corrections_preserve_old_versions_and_historical_snapshot(self):
        self.collect()
        updated = deepcopy(self.data)
        updated['pages'][1]['bars'][0]['close'] = '54'
        self.collect(self.source_for(updated), at=LATE)
        earlier = self.store.inspect('SYNTH', as_of='2025-01-15')
        latest = self.store.inspect('SYNTH', as_of=LATE)
        self.assertEqual(earlier['bars'][-1]['close'], '53')
        self.assertEqual(latest['bars'][-1]['close'], '54')
        self.assertEqual(latest['stored_bar_versions'], 4)
        self.assertEqual(latest['bars'][0]['observed_at'], EARLY)
        self.assertEqual(latest['bars'][-1]['observed_at'], LATE)
        with self.assertRaisesRegex(MarketDataError, 'cutoff'):
            self.store.inspect('SYNTH', as_of='2024-12-31')

    def test_incremental_window_rechecks_overlap_and_refresh_rechecks_full_interval(self):
        self.collect()
        updated = deepcopy(self.data)
        updated['pages'][1]['bars'][0]['close'] = '54'
        source = self.source_for(updated)
        with patch.object(source, 'fetch_page', wraps=source.fetch_page) as fetch:
            self.collect(source, at=LATE, overlap_days=1)
        self.assertEqual(fetch.call_args.kwargs['start'], '2024-12-01')
        self.assertEqual(len(self.store.inspect('SYNTH', as_of=LATE)['bars']), 3)
        with patch.object(source, 'fetch_page', wraps=source.fetch_page) as fetch:
            self.collect(source, at='2025-03-01T00:00:00+00:00', refresh=True)
        self.assertEqual(fetch.call_args.kwargs['start'], '2024-11-27')

    def test_missing_session_is_not_filled_and_failed_refresh_preserves_pointer(self):
        original = self.collect()
        missing = deepcopy(self.data)
        missing['pages'][0]['bars'].pop()
        other = MarketStore(self.root / 'missing')
        collect_market(self.source_for(missing), other, sleep=lambda _: None)
        report = other.inspect('SYNTH')
        self.assertEqual(report['quality']['missing_sessions'], ['2024-11-29'])
        self.assertEqual(len(report['bars']), 2)
        with patch.object(self.source, 'fetch_page', side_effect=RuntimeError('SECRET')):
            with self.assertRaisesRegex(MarketDataError, 'sensitive request details suppressed') as raised:
                self.collect(at=LATE, refresh=True)
        self.assertNotIn('SECRET', str(raised.exception))
        self.assertEqual(self.store.latest(original['dataset_key'])['snapshot_id'], original['snapshot_id'])

    def test_interrupted_publication_does_not_expose_orphan_snapshot(self):
        first = self.collect()
        changed = deepcopy(self.data)
        changed['pages'][1]['bars'][0]['close'] = '54'
        def interrupted(path, value):
            if path.name == 'latest.json':
                raise OSError('synthetic interruption')
            write_json(path, value)
        with patch('investor_ai.market_store.write_json', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.collect(self.source_for(changed), at=LATE)
        self.assertEqual(self.store.inspect('SYNTH', as_of=LATE)['bars'][-1]['close'], '53')
        self.assertEqual(self.store.latest(first['dataset_key'])['snapshot_id'], first['snapshot_id'])
        retry = self.collect(self.source_for(changed), at='2025-03-01T00:00:00+00:00')
        self.assertEqual(retry['status'], 'success')

    def test_raw_adjusted_and_feeds_cannot_be_silently_combined(self):
        self.collect()
        adjusted = deepcopy(self.data)
        adjusted.update(adjustment='split', adjustment_as_of='2024-12-02')
        self.collect(self.source_for(adjusted))
        self.assertEqual(self.store.inspect('SYNTH', as_of=EARLY)['adjustment'], 'raw')
        self.assertEqual(self.store.inspect('SYNTH', as_of=EARLY, adjustment='split')['adjustment'], 'split')
        adjusted['adjustment_as_of'] = '2024-12-03'
        self.collect(self.source_for(adjusted))
        with self.assertRaisesRegex(MarketDataError, 'Ambiguous market series'):
            self.store.inspect('SYNTH', as_of=EARLY, adjustment='split')

    def test_path_and_normalized_checksum_guards(self):
        result = self.collect()
        manifest = self.store.latest(result['dataset_key'])
        with self.assertRaisesRegex(MarketDataError, 'escapes'):
            self.store._read({'path': '../outside.json', 'sha256': 'a' * 64})
        (self.store.root / manifest['data']['path']).write_text('bad')
        with self.assertRaisesRegex(MarketDataError, 'checksum'):
            self.store.inspect('SYNTH', as_of=EARLY)

    def test_synthetic_input_gate_and_secret_field_rejection(self):
        for key, value in [('synthetic', False), ('api_key', 'DO_NOT_PERSIST')]:
            with self.subTest(key=key):
                data = {**self.data, key: value}
                with self.assertRaises(MarketDataError):
                    self.source_for(data)

    def test_error_page_cannot_be_treated_as_a_complete_empty_ledger(self):
        broken = deepcopy(self.data)
        broken['pages'] = [{'error': 'synthetic source error'}]
        with self.assertRaises(MarketDataError):
            self.collect(self.source_for(broken))

    def test_pagination_loop_or_budget_does_not_publish_partial_data(self):
        for budget in (1, 3):
            with self.subTest(budget=budget), patch.object(self.source, 'fetch_page', return_value=b'{"bars": [], "actions": [], "next_page_token": "loop"}'):
                with self.assertRaisesRegex(MarketDataError, 'budget|pagination'):
                    self.collect(max_requests=budget)
        self.assertEqual(list(self.store.root.glob('datasets/*/latest.json')), [])

    def test_transient_errors_retries_cooldowns_and_request_budget(self):
        original = self.source.fetch_page
        calls = []
        def flaky(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RetryableSourceError(2)
            return original(**kwargs)
        elapsed = [0.0]
        def sleep(delay):
            elapsed[0] += delay
        with patch.object(self.source, 'fetch_page', side_effect=flaky):
            result = self.collect(clock=lambda: elapsed[0], sleep=sleep)
        self.assertEqual(result['requests'], 3)
        self.assertGreaterEqual(elapsed[0], 2.5)
        with patch.object(self.source, 'fetch_page', side_effect=RetryableSourceError(120)):
            with self.assertRaisesRegex(MarketDataError, 'cooldown'):
                self.collect(refresh=True)

    def test_cli_collect_inspect_and_disabled_live_provider(self):
        folder = str(self.store.root)
        for args in (['market', 'collect', '--source', str(FIXTURE)], ['market', 'inspect', 'SYNTH', '--json']):
            output = StringIO()
            with redirect_stdout(output), patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
                self.assertEqual(main(['--data-dir', folder, *args]), 0)
            self.assertTrue(json.loads(output.getvalue())['synthetic'])
        error = StringIO()
        with redirect_stderr(error):
            self.assertEqual(main(['market', 'collect', '--provider', 'alpaca']), 1)
        self.assertIn('integration is incomplete', error.getvalue())
