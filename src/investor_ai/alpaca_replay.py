"""Alpaca-specific request/response replay, using authored synthetic data only.

There is deliberately no HTTP transport, authentication, environment lookup or
live-enable flag. Provider permissions remain unresolved. Successful response
bytes, requests and headers are retained in the canonical page's evidence.
"""

from copy import deepcopy
from datetime import datetime, time as daytime, timedelta
from decimal import Decimal, localcontext
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

from .market_data import MarketDataError, _date, _time, _number, _range
from .market_store import decode, _no_secrets
from .storage import utc_now

ENDPOINTS = {
    'bars': 'https://data.alpaca.markets/v2/stocks/bars',
    'actions': 'https://data.alpaca.markets/v1/corporate-actions',
    'calendar': 'https://paper-api.alpaca.markets/v2/calendar',
}
NY = ZoneInfo('America/New_York')
SAFE_HEADERS = {'content-type', 'x-request-id', 'x-ratelimit-limit',
                'x-ratelimit-remaining', 'x-ratelimit-reset', 'retry-after'}


def _quota_delay(status, headers, now):
    if not ((status in {200, 429, 500, 502, 503, 504} and headers.get('x-ratelimit-remaining') == '0') or
            (status == 429 and 'x-ratelimit-reset' in headers)):
        return 0
    try:
        reset = float(headers['x-ratelimit-reset'])
        if not math.isfinite(reset) or reset < 0:
            raise ValueError('Invalid reset')
        return max(0, reset - now.timestamp())
    except (KeyError, ValueError) as exc:
        raise MarketDataError('Missing/invalid rate-limit reset') from exc


def _retry_delay(headers, now, attempt, quota=0):
    delay = max(2 ** attempt, quota)
    if 'retry-after' in headers:
        try:
            requested = float(headers['retry-after'])
        except ValueError:
            try:
                after = parsedate_to_datetime(headers['retry-after'])
                if after.tzinfo is None:
                    raise ValueError('Missing timezone')
                requested = max(0, (after - now).total_seconds())
            except (ValueError, TypeError, OverflowError) as exc:
                raise MarketDataError('Invalid replay Retry-After') from exc
        if not math.isfinite(requested) or requested < 0:
            raise MarketDataError('Invalid replay Retry-After')
        delay = max(delay, requested)
    return delay


def _json(body):
    """Provider JSON may use numeric decimals; never convert through floats."""
    def invalid(_):
        raise MarketDataError('Nonfinite provider JSON')
    try:
        value = json.loads(body, parse_float=Decimal, parse_constant=invalid)
    except (ValueError, UnicodeError) as exc:
        raise MarketDataError('Malformed replay response JSON') from exc
    _private(value)
    return value


def _private(value):
    _no_secrets(value)
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower().replace('-', '_') in {'apca_api_key_id', 'apca_api_secret_key', 'access_token', 'refresh_token'}:
                raise MarketDataError('Authentication fields are forbidden in replay evidence')
            _private(child)
    elif isinstance(value, list):
        for child in value:
            _private(child)


def _next(page, field):
    if not isinstance(page, dict) or field not in page or 'next_page_token' not in page:
        raise MarketDataError('Missing provider payload or pagination state')
    token = page['next_page_token']
    if token is not None and (not isinstance(token, str) or not token or len(token) > 4096):
        raise MarketDataError('Invalid provider page token')
    return token


def _calendar(value, start, end):
    if not isinstance(value, list):
        raise MarketDataError('Expected a Trading API calendar array')
    sessions = []
    for row in value:
        day = _date(row['date']).isoformat()
        if not start <= day <= end:
            raise MarketDataError('Calendar response outside requested dates')
        session = {'date': day}
        for key in ('open', 'close'):
            text = row[key]
            if not isinstance(text, str) or not re.fullmatch(r'[0-2][0-9]:[0-5][0-9]', text):
                raise MarketDataError('Expected local HH:MM calendar time')
            try:
                at = datetime.combine(_date(day), daytime.fromisoformat(text), NY)
            except ValueError as exc:
                raise MarketDataError('Invalid calendar time') from exc
            session[key + '_at'] = at.isoformat()
        sessions.append(session)
    return {'start': start, 'end': end, 'sessions': sessions}


def _bars(value, symbol, start, end):
    if not isinstance(value, dict) or set(value) - {symbol}:
        raise MarketDataError('Unexpected symbol in daily bars response')
    rows = value.get(symbol, [])
    if not isinstance(rows, list):
        raise MarketDataError('Expected per-symbol bar arrays')
    result = []
    for row in rows:
        timestamp = _time(row['t']).astimezone(NY)
        day = timestamp.date().isoformat()
        if timestamp.time() != daytime(0) or not start <= day <= end:
            raise MarketDataError('Daily bar timestamp is not a requested New York day boundary')
        # t is the aggregation's left edge, NOT a publication/availability time.
        result.append({'date': day, **{key: _number(row[short], positive=True)
                                     for key, short in [('open', 'o'), ('high', 'h'), ('low', 'l'), ('close', 'c')]},
                       'volume': _number(row['v'], integer=True)})
    return result


def _actions(value, symbol, process, effective):
    if not isinstance(value, dict):
        raise MarketDataError('Expected corporate-action category arrays')
    result = []
    for category, rows in value.items():
        if not isinstance(rows, list):
            raise MarketDataError('Invalid corporate-action category')
        if rows and category not in {'forward_splits', 'reverse_splits', 'cash_dividends'}:
            raise MarketDataError('Unsupported Alpaca corporate action; explicit reconciliation required')
        for row in rows:
            if row.get('symbol') != symbol:
                raise MarketDataError('Unexpected symbol in corporate actions')
            processed = _date(row['process_date']).isoformat()
            ex_date = _date(row['ex_date']).isoformat()
            if not process['start'] <= processed <= process['end']:
                raise MarketDataError('Action process date outside its independent request window')
            if not effective['start'] <= ex_date <= effective['end']:
                raise MarketDataError('Action effective date outside reviewed coverage; widen coverage explicitly')
            action = {'id': row['id'], 'ex_date': ex_date}
            if category in {'forward_splits', 'reverse_splits'}:
                new, old = (_number(row[key], positive=True) for key in ('new_rate', 'old_rate'))
                with localcontext() as context:
                    context.prec = 34
                    ratio = Decimal(new) / Decimal(old)
                if ((category == 'forward_splits' and ratio <= 1) or
                        (category == 'reverse_splits' and ratio >= 1)):
                    raise MarketDataError('Split rates contradict their provider category')
                action.update(kind='split', ratio=str(ratio))
            else:
                # An empty Alpaca currency can mean unknown, not just USD.
                if row.get('currency') != 'USD':
                    raise MarketDataError('Dividend requires explicit matching USD currency evidence')
                action.update(kind='dividend', amount=_number(row['rate']), currency='USD')
            if row.get('payable_date') is not None:
                action['payment_date'] = _date(row['payable_date']).isoformat()
            result.append(action)
    return result


class AlpacaReplaySource:
    """Replay three independently paginated endpoint streams behind the source seam.

    Requests must match authored transcripts exactly. prepare() runs only after
    cache lookup, under the existing store lock; fetch_page() emits one canonical
    envelope containing every successful original response body and request.
    """
    def __init__(self, path, *, max_requests=100, retries=2, requests_per_second=2,
                 max_cooldown=60, clock=time.monotonic, sleep=time.sleep, now=utc_now):
        body = Path(path).read_bytes()
        if len(body) > 16 * 1024 * 1024:
            raise MarketDataError('Replay fixture exceeds 16 MiB')
        fixture = decode(body)
        _private(fixture)
        if (fixture.get('synthetic') is not True or type(fixture.get('schema_version')) is not int or
                fixture['schema_version'] != 1):
            raise MarketDataError('Only schema-1 authored synthetic Alpaca replay is enabled')
        if (type(max_requests) is not int or not 1 <= max_requests <= 1000 or
                type(retries) is not int or not 0 <= retries <= 5 or
                not math.isfinite(requests_per_second) or not 0 < requests_per_second <= 2 or
                not math.isfinite(max_cooldown) or not 0 <= max_cooldown <= 60):
            raise MarketDataError('Invalid Alpaca replay request limits')
        instrument = fixture['instrument']
        if instrument['currency'] != 'USD' or instrument['timezone'] != 'America/New_York' or instrument['mic'] not in {'XNAS', 'XNYS'}:
            raise MarketDataError('Replay supports explicitly identified USD US-equity fixtures only')
        if fixture['feed'] != 'iex' or fixture.get('symbol_asof') != '-':
            raise MarketDataError('Replay requires explicit IEX and disabled symbol remapping; no entitlement fallback')
        if fixture['adjustment'] not in {'raw', 'split', 'dividend'}:
            raise MarketDataError('Unsupported adjustment: all/spin-off require additional financial treatment')
        coverage = _range(fixture['coverage'])
        self.effective = _range(fixture['action_effective_coverage'])
        self.process = _range(fixture['action_process_coverage'])
        reviewed = fixture.get('action_review')
        if reviewed is not None and (not isinstance(reviewed, dict) or reviewed.get('synthetic') is not True or
                                     not isinstance(reviewed.get('basis'), str) or not reviewed['basis'].strip()):
            raise MarketDataError('Action completeness needs an explicit authored synthetic review')
        self.header = {'schema_version': 1, 'synthetic': True, 'provider': 'alpaca-replay', 'feed': 'iex',
                       'instrument': deepcopy(instrument), 'adjustment': fixture['adjustment'],
                       'adjustment_as_of': fixture.get('adjustment_as_of'), 'coverage': coverage,
                       'action_coverage': {**self.effective, 'complete': reviewed is not None}}
        self.responses = deepcopy(fixture['responses'])
        if not isinstance(self.responses, list) or not self.responses:
            raise MarketDataError('Replay needs independent endpoint responses')
        self.review = deepcopy(reviewed)
        self.fingerprint = hashlib.sha256(body).hexdigest()
        self.max_requests, self.retries = max_requests, retries
        self.interval, self.max_cooldown = 1 / requests_per_second, max_cooldown
        self.clock, self.sleep, self.now = clock, sleep, now
        self.requests, self.evidence, self._used = [], [], set()
        self._last, self._cooldown, self._page = None, 0, None

    def _wait(self, delay):
        if not math.isfinite(delay) or delay < 0 or delay > self.max_cooldown:
            raise MarketDataError('Alpaca replay cooldown exceeds bounded policy')
        if delay:
            self.sleep(delay)

    def _request(self, stream, params):
        for attempt in range(self.retries + 1):
            if len(self.requests) >= self.max_requests:
                raise MarketDataError('Alpaca replay shared request budget exhausted')
            self._wait(self._cooldown)
            self._cooldown = 0
            if self._last is not None:
                self._wait(max(0, self.interval - (self.clock() - self._last)))
            self._last = self.clock()
            matches = [(i, r) for i, r in enumerate(self.responses) if i not in self._used and
                       r.get('stream') == stream and r.get('params') == params]
            if not matches:
                raise MarketDataError('No synthetic transcript matches the planned endpoint request')
            index, response = matches[0]
            self._used.add(index)
            status = response['status']
            if type(status) is not int:
                raise MarketDataError('Invalid replay HTTP status')
            headers = {key.lower(): value for key, value in response.get('headers', {}).items()}
            if set(headers) - SAFE_HEADERS or any(not isinstance(v, str) for v in headers.values()):
                raise MarketDataError('Unsafe or invalid replay response headers')
            request = {'endpoint': ENDPOINTS[stream], 'params': deepcopy(params), 'status': status}
            self.requests.append(request)
            now = _time(self.now())
            quota_delay = _quota_delay(status, headers, now)
            if status == 200:
                body = response['body']
                if not isinstance(body, str) or len(body.encode()) > 16 * 1024 * 1024:
                    raise MarketDataError('Invalid or oversized replay response body')
                parsed = _json(body)
                self.evidence.append({**request, 'headers': headers, 'body': body,
                                      'sha256': hashlib.sha256(body.encode()).hexdigest(),
                                      'observed_at': now.isoformat()})
                self._cooldown = quota_delay
                return parsed
            if status not in {429, 500, 502, 503, 504} or attempt == self.retries:
                raise MarketDataError(f'Alpaca replay HTTP {status}; no paid/feed/authentication fallback')
            self._wait(_retry_delay(headers, now, attempt, quota_delay))
        raise AssertionError('unreachable')

    def _pages(self, stream, params, field):
        token, seen = None, set()
        while True:
            query = {**params, **({'page_token': token} if token is not None else {})}
            page = self._request(stream, query)
            next_token = _next(page, field)
            yield page[field]
            if next_token is None:
                return
            if next_token in seen:
                raise MarketDataError('Repeated provider pagination token')
            seen.add(next_token)
            token = next_token

    def prepare(self, *, start, end, request_budget=None, previous=None):
        """Fetch independent replay streams; their request count shares one budget."""
        if request_budget is not None:
            if type(request_budget) is not int or request_budget < 1:
                raise MarketDataError('Invalid shared preparation request budget')
            self.max_requests = min(self.max_requests, request_budget)
        try:
            return self._prepare(start, end)
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, MarketDataError):
                raise
            raise MarketDataError('Missing or incompatible Alpaca replay evidence') from exc

    def _prepare(self, start, end):
        if self._page is not None or self.requests:
            raise MarketDataError('Create a fresh replay source for each collection attempt')
        _range({'start': start, 'end': end})
        # A daily aggregation includes extended trading, not only regular close.
        finish = datetime.combine(_date(end) + timedelta(days=1), daytime(0), NY)
        if _time(self.now()) < finish + timedelta(minutes=15):
            raise MarketDataError('Requested daily aggregation has not ended plus conservative feed delay')
        calendar_bounds = self.header['coverage']
        calendar = _calendar(self._request('calendar', {**calendar_bounds, 'date_type': 'TRADING'}), **calendar_bounds)
        symbol = self.header['instrument']['symbol']
        first = datetime.combine(_date(start), daytime(0), NY)
        params = {'symbols': symbol, 'timeframe': '1Day', 'start': first.isoformat(),
                  'end': (finish - timedelta(microseconds=1)).isoformat(), 'limit': 10000,
                  'adjustment': self.header['adjustment'], 'feed': 'iex', 'asof': '-', 'currency': 'USD', 'sort': 'asc'}
        bars = [bar for page in self._pages('bars', params, 'bars') for bar in _bars(page, symbol, start, end)]
        action_params = {'symbols': symbol, **self.process, 'limit': 1000, 'sort': 'asc', 'region': 'us', 'data_quality': 'all'}
        actions = [action for page in self._pages('actions', action_params, 'corporate_actions')
                   for action in _actions(page, symbol, self.process, self.effective)]
        metadata = {'mode': 'synthetic_alpaca_replay', 'live_validated': False, 'adapter_version': '1',
                    'symbol_asof': '-', 'action_process_coverage': self.process,
                    'action_completeness_basis': self.review,
                    'adjustment_anchor_kind': 'authored_synthetic_snapshot' if self.header['adjustment'] != 'raw' else None,
                    'provider_requests': deepcopy(self.requests),
                    'limitations': ['Process-date pagination and data_quality do not establish ex-date completeness',
                                    'Calendar is US market evidence, not historical listing-life evidence',
                                    'No historical publication timestamp inferred from daily t or action process_date']}
        self._window = {'start': start, 'end': end}
        self._page = json.dumps({'bars': bars, 'actions': actions, 'next_page_token': None,
                                 'alpaca_response_evidence': self.evidence}, sort_keys=True, allow_nan=False).encode()
        return {**deepcopy(self.header), 'coverage': self._window, 'calendar': calendar, 'source_metadata': metadata,
                'evidence_observed_through': max(e['observed_at'] for e in self.evidence)}

    def fetch_page(self, *, start, end, token):
        if self._page is None or token is not None or self._window != {'start': start, 'end': end}:
            raise MarketDataError('Replay source not prepared for this exact collection window')
        return self._page
