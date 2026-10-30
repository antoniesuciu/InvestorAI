"""Bounded read-only access diagnostics. Raw real data stays in memory only.

This module cannot collect into MarketStore and has no Trading API route.
Technical access never confers archive, redistribution or feed-use permission.
"""

from dataclasses import dataclass, field
from datetime import datetime, time as daytime, timedelta
from decimal import Decimal
from http.client import HTTPException
import math
from pathlib import Path
import re
import shlex
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .alpaca_replay import ENDPOINTS, NY, _json, _bars, _next, _quota_delay, _retry_delay
from .market_data import MarketDataError, _date, _time
from .storage import utc_now

KEY_NAMES = ('ALPACA_API_KEY_ID', 'APCA_API_KEY_ID', 'ALPACA_API_KEY')
SECRET_NAMES = ('ALPACA_SECRET_KEY', 'APCA_API_SECRET_KEY', 'ALPACA_API_SECRET')
SYMBOLS = ('AAPL', 'MSFT')


@dataclass(frozen=True)
class _Credentials:
    key: str = field(repr=False)
    secret: str = field(repr=False)


def _credentials(path):
    """Parse selected dotenv literals; never source shell code or expand variables."""
    try:
        body = Path(path).read_bytes()
        if len(body) > 64 * 1024:
            raise ValueError('Oversized configuration')
        values = {}
        for line in body.decode().splitlines():
            match = re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)', line)
            if match and match[1] in (*KEY_NAMES, *SECRET_NAMES, 'ALPACA_PAPER'):
                parts = shlex.split(match[2], comments=True, posix=True)
                if len(parts) != 1 or match[1] in values:
                    raise ValueError('Invalid or duplicate setting')
                values[match[1]] = parts[0]
        if 'ALPACA_PAPER' in values and values['ALPACA_PAPER'].lower() not in {'true', '1', 'yes'}:
            raise ValueError('Explicit paper context required')
        def unique(names):
            candidates = {values[name] for name in names if name in values}
            if len(candidates) != 1:
                raise ValueError('Missing or conflicting aliases')
            value = candidates.pop()
            if not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value):
                raise ValueError('Invalid credential format')
            return value
        return _Credentials(unique(KEY_NAMES), unique(SECRET_NAMES))
    except (OSError, UnicodeError, ValueError):
        raise MarketDataError('Missing, invalid or conflicting Alpaca paper credentials; values withheld') from None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _summary(body, day):
    """Validate temporary financial values, returning metadata without prices."""
    payload = _json(body)
    token = _next(payload, 'bars')
    values = payload['bars']
    if not isinstance(values, dict) or set(values) - set(SYMBOLS):
        raise MarketDataError('Unexpected probe symbols')
    counts = {}
    for symbol in SYMBOLS:
        rows = _bars({symbol: values.get(symbol, [])}, symbol, day, day)
        if len(rows) > 1:
            raise MarketDataError('Conflicting or duplicate daily probe bars')
        for row in rows:
            # Independent ordering check; fixed-point strings are exact here.
            o, h, l, c = (Decimal(row[key]) for key in ('open', 'high', 'low', 'close'))
            if not l <= min(o, c) <= max(o, c) <= h:
                raise MarketDataError('Inconsistent probe OHLC')
        counts[symbol] = len(rows)
    return {'symbol_counts': counts, 'ohlcv_validation': 'passed', 'more_pages': token is not None,
            'sample_complete': token is None and all(counts[symbol] == 1 for symbol in SYMBOLS)}


def probe_access(env_file='.env', *, feeds=('iex',), dates=('2024-11-01',), max_requests=8,
                 retries=1, timeout=20, requests_per_second=2, max_cooldown=5,
                 opener=None, clock=time.monotonic, sleep=time.sleep, now=utc_now):
    """GET at most two symbols/one day per query; expose status/counts only.

    No general URL, method, endpoint, credential argument or storage interface is
    exposed. SIP is an explicit diagnostic option, never an automatic fallback.
    Pagination is reported rather than followed; this is not bulk acquisition.
    """
    if (not isinstance(feeds, (tuple, list)) or not 1 <= len(feeds) <= 2 or
            len(set(feeds)) != len(feeds) or any(feed not in {'iex', 'sip'} for feed in feeds) or
            not isinstance(dates, (tuple, list)) or not 1 <= len(dates) <= 3 or
            len(set(dates)) != len(dates)):
        raise MarketDataError('Probe accepts one/two explicit feeds and up to three distinct dates')
    if (type(max_requests) is not int or not 1 <= max_requests <= 12 or
            type(retries) is not int or not 0 <= retries <= 2 or
            not math.isfinite(timeout) or not 0 < timeout <= 30 or
            not math.isfinite(requests_per_second) or not 0 < requests_per_second <= 2 or
            not math.isfinite(max_cooldown) or not 0 <= max_cooldown <= 60):
        raise MarketDataError('Invalid bounded probe policy')
    at = _time(now())
    for day in dates:
        end = datetime.combine(_date(day) + timedelta(days=1), daytime(0), NY)
        if at < end + timedelta(minutes=15):
            raise MarketDataError('Probe requires an ended historical day plus feed delay')
    credentials = _credentials(env_file)
    open_request = opener or build_opener(_NoRedirect()).open
    attempts, last, cooldown = 0, None, 0
    results = []

    def wait(seconds):
        if not math.isfinite(seconds) or not 0 <= seconds <= max_cooldown:
            raise MarketDataError('Probe cooldown exceeds bounded policy')
        if seconds:
            sleep(seconds)

    stop = False
    for day in dates:
        for feed in feeds:
            start = datetime.combine(_date(day), daytime(0), NY)
            finish = datetime.combine(_date(day) + timedelta(days=1), daytime(0), NY)
            params = {'symbols': ','.join(SYMBOLS), 'timeframe': '1Day', 'start': start.isoformat(),
                      'end': (finish - timedelta(microseconds=1)).isoformat(), 'limit': 2,
                      'adjustment': 'raw', 'feed': feed, 'asof': '-', 'currency': 'USD', 'sort': 'asc'}
            url = ENDPOINTS['bars'] + '?' + urlencode(params)
            item = {'feed': feed, 'market_date': day, 'raw_response_persisted': False}
            try:
                for attempt in range(retries + 1):
                    if attempts >= max_requests:
                        raise MarketDataError('Probe request budget exhausted')
                    wait(cooldown)
                    cooldown = 0
                    if last is not None:
                        wait(max(0, 1 / requests_per_second - (clock() - last)))
                    last = clock()
                    request = Request(url, method='GET', headers={
                        'APCA-API-KEY-ID': credentials.key, 'APCA-API-SECRET-KEY': credentials.secret,
                        'Accept': 'application/json', 'Accept-Encoding': 'identity',
                        'User-Agent': 'InvestorAI/0.1 read-only access verification'})
                    attempts += 1
                    status, headers, body = None, {}, None
                    try:
                        with open_request(request, timeout=timeout) as response:
                            if response.geturl() != url:
                                raise MarketDataError('Probe redirects are forbidden')
                            status = response.status
                            headers = {str(k).lower(): str(v) for k, v in response.headers.items()
                                       if str(k).lower().startswith('x-ratelimit-') or str(k).lower() == 'retry-after'}
                            if status == 200:
                                body = response.read(1024 * 1024 + 1)
                                if len(body) > 1024 * 1024:
                                    raise MarketDataError('Probe response exceeded 1 MiB')
                    except HTTPError as exc:
                        status = exc.code
                        headers = {str(k).lower(): str(v) for k, v in exc.headers.items()
                                   if str(k).lower().startswith('x-ratelimit-') or str(k).lower() == 'retry-after'}
                        exc.close()  # Error bodies can contain account details; do not read them.
                    item['http_status'] = status
                    quota = _quota_delay(status, headers, _time(now()))
                    if status == 200:
                        item.update(_summary(body, day))
                        cooldown = quota
                        break
                    if status in {429, 500, 502, 503, 504} and attempt < retries:
                        wait(_retry_delay(headers, _time(now()), attempt, quota))
                        continue
                    item['result'] = ('authentication_or_entitlement_error' if status in {401, 403} else 'http_error')
                    stop = status in {401, 429} or status in {500, 502, 503, 504}
                    break
            except (URLError, TimeoutError, OSError, HTTPException):
                item['result'] = 'network_unavailable'
                stop = True
            except (MarketDataError, ValueError, KeyError, TypeError):
                item['result'] = 'response_or_policy_failure'
                stop = True
            results.append(item)
            if stop:
                break
        if stop:
            break
    return {'checked_at': _time(now()).isoformat(), 'endpoint': ENDPOINTS['bars'],
            'mode': 'temporary_read_only_access_probe', 'requests_attempted': attempts,
            'trading_endpoints_used': False, 'raw_response_persisted': False,
            'retention_permission': 'unresolved', 'sip_use_permission': 'unresolved',
            'results': results}
