"""Read-only Alpaca historical acquisition; no order or live-trading interface."""
from dataclasses import dataclass, field
from copy import deepcopy
from datetime import datetime, time as daytime, timedelta
import hashlib
from http.client import HTTPException
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .alpaca_probe import _credentials, _NoRedirect
from .alpaca_replay import ENDPOINTS, SAFE_HEADERS, NY, _json, _next, _quota_delay, _retry_delay
from .market_data import MarketDataError, _date, _time
from .storage import utc_now


@dataclass(frozen=True)
class AlpacaResponse:
    stream: str
    params: dict
    body: bytes = field(repr=False)
    observed_at: str
    endpoint: str
    headers: dict

    def json(self):
        return _json(self.body)

    def evidence(self):
        return {'stream': self.stream, 'params': deepcopy(self.params), 'endpoint': self.endpoint,
                'body': self.body.decode('utf-8'), 'sha256': hashlib.sha256(self.body).hexdigest(),
                'observed_at': self.observed_at, 'headers': deepcopy(self.headers)}


class AlpacaClient:
    """One shared bounded limiter/budget for an entire watchlist invocation.

    Only fixed GET market bars/actions and paper-host calendar/asset routes exist.
    Authentication and error bodies never enter response provenance.
    """
    def __init__(self, env_file='.env', *, max_requests=100, retries=2, timeout=30,
                 requests_per_second=2, max_cooldown=60, opener=None,
                 clock=time.monotonic, sleep=time.sleep, now=utc_now):
        if (type(max_requests) is not int or not 1 <= max_requests <= 1000 or
                type(retries) is not int or not 0 <= retries <= 5 or
                not math.isfinite(timeout) or not 0 < timeout <= 60 or
                not math.isfinite(requests_per_second) or not 0 < requests_per_second <= 2 or
                not math.isfinite(max_cooldown) or not 0 <= max_cooldown <= 60):
            raise MarketDataError('Invalid bounded Alpaca client policy')
        self._identity = _credentials(env_file)
        self._open = opener or build_opener(_NoRedirect()).open
        self.max_requests, self.retries, self.timeout = max_requests, retries, timeout
        self.interval, self.max_cooldown = 1 / requests_per_second, max_cooldown
        self.clock, self.sleep, self.now = clock, sleep, now
        self.requests, self._last, self._cooldown = [], None, 0

    def _wait(self, delay):
        if not math.isfinite(delay) or not 0 <= delay <= self.max_cooldown:
            raise MarketDataError('Alpaca cooldown exceeds bounded policy')
        if delay:
            self.sleep(delay)

    def get(self, stream, params):
        params = dict(params)
        allowed = {'asset': {'symbol'}, 'calendar': {'start', 'end', 'date_type'},
                   'bars': {'symbols', 'timeframe', 'start', 'end', 'limit', 'adjustment', 'feed', 'asof', 'currency', 'page_token', 'sort'},
                   'actions': {'symbols', 'start', 'end', 'limit', 'sort', 'region', 'data_quality', 'page_token'}}
        if stream not in allowed or set(params) - allowed[stream]:
            raise MarketDataError('Unsupported endpoint query fields; credentials belong only in authentication headers')
        if any(self._identity.key in str(value) or self._identity.secret in str(value) for value in params.values()):
            raise MarketDataError('Credentials are forbidden in query/provenance fields')
        if stream == 'asset':
            symbol = params.pop('symbol', None)
            if params or not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,19}', symbol):
                raise MarketDataError('Invalid asset lookup')
            endpoint = 'https://paper-api.alpaca.markets/v2/assets/' + symbol
            recorded = {'symbol': symbol}
        elif stream in {'bars', 'actions', 'calendar'}:
            endpoint, recorded = ENDPOINTS[stream], dict(params)
        else:
            raise MarketDataError('Only read-only historical market routes are supported')
        if stream == 'bars':
            if (params.get('feed') not in {'sip', 'iex'} or params.get('timeframe') != '1Day' or
                    params.get('adjustment') not in {'raw', 'split', 'dividend'}):
                raise MarketDataError('Explicit free historical feed, daily timeframe and supported adjustment required')
            end = params.get('end')
            bound = _time(end) if isinstance(end, str) and 'T' in end else datetime.combine(_date(end) + timedelta(days=1), daytime(0), NY)
            if bound > _time(self.now()) - timedelta(minutes=15):
                raise MarketDataError('Recent/paid feed access is disabled; request completed historical days')
        url = endpoint + ('?' + urlencode(params) if params else '')
        for attempt in range(self.retries + 1):
            if len(self.requests) >= self.max_requests:
                raise MarketDataError('Shared Alpaca request budget exhausted')
            self._wait(self._cooldown)
            self._cooldown = 0
            if self._last is not None:
                self._wait(max(0, self.interval - (self.clock() - self._last)))
            self._last = self.clock()
            request = Request(url, method='GET', headers={
                'APCA-API-KEY-ID': self._identity.key, 'APCA-API-SECRET-KEY': self._identity.secret,
                'Accept': 'application/json', 'Accept-Encoding': 'identity', 'User-Agent': 'InvestorAI/0.1 private research'})
            self.requests.append({'stream': stream, 'endpoint': endpoint, 'params': recorded})
            status, headers, body = None, {}, None
            try:
                with self._open(request, timeout=self.timeout) as response:
                    if response.geturl() != url:
                        raise MarketDataError('Alpaca redirects are forbidden')
                    status = response.status
                    headers = {str(k).lower(): str(v) for k, v in response.headers.items() if str(k).lower() in SAFE_HEADERS}
                    if status == 200:
                        body = response.read(16 * 1024 * 1024 + 1)
                        if len(body) > 16 * 1024 * 1024:
                            raise MarketDataError('Alpaca response exceeded 16 MiB')
            except HTTPError as exc:
                status = exc.code
                headers = {str(k).lower(): str(v) for k, v in exc.headers.items() if str(k).lower() in SAFE_HEADERS}
                exc.close()
            except (URLError, TimeoutError, OSError, HTTPException):
                if attempt == self.retries:
                    raise MarketDataError('Alpaca network request failed; sensitive diagnostics withheld') from None
                self._wait(2 ** attempt)
                continue
            self.requests[-1]['status'] = status
            observed = _time(self.now())
            quota = _quota_delay(status, headers, observed)
            if status == 200:
                _json(body)
                self._cooldown = quota
                return AlpacaResponse(stream, deepcopy(recorded), body, observed.isoformat(), endpoint, deepcopy(headers))
            if status not in {429, 500, 502, 503, 504} or attempt == self.retries:
                raise MarketDataError(f'Alpaca HTTP {status}; no authentication/feed/paid fallback')
            self._wait(_retry_delay(headers, observed, attempt, quota))
        raise AssertionError('unreachable')
