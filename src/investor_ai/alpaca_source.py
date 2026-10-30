"""Alpaca daily evidence adapter over the existing versioned market store."""
from copy import deepcopy
from datetime import datetime, time as daytime, timedelta
from decimal import Decimal, localcontext
import hashlib
import json
import re
from uuid import UUID

from .alpaca_replay import NY, _calendar, _bars, _json, _next
from .market_data import MarketDataError, _date, _time, _number, normalize_live_bundle
from .market_store import collect_market, _latest

EXCHANGES = {'NASDAQ': 'XNAS', 'NYSE': 'XNYS', 'AMEX': 'XASE', 'ARCA': 'ARCX', 'BATS': 'BATS'}


def _instrument(value, symbol):
    if value.get('symbol') != symbol or value.get('class') != 'us_equity' or value.get('exchange') not in EXCHANGES:
        raise MarketDataError('Unsupported or changed current asset identity; no historical mapping is guessed')
    try:
        asset = str(UUID(value['id'])).upper()
    except (ValueError, KeyError, TypeError) as exc:
        raise MarketDataError('Invalid Alpaca asset identifier') from exc
    return {'id': asset, 'symbol': symbol, 'mic': EXCHANGES[value['exchange']],
            'currency': 'USD', 'timezone': 'America/New_York'}


def _canonical(evidence, header, process):
    """Reparse original response bodies; preserve unavailable actions as diagnostics."""
    symbol = header['instrument']['symbol']
    bars, actions, calendar, diagnostics = [], [], None, []
    for item in evidence:
        if hashlib.sha256(item['body'].encode()).hexdigest() != item['sha256']:
            raise MarketDataError('Original Alpaca response checksum mismatch')
        payload = _json(item['body'])
        stream = item['stream']
        if stream == 'asset':
            if _instrument(payload, symbol) != header['instrument']:
                raise MarketDataError('Asset identity changed; prior evidence retained')
        elif stream == 'calendar':
            calendar = _calendar(payload, header['coverage']['start'], header['coverage']['end'])
        elif stream == 'bars':
            _next(payload, 'bars')
            bars.extend(_bars(payload['bars'], symbol, **header['coverage']))
        elif stream == 'actions':
            _next(payload, 'corporate_actions')
            if not isinstance(payload['corporate_actions'], dict):
                raise MarketDataError('Expected corporate-action category object')
            for kind, rows in payload['corporate_actions'].items():
                if not isinstance(rows, list):
                    raise MarketDataError('Malformed action category')
                for row in rows:
                    if not isinstance(row, dict):
                        raise MarketDataError('Expected corporate-action records')
                    if kind not in {'forward_splits', 'reverse_splits', 'cash_dividends'}:
                        diagnostics.append({'provider_type': kind, 'id': row.get('id'), 'reason': 'unsupported_action'})
                        continue
                    if row.get('symbol') != symbol:
                        raise MarketDataError('Wrong symbol in action response')
                    try:
                        identifier = row['id']
                        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', identifier):
                            raise ValueError('Invalid ID')
                        processed, ex_date = _date(row['process_date']).isoformat(), _date(row['ex_date']).isoformat()
                        if not process['start'] <= processed <= process['end']:
                            raise ValueError('Process date outside query')
                        action = {'id': identifier, 'ex_date': ex_date, 'process_date': processed, 'provider_type': kind}
                        if kind == 'cash_dividends':
                            action.update(kind='dividend', amount=_number(row['rate']), currency=row.get('currency') or None)
                        else:
                            new, old = (_number(row[key], positive=True) for key in ('new_rate', 'old_rate'))
                            with localcontext() as context:
                                context.prec = 34
                                ratio = Decimal(new) / Decimal(old)
                            if (kind == 'forward_splits' and ratio <= 1) or (kind == 'reverse_splits' and ratio >= 1):
                                raise ValueError('Rates contradict subtype')
                            action.update(kind='split', ratio=str(ratio), new_rate=new, old_rate=old)
                        for source, target in [('payable_date', 'payment_date'), ('record_date', 'record_date')]:
                            if row.get(source):
                                action[target] = _date(row[source]).isoformat()
                        actions.append(action)
                    except (KeyError, TypeError, ValueError):
                        diagnostics.append({'provider_type': kind, 'id': row.get('id'), 'reason': 'incomplete_or_invalid_action'})
    if calendar is None:
        raise MarketDataError('Calendar evidence is missing')
    return bars, actions, calendar, diagnostics


class AlpacaSource:
    def __init__(self, client, instrument, *, start, end, feed='sip', adjustment='raw', page_size=1000,
                 action_page_size=1000, process_start='2016-01-01', action_overlap_days=7, asset_response=None):
        if feed not in {'sip', 'iex'} or adjustment not in {'raw', 'split', 'dividend'}:
            raise MarketDataError('Unsupported feed/adjustment; no paid or combined adjustment fallback')
        if type(page_size) is not int or not 1 <= page_size <= 10000 or type(action_page_size) is not int or not 1 <= action_page_size <= 1000:
            raise MarketDataError('Invalid provider page sizes')
        if type(action_overlap_days) is not int or action_overlap_days < 0:
            raise MarketDataError('Invalid action correction overlap')
        if _date(start) > _date(end) or _date(process_start) > _time(client.now()).astimezone(NY).date():
            raise MarketDataError('Invalid historical acquisition range')
        anchor = _time(client.now()).astimezone(NY).date().isoformat() if adjustment != 'raw' else None
        self.header = {'schema_version': 1, 'synthetic': False, 'provider': 'alpaca', 'feed': feed,
                       'instrument': instrument, 'adjustment': adjustment, 'adjustment_as_of': anchor,
                       'coverage': {'start': start, 'end': end}}
        self.client, self.asset_response = client, asset_response
        self.page_size, self.action_page_size = page_size, action_page_size
        self.process_start, self.action_overlap_days = process_start, action_overlap_days
        self.requires_full_refresh = adjustment != 'raw'
        self._start_index = len(client.requests) - (1 if asset_response else 0)
        self._page = None
        self.fingerprint = hashlib.sha256(json.dumps([self.header, page_size, action_page_size, process_start, action_overlap_days], sort_keys=True).encode()).hexdigest()

    def _pages(self, stream, params, field, evidence):
        token, seen = None, set()
        while True:
            response = self.client.get(stream, {**params, **({'page_token': token} if token else {})})
            evidence.append(response.evidence())
            page = response.json()
            token = _next(page, field)
            if token is None:
                return
            if token in seen:
                raise MarketDataError('Repeated Alpaca pagination token')
            seen.add(token)

    def prepare(self, *, start, end, request_budget, previous=None):
        try:
            return self._prepare(start=start, end=end, request_budget=request_budget, previous=previous)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            if isinstance(exc, MarketDataError):
                raise
            raise MarketDataError('Missing or incompatible Alpaca evidence; prior snapshot retained') from None

    def _prepare(self, *, start, end, request_budget, previous=None):
        if self._page is not None:
            raise MarketDataError('Use a fresh Alpaca source for each acquisition')
        self.client.max_requests = min(self.client.max_requests, len(self.client.requests) + request_budget)
        finish = datetime.combine(_date(end) + timedelta(days=1), daytime(0), NY)
        if _time(self.client.now()) < finish + timedelta(minutes=15):
            raise MarketDataError('Daily aggregation is not historical/finished')
        if self.header['adjustment'] != 'raw' and self.header['adjustment_as_of'] != _time(self.client.now()).astimezone(NY).date().isoformat():
            raise MarketDataError('Adjustment vintage crossed a local date; restart acquisition')
        symbol = self.header['instrument']['symbol']
        asset = self.asset_response or self.client.get('asset', {'symbol': symbol})
        evidence = [asset.evidence()]
        header = {**deepcopy(self.header), 'coverage': {'start': start, 'end': end}}
        calendar = self.client.get('calendar', {**header['coverage'], 'date_type': 'TRADING'})
        evidence.append(calendar.evidence())
        first = datetime.combine(_date(start), daytime(0), NY)
        self._pages('bars', {'symbols': symbol, 'timeframe': '1Day', 'start': first.isoformat(),
                            'end': (finish - timedelta(microseconds=1)).isoformat(), 'limit': self.page_size,
                            'feed': header['feed'], 'adjustment': header['adjustment'], 'asof': '-', 'currency': 'USD', 'sort': 'asc'}, 'bars', evidence)
        process = {'start': self.process_start, 'end': _time(self.client.now()).astimezone(NY).date().isoformat()}
        if previous:
            prior = previous.get('source_metadata', {}).get('action_process_coverage')
            if prior:
                process['start'] = max(process['start'], (_date(prior['end']) - timedelta(days=self.action_overlap_days)).isoformat())
        self._pages('actions', {'symbols': symbol, **process, 'limit': self.action_page_size,
                               'sort': 'asc', 'region': 'us', 'data_quality': 'all'}, 'corporate_actions', evidence)
        bars, actions, calendar, diagnostics = _canonical(evidence, header, process)
        if header['adjustment'] != 'raw':
            anchor = header['adjustment_as_of']
            observed_days = [_time(e['observed_at']).astimezone(NY).date().isoformat() for e in evidence if e['stream'] == 'bars']
            observed_days.append(_time(self.client.now()).astimezone(NY).date().isoformat())
            if any(day != anchor for day in observed_days):
                raise MarketDataError('Adjustment acquisition crossed a local date; restart with a new vintage')
        ex_dates = [action['ex_date'] for action in actions]
        effective = {'start': min([start, *ex_dates]), 'end': max([end, *ex_dates]), 'complete': False}
        metadata = {'adapter_version': '1', 'symbol_asof': '-', 'action_process_coverage': process,
                    'adjustment_vintage': hashlib.sha256(json.dumps([(e['sha256'], e['observed_at']) for e in evidence if e['stream'] == 'bars']).encode()).hexdigest() if header['adjustment'] != 'raw' else None,
                    'action_overlap_days': self.action_overlap_days, 'action_diagnostics': diagnostics,
                    'unknown_cash_currency_ids': [a['id'] for a in actions if a['kind'] == 'dividend' and not a.get('currency')],
                    'provider_requests': deepcopy(self.client.requests[self._start_index:]),
                    'adjustment_anchor_kind': 'retrieval_day_provider_latest' if header['adjustment'] != 'raw' else None,
                    'rights': 'User-authorized private noncommercial research; contractual clearance and post-termination retention unverified',
                    'limitations': ['Pagination is not action completeness certification', 'Current asset metadata is not a historical security master',
                                    'Adjustment knowledge is not frozen by symbol asof; adjusted histories refresh in full']}
        self._window = header['coverage']
        self._page = json.dumps({'bars': bars, 'actions': actions, 'next_page_token': None,
                                 'alpaca_response_evidence': evidence}, sort_keys=True, allow_nan=False).encode()
        return {**header, 'calendar': calendar, 'action_coverage': effective, 'source_metadata': metadata,
                'evidence_observed_through': max(e['observed_at'] for e in evidence)}

    def fetch_page(self, *, start, end, token):
        if self._page is None or token is not None or self._window != {'start': start, 'end': end}:
            raise MarketDataError('Alpaca source not prepared for this exact price window')
        return self._page


def collect_alpaca(client, store, identifiers, *, start, end, feed='sip', adjustment='raw', page_size=1000,
                   action_page_size=1000, process_start='2016-01-01', action_overlap_days=7, **options):
    """Bounded watchlist orchestration; all network requests share one client."""
    if not identifiers or len(identifiers) > 20 or len(set(identifiers)) != len(identifiers):
        raise MarketDataError('Supply 1-20 distinct symbols')
    results = []
    for symbol in identifiers:
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,19}', symbol):
            raise MarketDataError('Invalid market symbol')
        try:
            prefetched = None
            with store.collection_lock():
                known = {}
                for pointer in (store.root / 'datasets').glob('*/latest.json'):
                    manifest = store.latest(pointer.parent.name)
                    if manifest['provider'] == 'alpaca' and manifest['synthetic'] is False and manifest['instrument']['symbol'] == symbol:
                        known[manifest['instrument']['id']] = manifest['instrument']
                if len(known) > 1:
                    raise MarketDataError('Ambiguous cached security identity; explicit reconciliation required')
                if known:
                    instrument = next(iter(known.values()))
                else:
                    prefetched = client.get('asset', {'symbol': symbol})
                    instrument = _instrument(prefetched.json(), symbol)
            source = AlpacaSource(client, instrument, start=start, end=end, feed=feed, adjustment=adjustment,
                                  page_size=page_size, action_page_size=action_page_size, process_start=process_start,
                                  action_overlap_days=action_overlap_days, asset_response=prefetched)
            result = collect_market(source, store, **options)
            results.append({'symbol': symbol, **result})
        except (MarketDataError, ValueError, KeyError, TypeError, OSError):
            # The client reports HTTP status without raw bodies/secrets; do not expose arbitrary input/error text.
            raise
    return results


def replay_alpaca_snapshot(store, manifest):
    """Verify all saved raw provider responses and reproduce retained observations offline."""
    saved = store.load(manifest)
    groups, pages = [], []
    for ref in manifest['sources']:
        if ref.get('kind') == 'header':
            header = json.loads(store._read(ref))
            groups.append((header, pages, ref['retrieved_at']))
            pages = []
        else:
            pages.append(ref)
    if pages or not groups:
        raise MarketDataError('Incomplete replay source groups')
    observations = {'bars': [], 'actions': []}
    for header, refs, observed in groups:
        canonical = []
        for ref in refs:
            page = json.loads(store._read(ref))
            bars, actions, calendar, diagnostics = _canonical(page['alpaca_response_evidence'], header, header['source_metadata']['action_process_coverage'])
            if bars != page['bars'] or actions != page['actions'] or calendar != header['calendar']:
                raise MarketDataError('Provider response replay differs from saved canonical evidence')
            canonical.append(page)
        normalized = normalize_live_bundle({**header, 'pages': canonical}, observed_at=observed)
        for name, identity in [('bars', 'date'), ('actions', 'id')]:
            current = {row[identity]: row for row in _latest(observations[name], identity)}
            for row in normalized[name]:
                if row[identity] not in current or row['record_id'] != current[row[identity]]['record_id']:
                    observations[name].append(row)
    signature = lambda rows: sorted((r['record_id'], r['observed_at']) for r in rows)
    if any(signature(observations[name]) != signature(saved[name]) for name in observations):
        raise MarketDataError('Replayed financial observation history differs from snapshot')
    return {'matches': True, 'snapshot_id': manifest['snapshot_id'], 'groups': len(groups),
            'bar_versions': len(saved['bars']), 'action_versions': len(saved['actions']), 'network_requests': 0}
