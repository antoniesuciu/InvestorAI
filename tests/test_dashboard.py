"""Read-only dashboard contracts over fresh, explicitly synthetic local evidence.

No live stores, credentials, collectors or network access are needed. Financial
examples and split returns have independent hand-calculated expectations.
"""

from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from investor_ai.alpaca import AlpacaClient
from investor_ai.collector import Collector
from investor_ai.dashboard import create_app
from investor_ai.dashboard_models import CompanyList, ComparisonReport, FinancialReport, MarketReport
from investor_ai.dashboard_service import DashboardService
from investor_ai.financials import METRICS, PARSER_VERSION, extract_facts
from investor_ai.market_store import JsonFileSource, MarketStore, collect_market
from investor_ai.sec import Response, SecClient
from investor_ai.storage import Store


EARLY = '2025-03-01T00:00:00+00:00'
LATE = '2025-06-01T00:00:00+00:00'
AS_OF = '2025-07-01T00:00:00+00:00'
EXACT_REVENUE = 9007199254740993
SECRET = 'synthetic-secret-must-stay-private'
FIXTURE = Path(__file__).parent / 'fixtures' / 'market_data.json'
CIKS = {'AAPL': '0000123456', 'MSFT': '0000234567'}


@pytest.fixture(autouse=True)
def offline_boundary(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Dashboard tests prohibit upstream collection and network access')

    monkeypatch.setattr(SecClient, 'get', forbidden)
    monkeypatch.setattr(AlpacaClient, 'get', forbidden)
    monkeypatch.setattr(Collector, 'collect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setenv('APCA_API_KEY_ID', SECRET)
    monkeypatch.setenv('APCA_API_SECRET_KEY', SECRET)


def publish_financials(store, ticker, *, at=EARLY, revenue=EXACT_REVENUE, cik=None,
                       currencies=('USD',)):
    """Publish genuine SEC-store objects from invented CompanyFacts/submissions."""
    cik = cik or CIKS[ticker]
    month = 9 if ticker == 'AAPL' else 6
    periods = []
    for year in (2023, 2024):
        end = f'{year}-{month:02d}-30'
        start = f'{year - 1}-{month + 1:02d}-01'
        periods.append((start, end, year, 'FY', '10-K', f'{cik}-{year % 100:02d}-000001'))
    quarter_start, quarter_end = (('2024-01-01', '2024-03-31') if ticker == 'AAPL'
                                  else ('2023-10-01', '2023-12-31'))
    periods.append((quarter_start, quarter_end, 2024, 'Q2', '10-Q', f'{cik}-24-000002'))
    filing_rows = []
    facts = {}
    flow_values = {
        'RevenueFromContractWithCustomerExcludingAssessedTax': (100, revenue, 60),
        'NetIncomeLoss': (10, 18, 6),
        'NetCashProvidedByUsedInOperatingActivities': (20, 30, 12),
        'PaymentsToAcquirePropertyPlantAndEquipment': (5, 8, 2),
        'GrossProfit': (40, 54, 24),
        'OperatingIncomeLoss': (20, 24, 12),
    }
    balances = {'Assets': 240, 'Liabilities': 140, 'AssetsCurrent': 90,
                'LiabilitiesCurrent': 45, 'StockholdersEquity': 100,
                'CashAndCashEquivalentsAtCarryingValue': 0}
    for index, (start, end, year, fp, form, accession) in enumerate(periods):
        filed = f'{year}-11-01' if fp == 'FY' else '2024-05-01'
        filing_rows.append(dict(accessionNumber=accession, filingDate=filed,
                                reportDate=end, acceptanceDateTime=f'{filed}T20:00:00Z',
                                form=form, primaryDocument=f'synthetic-{end}.htm'))
        common = dict(end=end, accn=accession, fy=year, fp=fp, form=form, filed=filed)
        for concept, values in flow_values.items():
            facts.setdefault(concept, {'units': {'USD': []}})['units']['USD'].append(
                dict(common, start=start, val=values[index]))
            if index == 1:
                # The current filing explicitly repeats unchanged prior-year
                # comparatives, providing a coherent basis for year-on-year math.
                previous_start, previous_end, *_ = periods[0]
                facts[concept]['units']['USD'].append(
                    dict(common, start=previous_start, end=previous_end, val=values[0]))
        for concept, value in balances.items():
            facts.setdefault(concept, {'units': {'USD': []}})['units']['USD'].append(
                dict(common, val=value))
    if currencies != ('USD',):
        for definition in facts.values():
            values = definition['units']['USD']
            definition['units'] = {currency: [dict(row) for row in values] for currency in currencies}
    name = f'SYNTHETIC {ticker} EXAMPLE — NOT REAL ISSUER DATA'
    companyfacts = {'cik': int(cik), 'entityName': name, 'facts': {'us-gaap': facts}}
    submissions = {'cik': cik, 'name': name, 'tickers': [ticker],
                   'fiscalYearEnd': f'{month:02d}30',
                   'filings': {'recent': {key: [r[key] for r in filing_rows]
                                          for key in filing_rows[0]}, 'files': []}}
    urls = {'companyfacts': f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json',
            'submissions': f'https://data.sec.gov/submissions/CIK{cik}.json'}
    sources = {key: store.save_response(Response(urls[key], json.dumps(value).encode(), at,
                                                  {'Authorization': SECRET}))
               for key, value in [('companyfacts', companyfacts), ('submissions', submissions)]}
    sources['submissions_history'] = []
    records = extract_facts(companyfacts, submissions, retrieved_at=at, source_url=urls['companyfacts'])
    metadata = dict(cik=cik, name=name, tickers=[ticker], retrieved_at=at,
                    parser_version=PARSER_VERSION, collector_version='synthetic-test',
                    requested_identifier=ticker, fiscal_year_end=f'{month:02d}30',
                    sources=sources, filing_history_complete=True, unloaded_submissions_files=[],
                    missing_metrics=sorted(set(METRICS) - {r['metric'] for r in records}),
                    limitations=['Invented dashboard test data'], private_marker=SECRET)
    with patch('investor_ai.storage.utc_now', return_value=at):
        manifest = store.publish(metadata, records)
        store.record_attempt(cik, 'partial')
    return manifest


def publish_market(root, store, ticker, *, adjustment='raw', at=EARLY,
                   incomplete=False, missing_session=False, final_close='53', session_dates=None):
    bundle = json.loads(FIXTURE.read_text())
    bundle['feed'] = 'sip'
    bundle['instrument'].update(id=f'{ticker}-SYNTHETIC', symbol=ticker, cik=CIKS[ticker])
    bundle['action_coverage']['complete'] = not incomplete
    if session_dates is not None:
        original_dates = ['2024-11-27', '2024-11-29', '2024-12-02']
        mapping = dict(zip(original_dates, session_dates, strict=True))
        for key in ('coverage', 'action_coverage', 'calendar'):
            bundle[key].update(start=session_dates[0], end=session_dates[-1])
        for session in bundle['calendar']['sessions']:
            session['date'] = mapping[session['date']]
            for key in ('open_at', 'close_at'):
                session[key] = mapping[session[key][:10]] + session[key][10:]
        for page in bundle['pages']:
            for bar in page['bars']:
                bar['date'] = mapping[bar['date']]
            for action in page['actions']:
                action['ex_date'] = mapping[action['ex_date']]
    if missing_session:
        bundle['pages'][0]['bars'].pop()
    closing_times = {session['date']: session['close_at'] for session in bundle['calendar']['sessions']}
    for page in bundle['pages']:
        for bar in page['bars']:
            bar['published_at'] = (datetime.fromisoformat(closing_times[bar['date']].replace('Z', '+00:00'))
                                   + timedelta(minutes=1)).isoformat()
    bundle['pages'][1]['bars'][0]['close'] = final_close
    if Decimal(final_close) > Decimal(bundle['pages'][1]['bars'][0]['high']):
        bundle['pages'][1]['bars'][0]['high'] = final_close
    bundle['adjustment'] = adjustment
    if adjustment != 'raw':
        bundle['adjustment_as_of'] = '2025-02-28'
        # Split-adjusted starting close is 50; final close 53 gives +6%.
        bundle['pages'][0]['bars'][0].update(open='50', high='51', low='49.5', close='50', volume='2000')
    source_path = root / f'{ticker}-{adjustment}-{at[:10]}.json'
    source_path.write_text(json.dumps(bundle))
    with patch('investor_ai.market_store.utc_now', return_value=at):
        result = collect_market(JsonFileSource(source_path), store, refresh=True, sleep=lambda _: None)
    return store.latest(result['dataset_key'])


@pytest.fixture
def make_dashboard(tmp_path):
    created = []

    def make(*, market=True, financial=True, incomplete=False, missing_session=False):
        root = tmp_path / str(len(created))
        root.mkdir()
        sec = Store(root / 'sec')
        market_store = MarketStore(root / 'market')
        manifests = {}
        if financial:
            for ticker in CIKS:
                manifests[ticker] = publish_financials(sec, ticker)
        if market:
            for ticker in CIKS:
                for adjustment in ('raw', 'split', 'dividend'):
                    publish_market(root, market_store, ticker, adjustment=adjustment,
                                   incomplete=incomplete, missing_session=missing_session)
        service = DashboardService(sec.root, market_store.root, synthetic=True)
        client = TestClient(create_app(service=service), base_url='http://localhost')
        created.append(client)
        return SimpleNamespace(root=root, sec=sec, market=market_store, service=service,
                               client=client, manifests=manifests)

    yield make
    for client in created:
        client.close()


@pytest.fixture
def dashboard(make_dashboard):
    return make_dashboard()


def checked(response, model=None):
    assert response.status_code == 200, response.text
    payload = response.json()
    if model:
        model.model_validate(payload)
    assert SECRET not in response.text
    return payload


def financial_period(payload, end='2024-09-30'):
    return next(item for item in payload['periods'] if item['period']['end'] == end)


def assert_private(response, root):
    assert str(root) not in response.text
    assert SECRET not in response.text
    forbidden_keys = {'path', 'headers', 'Authorization', 'private_marker', 'sources',
                      'source_metadata', 'credentials', 'api_key', 'api_secret', 'evidence_file'}

    def visit(value):
        if isinstance(value, dict):
            assert not forbidden_keys.intersection(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(response.json())


def test_health_and_company_discovery_are_local_and_explicitly_synthetic(dashboard):
    assert checked(dashboard.client.get('/api/health')) == {'status': 'ok', 'read_only': True, 'offline': True}
    response = dashboard.client.get('/api/companies')
    payload = checked(response, CompanyList)
    assert payload['data_kind'] == 'synthetic'
    assert [company['ticker'] for company in payload['companies']] == ['AAPL', 'MSFT']
    for company in payload['companies']:
        assert company['financial_available'] and company['market_available']
        assert company['currency'] == 'USD'
        assert company['data_kind'] == 'synthetic'
        assert company['latest_market_session'] == '2024-12-02'
    assert_private(response, dashboard.root)


def test_annual_financials_preserve_decimal_precision_formulas_and_original_sources(dashboard):
    response = dashboard.client.get('/api/companies/AAPL/financials', params={'as_of': AS_OF})
    payload = checked(response, FinancialReport)
    metrics = financial_period(payload)['metrics']
    assert metrics['revenue']['value'] == str(EXACT_REVENUE)
    assert isinstance(metrics['revenue']['value'], str)
    assert metrics['revenue_change_yoy']['value'] == str(EXACT_REVENUE - 100)
    assert metrics['free_cash_flow']['value'] == '22'  # 30 - 8.
    assert metrics['current_ratio']['value'] == '2'  # 90 / 45.
    assert metrics['free_cash_flow']['classification'] == 'derived'
    assert metrics['revenue']['classification'] == 'reported'
    assert metrics['free_cash_flow']['formula'] == 'operating_cash_flow - capital_expenditure'
    assert {item['value'] for item in metrics['free_cash_flow']['inputs']} == {'30', '8'}
    evidence = metrics['revenue']['inputs'][0]
    assert evidence['unit'] == 'USD'
    assert evidence['start'] == '2023-10-01' and evidence['end'] == '2024-09-30'
    assert evidence['accession'] == f'{CIKS["AAPL"]}-24-000001'
    assert evidence['filing_url'].startswith('https://www.sec.gov/Archives/edgar/data/')
    assert evidence['source_url'].endswith(f'CIK{CIKS["AAPL"]}.json')
    assert evidence['retrieved_at'] == EARLY
    assert evidence['analysis_available_at'] == EARLY
    assert payload['snapshot']['snapshot_id'] == dashboard.manifests['AAPL']['snapshot_id']
    assert payload['freshness']['retrieved_at'] == EARLY
    assert payload['freshness']['latest_period'] == '2024-09-30'
    assert payload['freshness']['status'] == 'stale'
    assert payload['limitations'] and payload['data_kind'] == 'synthetic'
    assert_private(response, dashboard.root)


def test_missing_debt_is_unavailable_and_reported_zero_cash_remains_zero(dashboard):
    payload = checked(dashboard.client.get('/api/companies/AAPL/financials', params={'as_of': AS_OF}))
    metrics = financial_period(payload)['metrics']
    assert metrics['noncurrent_debt']['value'] is None
    assert metrics['noncurrent_debt']['status'] != 'ok'
    assert metrics['noncurrent_debt_to_equity']['value'] is None
    assert metrics['noncurrent_debt_to_equity']['status'] == 'unavailable_inputs'
    assert metrics['cash']['value'] == '0'
    assert metrics['cash']['status'] == 'ok'
    assert metrics['cash_to_current_liabilities']['value'] == '0'


def test_quarterly_financials_keep_fiscal_labels_and_actual_reporting_dates(dashboard):
    payload = checked(dashboard.client.get('/api/companies/AAPL/financials',
                      params={'frequency': 'quarterly', 'as_of': AS_OF}), FinancialReport)
    assert payload['frequency'] == 'quarterly'
    assert len(payload['periods']) == 1
    item = payload['periods'][0]
    assert item['period']['start'] == '2024-01-01'
    assert item['period']['end'] == '2024-03-31'
    assert item['period']['fiscal_year'] == 2024 and item['period']['fiscal_quarter'] == 2
    assert item['metrics']['revenue']['value'] == '60'
    assert item['metrics']['net_margin_pct']['value'] == '10.0'  # 6 / 60 * 100.
    assert item['metrics']['free_cash_flow']['value'] == '10'


def test_financial_dates_filter_reporting_periods_without_refilling_missing_data(dashboard):
    payload = checked(dashboard.client.get('/api/companies/AAPL/financials',
                      params={'start': '2024-01-01', 'end': '2024-12-31', 'as_of': AS_OF}))
    assert [p['period']['end'] for p in payload['periods']] == ['2024-09-30']
    empty = checked(dashboard.client.get('/api/companies/AAPL/financials',
                    params={'start': '2020-01-01', 'end': '2020-12-31', 'as_of': AS_OF}))
    assert empty['periods'] == []
    assert empty['snapshot']['snapshot_id'] == payload['snapshot']['snapshot_id']


def test_historical_financial_snapshot_excludes_a_later_observed_revision(dashboard):
    second = publish_financials(dashboard.sec, 'AAPL', at=LATE, revenue=EXACT_REVENUE + 100)
    old = checked(dashboard.client.get('/api/companies/AAPL/financials', params={'as_of': '2025-04-01'}))
    new = checked(dashboard.client.get('/api/companies/AAPL/financials', params={'as_of': AS_OF}))
    assert financial_period(old)['metrics']['revenue']['value'] == str(EXACT_REVENUE)
    assert financial_period(new)['metrics']['revenue']['value'] == str(EXACT_REVENUE + 100)
    assert old['snapshot']['snapshot_id'] == dashboard.manifests['AAPL']['snapshot_id']
    assert new['snapshot']['snapshot_id'] == second['snapshot_id']
    assert financial_period(old)['metrics']['revenue']['inputs'][0]['analysis_available_at'] == EARLY


@pytest.mark.parametrize('endpoint,params', [
    ('financials', {}), ('market', {'adjustment': 'raw'}),
])
def test_filing_and_price_dates_do_not_claim_availability_before_local_observation(dashboard, endpoint, params):
    response = dashboard.client.get(f'/api/companies/AAPL/{endpoint}', params={**params, 'as_of': '2025-02-28'})
    assert response.status_code == 409
    assert set(response.json()['detail']) == {'code', 'message'}
    assert_private(response, dashboard.root)


@pytest.mark.parametrize('adjustment', ['raw', 'split'])
def test_market_price_return_is_six_percent_across_a_two_for_one_split(dashboard, adjustment):
    response = dashboard.client.get('/api/companies/AAPL/market',
                                   params={'adjustment': adjustment, 'as_of': AS_OF})
    payload = checked(response, MarketReport)
    assert payload['adjustment'] == adjustment and payload['feed'] == 'sip'
    source = dashboard.market.inspect('AAPL', as_of=AS_OF, adjustment=adjustment, feed='sip')
    assert payload['instrument_id'] == source['instrument']['id']
    assert payload['data_kind'] == 'synthetic'
    assert [bar['date'] for bar in payload['bars']] == ['2024-11-27', '2024-11-29', '2024-12-02']
    assert payload['bars'][0]['close'] == ('100' if adjustment == 'raw' else '50')
    assert payload['price_return']['status'] == 'ok'
    assert Decimal(payload['price_return']['value']) == Decimal('0.06')  # 53 * 2 / 100 - 1.
    assert payload['price_return']['unit'] == 'fraction'
    assert any('not total return' in note for note in payload['limitations'])
    split = next(action for action in payload['actions'] if action['kind'] == 'split')
    assert split['ratio'] == '2'
    assert split['declared_at'] == '2024-11-01T12:00:00+00:00'
    assert len(split['record_id']) == 64
    assert payload['mic'] == 'XNAS' and payload['timezone'] == 'America/New_York'
    assert payload['calendar']['observed_at'] == EARLY
    assert len(payload['calendar']['sessions']) == 3
    assert payload['calendar']['sessions'][1]['close_at'] == '2024-11-29T18:00:00+00:00'
    for bar in payload['bars']:
        assert all(isinstance(bar[field], str) for field in ('open', 'high', 'low', 'close', 'volume'))
        assert bar['observed_at'] == EARLY and len(bar['record_id']) == 64
        assert bar['published_at'].startswith(bar['date'])
        assert bar['published_at'] != bar['observed_at']
        assert len(bar['source']['sha256']) == 64
    assert payload['freshness']['latest_session'] == '2024-12-02'
    assert payload['freshness']['retrieved_at'] == EARLY
    assert_private(response, dashboard.root)


def test_dividend_adjustment_is_explicit_and_does_not_claim_price_or_total_return(dashboard):
    payload = checked(dashboard.client.get('/api/companies/AAPL/market',
                      params={'adjustment': 'dividend', 'as_of': AS_OF}), MarketReport)
    assert payload['adjustment'] == 'dividend'
    assert payload['adjustment_as_of'] == '2025-02-28'
    assert payload['price_return']['value'] is None
    assert payload['price_return']['status'] == 'unavailable'
    assert payload['price_return']['reason']


def test_market_date_filter_uses_observed_sessions_and_recalculates_return(dashboard):
    payload = checked(dashboard.client.get('/api/companies/AAPL/market', params={
        'adjustment': 'raw', 'start': '2024-11-28', 'end': '2024-12-02', 'as_of': AS_OF}))
    assert [bar['date'] for bar in payload['bars']] == ['2024-11-29', '2024-12-02']
    assert payload['price_return']['start'] == '2024-11-29'
    assert payload['price_return']['end'] == '2024-12-02'
    assert abs(Decimal(payload['price_return']['value']) - Decimal(2) / Decimal(51)) < Decimal('1e-27')
    assert all('2024-11-28' <= action['ex_date'] <= '2024-12-02' for action in payload['actions'])
    one = checked(dashboard.client.get('/api/companies/AAPL/market', params={
        'start': '2024-11-29', 'end': '2024-11-29', 'as_of': AS_OF}))
    assert len(one['bars']) == 1
    assert one['price_return']['status'] == 'unavailable' and one['price_return']['value'] is None
    empty = checked(dashboard.client.get('/api/companies/AAPL/market', params={
        'start': '2020-01-01', 'end': '2020-12-31', 'as_of': AS_OF}))
    assert empty['bars'] == [] and empty['price_return']['value'] is None


def test_missing_action_coverage_exposes_prices_without_inventing_a_raw_return(make_dashboard):
    dashboard = make_dashboard(incomplete=True)
    payload = checked(dashboard.client.get('/api/companies/AAPL/market',
                      params={'adjustment': 'raw', 'as_of': AS_OF}))
    assert len(payload['bars']) == 3
    assert payload['quality']['actions_complete'] is False
    assert payload['price_return']['value'] is None and payload['price_return']['status'] == 'unavailable'
    assert payload['price_return']['reason']


def test_missing_market_sessions_stay_missing_instead_of_becoming_zero_bars(make_dashboard):
    dashboard = make_dashboard(missing_session=True)
    payload = checked(dashboard.client.get('/api/companies/AAPL/market',
                      params={'adjustment': 'raw', 'as_of': AS_OF}))
    assert payload['quality']['missing_sessions'] == ['2024-11-29']
    assert [bar['date'] for bar in payload['bars']] == ['2024-11-27', '2024-12-02']
    assert all(Decimal(bar['close']) > 0 for bar in payload['bars'])


def test_market_cutoff_selects_previous_snapshot_and_excludes_future_price_correction(dashboard):
    publish_market(dashboard.root, dashboard.market, 'AAPL', at=LATE, final_close='54')
    old = checked(dashboard.client.get('/api/companies/AAPL/market',
                  params={'adjustment': 'raw', 'as_of': '2025-04-01'}))
    new = checked(dashboard.client.get('/api/companies/AAPL/market',
                  params={'adjustment': 'raw', 'as_of': AS_OF}))
    assert old['bars'][-1]['close'] == '53' and new['bars'][-1]['close'] == '54'
    assert old['snapshot']['snapshot_id'] != new['snapshot']['snapshot_id']
    assert Decimal(old['price_return']['value']) == Decimal('0.06')
    assert Decimal(new['price_return']['value']) == Decimal('0.08')


@pytest.mark.parametrize('endpoint', ['financials', 'market'])
def test_failed_collection_stays_separate_from_successful_snapshot_and_historical_cutoff(dashboard, endpoint):
    if endpoint == 'financials':
        with patch('investor_ai.storage.utc_now', return_value=LATE):
            dashboard.sec.record_attempt(CIKS['AAPL'], 'failed', error=f'{SECRET} /private/data')
        params = {}
    else:
        manifest = dashboard.market.inspect('AAPL', as_of=AS_OF, adjustment='raw', feed='sip')['snapshot']
        with patch('investor_ai.market_store.utc_now', return_value=LATE):
            dashboard.market.status(manifest['dataset_key'], 'failed')
        params = {'adjustment': 'raw'}
    response = dashboard.client.get(f'/api/companies/AAPL/{endpoint}', params={**params, 'as_of': AS_OF})
    payload = checked(response)
    assert payload['freshness']['retrieved_at'] == EARLY
    assert payload['freshness']['completed_at'] == EARLY
    assert payload['freshness']['last_attempt_at'] == LATE
    assert payload['freshness']['last_attempt_status'] == 'failed'
    assert payload['freshness']['last_failed_at'] == LATE
    assert payload['freshness']['status'] == 'stale'
    old = checked(dashboard.client.get(f'/api/companies/AAPL/{endpoint}',
                  params={**params, 'as_of': '2025-04-01'}))
    assert old['freshness']['last_attempt_at'] is None
    assert old['freshness']['last_failed_at'] is None
    assert old['snapshot']['snapshot_id'] == payload['snapshot']['snapshot_id']
    assert_private(response, dashboard.root)


@pytest.mark.parametrize('params', [
    {'adjustment': 'raw', 'feed': 'iex'},
    {'adjustment': 'split', 'adjustment_as_of': '2024-12-02'},
])
def test_missing_feed_or_adjustment_vintage_does_not_silently_fall_back(dashboard, params):
    response = dashboard.client.get('/api/companies/AAPL/market', params={**params, 'as_of': AS_OF})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'market_data_unavailable'


def test_compare_preserves_different_fiscal_calendars_and_common_session_baseline(dashboard):
    response = dashboard.client.get('/api/compare', params={'tickers': 'AAPL,MSFT', 'as_of': AS_OF})
    payload = checked(response, ComparisonReport)
    assert payload['financial_alignment'] == 'exact_reporting_intervals_only'
    assert payload['common_market_range'] == {'start': '2024-11-27', 'end': '2024-12-02'}
    companies = {item['company']['ticker']: item for item in payload['companies']}
    assert companies['AAPL']['financials']['periods'][-1]['period']['end'] == '2024-09-30'
    assert companies['MSFT']['financials']['periods'][-1]['period']['end'] == '2024-06-30'
    assert any('differ' in warning.lower() for warning in payload['warnings'])
    for item in companies.values():
        assert item['error'] is None
        assert item['performance'][0] == {'date': '2024-11-27', 'value': '0', 'status': 'baseline'}
        assert Decimal(item['performance'][-1]['value']) == Decimal('0.06')
        assert item['performance'][-1]['status'] == 'ok'
    assert_private(response, dashboard.root)


def test_compare_with_missing_market_data_keeps_financials_and_explains_gap(make_dashboard):
    dashboard = make_dashboard(market=False)
    payload = checked(dashboard.client.get('/api/compare', params={'tickers': 'AAPL,MSFT', 'as_of': AS_OF}))
    assert payload['common_market_range'] is None
    for item in payload['companies']:
        assert item['financials']['periods']
        assert item['market'] is None and item['performance'] == []
        assert item['error']
    assert payload['warnings']


def test_compare_missing_one_market_cannot_show_an_individual_window_as_comparative_performance(make_dashboard):
    dashboard = make_dashboard(market=False)
    publish_market(dashboard.root, dashboard.market, 'AAPL', adjustment='split')
    payload = checked(dashboard.client.get('/api/compare', params={'tickers': 'AAPL,MSFT', 'as_of': AS_OF}))
    assert payload['common_market_range'] is None
    available = next(item for item in payload['companies'] if item['company']['ticker'] == 'AAPL')
    assert len(available['market']['bars']) == 3
    assert available['market']['price_return']['value'] is None
    assert available['market']['price_return']['status'] == 'unavailable'
    assert available['performance'] == []


@pytest.mark.parametrize('sessions', [
    ['2024-12-03', '2024-12-04', '2024-12-05'],  # No shared sessions.
    ['2024-12-02', '2024-12-03', '2024-12-04'],  # Only one shared session.
])
def test_compare_requires_two_common_sessions_instead_of_comparing_different_windows(make_dashboard, sessions):
    dashboard = make_dashboard(market=False)
    publish_market(dashboard.root, dashboard.market, 'AAPL', adjustment='split')
    publish_market(dashboard.root, dashboard.market, 'MSFT', adjustment='split', session_dates=sessions)
    payload = checked(dashboard.client.get('/api/compare', params={'tickers': 'AAPL,MSFT', 'as_of': AS_OF}))
    assert payload['common_market_range'] is None
    assert payload['warnings']
    for item in payload['companies']:
        assert len(item['market']['bars']) == 3
        assert item['performance'] == []
        assert item['market']['price_return']['status'] == 'unavailable'
        assert item['market']['price_return']['value'] is None


def test_market_only_company_reports_missing_financials_and_partial_comparison(make_dashboard):
    dashboard = make_dashboard(financial=False)
    companies = checked(dashboard.client.get('/api/companies'))['companies']
    assert all(not company['financial_available'] and company['market_available'] for company in companies)
    response = dashboard.client.get('/api/companies/AAPL/financials')
    assert response.status_code == 409 and response.json()['detail']['code'] == 'financial_data_unavailable'
    payload = checked(dashboard.client.get('/api/compare', params={'tickers': 'AAPL,MSFT', 'as_of': AS_OF}))
    for item in payload['companies']:
        assert item['financials'] is None and item['market']['bars']
        assert item['error']


@pytest.mark.parametrize('path,params', [
    ('/api/companies/AAPL/financials', {'frequency': 'monthly'}),
    ('/api/companies/AAPL/financials', {'start': '2025-02-30'}),
    ('/api/companies/AAPL/financials', {'start': '2025-04-01', 'end': '2025-03-01'}),
    ('/api/companies/AAPL/financials', {'as_of': '2025-03-01T00:00:00'}),
    ('/api/companies/AAPL/financials', {'as_of': SECRET}),
    ('/api/companies/AAPL/market', {'adjustment': 'all'}),
    ('/api/companies/AAPL/market', {'feed': '../private'}),
    ('/api/companies/AAPL/market', {'adjustment_as_of': '2025-02-30'}),
    ('/api/compare', {}),
    ('/api/compare', {'tickers': 'AAPL'}),
    ('/api/compare', {'tickers': 'AAPL,AAPL'}),
    ('/api/compare', {'tickers': 'AAPL,MSFT,GOOG,ORCL,IBM'}),
    ('/api/compare', {'tickers': 'AAPL,'}),
    ('/api/compare', {'tickers': SECRET}),
])
def test_invalid_parameters_have_sanitized_fixed_shape(dashboard, path, params):
    response = dashboard.client.get(path, params=params)
    assert response.status_code == 422
    assert set(response.json()) == {'detail'}
    assert set(response.json()['detail']) == {'code', 'message'}
    assert response.json()['detail']['code'] == 'invalid_parameters'
    assert_private(response, dashboard.root)


@pytest.mark.parametrize('path,params', [
    ('/api/companies/UNKNOWN/financials', {}),
    ('/api/companies/UNKNOWN/market', {}),
    ('/api/compare', {'tickers': 'AAPL,UNKNOWN'}),
])
def test_unknown_companies_are_404_without_echoing_query_or_paths(dashboard, path, params):
    response = dashboard.client.get(path, params=params)
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'unknown_company'
    assert 'UNKNOWN' not in response.text
    assert_private(response, dashboard.root)


@pytest.mark.parametrize('endpoint', ['/api/companies', '/api/companies/AAPL/financials'])
def test_ambiguous_ticker_cannot_silently_select_one_of_two_sec_companies(dashboard, endpoint):
    publish_financials(dashboard.sec, 'AAPL', cik='0000345678')
    response = dashboard.client.get(endpoint, params={'as_of': AS_OF})
    assert response.status_code == 409
    assert set(response.json()['detail']) == {'code', 'message'}
    assert response.json()['detail']['code'] == 'ambiguous_company'
    assert_private(response, dashboard.root)


def test_ambiguous_financial_currency_cannot_implicitly_default_to_us_dollars(make_dashboard):
    dashboard = make_dashboard(financial=False, market=False)
    publish_financials(dashboard.sec, 'AAPL', currencies=('USD', 'EUR'))
    companies = checked(dashboard.client.get('/api/companies'), CompanyList)['companies']
    assert len(companies) == 1 and companies[0]['currency'] is None
    response = dashboard.client.get('/api/companies/AAPL/financials', params={'as_of': AS_OF})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'financial_currency_unavailable'
    assert_private(response, dashboard.root)


def test_financial_currency_preserves_aud_instead_of_using_a_short_currency_allowlist(make_dashboard):
    dashboard = make_dashboard(financial=False, market=False)
    publish_financials(dashboard.sec, 'AAPL', currencies=('AUD',))
    companies = checked(dashboard.client.get('/api/companies'), CompanyList)['companies']
    assert len(companies) == 1 and companies[0]['currency'] == 'AUD'
    payload = checked(dashboard.client.get('/api/companies/AAPL/financials', params={'as_of': AS_OF}), FinancialReport)
    assert payload['unit'] == 'AUD'
    metrics = financial_period(payload)['metrics']
    assert metrics['revenue']['value'] == str(EXACT_REVENUE)
    assert metrics['revenue']['unit'] == 'AUD'
    assert metrics['revenue']['inputs'][0]['unit'] == 'AUD'
    assert metrics['free_cash_flow']['value'] == '22'


def test_empty_stores_are_healthy_and_show_no_fabricated_companies(tmp_path):
    sec_path, market_path = tmp_path / 'absent-sec', tmp_path / 'absent-market'
    with TestClient(create_app(sec_dir=sec_path, market_dir=market_path), base_url='http://localhost') as client:
        checked(client.get('/api/health'))
        assert checked(client.get('/api/companies'), CompanyList) == {'companies': [], 'data_kind': 'reported'}
        assert client.get('/api/companies/AAPL/financials').status_code == 404
    assert not sec_path.exists() and not market_path.exists()


@pytest.mark.parametrize('kind', ['sec-facts', 'sec-raw', 'market-raw', 'manifest'])
def test_corrupt_local_evidence_fails_closed_without_local_paths(dashboard, kind):
    manifest = dashboard.manifests['AAPL']
    if kind == 'sec-facts':
        path = dashboard.sec.root / 'companies' / CIKS['AAPL'] / 'snapshots' / manifest['snapshot_id'] / 'facts.json'
    elif kind == 'sec-raw':
        path = dashboard.sec.root / manifest['sources']['companyfacts']['path']
    elif kind == 'market-raw':
        market_manifest = dashboard.market.inspect('AAPL', as_of=AS_OF, adjustment='raw', feed='sip')['snapshot']
        path = dashboard.market.root / market_manifest['sources'][0]['path']
    else:
        path = dashboard.sec.root / 'companies' / CIKS['AAPL'] / 'latest.json'
    path.write_text(f'corrupt evidence {SECRET} /private/secret/path')
    endpoint = 'market' if kind == 'market-raw' else 'financials'
    response = dashboard.client.get(f'/api/companies/AAPL/{endpoint}',
                                   params={'as_of': AS_OF, **({'adjustment': 'raw'} if endpoint == 'market' else {})})
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'local_data_unavailable'
    assert '/private/secret/path' not in response.text
    assert_private(response, dashboard.root)


def test_response_models_strip_unexpected_service_fields(tmp_path):
    service = SimpleNamespace(companies=lambda: {
        'companies': [{'ticker': 'SYNTH', 'name': 'Synthetic fixture', 'financial_available': False,
                       'market_available': False, 'private_marker': SECRET}],
        'data_kind': 'synthetic', 'credentials': SECRET,
    })
    with TestClient(create_app(service=service), base_url='http://localhost') as client:
        response = client.get('/api/companies')
    payload = checked(response, CompanyList)
    assert payload['companies'][0]['ticker'] == 'SYNTH'
    assert_private(response, tmp_path)


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE'])
def test_write_methods_are_rejected(dashboard, method):
    response = dashboard.client.request(method, '/api/companies', json={'refresh': True})
    assert response.status_code == 405
    assert response.json()['detail']['code'] == 'read_only'


def test_cors_only_allows_dashboard_origins_and_does_not_authorize_other_origins(dashboard):
    for origin in ('http://localhost:5173', 'http://127.0.0.1:5173'):
        response = dashboard.client.get('/api/companies', headers={'Origin': origin})
        checked(response)
        assert response.headers['access-control-allow-origin'] == origin
        preflight = dashboard.client.options('/api/companies', headers={
            'Origin': origin, 'Access-Control-Request-Method': 'GET'})
        assert preflight.status_code == 200
        assert set(preflight.headers['access-control-allow-methods'].split(', ')) == {'GET', 'OPTIONS'}
        assert 'access-control-allow-credentials' not in preflight.headers
    for origin in ('https://attacker.example', 'null', 'http://localhost:5174'):
        response = dashboard.client.get('/api/companies', headers={'Origin': origin})
        assert response.status_code == 403
        assert 'access-control-allow-origin' not in response.headers


def test_host_and_remote_client_boundaries(dashboard):
    response = dashboard.client.get('/api/companies', headers={'Host': 'attacker.example'})
    assert response.status_code == 400
    with TestClient(create_app(service=dashboard.service), base_url='http://localhost',
                    client=('198.51.100.10', 12345)) as remote:
        assert remote.get('/api/health').status_code == 403


def test_dashboard_requests_never_modify_store_or_read_dotenv(dashboard, monkeypatch):
    original_read_text, original_read_bytes = Path.read_text, Path.read_bytes

    def read_text(path, *args, **kwargs):
        assert path.name != '.env', 'Dashboard must not load credential files'
        return original_read_text(path, *args, **kwargs)

    def read_bytes(path, *args, **kwargs):
        assert path.name != '.env', 'Dashboard must not load credential files'
        return original_read_bytes(path, *args, **kwargs)

    def fingerprint():
        return {str(path.relative_to(dashboard.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in dashboard.root.rglob('*') if path.is_file()}

    before = fingerprint()
    monkeypatch.setattr(Path, 'read_text', read_text)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    for path, params in [('/api/health', {}), ('/api/companies', {}),
                         ('/api/companies/AAPL/financials', {'as_of': AS_OF}),
                         ('/api/companies/AAPL/market', {'as_of': AS_OF}),
                         ('/api/compare', {'tickers': 'AAPL,MSFT', 'as_of': AS_OF})]:
        response = dashboard.client.get(path, params=params)
        checked(response)
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
    assert fingerprint() == before
