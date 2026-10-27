"""Independent reproductions of review findings, with invented local data only."""

from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from investor_ai.cli import main
from investor_ai.collector import Collector, TICKERS_URL
from investor_ai.financials import extract_facts, filing_index
from investor_ai.sec import Response, SecError, is_cik
from investor_ai.storage import Store, write_json
from test_collector import CIK, URL, FakeClient, fixtures


class FinancialReviewTests(unittest.TestCase):
    def setUp(self):
        self.facts, self.submissions = fixtures()

    def parse(self):
        return extract_facts(self.facts, self.submissions, retrieved_at="2025-01-01T00:00:00Z", source_url=URL)

    def test_comparative_revision_recovers_fiscal_year_from_exact_reported_period(self):
        revised = next(r for r in self.parse() if r['metric'] == 'revenue' and r['value'] == '1100')
        self.assertEqual(revised['fiscal_year'], 2023)
        self.assertEqual(revised['filing_fiscal_year'], 2024)
        self.assertEqual(revised['filed'], '2024-11-01')
        self.assertEqual(revised['accession'], '0000123456-24-000001')
        self.assertEqual(revised['fiscal_label_basis'], 'matching_reported_period')
        self.assertIn('0000123456-23-000001', {s['accession'] for s in revised['fiscal_label_sources']})

    def test_same_end_but_different_duration_does_not_inherit_fiscal_year(self):
        rows = self.facts['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax']['units']['USD']
        stub = deepcopy(next(r for r in rows if r['val'] == 1100))
        stub.update(start='2023-01-01', val=777)
        rows.append(stub)
        row = next(r for r in self.parse() if r['value'] == '777')
        self.assertIsNone(row['fiscal_year'])

    def test_future_filing_cannot_supply_fiscal_labels_to_an_earlier_observation(self):
        rows = self.facts['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax']['units']['USD']
        earlier = deepcopy(next(row for row in rows if row['val'] == 1000))
        earlier.update(val=999, filed='2023-10-15', accn='0000123456-23-000099', fy=None, fp=None, form='8-K')
        rows.append(earlier)
        row = next(row for row in self.parse() if row['value'] == '999')
        self.assertIsNone(row['fiscal_year'])
        self.assertEqual(row['fiscal_label_sources'], [])

    def test_year_end_balance_is_not_labeled_as_interim_fourth_quarter(self):
        row = next(r for r in self.parse() if r['metric'] == 'assets')
        self.assertEqual(row['fiscal_year'], 2023)
        self.assertIsNone(row['fiscal_quarter'])
        self.assertEqual(row['fiscal_period'], 'FY')
        self.assertEqual(row['period_type'], 'instant')

    def test_cumulative_nine_month_flow_has_no_standalone_quarter_number(self):
        rows = self.parse()
        ytd = next(r for r in rows if r['metric'] == 'operating_cash_flow' and r['value'] == '75')
        q1 = next(r for r in rows if r['metric'] == 'operating_cash_flow' and r['value'] == '20')
        annual = next(r for r in rows if r['metric'] == 'operating_cash_flow' and r['value'] == '90')
        self.assertIsNone(ytd['fiscal_quarter'])
        self.assertEqual(ytd['fiscal_period'], 'Q3')
        self.assertEqual(q1['fiscal_quarter'], 1)
        self.assertEqual(q1['period_type'], 'quarterly')
        self.assertTrue(all(r['is_year_to_date'] for r in (q1, ytd, annual)))

    def test_near_report_date_mismatch_remains_unknown_without_evidence(self):
        self.submissions['filings']['recent']['reportDate'][0] = '2023-10-02'
        row = next(r for r in self.parse() if r['metric'] == 'assets')
        self.assertIsNone(row['fiscal_year'])

    def test_comparative_quarter_uses_exact_original_period_not_later_filing_focus(self):
        table = self.submissions['filings']['recent']
        original = {'accessionNumber': '0000123456-23-000002', 'filingDate': '2023-02-01',
                    'reportDate': '2022-12-31', 'acceptanceDateTime': '2023-02-01T20:00:00Z',
                    'form': '10-Q', 'primaryDocument': 'synthetic-quarter.htm'}
        for key in table:
            table[key].append(original[key])
        values = self.facts['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax']['units']['USD']
        original_fact = deepcopy(next(row for row in values if row['val'] == 250))
        original_fact.update(val=240, accn=original['accessionNumber'], fy=2023, filed=original['filingDate'])
        values.append(original_fact)
        revised = next(row for row in self.parse() if row['value'] == '250')
        self.assertEqual((revised['fiscal_year'], revised['fiscal_quarter']), (2023, 1))
        self.assertEqual(revised['filing_fiscal_year'], 2024)

    def test_conflicting_fiscal_anchors_stay_unknown_and_flagged(self):
        self.submissions['filings']['recent']['reportDate'][1] = '2023-09-30'
        revised = next(r for r in self.parse() if r['value'] == '1100')
        self.assertIsNone(revised['fiscal_year'])
        self.assertIn('ambiguous_fiscal_period', revised['quality_flags'])

    def test_conflicting_fiscal_evidence_also_flags_unanchored_later_comparatives(self):
        self.submissions['filings']['recent']['reportDate'][1] = '2023-09-30'
        values = self.facts['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax']['units']['USD']
        comparative = deepcopy(next(row for row in values if row['val'] == 1100))
        comparative.update(accn='0000123456-24-000003', fp='Q3', val=1300)
        self.facts['facts']['us-gaap']['SalesRevenueNet'] = {'units': {'USD': [comparative]}}
        self.facts['facts']['us-gaap'] = {key: value for key, value in self.facts['facts']['us-gaap'].items()
                                        if key in {'RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'}}
        row = next(row for row in self.parse() if row['value'] == '1300')
        self.assertIsNone(row['fiscal_year'])
        self.assertIsNone(row['fiscal_label_basis'])
        self.assertIn('ambiguous_fiscal_period', row['quality_flags'])

    def test_amendment_matches_its_own_historical_report_period(self):
        table = self.submissions['filings']['recent']
        table['reportDate'][1] = '2023-09-30'
        table['form'][1] = '10-K/A'
        rows = self.facts['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax']['units']['USD']
        revised = next(row for row in rows if row['val'] == 1100)
        revised.update(fy=2023, form='10-K/A')
        parsed = self.parse()
        row = next(row for row in parsed if row['value'] == '1100')
        self.assertEqual(row['fiscal_year'], 2023)
        self.assertEqual(row['form'], '10-K/A')
        eligible = [r for r in parsed if r['filed'] <= '2023-12-31']
        self.assertNotIn('1100', {r['value'] for r in eligible})

    def test_real_metadata_conflicts_are_rejected_but_missing_columns_are_merged(self):
        historical = deepcopy(self.submissions['filings']['recent'])
        self.submissions['filings']['recent']['isInlineXBRL'] = [1, 1, 1, 1]
        index = filing_index(self.submissions, [historical])
        self.assertEqual(index['0000123456-23-000001']['isInlineXBRL'], 1)
        historical['reportDate'][0] = '2023-10-01'
        with self.assertRaises(SecError):
            filing_index(self.submissions, [historical])


class StorageReviewTests(unittest.TestCase):
    def test_abrupt_process_exit_leaves_only_unpublished_temp_and_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            script = '''
from contextlib import contextmanager
from pathlib import Path
import os
import sys
from unittest.mock import patch
from investor_ai.sec import Response
from investor_ai.storage import Store
original = Path.open
@contextmanager
def interrupted(path, mode='r', *args, **kwargs):
    with original(path, mode, *args, **kwargs) as handle:
        if 'raw' in path.parts and mode in {'xb', 'wb'}:
            class Writer:
                def write(self, body):
                    handle.write(body[:8])
                    handle.flush()
                    os._exit(17)
            yield Writer()
        else:
            yield handle
with patch.object(Path, 'open', interrupted):
    Store(sys.argv[1]).save_response(Response('https://data.sec.gov/synthetic', b'{"invented": "a long synthetic body"}', '2025-01-01', {}))
'''
            env = os.environ.copy()
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
            child = subprocess.run([sys.executable, '-c', script, directory], env=env, capture_output=True, timeout=10)
            self.assertEqual(child.returncode, 17, child.stderr.decode())
            store = Store(directory)
            self.assertEqual(list((store.root / 'raw').glob('*.json')), [])
            self.assertTrue(list((store.root / 'raw').glob('*.tmp')))
            response = Response('https://data.sec.gov/synthetic', b'{"invented": "a long synthetic body"}', '2025-01-01', {})
            self.assertEqual(store.raw(store.save_response(response)), response.body)

    def test_interrupted_raw_write_does_not_publish_partial_object_and_retry_succeeds(self):
        for error in (OSError('synthetic disk full'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__), tempfile.TemporaryDirectory() as directory:
                store = Store(directory)
                response = Response(URL, b'{"invented": "a sufficiently long synthetic body"}', '2025-01-01T00:00:00Z', {})
                original_open = Path.open

                @contextmanager
                def interrupted_open(path, mode='r', *args, **kwargs):
                    with original_open(path, mode, *args, **kwargs) as handle:
                        if 'raw' in path.parts and mode in {'xb', 'wb'}:
                            class Interrupted:
                                def write(self, body):
                                    handle.write(body[:8])
                                    handle.flush()
                                    raise error
                            yield Interrupted()
                        else:
                            yield handle

                with patch.object(Path, 'open', interrupted_open), self.assertRaises(type(error)):
                    store.save_response(response)
                self.assertEqual(list((store.root / 'raw').glob('*.json')), [])
                reference = store.save_response(response)
                self.assertEqual(store.raw(reference), response.body)

    def test_interrupted_snapshot_publication_keeps_prior_snapshot_and_can_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store, client = Store(directory), FakeClient()
            collector = Collector(client, store)
            collector.collect(['SYNTH'])
            old = store.latest(CIK)
            old_rows = store.records(old)
            def fail_pointer(path, value):
                if path.name == 'latest.json':
                    raise OSError('synthetic publication interruption')
                return write_json(path, value)
            with patch('investor_ai.storage.write_json', side_effect=fail_pointer):
                result = collector.collect(['SYNTH'], refresh=True)
            self.assertEqual(result[0]['status'], 'failed')
            self.assertEqual(store.latest(CIK), old)
            self.assertEqual(store.records(old), old_rows)
            self.assertEqual(collector.collect(['SYNTH'], refresh=True)[0]['status'], 'success')
            self.assertEqual(store.records(old), old_rows)


class TickerAndCLIReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store, self.client = Store(self.temp.name), FakeClient()
        self.client.submissions['tickers'] = ['SYN-B']

    def test_dot_alias_resolves_directory_and_validates_official_dash_ticker(self):
        base_get = self.client.get
        def get(url):
            if url == TICKERS_URL:
                return Response(url, json.dumps({'0': {'ticker': 'SYN-B', 'cik_str': 123456}}).encode(), '2099-01-01T00:00:00Z', {})
            return base_get(url)
        self.client.get = get
        result = Collector(self.client, self.store).collect(['SYN.B'])
        self.assertEqual(result[0]['status'], 'success')

    def test_dot_alias_override_and_cached_lookup_match_official_ticker(self):
        collector = Collector(self.client, self.store, cik_overrides={'SYN.B': CIK})
        self.assertEqual(collector.collect(['SYN.B'])[0]['status'], 'success')
        self.assertEqual(collector.collect(['SYN-B'])[0]['status'], 'cached')
        self.assertEqual(self.store.resolve_local('SYN.B'), CIK)

    def test_cik_named_ticker_is_not_misclassified_as_a_numeric_cik(self):
        self.assertFalse(is_cik('CIK'))
        self.assertTrue(is_cik('CIK123456'))

    def test_duplicate_cik_cannot_hide_a_mismatched_ticker_override(self):
        self.client.submissions['tickers'] = ['SYNTH']
        collector = Collector(self.client, self.store, cik_overrides={'SYNTH': CIK, 'WRONG': CIK})
        result = collector.collect(['SYNTH', 'WRONG'])
        self.assertEqual([r['status'] for r in result], ['success', 'failed'])
        self.assertIn('association', result[1]['error'])

    def test_conflicting_dot_dash_overrides_are_rejected(self):
        with self.assertRaises(ValueError):
            Collector(self.client, self.store, cik_overrides={'SYN.B': CIK, 'SYN-B': '2'})

    def test_ambiguous_local_ticker_alias_requires_an_explicit_cik(self):
        Collector(self.client, self.store).collect([CIK])
        other = FakeClient()
        other.facts['cik'] = 2
        other.submissions.update(cik='2', tickers=['SYN.B'])
        self.assertEqual(Collector(other, self.store).collect(['2'])[0]['status'], 'success')
        with self.assertRaises(ValueError):
            self.store.resolve_local('SYN.B')
        self.assertEqual(self.store.resolve_local(CIK), CIK)

    def test_history_limit_has_an_actionable_fallback_without_older_metadata(self):
        self.client.submissions['tickers'] = ['SYNTH']
        self.client.submissions['filings']['files'] = [{'name': f'CIK{CIK}-submissions-{i:03}.json'} for i in range(21)]
        collector = Collector(self.client, self.store)
        result = collector.collect([CIK], filing_history=True)[0]
        self.assertEqual(result['status'], 'failed')
        self.assertIn('omit --filing-history', result['error'])
        self.assertEqual(collector.collect([CIK])[0]['status'], 'success')
        self.assertFalse(self.store.latest(CIK)['filing_history_complete'])
        self.assertEqual(len(self.store.latest(CIK)['unloaded_submissions_files']), 21)

    def test_companies_reports_failed_refresh_without_changing_success_time(self):
        self.client.submissions['tickers'] = ['SYNTH']
        collector = Collector(self.client, self.store)
        collector.collect(['SYNTH'])
        old = self.store.latest(CIK)
        self.client.fail_facts = True
        collector.collect(['SYNTH'], refresh=True)
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--data-dir', self.temp.name, 'companies'])
        company, = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(company['last_attempt_status'], 'failed')
        self.assertEqual(company['last_successful_retrieval_at'], old['retrieved_at'])
        self.assertGreaterEqual(company['age_hours'], 0)

    def test_text_inspection_preserves_zero_negative_values_and_accessions(self):
        self.client.submissions['tickers'] = ['SYNTH']
        Collector(self.client, self.store).collect(['SYNTH'])
        output = StringIO()
        with redirect_stdout(output):
            code = main(['--data-dir', self.temp.name, 'inspect', 'SYNTH', '--limit', '0'])
        self.assertEqual(code, 0)
        lines = output.getvalue().splitlines()
        header = lines[2].split('\t')
        rows = [dict(zip(header, line.split('\t'))) for line in lines[3:-1]]
        self.assertEqual(next(r['value'] for r in rows if r['metric'] == 'cash'), '0')
        self.assertEqual(next(r['value'] for r in rows if r['metric'] == 'net_income'), '-50')
        self.assertTrue(all(r['accession'].startswith(CIK + '-') for r in rows))
