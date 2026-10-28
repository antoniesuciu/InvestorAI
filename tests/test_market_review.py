"""Gemini Milestone 3 regressions; all values and source windows are invented."""

from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from investor_ai.market_data import MarketDataError, normalize_bundle, split_price_return, coverage_covers
from investor_ai.market_store import JsonFileSource, MarketStore, collect_market
from investor_ai.storage import Store
from investor_ai.sec import SecError
from test_market_data import fixture, OBSERVED

JAN = '2025-01-01T00:00:00+00:00'
FEB = '2025-02-01T00:00:00+00:00'
MAR = '2025-03-01T00:00:00+00:00'


class IncrementalCoverageReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MarketStore(self.root / 'store')

    def collect(self, start, end, at=JAN, *, complete=True, bundle=None, **options):
        data = deepcopy(bundle) if bundle is not None else fixture()
        data['coverage'] = {'start': start, 'end': end}
        data['action_coverage'] = {**data['coverage'], 'complete': complete}
        path = self.root / 'input.json'
        path.write_text(json.dumps(data))
        with patch('investor_ai.market_store.utc_now', return_value=at):
            return collect_market(JsonFileSource(path), self.store, sleep=lambda _: None, cache_hours=0, **options)

    def result(self, at=MAR, start='2024-11-27', end='2024-12-02'):
        report = self.store.inspect('SYNTH', as_of=at)
        return split_price_return(report['bars'], report['actions'], start=start, end=end,
                                  as_of=at, action_coverage=report['action_coverage'])

    def test_contiguous_complete_windows_survive_repeated_incremental_updates(self):
        self.collect('2024-11-27', '2024-11-29')
        self.collect('2024-11-30', '2024-12-02', FEB)
        self.collect('2024-12-02', '2024-12-02', MAR)
        report = self.store.inspect('SYNTH', as_of=MAR)
        self.assertTrue(report['quality']['actions_complete'])
        self.assertEqual(report['action_coverage']['start'], '2024-11-27')
        self.assertEqual(Decimal(self.result()['value']), Decimal('0.06'))
        self.assertGreaterEqual(len(report['action_coverage']['windows']), 3)

    def test_overlapping_complete_windows_reconcile_correction_and_preserve_old_view(self):
        self.collect('2024-11-27', '2024-12-02')
        changed = fixture()
        changed['pages'][0]['actions'][0]['ratio'] = '3'
        self.collect('2024-11-29', '2024-12-02', FEB, bundle=changed)
        self.assertEqual(Decimal(self.result(FEB)['value']), Decimal('0.59'))
        self.assertEqual(Decimal(self.result(JAN)['value']), Decimal('0.06'))
        self.assertTrue(self.store.inspect('SYNTH', as_of=FEB)['quality']['actions_complete'])

    def test_incomplete_initial_history_is_not_repaired_by_a_later_partial_window(self):
        self.collect('2024-11-27', '2024-11-29', complete=False)
        self.collect('2024-11-30', '2024-12-02', FEB)
        self.assertFalse(self.store.inspect('SYNTH', as_of=FEB)['quality']['actions_complete'])
        with self.assertRaises(MarketDataError):
            self.result(FEB)

    def test_unverified_gap_stays_open_until_a_complete_bridge_is_observed(self):
        self.collect('2024-11-27', '2024-11-27')
        self.collect('2024-11-29', '2024-12-02', FEB)
        self.assertFalse(self.store.inspect('SYNTH', as_of=FEB)['quality']['actions_complete'])
        with self.assertRaises(MarketDataError):
            self.result(FEB)
        self.assertLess(abs(Fraction(Decimal(self.result(FEB, start='2024-11-29')['value'])) - (Fraction(53, 51) - 1)), Fraction(1, 10 ** 32))
        self.collect('2024-11-28', '2024-11-28', MAR, refresh=True)
        self.assertTrue(self.store.inspect('SYNTH', as_of=MAR)['quality']['actions_complete'])
        self.assertEqual(Decimal(self.result(MAR)['value']), Decimal('0.06'))
        with self.assertRaises(MarketDataError):
            self.result(FEB)  # A later bridge must not repair the historical view.

    def test_complete_refresh_cannot_silently_drop_a_known_action(self):
        first = self.collect('2024-11-27', '2024-12-02')
        changed = fixture()
        changed['pages'][0]['actions'] = []
        with self.assertRaisesRegex(MarketDataError, 'disappearance'):
            self.collect('2024-11-29', '2024-12-02', FEB, bundle=changed)
        self.assertEqual(self.store.latest(first['dataset_key'])['snapshot_id'], first['snapshot_id'])

    def test_incomplete_update_invalidates_only_its_overlap_in_current_view(self):
        self.collect('2024-11-27', '2024-12-02')
        self.collect('2024-11-29', '2024-12-02', FEB, complete=False)
        self.assertFalse(self.store.inspect('SYNTH', as_of=FEB)['quality']['actions_complete'])
        with self.assertRaises(MarketDataError):
            self.result(FEB)
        self.assertEqual(Decimal(self.result(JAN)['value']), Decimal('0.06'))

    def test_lock_collision_is_a_market_error_and_sec_contract_is_unchanged(self):
        with Store(self.store.root).collection_lock():
            with self.assertRaises(MarketDataError):
                self.collect('2024-11-27', '2024-12-02')
            with self.assertRaises(SecError):
                with Store(self.store.root).collection_lock():
                    self.fail('SEC collision must remain a SecError')

    def test_coverage_evidence_keeps_original_times_and_raw_references(self):
        self.collect('2024-11-27', '2024-11-29')
        self.collect('2024-11-30', '2024-12-02', FEB)
        proof = self.store.inspect('SYNTH', as_of=FEB)['action_coverage']
        self.assertEqual({w['evidence']['observed_at'] for w in proof['windows']}, {JAN, FEB})
        self.assertTrue(all(w['evidence']['sources'] for w in proof['windows']))
        for window in proof['windows']:
            for source in window['evidence']['sources']:
                self.store._read(source)
        damaged = deepcopy(proof)
        damaged['windows'][0]['start'] = '2024-11-26'
        with self.assertRaises(MarketDataError):
            coverage_covers(damaged, start='2024-11-27', end='2024-12-02', as_of=FEB)
        with self.assertRaises(MarketDataError):
            coverage_covers(proof, start='2024-11-27', end='2024-12-02', as_of=JAN)

    def test_disjoint_complete_window_never_authorizes_a_missing_date(self):
        self.collect('2024-11-27', '2024-11-27')
        self.collect('2024-11-29', '2024-12-02', FEB)
        proof = self.store.inspect('SYNTH', as_of=FEB)['action_coverage']
        self.assertFalse(proof['complete'])
        self.assertFalse(coverage_covers(proof, start='2024-11-28', end='2024-12-02', as_of=FEB))
        self.assertTrue(coverage_covers(proof, start='2024-11-29', end='2024-12-02', as_of=FEB))

    def test_market_lock_releases_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, 'invented'):
            with self.store.collection_lock():
                raise RuntimeError('invented')
        self.assertEqual(self.collect('2024-11-27', '2024-12-02')['status'], 'success')

    def test_earlier_publication_does_not_backdate_local_snapshot_availability(self):
        data = fixture()
        data['pages'][0]['bars'][0]['published_at'] = '2024-11-27T21:00:00Z'
        self.collect('2024-11-27', '2024-12-02', JAN, bundle=data)
        with self.assertRaisesRegex(MarketDataError, 'cutoff'):
            self.store.inspect('SYNTH', as_of='2024-12-03')
        report = self.store.inspect('SYNTH', as_of=JAN)
        bar = report['bars'][0]
        self.assertEqual(bar['date'], '2024-11-27')
        self.assertEqual(bar['published_at'], '2024-11-27T21:00:00+00:00')
        self.assertEqual(bar['observed_at'], JAN)
        self.assertEqual(bar['source']['retrieved_at'], JAN)


class ReportingSemanticsReviewTests(unittest.TestCase):
    def test_main_us_stock_fixture_uses_positive_trade_volume(self):
        self.assertGreater(int(fixture()['pages'][1]['bars'][0]['volume']), 0)

    def test_zero_volume_with_moving_prices_is_a_warning_not_a_universal_error(self):
        data = fixture()
        data['pages'][1]['bars'][0]['volume'] = '0'
        result = normalize_bundle(data, observed_at=OBSERVED)
        self.assertEqual(result['quality']['zero_volume_price_movement_sessions'], ['2024-12-02'])

    def test_flat_zero_volume_is_preserved_as_source_evidence(self):
        data = fixture()
        row = data['pages'][1]['bars'][0]
        row.update(open='53', high='53', low='53', close='53', volume='0')
        result = normalize_bundle(data, observed_at=OBSERVED)
        self.assertEqual(result['quality']['zero_volume_sessions'], ['2024-12-02'])
        self.assertEqual(result['quality']['zero_volume_price_movement_sessions'], [])

    def test_special_dividend_can_be_paid_before_its_ex_date(self):
        data = fixture()
        dividend = data['pages'][1]['actions'][0]
        dividend.update(amount='30', payment_date='2024-11-29', ex_date='2024-12-02')
        result = normalize_bundle(data, observed_at=OBSERVED)
        event = next(r for r in result['actions'] if r['kind'] == 'dividend')
        self.assertLess(event['payment_date'], event['ex_date'])
        self.assertEqual(event['amount'], '30')

    def test_ordinary_dividend_and_invalid_payment_dates(self):
        data = fixture()
        event = next(r for r in normalize_bundle(data, observed_at=OBSERVED)['actions'] if r['kind'] == 'dividend')
        self.assertGreater(event['payment_date'], event['ex_date'])
        data['pages'][1]['actions'][0]['payment_date'] = '2024-13-10'
        with self.assertRaises(MarketDataError):
            normalize_bundle(data, observed_at=OBSERVED)

    def test_reported_repeating_split_ratio_retains_decimal_precision(self):
        data = fixture()
        data['pages'][0]['actions'][0]['ratio'] = '0.4285714285714285714285714285714286'
        data['pages'][1]['bars'][0].update(open='210', high='210', low='210', close='210')
        normalized = normalize_bundle(data, observed_at=OBSERVED)
        result = split_price_return(normalized['bars'], normalized['actions'], start='2024-11-27', end='2024-12-02',
                                    as_of=OBSERVED, action_coverage=normalized['action_coverage'])
        self.assertLess(abs(Fraction(Decimal(result['value'])) - Fraction(-1, 10)), Fraction(1, 10 ** 32))

    def test_compounded_and_boundary_split_factors(self):
        data = fixture()
        data['pages'][1]['actions'].append({'id': 'SECOND', 'kind': 'split', 'ex_date': '2024-12-02', 'ratio': '3'})
        normalized = normalize_bundle(data, observed_at=OBSERVED)
        def calculate(start):
            return split_price_return(normalized['bars'], normalized['actions'], start=start, end='2024-12-02',
                                      as_of=OBSERVED, action_coverage=normalized['action_coverage'])
        self.assertEqual(calculate('2024-11-27')['split_factor'], '6')
        self.assertEqual(calculate('2024-11-29')['split_factor'], '3')  # Split on start excluded, split on end included.
