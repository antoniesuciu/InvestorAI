"""Local-only, read-only HTTP facade. Run with a loopback uvicorn binding."""
from datetime import date
import ipaddress
import os
import re
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .analysis import cutoff
from .dashboard_models import CompanyList, ComparisonReport, FinancialReport, MarketReport
from .dashboard_service import DashboardError, DashboardService

ORIGINS = ('http://localhost:5173', 'http://127.0.0.1:5173')
TICKER = re.compile(r'[A-Z0-9][A-Z0-9.\-]{0,14}\Z')
FEED = re.compile(r'[a-z][a-z0-9_\-]{0,31}\Z')


def _error(status, code, message):
    return JSONResponse(status_code=status, content={'detail': {'code': code, 'message': message}})


def _ticker(value):
    value = value.upper()
    if not TICKER.fullmatch(value):
        raise DashboardError(422, 'invalid_parameters', 'Use a valid locally collected ticker symbol.')
    return value


def _parameters(start, end, as_of):
    try:
        for value in (start, end):
            if value is not None and (len(value) != 10 or date.fromisoformat(value).isoformat() != value):
                raise ValueError
        if start is not None and end is not None and start > end:
            raise ValueError
        return cutoff(as_of)
    except (TypeError, ValueError, OverflowError):
        raise DashboardError(422, 'invalid_parameters',
            'Use ordered ISO calendar dates and a timezone-aware cutoff timestamp (or a UTC end-of-day date).') from None


def create_app(*, sec_dir=None, market_dir=None, service=None):
    """Paths are server-side settings only; there is no file or credential API."""
    adapter = service or DashboardService(
        sec_dir or os.environ.get('INVESTOR_SEC_DATA_DIR', 'data/sec'),
        market_dir or os.environ.get('INVESTOR_MARKET_DATA_DIR', 'data/market'))
    application = FastAPI(title='InvestorAI local research API', version='1', docs_url=None, redoc_url=None)
    application.state.service = adapter
    application.add_middleware(CORSMiddleware, allow_origins=list(ORIGINS), allow_credentials=False,
                               allow_methods=['GET', 'OPTIONS'], allow_headers=['Accept'])
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]'])

    @application.middleware('http')
    async def local_boundary(request: Request, call_next):
        # Origin rejection protects GET responses too; CORS alone only controls browser readability.
        origin = request.headers.get('origin')
        client = request.client.host if request.client else None
        try:
            local = client is not None and ipaddress.ip_address(client).is_loopback
        except ValueError:
            local = client == 'testclient'  # Starlette's in-process test transport, never a TCP address.
        if not local or (origin is not None and origin not in ORIGINS):
            response = _error(403, 'local_access_only', 'Only the local dashboard origin and loopback clients are permitted.')
        elif request.method not in {'GET', 'OPTIONS'}:
            response = _error(405, 'read_only', 'The dashboard API is read-only.')
        else:
            try:
                response = await call_next(request)
            except Exception:
                # Do not expose filesystem paths, provider responses, credentials, or raw validation inputs.
                response = _error(503, 'local_data_unavailable', 'Local data could not be read or validated. Inspect the local store separately.')
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @application.exception_handler(DashboardError)
    async def domain_error(request, exc):
        return _error(exc.status, exc.code, exc.message)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return _error(422, 'invalid_parameters', 'One or more query parameters are invalid.')

    @application.get('/api/health')
    def health():
        return {'status': 'ok', 'read_only': True, 'offline': True}

    @application.get('/api/companies', response_model=CompanyList)
    def companies():
        return adapter.companies()

    @application.get('/api/companies/{ticker}/financials', response_model=FinancialReport)
    def financials(ticker: str, frequency: Literal['annual', 'quarterly'] = 'annual',
                   start: str | None = Query(None, max_length=10), end: str | None = Query(None, max_length=10),
                   as_of: str | None = Query(None, max_length=40)):
        at = _parameters(start, end, as_of)
        return adapter.financials(_ticker(ticker), frequency=frequency, start=start, end=end, as_of=at)

    @application.get('/api/companies/{ticker}/market', response_model=MarketReport)
    def market(ticker: str, adjustment: Literal['raw', 'split', 'dividend'] = 'split',
               feed: str = Query('sip', max_length=32), start: str | None = Query(None, max_length=10),
               end: str | None = Query(None, max_length=10), as_of: str | None = Query(None, max_length=40),
               adjustment_as_of: str | None = Query(None, max_length=10)):
        at = _parameters(start, end, as_of)
        if not FEED.fullmatch(feed):
            raise DashboardError(422, 'invalid_parameters', 'Use a valid local feed identifier.')
        if adjustment_as_of is not None:
            _parameters(adjustment_as_of, None, as_of)
        return adapter.market(_ticker(ticker), adjustment=adjustment, feed=feed, start=start, end=end,
                              as_of=at, adjustment_as_of=adjustment_as_of)

    @application.get('/api/compare', response_model=ComparisonReport)
    def compare(tickers: str = Query(..., max_length=100), frequency: Literal['annual', 'quarterly'] = 'annual',
                start: str | None = Query(None, max_length=10), end: str | None = Query(None, max_length=10),
                as_of: str | None = Query(None, max_length=40)):
        symbols = [_ticker(value.strip()) for value in tickers.split(',')]
        if not 2 <= len(symbols) <= 4 or len(set(symbols)) != len(symbols):
            raise DashboardError(422, 'invalid_parameters', 'Select two to four distinct local companies.')
        at = _parameters(start, end, as_of)
        return adapter.compare(symbols, frequency=frequency, start=start, end=end, as_of=at)

    return application


app = create_app()
