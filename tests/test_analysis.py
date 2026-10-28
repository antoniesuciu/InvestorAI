"""Hand-calculated examples and financial/temporal regressions; all invented data."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from io import StringIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from investor_ai.analysis import analyze
from investor_ai.analysis_store import analyze_company
from investor_ai.cli import main
from investor_ai.collector import Collector
from investor_ai.financials import ANALYSIS_METRICS, METRICS, extract_facts
from investor_ai.sec import SecClient, SecError
from investor_ai.storage import Store, write_json
from test_collector import CIK, URL, FakeClient, fixtures

AS_OF = '2025-03-01T00:00:00+00:00'
ACC = '0000123456-25-000001'


def observation(metric, value, *, year=2024, quarter=None, accession=ACC,
                filed='2025-02-01', retrieved='2025-02-02T00:00:00Z',
                start=None, end=None, kind=None, concept=None, unit='USD'):
    instant = metric in {'assets', 'liabilities', 'cash', 'current_assets', 'current_liabilities', 'equity', 'noncurrent_debt'}
    end = end or f'{year}-12-31'
    start = None if instant else (start or f'{year}-01-01')
    kind = 'instant' if instant else (kind or 'annual')
    row = {'cik': CIK, 'metric': metric, 'concept': concept or ANALYSIS_METRICS[metric][0],
           'taxonomy': 'us-gaap', 'unit': unit, 'value': str(value), 'start': start, 'end': end,
           'period_type': kind, 'fiscal_year': year, 'fiscal_quarter': quarter,
           'fiscal_period': f'Q{quarter}' if quarter else 'FY', 'accession': accession,
           'form': '10-K' if kind == 'annual' or instant else '10-Q', 'filed': filed,
           'retrieved_at': retrieved, 'acceptance_datetime': None, 'public_available_at': None,
           'quality_flags': [], 'classification': 'reported', 'source_url': URL,
           'filing_url': f'https://www.sec.gov/Archives/edgar/data/123456/{accession}-index.html',
           'filing_fiscal_year': 2024, 'filing_fiscal_period': 'FY',
           'fiscal_label_sources': [{'accession': accession, 'filed': filed}]}
    row['record_id'] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    return row


def annual_example():
    data = {
        2023: {'revenue': 100, 'net_income': 10, 'operating_cash_flow': 20, 'capital_expenditure': 5,
               'gross_profit': 40, 'operating_income': 20, 'assets': 200, 'liabilities': 120,
               'current_assets': 80, 'current_liabilities': 40, 'equity': 80, 'noncurrent_debt': 30, 'cash': 20},
        2024: {'revenue': 120, 'net_income': 18, 'operating_cash_flow': 30, 'capital_expenditure': 8,
               'gross_profit': 54, 'operating_income': 24, 'assets': 240, 'liabilities': 140,
               'current_assets': 90, 'current_liabilities': 45, 'equity': 100, 'noncurrent_debt': 40, 'cash': 15},
    }
    return [observation(metric, value, year=year) for year, values in data.items() for metric, value in values.items()]


def quarter_example():
    rows = [observation('revenue', 60, start='2024-04-01', end='2024-06-30', kind='quarterly', quarter=2),
            observation('net_income', 6, start='2024-04-01', end='2024-06-30', kind='quarterly', quarter=2),
            observation('revenue', 50, year=2023, start='2023-04-01', end='2023-06-30', kind='quarterly', quarter=2),
            observation('net_income', 5, year=2023, start='2023-04-01', end='2023-06-30', kind='quarterly', quarter=2)]
    for metric, first, half in [('operating_cash_flow', 30, 70), ('capital_expenditure', 6, 15)]:
        rows += [observation(metric, first, start='2024-01-01', end='2024-03-31', kind='quarterly', quarter=1),
                 observation(metric, half, start='2024-01-01', end='2024-06-30', kind='year_to_date', quarter=2)]
    return rows


def period(report, end='2024-12-31'):
    return next(item for item in report['periods'] if item['period']['end'] == end)


class FormulaTests(unittest.TestCase):
    def setUp(self):
        self.rows = annual_example()

    def result(self):
        return period(analyze(self.rows, as_of=AS_OF))['metrics']

    def test_hand_calculated_growth_margins_cashflow_and_ratios(self):
        result = self.result()
        expected = {'revenue_growth_yoy_pct': '20.0', 'net_income_growth_yoy_pct': '80.0',
                    'net_margin_pct': '15.00', 'gross_margin_pct': '45.00', 'operating_margin_pct': '20.0',
                    'free_cash_flow': '22', 'current_ratio': '2', 'noncurrent_debt_to_equity': '0.4',
                    'net_income_change_yoy': '8'}
        for key, value in expected.items():
            with self.subTest(metric=key):
                self.assertEqual(Decimal(result[key]['value']), Decimal(value))
        self.assertEqual(Decimal(result['cash_to_current_liabilities']['value']), Decimal('0.3333333333333333333333333333333333'))
        self.assertEqual(result['free_cash_flow']['classification'], 'derived')
        self.assertEqual(result['revenue']['classification'], 'reported')

    def test_loss_margins_are_valid_but_negative_growth_base_is_not(self):
        for row in self.rows:
            if row['metric'] == 'net_income':
                row['value'] = '-10' if row['fiscal_year'] == 2023 else '-6'
        result = self.result()
        self.assertEqual(Decimal(result['net_margin_pct']['value']), Decimal('-5'))
        self.assertEqual(result['net_income_growth_yoy_pct']['status'], 'nonpositive_denominator')
        self.assertEqual(result['net_income_change_yoy']['value'], '4')

    def test_transition_from_profit_to_loss_is_explicit(self):
        next(row for row in self.rows if row['metric'] == 'net_income' and row['fiscal_year'] == 2024)['value'] = '-5'
        value = self.result()['net_income_growth_yoy_pct']
        self.assertEqual(Decimal(value['value']), Decimal('-150'))
        self.assertIn('negative_current_value', value['flags'])

    def test_zero_missing_and_negative_balance_are_distinct(self):
        self.rows = [row for row in self.rows if not (row['metric'] == 'capital_expenditure' and row['fiscal_year'] == 2024)]
        next(row for row in self.rows if row['metric'] == 'cash' and row['fiscal_year'] == 2024)['value'] = '0'
        next(row for row in self.rows if row['metric'] == 'equity' and row['fiscal_year'] == 2024)['value'] = '-1'
        result = self.result()
        self.assertIsNone(result['free_cash_flow']['value'])
        self.assertEqual(result['cash_to_current_liabilities']['value'], '0')
        self.assertEqual(result['noncurrent_debt_to_equity']['status'], 'nonpositive_denominator')

    def test_zero_revenue_or_current_liabilities_has_no_ratio(self):
        for row in self.rows:
            if row['fiscal_year'] == 2024 and row['metric'] in {'revenue', 'current_liabilities'}:
                row['value'] = '0'
        result = self.result()
        self.assertEqual(result['net_margin_pct']['status'], 'nonpositive_denominator')
        self.assertEqual(result['current_ratio']['status'], 'nonpositive_denominator')

    def test_capex_payment_sign_is_not_silently_reversed(self):
        next(row for row in self.rows if row['metric'] == 'capital_expenditure' and row['fiscal_year'] == 2024)['value'] = '-8'
        self.assertIn('negative_capex_payment', self.result()['free_cash_flow']['flags'])

    def test_conflicting_accounting_concepts_require_explicit_selection(self):
        self.rows.append(observation('revenue', 130, concept='Revenues'))
        self.assertEqual(self.result()['revenue']['status'], 'ambiguous_concept')
        chosen = analyze(self.rows, as_of=AS_OF, concepts={'revenue': ANALYSIS_METRICS['revenue'][0]})
        self.assertEqual(period(chosen)['metrics']['revenue']['value'], '120')

    def test_competing_values_are_not_arbitrarily_selected(self):
        self.rows.append(observation('revenue', 121))
        self.assertEqual(self.result()['revenue']['status'], 'conflicting_values')

    def test_growth_never_bridges_different_accounting_concepts(self):
        row = next(row for row in self.rows if row['metric'] == 'revenue' and row['fiscal_year'] == 2023)
        row['concept'] = 'Revenues'
        result = self.result()['revenue_growth_yoy_pct']
        self.assertIsNone(result['value'])
        self.assertIn('concept_change', result['flags'])

    def test_preserves_inputs_precision_and_decimal_context(self):
        before = deepcopy(self.rows)
        precision = getcontext().prec
        result = self.result()
        self.assertEqual(self.rows, before)
        self.assertEqual(getcontext().prec, precision)
        inputs = result['free_cash_flow']['inputs']
        self.assertEqual({row['value'] for row in inputs}, {'30', '8'})
        self.assertTrue(all(row['accession'] == ACC and row['filing_url'] and row['unit'] == 'USD' for row in inputs))

    def test_balance_ratios_use_their_own_coherent_filing_not_an_income_comparative(self):
        for row in self.rows:
            if row['start'] is None:
                row['accession'] = '0000123456-24-000001'
                row['filed'] = '2024-12-31'
        result = self.result()
        self.assertEqual(result['current_ratio']['value'], '2')
        self.assertEqual({r['accession'] for r in result['current_ratio']['inputs']}, {'0000123456-24-000001'})

    def test_zero_prior_earnings_has_no_percentage_but_keeps_absolute_change(self):
        next(row for row in self.rows if row['metric'] == 'net_income' and row['fiscal_year'] == 2023)['value'] = '0'
        result = self.result()
        self.assertEqual(result['net_income_growth_yoy_pct']['status'], 'nonpositive_denominator')
        self.assertEqual(result['net_income_change_yoy']['value'], '18')


class QuarterTests(unittest.TestCase):
    def test_third_quarter_is_nine_months_less_six_months(self):
        rows = []
        for metric, half, nine in [('operating_cash_flow', 70, 90), ('capital_expenditure', 15, 30)]:
            rows += [observation(metric, half, start='2024-01-01', end='2024-06-30', kind='year_to_date', quarter=2),
                     observation(metric, nine, start='2024-01-01', end='2024-09-30', kind='year_to_date', quarter=3)]
        result = period(analyze(rows, as_of=AS_OF, frequency='quarterly'), '2024-09-30')
        self.assertEqual(result['period']['start'], '2024-07-01')
        self.assertEqual(result['period']['fiscal_quarter'], 3)
        self.assertEqual(result['metrics']['free_cash_flow']['value'], '5')

    def test_directly_reported_second_quarter_cashflow_needs_no_subtraction(self):
        rows = [observation(metric, value, start='2024-04-01', end='2024-06-30', kind='quarterly', quarter=2)
                for metric, value in [('operating_cash_flow', 40), ('capital_expenditure', 9)]]
        result = period(analyze(rows, as_of=AS_OF, frequency='quarterly'), '2024-06-30')['metrics']
        self.assertEqual(result['free_cash_flow']['value'], '31')
        self.assertEqual(result['operating_cash_flow']['classification'], 'reported')

    def test_conflicting_cumulative_inputs_cannot_produce_a_unique_quarter(self):
        rows = quarter_example() + [observation('operating_cash_flow', 31, start='2024-01-01', end='2024-03-31', kind='quarterly', quarter=1)]
        result = period(analyze(rows, as_of=AS_OF, frequency='quarterly'), '2024-06-30')['metrics']
        self.assertEqual(result['operating_cash_flow']['status'], 'conflicting_values')
        self.assertIsNone(result['free_cash_flow']['value'])

    def test_same_filing_cumulative_to_quarter_conversion(self):
        report = analyze(quarter_example(), as_of=AS_OF, frequency='quarterly')
        result = period(report, '2024-06-30')['metrics']
        self.assertEqual(result['operating_cash_flow']['value'], '40')
        self.assertEqual(result['capital_expenditure']['value'], '9')
        self.assertEqual(result['free_cash_flow']['value'], '31')
        self.assertEqual(Decimal(result['revenue_growth_yoy_pct']['value']), Decimal('20'))
        self.assertEqual(len(result['operating_cash_flow']['inputs']), 2)
        self.assertEqual(result['operating_cash_flow']['formula'], 'cumulative_current - cumulative_previous')

    def test_cross_filing_quarter_subtraction_is_refused(self):
        rows = quarter_example()
        for row in rows:
            if row['end'] == '2024-03-31':
                row['accession'] = '0000123456-24-000002'
                row['filed'] = '2024-05-01'
                row['fiscal_label_sources'] = [{'accession': row['accession'], 'filed': row['filed']}]
        result = period(analyze(rows, as_of=AS_OF, frequency='quarterly'), '2024-06-30')['metrics']
        self.assertIsNone(result['free_cash_flow']['value'])
        self.assertIn('cross_filing_comparability_evidence_required', result['free_cash_flow']['flags'])

    def test_cumulative_start_currency_and_fiscal_year_must_match(self):
        for changed in ('start', 'unit', 'fiscal_year'):
            with self.subTest(changed=changed):
                rows = quarter_example()
                for row in rows:
                    if row['end'] == '2024-03-31':
                        row[changed] = {'start': '2024-01-02', 'unit': 'EUR', 'fiscal_year': 2023}[changed]
                result = period(analyze(rows, as_of=AS_OF, frequency='quarterly'), '2024-06-30')['metrics']
                self.assertIsNone(result['free_cash_flow']['value'])

    def test_unknown_fiscal_year_does_not_generate_a_quarter(self):
        rows = quarter_example()
        for row in rows:
            row['fiscal_year'] = None
        result = period(analyze(rows, as_of=AS_OF, frequency='quarterly'), '2024-06-30')['metrics']
        self.assertIsNone(result['free_cash_flow']['value'])
        self.assertIn('ambiguous_fiscal_period', result['revenue_growth_yoy_pct']['flags'])

    def test_fourth_quarter_is_annual_less_nine_months_when_in_same_filing(self):
        rows = []
        for metric, nine, annual in [('operating_cash_flow', 90, 130), ('capital_expenditure', 20, 35)]:
            rows += [observation(metric, nine, start='2024-01-01', end='2024-09-30', kind='year_to_date', quarter=3),
                     observation(metric, annual)]
        result = period(analyze(rows, as_of=AS_OF, frequency='quarterly'))
        self.assertEqual(result['period']['start'], '2024-10-01')
        self.assertEqual(result['period']['fiscal_quarter'], 4)
        self.assertEqual(result['metrics']['free_cash_flow']['value'], '25')

    def test_quarter_reported_and_derived_disagreement_is_visible(self):
        rows = quarter_example() + [observation('operating_cash_flow', 41, start='2024-04-01', end='2024-06-30', kind='quarterly', quarter=2)]
        result = period(analyze(rows, as_of=AS_OF, frequency='quarterly'), '2024-06-30')['metrics']
        self.assertEqual(result['operating_cash_flow']['status'], 'conflicting_values')

    def test_53_week_year_comparisons_use_fiscal_labels_and_actual_intervals(self):
        rows = [observation('revenue', 110, start='2023-01-01', end='2024-01-06', year=2023),
                observation('revenue', 100, start='2022-01-02', end='2022-12-31', year=2022)]
        result = period(analyze(rows, as_of=AS_OF), '2024-01-06')['metrics']
        self.assertEqual(Decimal(result['revenue_growth_yoy_pct']['value']), Decimal(10))
        self.assertIn('different_period_lengths', result['revenue_growth_yoy_pct']['flags'])

    def test_53_week_year_can_have_a_14_week_fourth_quarter(self):
        rows = [observation('operating_cash_flow', 90, year=2023, start='2023-01-01', end='2023-09-30', kind='year_to_date', quarter=3),
                observation('operating_cash_flow', 130, year=2023, start='2023-01-01', end='2024-01-06')]
        result = period(analyze(rows, as_of=AS_OF, frequency='quarterly'), '2024-01-06')
        self.assertEqual(result['period']['fiscal_year'], 2023)
        self.assertEqual(result['period']['start'], '2023-10-01')
        self.assertEqual(result['metrics']['operating_cash_flow']['value'], '40')


class TemporalTests(unittest.TestCase):
    def test_records_retrieved_after_cutoff_do_not_enter_any_calculation(self):
        report = analyze(annual_example(), as_of='2025-02-01T23:00:00Z')
        self.assertEqual(report['periods'], [])
        self.assertEqual(report['excluded_observations']['unavailable_at_cutoff'], 26)

    def test_future_restatement_is_excluded_then_replaces_cohort_when_available(self):
        original = annual_example()
        amended = deepcopy(original)
        for row in amended:
            row.update(accession='0000123456-25-000099', filed='2025-04-01', retrieved_at='2025-04-02T00:00:00Z')
            row['record_id'] += ':amended'
            if row['metric'] == 'revenue' and row['fiscal_year'] == 2024:
                row['value'] = '150'
        early = period(analyze(original + amended, as_of=AS_OF))['metrics']
        later = period(analyze(original + amended, as_of='2025-05-01T00:00:00Z'))['metrics']
        self.assertEqual(early['revenue']['value'], '120')
        self.assertEqual(later['revenue']['value'], '150')
        self.assertTrue(all(r['accession'].endswith('000099') for r in later['net_margin_pct']['inputs']))
        self.assertEqual(analyze(original + amended, as_of='2025-05-01T00:00:00Z'),
                         analyze(list(reversed(original + amended)), as_of='2025-05-01T00:00:00Z'))

    def test_partial_new_filing_keeps_reported_history_without_mixing_ratio_vintages(self):
        rows = annual_example() + [observation('revenue', 150, accession='0000123456-25-000099', filed='2025-04-01', retrieved='2025-04-02T00:00:00Z')]
        result = period(analyze(rows, as_of='2025-05-01T00:00:00Z'))['metrics']
        self.assertEqual(result['net_income']['value'], '18')
        self.assertIsNone(result['net_margin_pct']['value'])
        self.assertEqual(result['net_margin_pct']['status'], 'incompatible_revision_vintages')

    def test_same_day_filing_order_needs_acceptance_evidence(self):
        rows = annual_example() + [observation('revenue', 150, accession='0000123456-25-000002')]
        result = period(analyze(rows, as_of=AS_OF))
        self.assertEqual(result['period']['selection_status'], 'ambiguous_filing_order')
        self.assertIsNone(result['metrics']['revenue']['value'])

    def test_same_day_order_uses_acceptance_time_when_every_candidate_has_it(self):
        rows = annual_example()
        for row in rows:
            row['acceptance_datetime'] = '2025-02-01T16:00:00Z'
        later = observation('revenue', 150, accession='0000123456-25-000002')
        later['acceptance_datetime'] = '2025-02-01T17:00:00Z'
        rows.append(later)
        result = period(analyze(rows, as_of=AS_OF))
        self.assertEqual(result['period']['accession'], later['accession'])
        self.assertEqual(result['metrics']['revenue']['value'], '150')

    def test_accepted_after_cutoff_and_future_fiscal_evidence_are_excluded(self):
        rows = annual_example()
        rows[0]['acceptance_datetime'] = '2026-01-01T00:00:00Z'
        rows[1]['fiscal_label_sources'][0]['filed'] = '2026-01-01'
        report = analyze(rows, as_of=AS_OF)
        self.assertEqual(report['excluded_observations']['unavailable_at_cutoff'], len(rows))
        rows[0]['acceptance_datetime'] = None
        report = analyze(rows, as_of=AS_OF)
        self.assertEqual(report['excluded_observations']['future_fiscal_evidence'], 1)

    def test_invalid_missing_availability_and_nonfinite_values_are_excluded(self):
        rows = annual_example()
        del rows[0]['retrieved_at']
        rows[1]['value'] = 'NaN'
        rows[2]['value'] = 1.1
        report = analyze(rows, as_of=AS_OF)
        self.assertEqual(sum(report['excluded_observations'].values()), 3)

    def test_invalid_intervals_and_unknown_metrics_do_not_influence_selection(self):
        rows = annual_example()
        invalid = observation('revenue', 999, start='2025-01-02', end='2025-01-01')
        unknown = observation('assets', 999)
        unknown['metric'] = 'unsupported_balance'
        unknown['accession'] = '0000123456-25-999999'
        unknown['filed'] = '2025-02-28'
        report = analyze(rows + [invalid, unknown], as_of=AS_OF)
        self.assertEqual(report['excluded_observations']['invalid_period'], 1)
        self.assertEqual(report['excluded_observations']['unsupported_metric'], 1)
        self.assertEqual(period(report)['metrics']['current_ratio']['value'], '2')

    def test_mixed_companies_naive_cutoffs_and_invalid_config_are_rejected(self):
        rows = annual_example()
        other = deepcopy(rows[0])
        other['cik'] = '0000000002'
        for kwargs in ({'as_of': '2025-03-01T00:00:00'}, {'frequency': 'monthly'}, {'concepts': {'foo': 'Assets'}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                analyze(rows, **kwargs)
        with self.assertRaises(ValueError):
            analyze(rows + [other], as_of=AS_OF)


class SnapshotAndCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.client = FakeClient()
        Collector(self.client, self.store).collect(['SYNTH'])

    def test_analysis_of_collected_raw_data_never_requests_network_or_changes_snapshot(self):
        manifest = self.store.latest(CIK)
        with patch.object(SecClient, 'get', side_effect=AssertionError('network forbidden')) as get:
            report = analyze_company(self.store, 'SYNTH')
        self.assertGreater(len(report['periods']), 0)
        self.assertEqual(get.call_count, 0)
        self.assertEqual(self.store.latest(CIK), manifest)
        self.assertEqual(report['snapshot']['extractor_version'], '3')

    def test_cutoff_before_collection_is_refused_even_for_old_filing_dates(self):
        with self.assertRaises(SecError):
            analyze_company(self.store, 'SYNTH', as_of='2024-12-31')

    def test_cli_json_and_trends_are_offline_and_expose_unavailable_results(self):
        for command in ('analyze', 'trends'):
            output = StringIO()
            with patch.object(SecClient, 'get', side_effect=AssertionError('network forbidden')), redirect_stdout(output):
                code = main(['--data-dir', self.temp.name, command, 'SYNTH', '--json', '--limit', '0'])
            self.assertEqual(code, 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report['periods'])
            self.assertEqual(report['availability_policy'], 'observed_by_cutoff')
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(['--data-dir', self.temp.name, 'trends', 'SYNTH']), 0)
        self.assertIn('NA[', output.getvalue())

    def test_cli_historical_failure_and_invalid_concept_are_explicit(self):
        for options in (['--as-of', '2024-01-01'], ['--concept', 'revenue=Unsupported'], ['--limit', '-1']):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(['--data-dir', self.temp.name, 'analyze', 'SYNTH', *options]), 1)

    def test_auxiliary_concepts_are_optional_without_changing_collector_default(self):
        facts, submissions = fixtures()
        base = extract_facts(facts, submissions, retrieved_at='2025-01-01T00:00:00Z', source_url=URL)
        facts['facts']['us-gaap']['AssetsCurrent'] = {'units': {'USD': [dict(end='2023-09-30', val=75, accn='0000123456-23-000001', fy=2023, fp='FY', form='10-K', filed='2023-11-01')]}}
        default = extract_facts(facts, submissions, retrieved_at='2025-01-01T00:00:00Z', source_url=URL)
        extended = extract_facts(facts, submissions, retrieved_at='2025-01-01T00:00:00Z', source_url=URL, metrics=ANALYSIS_METRICS)
        self.assertEqual(default, base)
        self.assertIn('current_assets', {r['metric'] for r in extended})
        self.assertNotIn('current_assets', METRICS)

    def historical_store(self):
        store = Store(Path(self.temp.name) / 'history')
        client = FakeClient()
        original_get = client.get
        observed = ['2025-01-01T00:00:00+00:00']
        def dated_get(url):
            return replace(original_get(url), retrieved_at=observed[0])
        client.get = dated_get
        with patch('investor_ai.storage.utc_now', return_value=observed[0]):
            Collector(client, store).collect(['SYNTH'])
        first = store.latest(CIK)
        observed[0] = '2025-02-01T00:00:00+00:00'
        with patch('investor_ai.storage.utc_now', return_value=observed[0]):
            Collector(client, store).collect(['SYNTH'], refresh=True)
        return store, first, store.latest(CIK)

    def test_adapter_selects_historical_snapshot_and_forbids_future_override(self):
        store, first, second = self.historical_store()
        report = analyze_company(store, 'SYNTH', as_of='2025-01-15')
        self.assertEqual(report['snapshot']['snapshot_id'], first['snapshot_id'])
        self.assertEqual(analyze_company(store, 'SYNTH', as_of='2025-02-15')['snapshot']['snapshot_id'], second['snapshot_id'])
        with self.assertRaises(SecError):
            analyze_company(store, 'SYNTH', as_of='2025-01-15', snapshot_id=second['snapshot_id'])

    def test_equal_snapshot_completion_times_require_explicit_selection(self):
        store, first, second = self.historical_store()
        path = store.root / 'companies' / CIK / 'snapshots' / first['snapshot_id'] / 'manifest.json'
        first['created_at'] = second['created_at']
        write_json(path, first)
        with self.assertRaises(SecError):
            analyze_company(store, 'SYNTH', as_of='2025-03-01')
        report = analyze_company(store, 'SYNTH', as_of='2025-03-01', snapshot_id=first['snapshot_id'])
        self.assertEqual(report['snapshot']['snapshot_id'], first['snapshot_id'])
