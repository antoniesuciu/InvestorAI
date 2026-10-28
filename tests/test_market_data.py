"""Invented market observations; no provider accounts or downloaded data."""

from copy import deepcopy
from decimal import Decimal, getcontext
import json
from pathlib import Path
import unittest

from investor_ai.market_data import MarketDataError, normalize_bundle, split_price_return

FIXTURE = Path(__file__).parent / 'fixtures' / 'market_data.json'
OBSERVED = '2024-12-03T12:00:00Z'


def fixture():
    return json.loads(FIXTURE.read_text())


class MarketNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.bundle = fixture()

    def normalize(self, **kwargs):
        return normalize_bundle(self.bundle, observed_at=kwargs.get('observed_at', OBSERVED))

    def test_valid_daily_prices_actions_and_decimal_strings_preserve_provenance(self):
        result = self.normalize()
        self.assertEqual(len(result['bars']), 3)
        self.assertEqual(len(result['actions']), 2)
        row = result['bars'][0]
        self.assertEqual(row['close'], '100')
        self.assertEqual(row['provider'], 'synthetic')
        self.assertEqual(row['instrument_id'], 'SYNTH-XNAS')
        self.assertEqual(len(row['record_id']), 64)
        self.assertEqual(row['source_page'], 0)
        self.assertEqual(result['bars'][-1]['source_page'], 1)
        self.assertTrue(result['synthetic'])

    def test_input_immutability_and_decimal_context(self):
        original, precision = deepcopy(self.bundle), getcontext().prec
        self.normalize()
        self.assertEqual(self.bundle, original)
        self.assertEqual(getcontext().prec, precision)

    def test_synthetic_label_cannot_enable_unlicensed_real_data(self):
        for label in (False, 1, 'true', None):
            self.bundle['synthetic'] = label
            with self.subTest(label=label), self.assertRaises(MarketDataError):
                self.normalize()

    def test_raw_and_adjusted_representations_never_share_record_ids(self):
        raw = self.normalize()
        self.bundle.update(adjustment='split', adjustment_as_of='2024-12-02')
        adjusted = self.normalize()
        self.assertNotEqual(raw['bars'][0]['record_id'], adjusted['bars'][0]['record_id'])
        self.assertEqual(adjusted['bars'][0]['adjustment'], 'split')
        self.assertEqual(adjusted['bars'][0]['adjustment_as_of'], '2024-12-02')

    def test_adjustment_anchor_must_be_known_and_cover_each_bar(self):
        for adjustment, anchor in [('raw', '2024-12-02'), ('split', None), ('all', '2024-12-04'), ('dividend', '2024-11-29'), ('unknown', None)]:
            self.bundle.update(adjustment=adjustment, adjustment_as_of=anchor)
            with self.subTest(adjustment=adjustment, anchor=anchor), self.assertRaises(MarketDataError):
                self.normalize()

    def test_missing_sessions_do_not_invent_bars_or_holiday_observations(self):
        self.bundle['pages'][0]['bars'].pop()
        result = self.normalize()
        self.assertEqual(result['quality']['missing_sessions'], ['2024-11-29'])
        self.assertEqual([r['date'] for r in result['bars']], ['2024-11-27', '2024-12-02'])
        self.assertNotIn('2024-11-28', result['quality']['missing_sessions'])

    def test_early_close_is_an_explicit_session_not_a_missing_half_day(self):
        result = self.normalize()
        self.assertEqual(result['calendar']['sessions'][1]['close_at'], '2024-11-29T18:00:00+00:00')
        self.assertEqual(result['quality']['missing_sessions'], [])

    def test_dst_uses_local_exchange_session_dates_with_explicit_utc_times(self):
        self.bundle['coverage'] = {'start': '2024-03-08', 'end': '2024-03-11'}
        self.bundle['action_coverage'] = {**self.bundle['coverage'], 'complete': False}
        self.bundle['calendar'] = {**self.bundle['coverage'], 'sessions': [
            {'date': '2024-03-08', 'open_at': '2024-03-08T14:30:00Z', 'close_at': '2024-03-08T21:00:00Z'},
            {'date': '2024-03-11', 'open_at': '2024-03-11T13:30:00Z', 'close_at': '2024-03-11T20:00:00Z'}]}
        first, second = deepcopy(self.bundle['pages'][0]['bars'])
        first['date'], second['date'] = '2024-03-08', '2024-03-11'
        self.bundle['pages'] = [{'bars': [first, second], 'actions': []}]
        self.assertEqual(self.normalize()['quality']['missing_sessions'], [])

    def test_duplicate_numeric_representations_are_deduplicated_across_pages(self):
        row = deepcopy(self.bundle['pages'][0]['bars'][0])
        row['close'] = '100.00'
        self.bundle['pages'][1]['bars'].append(row)
        self.bundle['pages'][1]['actions'].append(deepcopy(self.bundle['pages'][0]['actions'][0]))
        result = self.normalize()
        self.assertEqual(len(result['bars']), 3)
        self.assertEqual(result['quality']['duplicate_bars'], 1)
        self.assertEqual(result['quality']['duplicate_actions'], 1)

    def test_conflicting_duplicate_bar_is_not_silently_last_write_wins(self):
        revised = deepcopy(self.bundle['pages'][0]['bars'][0])
        revised['close'] = '101'
        self.bundle['pages'][1]['bars'].append(revised)
        with self.assertRaisesRegex(MarketDataError, 'Conflicting duplicate daily'):
            self.normalize()

    def test_conflicting_action_id_is_not_silently_overwritten(self):
        revised = deepcopy(self.bundle['pages'][0]['actions'][0])
        revised['ratio'] = '3'
        self.bundle['pages'][1]['actions'].append(revised)
        with self.assertRaisesRegex(MarketDataError, 'Conflicting duplicate corporate'):
            self.normalize()

    def test_invalid_prices_and_ohlc_relationships_are_rejected(self):
        for key, value in [('close', 'NaN'), ('open', '0'), ('high', '-1'), ('low', '101'),
                           ('close', 100.1), ('open', True), ('close', '200')]:
            bundle = fixture()
            bundle['pages'][0]['bars'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(MarketDataError):
                normalize_bundle(bundle, observed_at=OBSERVED)

    def test_zero_volume_is_preserved_but_missing_negative_or_fractional_volume_is_invalid(self):
        self.bundle['pages'][1]['bars'][0]['volume'] = '0'
        result = self.normalize()
        self.assertEqual(result['quality']['zero_volume_sessions'], ['2024-12-02'])
        for volume in (None, '-1', '1.5', True, 2.0):
            self.bundle['pages'][0]['bars'][0]['volume'] = volume
            with self.subTest(volume=volume), self.assertRaises(MarketDataError):
                self.normalize()

    def test_session_and_publication_must_precede_actual_observation(self):
        with self.assertRaises(MarketDataError):
            self.normalize(observed_at='2024-12-02T20:00:00Z')
        self.bundle['pages'][0]['bars'][0]['published_at'] = '2024-12-04T00:00:00Z'
        with self.assertRaises(MarketDataError):
            self.normalize()

    def test_unexpected_session_and_invalid_calendar_are_rejected(self):
        self.bundle['pages'][0]['bars'][0]['date'] = '2024-11-28'
        with self.assertRaises(MarketDataError):
            self.normalize()
        self.bundle = fixture()
        self.bundle['calendar']['sessions'].append(deepcopy(self.bundle['calendar']['sessions'][0]))
        with self.assertRaises(MarketDataError):
            self.normalize()

    def test_dates_naive_timestamps_and_unsafe_instrument_identifiers_are_rejected(self):
        for key, value in [('id', '../escape'), ('timezone', 'Not/AZone'), ('cik', '0'), ('currency', 'US')]:
            self.bundle = fixture()
            self.bundle['instrument'][key] = value
            with self.subTest(key=key), self.assertRaises(MarketDataError):
                self.normalize()
        self.bundle = fixture()
        self.bundle['coverage']['start'] = '20241127'
        with self.assertRaises(MarketDataError):
            self.normalize()
        self.bundle = fixture()
        with self.assertRaises(MarketDataError):
            self.normalize(observed_at='2024-12-03T12:00:00')

    def test_actions_do_not_infer_missing_fields_or_assume_an_empty_ledger_is_complete(self):
        self.bundle['action_coverage']['complete'] = False
        self.bundle['pages'][0]['actions'] = []
        self.bundle['pages'][1]['actions'] = []
        self.assertFalse(self.normalize()['quality']['actions_complete'])
        self.bundle['action_coverage'].pop('complete')
        with self.assertRaises(MarketDataError):
            self.normalize()

    def test_action_ratios_currency_and_missing_ex_dates_are_checked(self):
        for kind in ('zero_split', 'currency', 'no_ex_date', 'unsupported'):
            self.bundle = fixture()
            if kind == 'zero_split': self.bundle['pages'][0]['actions'][0]['ratio'] = '0'
            if kind == 'currency': self.bundle['pages'][1]['actions'][0]['currency'] = 'EUR'
            if kind == 'no_ex_date': del self.bundle['pages'][0]['actions'][0]['ex_date']
            if kind == 'unsupported': self.bundle['pages'][0]['actions'][0]['kind'] = 'spin-off'
            with self.subTest(kind=kind), self.assertRaises(MarketDataError):
                self.normalize()


class SplitPriceReturnTests(unittest.TestCase):
    def setUp(self):
        self.data = normalize_bundle(fixture(), observed_at=OBSERVED)

    def result(self, **kwargs):
        return split_price_return(self.data['bars'], self.data['actions'], start='2024-11-27', end='2024-12-02',
                                  as_of=kwargs.get('as_of', OBSERVED), action_coverage=self.data['action_coverage'])

    def test_hand_calculated_split_price_return_excludes_cash_dividend(self):
        result = self.result()
        self.assertEqual(Decimal(result['value']), Decimal('0.06'))  # 53*2/100 - 1, not -47% or total return.
        self.assertEqual(result['split_factor'], '2')
        self.assertEqual(len(result['inputs']), 4)
        self.assertIn('not total return', result['limitations'][0])

    def test_reverse_split_ratio_has_new_shares_per_old_share_orientation(self):
        self.data['actions'][1 if self.data['actions'][1]['kind'] == 'split' else 0]['ratio'] = '0.5'
        self.data['bars'][-1]['close'] = '210'
        self.assertEqual(Decimal(self.result()['value']), Decimal('0.05'))

    def test_incomplete_or_future_coverage_cannot_authorize_a_return(self):
        self.data['action_coverage']['complete'] = False
        with self.assertRaises(MarketDataError): self.result()
        self.data['action_coverage']['complete'] = True
        self.data['action_coverage']['observed_at'] = '2024-12-04T00:00:00Z'
        with self.assertRaises(MarketDataError): self.result()

    def test_observation_cutoff_excludes_future_prices_and_revisions(self):
        with self.assertRaises(MarketDataError): self.result(as_of='2024-12-02T23:00:00Z')
        revised = deepcopy(self.data['bars'][-1])
        revised.update(observed_at='2024-12-04T00:00:00Z', close='99', record_id='later-price')
        self.data['bars'].append(revised)
        self.assertEqual(Decimal(self.result()['value']), Decimal('0.06'))
        self.assertEqual(Decimal(self.result(as_of='2024-12-05T00:00:00Z')['value']), Decimal('0.98'))

    def test_future_action_revision_does_not_change_an_earlier_observed_view(self):
        revised = deepcopy(next(a for a in self.data['actions'] if a['kind'] == 'split'))
        revised.update(observed_at='2024-12-04T00:00:00Z', ratio='3', record_id='later-split')
        self.data['actions'].append(revised)
        self.assertEqual(Decimal(self.result()['value']), Decimal('0.06'))

    def test_latest_action_revision_is_selected_before_ex_date_range_filter(self):
        revised = deepcopy(next(a for a in self.data['actions'] if a['kind'] == 'split'))
        revised.update(observed_at='2024-12-04T00:00:00Z', ex_date='2024-12-05', record_id='moved-split')
        self.data['actions'].append(revised)
        self.assertEqual(Decimal(self.result()['value']), Decimal('0.06'))
        later = self.result(as_of='2024-12-06T00:00:00Z')
        self.assertEqual(Decimal(later['value']), Decimal('-0.47'))
        self.assertFalse(any(r.get('id') == revised['id'] for r in later['inputs']))

    def test_foreign_complete_action_coverage_cannot_authorize_an_empty_ledger(self):
        for key, value in [('id', 'OTHER'), ('currency', 'EUR'), ('provider', 'other'), ('feed', 'other')]:
            other = fixture()
            if key in {'provider', 'feed'}:
                other[key] = value
            else:
                other['instrument'][key] = value
            if key == 'currency':
                other['pages'][1]['actions'][0]['currency'] = value
            proof = normalize_bundle(other, observed_at=OBSERVED)['action_coverage']
            with self.subTest(key=key), self.assertRaises(MarketDataError):
                split_price_return(self.data['bars'], [], start='2024-11-27', end='2024-12-02',
                                   as_of=OBSERVED, action_coverage=proof)

    def test_missing_source_context_in_action_coverage_is_not_treated_as_proof(self):
        self.data['action_coverage'] = {'start': '2024-11-27', 'end': '2024-12-02', 'complete': True, 'observed_at': OBSERVED}
        with self.assertRaises(MarketDataError): self.result()

    def test_complete_evidence_cannot_be_reused_with_a_dropped_action(self):
        self.data['actions'] = [r for r in self.data['actions'] if r['kind'] != 'split']
        with self.assertRaisesRegex(MarketDataError, 'ledger'):
            self.result()

    def test_adjusted_or_mismatched_endpoints_cannot_be_double_adjusted(self):
        for key, value in [('adjustment', 'split'), ('instrument_id', 'OTHER'), ('feed', 'other'), ('currency', 'EUR')]:
            original = self.data['bars'][-1][key]
            self.data['bars'][-1][key] = value
            with self.subTest(key=key), self.assertRaises(MarketDataError): self.result()
            self.data['bars'][-1][key] = original

    def test_absent_return_endpoint_is_unavailable_and_inputs_are_not_mutated(self):
        before = deepcopy(self.data)
        self.result()
        self.assertEqual(before, self.data)
        self.data['bars'].pop()
        with self.assertRaises(MarketDataError): self.result()
