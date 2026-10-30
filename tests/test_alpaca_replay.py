"""Provider-shaped synthetic fixtures; every test denies DNS and connections."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, time, timedelta
from decimal import Decimal
from fractions import Fraction
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from investor_ai.alpaca_replay import AlpacaReplaySource
from investor_ai.cli import main
from investor_ai.market_data import MarketDataError, split_price_return
from investor_ai.market_store import MarketStore, collect_market

FIXTURE = Path(__file__).parent / 'fixtures/alpaca_replay.json'
JAN = '2025-01-01T00:00:00+00:00'
FEB = '2025-02-01T00:00:00+00:00'
MAR = '2025-03-01T00:00:00+00:00'
NY = ZoneInfo('America/New_York')


def fixture():
    return json.loads(FIXTURE.read_text())


def body(entry, transform):
    value = json.loads(entry['body'])
    transform(value)
    entry['body'] = json.dumps(value)


def prepare_transcript(data, start=None):
    """Author exact response expectations for an incremental request, no HTTP."""
    data = deepcopy(data)
    start = start or data['coverage']['start']
    end = data['coverage']['end']
    for response in data['responses']:
        query = response['params']
        if response['stream'] == 'calendar':
            query.update(data['coverage'])
        elif response['stream'] == 'bars':
            query.update(start=datetime.combine(datetime.fromisoformat(start).date(), time(0), NY).isoformat(),
                         end=(datetime.combine(datetime.fromisoformat(end).date() + timedelta(days=1), time(0), NY) - timedelta(microseconds=1)).isoformat(),
                         adjustment=data['adjustment'])
            def filter_bars(value):
                value['bars']['SYNTH'] = [b for b in value['bars'].get('SYNTH', [])
                                           if start <= datetime.fromisoformat(b['t'].replace('Z', '+00:00')).astimezone(NY).date().isoformat() <= end]
            body(response, filter_bars)
        else:
            query.update(data['action_process_coverage'])
    return data


class AlpacaReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MarketStore(self.root / 'store')
        for target in ('socket.getaddrinfo', 'socket.socket.connect', 'socket.socket.connect_ex'):
            deny = patch(target, side_effect=AssertionError('Network forbidden'))
            deny.start()
            self.addCleanup(deny.stop)

    def source(self, data=None, at=JAN, **options):
        path = self.root / 'replay.json'
        path.write_text(json.dumps(data or fixture()))
        return AlpacaReplaySource(path, now=lambda: at, sleep=lambda _: None, **options)

    def collect(self, data=None, at=JAN, source=None, **options):
        adapter = source or self.source(data, at)
        with patch('investor_ai.market_store.utc_now', return_value=at):
            result = collect_market(adapter, self.store, sleep=lambda _: None, cache_hours=0, **options)
        return adapter, result, self.store.inspect('SYNTH', as_of=at)

    def price_return(self, report):
        return split_price_return(report['bars'], report['actions'], start=report['coverage']['start'],
                                  end=report['coverage']['end'], as_of=report['as_of'],
                                  action_coverage=report['action_coverage'])

    def test_end_to_end_pages_exact_decimal_raw_evidence_and_independent_return(self):
        source, _, report = self.collect()
        self.assertEqual(len(source.requests), 5)
        self.assertEqual([r['params'].get('page_token') for r in source.requests],
                         [None, None, 'synthetic-bars-next', None, 'synthetic-actions-next'])
        self.assertEqual(report['bars'][0]['open'], '100.01')
        self.assertNotIn('published_at', report['bars'][0])
        self.assertEqual(report['calendar']['sessions'][1]['close_at'], '2024-11-29T18:00:00+00:00')
        dividend = next(r for r in report['actions'] if r['kind'] == 'dividend')
        self.assertLess(dividend['payment_date'], dividend['ex_date'])
        # $100 initially; after the split 2 shares worth $53 = $106. Cash is excluded.
        self.assertEqual(Fraction(Decimal(self.price_return(report)['value'])), Fraction(6, 100))
        envelope = json.loads(self.store._read(report['bars'][0]['source']))
        expected_bodies = {r['body'] for r in fixture()['responses']}
        actual = envelope['alpaca_response_evidence']
        self.assertEqual({e['body'] for e in actual}, expected_bodies)
        for item in actual:
            self.assertEqual(hashlib.sha256(item['body'].encode()).hexdigest(), item['sha256'])
            self.assertIn('https://', item['endpoint'])
            self.assertLessEqual(item['observed_at'], report['as_of'])
        self.assertEqual(report['source_metadata']['symbol_asof'], '-')

    def test_incremental_prices_and_old_effective_date_corrections_are_independent(self):
        _, first, original = self.collect()
        data = fixture()
        data['coverage']['end'] = data['action_effective_coverage']['end'] = '2024-12-03'
        data['action_process_coverage']['end'] = '2024-12-04'
        body(data['responses'][0], lambda v: v.append({'date': '2024-12-03', 'open': '09:30', 'close': '16:00'}))
        body(data['responses'][2], lambda v: v['bars']['SYNTH'].append(
            {'t': '2024-12-03T05:00:00Z', 'o': 53, 'h': 55, 'l': 52, 'c': 54, 'v': 900}))
        body(data['responses'][3], lambda v: v['corporate_actions']['forward_splits'][0].update(new_rate=3, process_date='2024-12-03'))
        data = prepare_transcript(data, start='2024-12-02')
        adapter, _, report = self.collect(data, FEB, overlap_days=0)
        bar_request = next(r for r in adapter.requests if '/stocks/bars' in r['endpoint'])
        action_request = next(r for r in adapter.requests if '/corporate-actions' in r['endpoint'])
        self.assertEqual(bar_request['params']['start'], '2024-12-02T00:00:00-05:00')
        self.assertEqual(action_request['params']['start'], '2024-11-01')
        # A revised split effective Nov29 must affect the current view even though prices refresh Dec2+.
        self.assertEqual(Fraction(Decimal(self.price_return(report)['value'])), Fraction(62, 100))
        self.assertEqual(self.price_return(self.store.inspect('SYNTH', as_of=JAN))['value'], '0.06')
        self.assertEqual(self.store.load(original['snapshot']), self.store.load(self.store._manifest(first['dataset_key'], first['snapshot_id'])))
        body(data['responses'][3], lambda v: v['corporate_actions']['forward_splits'][0].update(new_rate=2))
        data = prepare_transcript(data, start='2024-12-03')
        _, _, reverted = self.collect(data, MAR, overlap_days=0)
        self.assertEqual(Fraction(Decimal(self.price_return(reverted)['value'])), Fraction(8, 100))
        self.assertEqual(reverted['stored_action_versions'], 4)  # Split A -> B -> A, plus dividend.
        self.assertEqual(self.store.inspect('SYNTH', as_of=FEB)['bars'][0]['observed_at'], JAN)

    def test_cache_hit_makes_no_replay_requests_and_retains_availability(self):
        _, first, _ = self.collect()
        source = self.source(at=FEB)
        with patch.object(source, 'prepare', side_effect=AssertionError('Cache must avoid replay')), \
                patch('investor_ai.market_store.utc_now', return_value='2025-01-01T01:00:00+00:00'):
            cached = collect_market(source, self.store)
        self.assertEqual(first['snapshot_id'], cached['snapshot_id'])
        self.assertEqual(source.requests, [])
        with self.assertRaisesRegex(MarketDataError, 'cutoff'):
            self.store.inspect('SYNTH', as_of='2024-12-03')

    def test_endpoint_receipts_cannot_be_backdated_by_a_different_collector_clock(self):
        source = self.source(at=FEB)
        with self.assertRaisesRegex(MarketDataError, 'observation|receipt|evidence'):
            self.collect(source=source, at=JAN)
        self.assertEqual(list((self.store.root / 'datasets').glob('*/latest.json')), [])

    def test_programmatic_collection_budget_includes_preparation_requests(self):
        source = self.source()
        with self.assertRaisesRegex(MarketDataError, 'budget'):
            self.collect(source=source, max_requests=1)
        self.assertEqual(len(source.requests), 1)

    def test_exhausted_action_pages_never_manufacture_completeness(self):
        data = fixture()
        data.pop('action_review')
        _, _, report = self.collect(data)
        self.assertEqual(len(report['actions']), 2)
        self.assertFalse(report['quality']['actions_complete'])
        self.assertIsNone(report['source_metadata']['action_completeness_basis'])
        with self.assertRaisesRegex(MarketDataError, 'Complete action evidence'):
            self.price_return(report)

    def test_split_adjusted_series_and_raw_series_are_separate_and_not_double_adjusted(self):
        _, raw_result, _ = self.collect()
        data = fixture()
        data.update(adjustment='split', adjustment_as_of='2025-01-31')
        body(data['responses'][1], lambda v: v['bars']['SYNTH'][0].update(o=50.005, h=51.11, l=49.555, c=50, v=2000))
        data = prepare_transcript(data)
        source = self.source(data, FEB)
        with patch('investor_ai.market_store.utc_now', return_value=FEB):
            result = collect_market(source, self.store, sleep=lambda _: None)
        report = self.store.inspect('SYNTH', as_of=FEB, adjustment='split', adjustment_as_of='2025-01-31')
        self.assertNotEqual(result['dataset_key'], raw_result['dataset_key'])
        self.assertEqual(report['bars'][0]['volume'], '2000')
        self.assertEqual(report['bars'][0]['close'], '50')
        self.assertEqual(report['source_metadata']['adjustment_anchor_kind'], 'authored_synthetic_snapshot')
        with self.assertRaisesRegex(MarketDataError, 'raw price'):
            self.price_return(report)
        self.assertEqual(self.store.inspect('SYNTH', as_of=FEB)['bars'][0]['close'], '100')

    def test_action_schema_gaps_fail_closed_without_replacing_a_saved_snapshot(self):
        _, first, _ = self.collect()
        changes = [
            lambda v: v['corporate_actions']['forward_splits'][0].pop('ex_date'),
            lambda v: v['corporate_actions']['forward_splits'][0].update(new_rate=0),
            lambda v: v['corporate_actions'].update(stock_dividends=[{'id': 'unhandled'}]),
            lambda v: v['corporate_actions']['forward_splits'][0].update(symbol='OTHER'),
            lambda v: v['corporate_actions']['forward_splits'][0].update(process_date='2025-01-01'),
        ]
        for change in changes:
            data = fixture()
            body(data['responses'][3], change)
            with self.subTest(change=changes.index(change)), self.assertRaises(MarketDataError):
                self.collect(data, FEB)
            self.assertEqual(self.store.latest(first['dataset_key'])['snapshot_id'], first['snapshot_id'])

    def test_dividend_currency_is_not_assumed_and_zero_cash_is_preserved(self):
        for currency in ('', None, 'EUR'):
            data = fixture()
            body(data['responses'][4], lambda v: v['corporate_actions']['cash_dividends'][0].update(currency=currency))
            with self.subTest(currency=currency), self.assertRaisesRegex(MarketDataError, 'currency'):
                self.collect(data)
        data = fixture()
        body(data['responses'][4], lambda v: v['corporate_actions']['cash_dividends'][0].update(rate=0))
        _, _, report = self.collect(data)
        self.assertEqual(next(r for r in report['actions'] if r['kind'] == 'dividend')['amount'], '0')

    def test_reverse_split_uses_new_rate_over_old_rate(self):
        data = fixture()
        def reverse(value):
            event = value['corporate_actions'].pop('forward_splits')[0]
            event.update(new_rate=1, old_rate=2)
            value['corporate_actions']['reverse_splits'] = [event]
        body(data['responses'][3], reverse)
        _, _, report = self.collect(data)
        self.assertEqual(next(r for r in report['actions'] if r['kind'] == 'split')['ratio'], '0.5')
        # $100 -> half a share worth $53 = $26.50, a $73.50 price loss.
        self.assertEqual(Fraction(Decimal(self.price_return(report)['value'])), Fraction(-147, 200))

    def test_provider_calendar_and_daily_boundaries_cross_dst(self):
        data = fixture()
        data['coverage'] = {'start': '2024-03-08', 'end': '2024-03-11'}
        data['action_effective_coverage'] = deepcopy(data['coverage'])
        data['action_process_coverage'] = {'start': '2024-03-01', 'end': '2024-03-12'}
        data['responses'][0]['body'] = json.dumps([
            {'date': day, 'open': '09:30', 'close': '16:00'} for day in ('2024-03-08', '2024-03-11')])
        for entry, timestamp in zip(data['responses'][1:3], ('2024-03-08T05:00:00Z', '2024-03-11T04:00:00Z')):
            entry['body'] = json.dumps({'bars': {'SYNTH': [
                {'t': timestamp, 'o': 100, 'h': 100, 'l': 100, 'c': 100, 'v': 100}]},
                'next_page_token': 'synthetic-bars-next' if entry is data['responses'][1] else None})
        for entry in data['responses'][3:]:
            value = json.loads(entry['body'])
            value['corporate_actions'] = {}
            entry['body'] = json.dumps(value)
        _, _, report = self.collect(prepare_transcript(data))
        self.assertEqual([s['open_at'] for s in report['calendar']['sessions']],
                         ['2024-03-08T14:30:00+00:00', '2024-03-11T13:30:00+00:00'])
        self.assertEqual(report['quality']['missing_sessions'], [])
        self.assertEqual([r['date'] for r in report['bars']], ['2024-03-08', '2024-03-11'])

    def test_dividend_adjustment_remains_supplied_evidence_with_unchanged_volume(self):
        data = fixture()
        data.update(adjustment='dividend', adjustment_as_of='2024-12-31')
        # Invented already-adjusted provider output, not a locally inferred method.
        body(data['responses'][1], lambda v: v['bars']['SYNTH'][0].update(o=98.76, h=100.97, l=97.86, c=98.75))
        source = self.source(prepare_transcript(data))
        with patch('investor_ai.market_store.utc_now', return_value=JAN):
            collect_market(source, self.store, sleep=lambda _: None)
        report = self.store.inspect('SYNTH', as_of=JAN, adjustment='dividend')
        self.assertEqual(report['bars'][0]['close'], '98.75')
        self.assertEqual(report['bars'][0]['volume'], '1000')
        self.assertTrue(all(r['params']['adjustment'] == 'dividend' for r in source.requests if '/stocks/bars' in r['endpoint']))

    def test_pagination_loops_malformed_pages_and_global_request_budget(self):
        data = fixture()
        body(data['responses'][2], lambda v: v.update(next_page_token='synthetic-bars-next'))
        with self.assertRaisesRegex(MarketDataError, 'Repeated provider'):
            self.collect(data)
        for field in ('bars', 'next_page_token'):
            data = fixture()
            body(data['responses'][1], lambda v: v.pop(field))
            with self.subTest(field=field), self.assertRaisesRegex(MarketDataError, 'pagination'):
                self.collect(data)
        source = self.source(max_requests=4)
        with self.assertRaisesRegex(MarketDataError, 'shared request budget'):
            self.collect(source=source)
        self.assertEqual(len(source.requests), 4)
        self.assertEqual(list((self.store.root / 'datasets').glob('*/latest.json')), [])

    def test_terminal_status_and_error_body_do_not_leak_or_retry(self):
        for status in (301, 400, 401, 403):
            data = fixture()
            data['responses'][0].update(status=status, body='secret-provider-error-body')
            source = self.source(data)
            with self.subTest(status=status), self.assertRaisesRegex(MarketDataError, f'HTTP {status}') as error:
                self.collect(source=source)
            self.assertNotIn('secret-provider-error-body', str(error.exception))
            self.assertEqual(len(source.requests), 1)

    def test_transient_http_retries_share_the_limiter_and_preserve_success_bodies(self):
        data = fixture()
        failed = deepcopy(data['responses'][0])
        failed.update(status=429, headers={'Retry-After': 'Wed, 01 Jan 2025 00:00:03 GMT'}, body='not persisted')
        data['responses'].insert(0, failed)
        elapsed, delays = [0.0], []
        def sleep(seconds):
            elapsed[0] += seconds
            delays.append(seconds)
        source = self.source(data, clock=lambda: elapsed[0])
        source.sleep = sleep
        _, _, report = self.collect(source=source)
        self.assertEqual(len(source.requests), 6)
        self.assertIn(3, delays)
        self.assertGreaterEqual(elapsed[0], 5)
        self.assertEqual(len(source.evidence), 5)
        self.assertEqual(self.price_return(report)['value'], '0.06')

    def test_invalid_or_excessive_cooldowns_fail_closed(self):
        for cooldown in ('NaN', 'Infinity', '-1', '61', 'not a date'):
            data = fixture()
            failed = deepcopy(data['responses'][0])
            failed.update(status=429, headers={'Retry-After': cooldown}, body='not persisted')
            data['responses'].insert(0, failed)
            with self.subTest(cooldown=cooldown), self.assertRaisesRegex(MarketDataError, '[Cc]ooldown|Retry-After'):
                self.collect(data)

    def test_rate_remaining_zero_reset_is_honored_or_stops_on_long_cooldown(self):
        for reset, should_pass in (('1735689602', True), ('1735689661', False), ('NaN', False)):
            data = fixture()
            data['responses'][0]['headers'].update({'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': reset})
            source = self.source(data)
            if should_pass:
                delays = []
                source.sleep = delays.append
                self.collect(source=source)
                self.assertIn(2, delays)
            else:
                with self.subTest(reset=reset), self.assertRaises(MarketDataError):
                    self.collect(source=source)

    def test_429_reset_without_retry_after_is_honored_and_bounded(self):
        for reset, should_pass in (('1735689603', True), ('1735689661', False)):
            data = fixture()
            failed = deepcopy(data['responses'][0])
            failed.update(status=429, headers={'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': reset}, body='not persisted')
            data['responses'].insert(0, failed)
            source = self.source(data)
            delays = []
            source.sleep = delays.append
            if should_pass:
                self.collect(source=source)
                self.assertIn(3, delays)
            else:
                with self.assertRaisesRegex(MarketDataError, 'cooldown'):
                    self.collect(source=source)
                self.assertEqual(len(source.requests), 1)

    def test_daily_timestamps_are_left_edges_and_current_day_is_not_final_at_regular_close(self):
        data = fixture()
        body(data['responses'][1], lambda v: v['bars']['SYNTH'][0].update(t='2024-11-27T21:00:00Z'))
        with self.assertRaisesRegex(MarketDataError, 'day boundary'):
            self.collect(data)
        source = self.source(at='2024-12-02T21:00:00Z')
        with self.assertRaisesRegex(MarketDataError, 'aggregation has not ended'):
            self.collect(source=source, at='2024-12-02T21:00:00Z')
        self.assertEqual(source.requests, [])

    def test_no_implicit_entitlements_adjustments_identity_mapping_or_secret_loading(self):
        for key, value in [('feed', 'sip'), ('symbol_asof', '2025-01-01'), ('adjustment', 'all'),
                           ('synthetic', False), ('schema_version', True), ('APCA-API-KEY-ID', 'fake')]:
            data = fixture()
            data[key] = value
            with self.subTest(key=key), self.assertRaises(MarketDataError):
                self.source(data)
        data = fixture()
        data['responses'][1]['headers']['APCA-API-SECRET-KEY'] = 'fake'
        with self.assertRaises(MarketDataError):
            self.source(data)

    def test_prepared_header_cannot_change_series_or_price_window(self):
        source = self.source()
        original = source.prepare
        def changed(**params):
            header = original(**params)
            header['provider'] = 'different'
            return header
        with patch.object(source, 'prepare', side_effect=changed), self.assertRaisesRegex(MarketDataError, 'identity'):
            self.collect(source=source)
        self.assertEqual(list((self.store.root / 'datasets').glob('*/latest.json')), [])

    def test_cli_replay_is_explicit_and_live_option_remains_disabled(self):
        output = StringIO()
        args = ['--data-dir', str(self.store.root), 'market', 'collect']
        with redirect_stdout(output):
            self.assertEqual(main(args + ['--provider', 'alpaca-replay', '--source', str(FIXTURE)]), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result['synthetic'])
        self.assertFalse(result['live_validated'])
        self.assertEqual(result['replay_provider_requests'], 5)
        error = StringIO()
        with redirect_stderr(error):
            self.assertEqual(main(args + ['--provider', 'alpaca']), 1)
        self.assertIn('incomplete', error.getvalue())
