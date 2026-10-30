"""Read-only dashboard adapter over the existing local engines.

No collector, HTTP client, credential loader or write operation belongs here.
Response models are an explicit allowlist; local evidence paths stay private.
"""
from datetime import date
from decimal import Decimal, localcontext
import json
import re

from .analysis import cutoff, utc_datetime
from .analysis_store import analyze_company
from .dashboard_models import (
    Company, CompanyList, ComparisonItem, ComparisonReport, Coverage, FinancialReport,
    Freshness, MarketReport, PerformancePoint, ReturnValue,
)
from .market_data import MarketDataError, adjusted_price_return, split_price_return
from .market_store import MarketStore
from .sec import SecError
from .storage import Store


class DashboardError(Exception):
    """Only these fixed, public messages may cross the API boundary."""
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


def _freshness(snapshot, at, *, latest_period=None, latest_session=None):
    retrieved = utc_datetime(snapshot['retrieved_at'])
    coverage = latest_session or latest_period
    age_days = (at.date() - date.fromisoformat(coverage)).days if coverage else None
    with localcontext() as context:
        context.prec = 34
        hours = Decimal(str((at - retrieved).total_seconds())) / Decimal(3600)
    stale = hours > 24 or (latest_session is not None and age_days is not None and age_days > 7)
    return Freshness(status='stale' if stale else 'available', retrieved_at=snapshot['retrieved_at'],
                     completed_at=snapshot['created_at'], latest_period=latest_period,
                     latest_session=latest_session, retrieval_age_hours=str(hours),
                     coverage_age_days=age_days,
                     note=('Local snapshot only. Retrieval age and historical coverage are separate; '
                           'stale means retrieval older than 24 hours or market coverage older than 7 calendar days. '
                           'This is an interface indicator, not a provider freshness guarantee.'))


def _visible(day, start, end):
    return (start is None or day >= start) and (end is None or day <= end)


def _attempt(freshness, path, at):
    """Expose operational status only when known by this analysis cutoff."""
    if not path.is_file():
        return freshness
    status = json.loads(path.read_text())
    attempted = status.get('last_attempt_at')
    if attempted and utc_datetime(attempted) <= at:
        freshness.last_attempt_at = attempted
        outcome = status.get('last_attempt_status')
        freshness.last_attempt_status = outcome if outcome in {'success', 'failed', 'cached', 'partial'} else 'unknown'
        failed = status.get('last_failed_attempt_at') or status.get('last_failed_at')
        if failed and utc_datetime(failed) <= at:
            freshness.last_failed_at = failed
        if outcome == 'failed':
            freshness.note += ' The latest collection attempt failed; the last successful snapshot remains displayed.'
            freshness.status = 'stale'
    return freshness


def _return(data, bars):
    if len(bars) < 2:
        return ReturnValue(status='unavailable', reason='At least two observed trading sessions are required.')
    endpoints = dict(start=bars[0]['date'], end=bars[-1]['date'], as_of=data['as_of'])
    try:
        if data['adjustment'] == 'raw':
            result = split_price_return(bars, data['actions'], action_coverage=data['action_coverage'], **endpoints)
        elif data['adjustment'] == 'split':
            result = adjusted_price_return(bars, **endpoints)
        else:
            return ReturnValue(status='unavailable', reason='Dividend-adjusted prices are not a supported price-return basis.',
                               start=endpoints['start'], end=endpoints['end'])
    except MarketDataError as exc:
        # Engine errors contain fixed domain diagnostics, never HTTP responses or credentials.
        return ReturnValue(status='unavailable', reason=str(exc), start=endpoints['start'], end=endpoints['end'])
    return ReturnValue(status='ok', **{k: result[k] for k in ('value', 'unit', 'formula', 'start', 'end')})


class DashboardService:
    def __init__(self, sec_dir='data/sec', market_dir='data/market', *, synthetic=False):
        self.sec = Store(sec_dir)
        self.market_store = MarketStore(market_dir)
        # Explicit dependency-injection seam for labeled fixture tests, never an HTTP option.
        self.synthetic = synthetic

    @property
    def data_kind(self):
        return 'synthetic' if self.synthetic else 'reported'

    def _market_manifests(self):
        manifests = []
        for pointer in sorted((self.market_store.root / 'datasets').glob('*/latest.json')):
            manifest = self.market_store.latest(pointer.parent.name)
            if manifest and manifest.get('synthetic') is self.synthetic:
                if self.synthetic or manifest.get('provider') == 'alpaca':
                    manifests.append(manifest)
        return manifests

    def companies(self):
        companies = {}
        for manifest in self.sec.companies():
            records = self.sec.records(manifest)
            latest = max((r['end'] for r in records), default=None)
            currencies = {r['unit'] for r in records if re.fullmatch(r'[A-Z]{3}', r.get('unit', ''))}
            currency = next(iter(currencies)) if len(currencies) == 1 else None
            for ticker in manifest['tickers']:
                previous = companies.get(ticker.upper())
                if previous is not None and previous.cik != manifest['cik']:
                    raise DashboardError(409, 'ambiguous_company',
                        'Local ticker aliases identify multiple SEC companies. Resolve the local identity conflict before analysis.')
                companies[ticker.upper()] = Company(ticker=ticker.upper(), name=manifest['name'], cik=manifest['cik'],
                    currency=currency, financial_available=True, market_available=False,
                    latest_reporting_period=latest, data_kind=self.data_kind)
        # Metadata discovery never reads provider credentials or invokes collection.
        for manifest in self._market_manifests():
            data = self.market_store.load(manifest)
            symbol = data['instrument']['symbol'].upper()
            company = companies.setdefault(symbol, Company(ticker=symbol, name=symbol,
                currency=data['instrument']['currency'], financial_available=False, market_available=True,
                data_kind=self.data_kind))
            company.market_available = True
            latest = max((r['date'] for r in data['bars']), default=None)
            if latest and (not company.latest_market_session or latest > company.latest_market_session):
                company.latest_market_session = latest
        return CompanyList(companies=sorted(companies.values(), key=lambda c: c.ticker), data_kind=self.data_kind)

    def _company(self, ticker):
        matches = [c for c in self.companies().companies if c.ticker == ticker.upper()]
        if not matches:
            raise DashboardError(404, 'unknown_company', 'Company has no available local snapshots.')
        return matches[0]

    def financials(self, ticker, *, frequency='annual', start=None, end=None, as_of=None):
        company = self._company(ticker)
        if not company.financial_available:
            raise DashboardError(409, 'financial_data_unavailable', 'No local SEC financial data is available for this company.')
        if company.currency is None:
            raise DashboardError(409, 'financial_currency_unavailable',
                'Local financial evidence has an unknown or ambiguous reporting currency. Select an evidenced basis outside the dashboard.')
        at = cutoff(as_of)
        try:
            report = analyze_company(self.sec, company.cik, frequency=frequency, as_of=at,
                                     unit=company.currency)
        except SecError as exc:
            if str(exc).startswith('No saved snapshot'):
                raise DashboardError(409, 'unavailable_at_cutoff', 'No local financial snapshot was available at this cutoff.') from None
            raise
        periods = report['periods']
        latest = max((p['period']['end'] for p in periods), default=None)
        diagnostics = [*report.get('evidence_diagnostics', []), *report.get('cashflow_derivation_diagnostics', [])]
        # Diagnostics in existing engines can be structured. Keep precise domain statuses, not filesystem paths.
        diagnostics = [item if isinstance(item, str) else json.dumps(item, sort_keys=True) for item in diagnostics]
        return FinancialReport(ticker=company.ticker, name=report['snapshot']['name'], cik=report['cik'],
            as_of=report['as_of'], frequency=frequency, unit=report['unit'], data_kind=self.data_kind,
            periods=[p for p in periods if _visible(p['period']['end'], start, end)],
            snapshot=report['snapshot'], freshness=_attempt(_freshness(report['snapshot'], at, latest_period=latest),
                self.sec.root / 'companies' / company.cik / 'status.json', at),
            limitations=report['limitations'], diagnostics=diagnostics)

    def _market_data(self, ticker, *, adjustment='split', feed='sip', as_of=None, adjustment_as_of=None):
        self._company(ticker)
        # Production dashboard only exposes the approved Alpaca local series. Test fixtures have their own provider.
        provider = None if self.synthetic else 'alpaca'
        try:
            data = self.market_store.inspect(ticker, provider=provider, feed=feed, adjustment=adjustment,
                as_of=as_of, adjustment_as_of=adjustment_as_of)
        except MarketDataError as exc:
            if str(exc).startswith(('No saved market snapshot', 'Ambiguous market series', 'Ambiguous market snapshot')):
                raise DashboardError(409, 'market_data_unavailable',
                    'No unique eligible local market series is available for this feed, adjustment and cutoff.') from None
            raise
        if data['synthetic'] is not self.synthetic:
            raise DashboardError(409, 'market_data_unavailable', 'The requested market series is not eligible for this dashboard.')
        return data

    def _market_report(self, data, *, start=None, end=None):
        bars = sorted((b for b in data['bars'] if _visible(b['date'], start, end)), key=lambda b: b['date'])
        latest = max((b['date'] for b in data['bars']), default=None)
        # Never send raw source metadata, paths, resource headers or configuration.
        quality = {key: data['quality'][key] for key in (
            'actions_complete', 'missing_sessions', 'zero_volume_sessions', 'zero_volume_price_movement_sessions',
            'unknown_cash_currency_events', 'unconfirmed_refresh_sessions', 'unconfirmed_refresh_actions',
            'duplicate_bars', 'duplicate_actions') if key in data['quality']}
        calendar = {**data['calendar'], 'sessions': [s for s in data['calendar']['sessions']
                    if _visible(s['date'], start, end)]}
        return MarketReport(ticker=data['instrument']['symbol'], as_of=data['as_of'], provider=data['provider'],
            instrument_id=data['instrument']['id'],
            feed=data['feed'], currency=data['instrument']['currency'], adjustment=data['adjustment'],
            mic=data['instrument']['mic'], timezone=data['instrument']['timezone'], calendar=calendar,
            adjustment_as_of=data.get('adjustment_as_of'), data_kind=self.data_kind, bars=bars,
            actions=[a for a in data['actions'] if _visible(a['ex_date'], start, end)], quality=quality,
            coverage=data['coverage'], snapshot=data['snapshot'],
            freshness=_attempt(_freshness(data['snapshot'], cutoff(data['as_of']), latest_session=latest),
                self.market_store.root / 'datasets' / data['snapshot']['dataset_key'] / 'status.json', cutoff(data['as_of'])),
            price_return=_return(data, bars), limitations=[
                'Price return excludes dividends and is not total return.',
                'Historical market dates do not establish source publication or pre-collection availability.',
                'Provider-adjusted prices are conditional on their recorded acquisition vintage.',
                'Corporate-action completeness and dividend currency remain explicit; missing evidence is never inferred.',
                'Only locally saved snapshots are displayed. Dashboard interactions never request provider data.'])

    def market(self, ticker, *, adjustment='split', feed='sip', start=None, end=None, as_of=None, adjustment_as_of=None):
        data = self._market_data(ticker, adjustment=adjustment, feed=feed, as_of=as_of,
                                 adjustment_as_of=adjustment_as_of)
        return self._market_report(data, start=start, end=end)

    def compare(self, tickers, *, frequency='annual', start=None, end=None, as_of=None):
        at = cutoff(as_of)
        items, datasets = [], {}
        warnings = ['Financial series retain each company’s exact fiscal intervals. Values are not normalized across currencies or fiscal calendars.']
        for ticker in tickers:
            company = self._company(ticker)
            item = ComparisonItem(company=company)
            errors = []
            try:
                item.financials = self.financials(ticker, frequency=frequency, start=start, end=end, as_of=at)
            except DashboardError as exc:
                errors.append(exc.message)
            try:
                datasets[ticker] = self._market_data(ticker, as_of=at)
                item.market = self._market_report(datasets[ticker], start=start, end=end)
            except DashboardError as exc:
                errors.append(exc.message)
            item.error = ' '.join(errors) or None
            items.append(item)
        intervals = {(p.period.start, p.period.end, f.unit) for item in items if (f := item.financials)
                     and (p := next(reversed(f.periods), None))}
        if len(intervals) > 1:
            warnings.append('Latest financial periods or currencies differ. Compare the displayed dates and units; equal fiscal-year labels do not imply equal periods.')
        common_range = None
        if all(item.market and item.market.bars for item in items):
            sessions = set.intersection(*({b.date for b in item.market.bars} for item in items))
            dates = sorted(sessions)
            currencies = {item.market.currency for item in items}
            if len(currencies) > 1:
                warnings.append('Price returns are in each company’s quote currency; currency conversion is not performed.')
            if len(dates) >= 2:
                common_range = Coverage(start=dates[0], end=dates[-1])
                for item in items:
                    data = datasets[item.company.ticker]
                    lookup = {b['date']: b for b in data['bars']}
                    first = lookup[dates[0]]
                    item.performance = [PerformancePoint(date=dates[0], value='0', status='baseline')]
                    for day in dates[1:]:
                        result = _return(data, [first, lookup[day]])
                        item.performance.append(PerformancePoint(date=day, value=result.value, status=result.status))
                    item.market.price_return = _return(data, [first, lookup[dates[-1]]])
            else:
                warnings.append('Fewer than two shared observed market sessions; comparative price returns are unavailable.')
        else:
            warnings.append('At least one company lacks eligible market data; comparative performance is unavailable.')
        if common_range is None:
            for item in items:
                if item.market is not None:
                    item.market.price_return = ReturnValue(status='unavailable',
                        reason='A comparison requires at least two shared observed sessions for every selected company.')
        return ComparisonReport(as_of=at.isoformat(), frequency=frequency, data_kind=self.data_kind,
            companies=items, warnings=warnings, common_market_range=common_range)
