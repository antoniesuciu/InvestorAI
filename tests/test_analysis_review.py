"""Realistic filing-vintage reproductions, with invented observations only."""

from contextlib import redirect_stdout
from copy import deepcopy
from decimal import Decimal
from io import StringIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from investor_ai.analysis import analyze
from investor_ai.analysis_evidence import fact_reference, validate_evidence, CHECKS
from investor_ai.analysis import cutoff
from investor_ai.analysis_store import analyze_company
from investor_ai.collector import Collector
from investor_ai.cli import main
from investor_ai.sec import SecClient, SecError
from investor_ai.storage import Store
from test_collector import FakeClient
from test_analysis import observation, annual_example, quarter_example, period, AS_OF, ACC


def filing(focus_year, accession, filed, values):
    result = []
    for year, metrics in values.items():
        for metric, value in metrics.items():
            row = observation(metric, value, year=year, accession=accession, filed=filed,
                              retrieved=filed + 'T20:00:00Z')
            row.update(filing_fiscal_year=focus_year, filing_report_date=f'{focus_year}-12-31',
                       fiscal_label_basis='filing_report_period' if focus_year == year else 'matching_reported_period')
            result.append(row)
    return result


class HistoricalStatementTests(unittest.TestCase):
    def test_consecutive_quarterly_comparatives_preserve_older_growth_without_stale_fallback(self):
        rows = []
        values = {2021: 80, 2022: 100, 2023: 120, 2024: 150}
        for focus in (2022, 2023, 2024):
            accession = f'0000123456-{focus % 100:02}-000001'
            for year in (focus - 1, focus):
                row = observation('revenue', values[year], year=year, quarter=2, kind='quarterly',
                                  start=f'{year}-04-01', end=f'{year}-06-30', accession=accession,
                                  filed=f'{focus}-08-01', retrieved=f'{focus}-08-02T00:00:00Z')
                row.update(filing_fiscal_year=focus, filing_fiscal_period='Q2', filing_report_date=f'{focus}-06-30')
                rows.append(row)
        result = period(analyze(rows, as_of=AS_OF, frequency='quarterly'), '2022-06-30')['metrics']['revenue_growth_yoy_pct']
        self.assertEqual(Decimal(result['value']), Decimal(25))
        self.assertEqual({r['accession'] for r in result['inputs']}, {'0000123456-22-000001'})
        next(r for r in rows if r['accession'] == '0000123456-23-000001' and r['fiscal_year'] == 2022)['value'] = '110'
        revised = period(analyze(rows, as_of=AS_OF, frequency='quarterly'), '2022-06-30')['metrics']['revenue_growth_yoy_pct']
        self.assertIsNone(revised['value'])
        self.assertEqual(revised['status'], 'incompatible_revision_vintages')

    def test_documented_interim_balance_revision_is_used_without_claiming_it_is_audited(self):
        rows = annual_example()
        old_asset = next(row for row in rows if row['metric'] == 'assets' and row['fiscal_year'] == 2024)
        old_equity = next(row for row in rows if row['metric'] == 'equity' and row['fiscal_year'] == 2024)
        updated = []
        for metric, value in [('assets', 250), ('liabilities', 140), ('equity', 110), ('noncurrent_debt', 40)]:
            row = observation(metric, value, accession='0000123456-25-000010', filed='2025-02-15', retrieved='2025-02-16T00:00:00Z')
            row.update(form='10-Q', filing_report_date='2025-03-31', filing_fiscal_period='Q1')
            updated.append(row)
        links = [proof(old_asset, updated[0], kind='balance_revision', verified='2025-02-20T00:00:00Z'),
                 proof(old_equity, updated[2], kind='balance_revision', verified='2025-02-20T00:00:00Z')]
        result = period(analyze(rows + updated, as_of=AS_OF, evidence=links))['metrics']
        self.assertEqual(Decimal(result['liabilities_to_assets']['value']), Decimal('0.56'))
        self.assertEqual({r['form'] for r in result['liabilities_to_assets']['inputs']}, {'10-Q'})
        self.assertTrue(result['liabilities_to_assets']['comparability_evidence'])

    def test_unreviewed_changed_interim_balance_is_not_silently_ignored_or_adopted(self):
        rows = annual_example()
        row = observation('assets', 250, accession='0000123456-25-000010', filed='2025-02-15', retrieved='2025-02-16T00:00:00Z')
        row.update(form='10-Q', filing_report_date='2025-03-31')
        metrics = period(analyze(rows + [row], as_of=AS_OF))['metrics']
        self.assertIsNone(metrics['assets']['value'])
        self.assertEqual(metrics['assets']['status'], 'unreconciled_interim_revision')

    def test_three_consecutive_annual_filings_preserve_older_yoy_pair(self):
        rows = filing(2023, '0000123456-24-000001', '2024-02-01', {
            2022: {'revenue': 90, 'net_income': 9}, 2023: {'revenue': 100, 'net_income': 10}})
        rows += filing(2024, '0000123456-25-000001', '2025-02-01', {
            2023: {'revenue': 100, 'net_income': 10}, 2024: {'revenue': 120, 'net_income': 18}})
        rows += filing(2025, '0000123456-26-000001', '2026-02-01', {
            2023: {'revenue': 100, 'net_income': 10}, 2024: {'revenue': 120, 'net_income': 18}, 2025: {'revenue': 150, 'net_income': 20}})
        report = analyze(rows, as_of='2026-03-01')
        metrics = period(report, '2023-12-31')['metrics']
        self.assertIsNotNone(metrics['revenue_growth_yoy_pct']['value'])
        self.assertAlmostEqual(float(metrics['revenue_growth_yoy_pct']['value']), 100 / 9)
        self.assertEqual({row['accession'] for row in metrics['revenue_growth_yoy_pct']['inputs']}, {'0000123456-24-000001'})

    def test_changed_partial_amendment_keeps_unaffected_reported_values_but_blocks_unreconciled_margin(self):
        rows = annual_example()
        changed = observation('net_income', 20, accession='0000123456-25-000099', filed='2025-02-15', retrieved='2025-02-16T00:00:00Z')
        changed['form'] = '10-K/A'
        result = period(analyze(rows + [changed], as_of=AS_OF))['metrics']
        self.assertEqual(result['revenue']['value'], '120')
        self.assertEqual(result['net_income']['value'], '20')
        self.assertIsNone(result['net_margin_pct']['value'])
        self.assertIn('incompatible_revision_vintages', result['net_margin_pct']['status'])

    def test_unchanged_partial_amendment_does_not_destroy_complete_statement(self):
        rows = annual_example()
        unchanged = observation('net_income', 18, accession='0000123456-25-000099', filed='2025-02-15', retrieved='2025-02-16T00:00:00Z')
        unchanged['form'] = '10-K/A'
        result = period(analyze(rows + [unchanged], as_of=AS_OF))['metrics']
        self.assertEqual(Decimal(result['net_margin_pct']['value']), Decimal(15))
        self.assertEqual(Decimal(result['net_income_growth_yoy_pct']['value']), Decimal(80))

    def test_known_later_revision_cannot_fall_back_to_obsolete_yoy_pair(self):
        rows = annual_example()
        revised = filing(2025, '0000123456-26-000001', '2026-02-01', {
            2024: {'revenue': 150, 'net_income': 20}})
        metrics = period(analyze(rows + revised, as_of='2026-03-01'))['metrics']
        self.assertEqual(metrics['revenue']['value'], '150')
        self.assertIsNone(metrics['revenue_growth_yoy_pct']['value'])

    def test_annual_balances_prefer_complete_10k_over_unchanged_10q_comparative(self):
        rows = annual_example()
        for row in rows:
            if row['start'] is None:
                row.update(filing_report_date='2024-12-31', filing_fiscal_period='FY')
        partial = observation('assets', 240, accession='0000123456-25-000010', filed='2025-02-15', retrieved='2025-02-16T00:00:00Z')
        partial.update(form='10-Q', filing_report_date='2025-03-31', filing_fiscal_period='Q1')
        result = period(analyze(rows + [partial], as_of=AS_OF))
        self.assertEqual(result['metrics']['current_ratio']['value'], '2')
        self.assertEqual({row['form'] for row in result['metrics']['current_ratio']['inputs']}, {'10-K'})
        self.assertEqual(result['period']['balance_accession'], ACC)

    def test_missing_debt_is_not_synthesized_as_zero(self):
        rows = [r for r in annual_example() if r['metric'] != 'noncurrent_debt']
        result = period(analyze(rows, as_of=AS_OF))['metrics']
        self.assertIsNone(result['noncurrent_debt']['value'])
        self.assertIsNone(result['noncurrent_debt_to_equity']['value'])

    def test_changed_same_day_interim_cannot_silently_leave_an_audited_value(self):
        rows = annual_example()
        for row in rows:
            row['acceptance_datetime'] = '2025-02-01T10:00:00Z'
        changed = observation('assets', 250, accession='0000123456-25-000002')
        changed.update(form='10-Q', acceptance_datetime='2025-02-01T18:00:00Z')
        value = period(analyze(rows + [changed], as_of=AS_OF))['metrics']['assets']
        self.assertIsNone(value['value'])
        self.assertEqual(value['status'], 'unreconciled_interim_revision')


class IntradayReviewTests(unittest.TestCase):
    def test_future_observed_acceptance_metadata_does_not_resolve_an_earlier_view(self):
        rows = annual_example()
        for row in rows:
            row.update(filed='2025-02-01', retrieved_at='2025-02-01T09:00:00Z')
        future = deepcopy(rows[0])
        future.update(retrieved_at='2025-02-02T00:00:00Z', acceptance_datetime='2025-02-01T18:00:00Z', record_id='future-metadata')
        early = analyze(rows + [future], as_of='2025-02-01T12:00:00Z')
        self.assertTrue(early['periods'])

    def test_known_accession_acceptance_is_not_bypassed_by_rows_missing_it(self):
        rows = annual_example()
        for row in rows:
            row.update(filed='2025-02-01', retrieved_at='2025-02-01T09:00:00Z')
        rows[0]['acceptance_datetime'] = '2025-02-01T18:00:00Z'
        early = analyze(rows, as_of='2025-02-01T12:00:00Z')
        self.assertFalse(early['periods'])

    def test_missing_acceptance_does_not_override_actual_observation_bound(self):
        rows = annual_example()
        for row in rows:
            row.update(filed='2025-02-01', retrieved_at='2025-02-01T09:00:00Z')
        self.assertFalse(analyze(rows, as_of='2025-02-01T08:00:00Z')['periods'])
        self.assertTrue(analyze(rows, as_of='2025-02-01T12:00:00Z')['periods'])


def proof(first, second, *, verified='2025-02-10T00:00:00Z', kind='cashflow_comparability'):
    return {'id': first['record_id'] + ':' + second['record_id'], 'kind': kind,
            'facts': [fact_reference(first), fact_reference(second)], 'verified_at': verified,
            'basis': 'Invented fixture: original values and unchanged reporting basis independently reviewed.',
            'checks': sorted(CHECKS), 'documents': [
                {'accession': acc, 'url': f'https://www.sec.gov/Archives/edgar/data/123456/{acc.replace("-", "")}/synthetic.htm',
                 'path': f'verification/{acc}.html', 'sha256': 'a' * 64, 'retrieved_at': '2025-02-02T00:00:00Z'}
                for acc in sorted({first['accession'], second['accession']})]}


class CrossFilingCashflowTests(unittest.TestCase):
    def setup_pair(self, q=2):
        ends = {1: '2024-03-31', 2: '2024-06-30', 3: '2024-09-30', 4: '2024-12-31'}
        rows, links = [], []
        for metric, smaller, larger in [('operating_cash_flow', 30, 70), ('capital_expenditure', 6, 15)]:
            short = observation(metric, smaller, start='2024-01-01', end=ends[q - 1],
                                kind='quarterly' if q == 2 else 'year_to_date', quarter=q - 1,
                                accession='0000123456-24-000001', filed='2024-11-01', retrieved='2025-02-02T00:00:00Z')
            long = observation(metric, larger, start='2024-01-01', end=ends[q],
                               kind='annual' if q == 4 else 'year_to_date', quarter=None if q == 4 else q,
                               accession=ACC, filed='2025-02-01', retrieved='2025-02-02T00:00:00Z')
            rows += [short, long]
            links.append(proof(long, short))
        return rows, links, ends[q]

    def test_q2_q3_q4_across_accessions_require_exact_reviewed_evidence(self):
        for q in (2, 3, 4):
            with self.subTest(quarter=q):
                rows, links, end = self.setup_pair(q)
                report = analyze(rows, as_of=AS_OF, frequency='quarterly', evidence=links)
                metrics = period(report, end)['metrics']
                self.assertEqual(metrics['operating_cash_flow']['value'], '40')
                self.assertEqual(metrics['capital_expenditure']['value'], '9')
                self.assertEqual(metrics['free_cash_flow']['value'], '31')
                self.assertEqual({r['accession'] for r in metrics['free_cash_flow']['inputs']}, {ACC, '0000123456-24-000001'})
                self.assertEqual(len(metrics['free_cash_flow']['comparability_evidence']), 2)

    def test_cross_filing_without_review_does_not_subtract(self):
        rows, _, end = self.setup_pair()
        report = analyze(rows, as_of=AS_OF, frequency='quarterly')
        self.assertFalse(any(item['period']['end'] == end for item in report['periods']))

    def test_future_review_cannot_supply_earlier_historical_result(self):
        rows, links, end = self.setup_pair()
        for link in links:
            link['verified_at'] = '2025-04-01T00:00:00Z'
        report = analyze(rows, as_of=AS_OF, frequency='quarterly', evidence=links)
        self.assertFalse(any(item['period']['end'] == end for item in report['periods']))

    def test_changed_shorter_revision_invalidates_older_comparability_link(self):
        rows, links, end = self.setup_pair()
        revised = deepcopy(rows[0])
        revised.update(value='35', accession='0000123456-25-000099', filed='2025-02-20',
                       retrieved_at='2025-02-21T00:00:00Z', record_id=rows[0]['record_id'] + ':revision')
        report = analyze(rows + [revised], as_of=AS_OF, frequency='quarterly', evidence=links)
        self.assertIsNone(period(report, end)['metrics']['free_cash_flow']['value'])

    def test_unresolved_fiscal_label_does_not_hide_a_known_cumulative_revision(self):
        rows, links, end = self.setup_pair()
        revised = deepcopy(rows[0])
        revised.update(value='35', fiscal_year=None, fiscal_quarter=None, fiscal_period=None,
                       accession='0000123456-25-000099', filed='2025-02-20',
                       retrieved_at='2025-02-21T00:00:00Z', record_id='unresolved-revision')
        report = analyze(rows + [revised], as_of=AS_OF, frequency='quarterly', evidence=links)
        self.assertIsNone(period(report, end)['metrics']['free_cash_flow']['value'])

    def test_evidence_cannot_override_fiscal_start_or_concept_incompatibility(self):
        for key, value in [('start', '2024-01-02'), ('unit', 'EUR'), ('fiscal_year', 2023),
                           ('concept', 'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations')]:
            with self.subTest(field=key):
                rows, _, end = self.setup_pair()
                rows[0][key] = value
                links = [proof(rows[1], rows[0]), proof(rows[3], rows[2])]
                report = analyze(rows, as_of=AS_OF, frequency='quarterly', evidence=links)
                matching = [item for item in report['periods'] if item['period']['end'] == end]
                self.assertTrue(not matching or matching[0]['metrics']['free_cash_flow']['value'] is None)

    def test_reconciled_revisions_use_new_exact_pairs_and_preserve_both_sources(self):
        old, _, end = self.setup_pair()
        revised, links = deepcopy(old), []
        for i, row in enumerate(revised):
            row.update(value=str(Decimal(row['value']) + (5 if i % 2 == 0 else 7)),
                       accession=f'0000123456-25-00010{i % 2}',
                       filed='2025-02-15' if i % 2 == 0 else '2025-02-20',
                       retrieved_at='2025-02-21T00:00:00Z', record_id=row['record_id'] + ':revised')
        links = [proof(revised[1], revised[0], verified='2025-02-22T00:00:00Z'),
                 proof(revised[3], revised[2], verified='2025-02-22T00:00:00Z')]
        result = period(analyze(old + revised, as_of=AS_OF, frequency='quarterly', evidence=links), end)['metrics']
        self.assertEqual(result['operating_cash_flow']['value'], '42')
        self.assertEqual(result['capital_expenditure']['value'], '11')
        self.assertEqual(result['free_cash_flow']['value'], '31')
        self.assertEqual({r['accession'] for r in result['free_cash_flow']['inputs']},
                         {'0000123456-25-000100', '0000123456-25-000101'})
        self.assertEqual(analyze(old + revised, as_of=AS_OF, frequency='quarterly', evidence=links),
                         analyze(list(reversed(old + revised)), as_of=AS_OF, frequency='quarterly', evidence=list(reversed(links))))

    def test_same_day_cross_filing_order_requires_both_acceptance_times(self):
        rows, links, end = self.setup_pair()
        for row in rows:
            row['filed'] = '2025-02-01'
        report = analyze(rows, as_of=AS_OF, frequency='quarterly', evidence=links)
        self.assertFalse(any(p['period']['end'] == end for p in report['periods']))
        for i, row in enumerate(rows):
            row['acceptance_datetime'] = f'2025-02-01T{10 if i % 2 == 0 else 18}:00:00Z'
        self.assertEqual(period(analyze(rows, as_of=AS_OF, frequency='quarterly', evidence=links), end)['metrics']['free_cash_flow']['value'], '31')


class EvidenceValidationTests(unittest.TestCase):
    def test_duplicate_evidence_ids_are_rejected_independently_of_entry_order(self):
        rows, links, _ = CrossFilingCashflowTests().setup_pair()
        links[1]['id'] = links[0]['id']
        valid, diagnostics = validate_evidence(links, cutoff(AS_OF))
        self.assertFalse(valid)
        self.assertEqual((valid, diagnostics), validate_evidence(list(reversed(links)), cutoff(AS_OF)))

    def test_malformed_timestamps_are_diagnostics_not_crashes(self):
        _, links, _ = CrossFilingCashflowTests().setup_pair()
        links[0]['verified_at'] = 123
        valid, diagnostics = validate_evidence(links[:1], cutoff(AS_OF))
        self.assertFalse(valid)
        self.assertEqual(diagnostics[0]['status'], 'invalid_comparability_evidence')


class DistressedStatementTests(unittest.TestCase):
    def test_losses_negative_equity_and_unclassified_balance_sheet_remain_explicit(self):
        rows = annual_example()
        values = {'revenue': 100, 'net_income': -10, 'operating_income': -8,
                  'operating_cash_flow': -12, 'capital_expenditure': 3,
                  'assets': 80, 'liabilities': 100, 'equity': -20}
        rows = [r for r in rows if r['metric'] not in {'current_assets', 'current_liabilities'}]
        for row in rows:
            if row['fiscal_year'] == 2024 and row['metric'] in values:
                row['value'] = str(values[row['metric']])
        metrics = period(analyze(rows, as_of=AS_OF))['metrics']
        for key, expected in [('net_margin_pct', -10), ('operating_margin_pct', -8),
                              ('free_cash_flow', -15), ('liabilities_to_assets', '1.25')]:
            self.assertEqual(Decimal(metrics[key]['value']), Decimal(expected))
        self.assertEqual(metrics['noncurrent_debt_to_equity']['status'], 'nonpositive_denominator')
        self.assertIsNone(metrics['current_ratio']['value'])
        self.assertIsNone(metrics['cash_to_current_liabilities']['value'])


class CLIDiagnosticReviewTests(unittest.TestCase):
    def render(self, rows, command):
        report = analyze(rows, as_of=AS_OF)
        report['snapshot'] = {'name': 'Synthetic reviewed example'}
        output = StringIO()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(output), \
                patch('investor_ai.cli.analyze_company', return_value=report):
            code = main(['--data-dir', folder, command, 'SYNTH', '--limit', '0'])
        self.assertEqual(code, 0)
        return output.getvalue()

    def test_text_cells_explain_underlying_unavailable_input_flags(self):
        rows = annual_example() + [observation('revenue', 130, concept='Revenues')]
        for command in ('analyze', 'trends'):
            with self.subTest(command=command):
                output = self.render(rows, command)
                self.assertIn('NA[unavailable_inputs; ambiguous_concept]', output)

    def test_available_growth_displays_its_loss_transition_flag(self):
        rows = annual_example()
        next(r for r in rows if r['metric'] == 'net_income' and r['fiscal_year'] == 2024)['value'] = '-5'
        for command in ('analyze', 'trends'):
            with self.subTest(command=command):
                output = self.render(rows, command)
                self.assertRegex(output, r'-150\.0000 \[[^\]]*negative_current_value[^\]]*\]')
                self.assertIn('different_period_lengths', output)  # 2024 is a leap year.


class EvidenceStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        Collector(FakeClient(), self.store).collect(['SYNTH'])
        _, self.entries, _ = CrossFilingCashflowTests().setup_pair()
        body = b'Synthetic original filing document, not downloaded data.'
        for entry in self.entries:
            for ref in entry['documents']:
                path = self.store.root / ref['path']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
                ref['sha256'] = hashlib.sha256(body).hexdigest()
        self.path = self.store.root / 'analysis_evidence.json'

    def save(self):
        self.path.write_text(json.dumps({'schema_version': 1, 'entries': self.entries}))

    def test_evidence_loading_checks_original_documents_without_network(self):
        self.save()
        with patch.object(SecClient, 'get', side_effect=AssertionError('network forbidden')):
            report = analyze_company(self.store, 'SYNTH')
        self.assertEqual(report['evidence_file'], str(self.path))
        self.assertFalse(report['evidence_diagnostics'])

    def test_changed_original_document_is_refused(self):
        self.save()
        (self.store.root / self.entries[0]['documents'][0]['path']).write_text('corrupt')
        with self.assertRaisesRegex(SecError, 'checksum'):
            analyze_company(self.store, 'SYNTH')

    def test_evidence_path_cannot_escape_store(self):
        self.entries[0]['documents'][0]['path'] = '../outside.html'
        self.save()
        with self.assertRaisesRegex(SecError, 'escapes'):
            analyze_company(self.store, 'SYNTH')

    def test_future_evidence_does_not_read_later_files(self):
        for entry in self.entries:
            entry['verified_at'] = '9999-01-01T00:00:00Z'
            for ref in entry['documents']:
                ref['path'] = 'does-not-exist.html'
        self.save()
        report = analyze_company(self.store, 'SYNTH')
        self.assertEqual({d['status'] for d in report['evidence_diagnostics']}, {'evidence_unavailable_at_cutoff'})

    def test_invalid_registry_schema_and_explicit_missing_file_are_errors(self):
        self.path.write_text('{"schema_version": 999, "entries": []}')
        with self.assertRaisesRegex(SecError, 'schema_version'):
            analyze_company(self.store, 'SYNTH')
        with self.assertRaisesRegex(SecError, 'does not exist'):
            analyze_company(self.store, 'SYNTH', evidence_path=Path(self.temp.name) / 'missing.json')
