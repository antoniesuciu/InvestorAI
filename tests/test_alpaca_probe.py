"""Read-only probe policy, using dummy credentials and synthetic HTTP responses."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from email.message import Message
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from investor_ai.alpaca_probe import probe_access, _credentials, _NoRedirect
from investor_ai.cli import main
from investor_ai.market_data import MarketDataError

NOW = '2025-01-01T00:00:00+00:00'
KEY = 'SYNTHETIC_KEY_NOT_REAL'
SECRET = 'SYNTHETIC_SECRET_NOT_REAL'
DAY = '2024-11-01'
PAYLOAD = ('{"bars":{"AAPL":[{"t":"2024-11-01T04:00:00Z","o":100.01,"h":102,"l":99,"c":101,"v":100}],'
           '"MSFT":[{"t":"2024-11-01T04:00:00Z","o":200,"h":204,"l":198,"c":202,"v":200}]},"next_page_token":null}').encode()


class Reply(BytesIO):
    def __init__(self, url, body=PAYLOAD, status=200, headers=None):
        super().__init__(body)
        self.url, self.status, self.headers = url, status, headers or {}

    def geturl(self):
        return self.url


class AlpacaProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = self.root / '.env'
        self.env.write_text(f'ALPACA_API_KEY_ID={KEY}\nALPACA_SECRET_KEY="{SECRET}"\nALPACA_PAPER=true\n')
        for name in ('socket.getaddrinfo', 'socket.socket.connect', 'socket.socket.connect_ex'):
            block = patch(name, side_effect=AssertionError('Network forbidden in tests'))
            block.start()
            self.addCleanup(block.stop)
        self.requests = []

    def opener(self, request, timeout):
        self.requests.append(request)
        self.assertEqual(request.get_method(), 'GET')
        url = urlparse(request.full_url)
        self.assertEqual((url.scheme, url.hostname, url.path), ('https', 'data.alpaca.markets', '/v2/stocks/bars'))
        query = parse_qs(url.query)
        self.assertEqual(query['symbols'], ['AAPL,MSFT'])
        self.assertEqual(query['timeframe'], ['1Day'])
        self.assertEqual(query['limit'], ['2'])
        self.assertNotIn(KEY, request.full_url)
        self.assertNotIn(SECRET, request.full_url)
        self.assertEqual(request.get_header('Apca-api-key-id'), KEY)
        self.assertEqual(request.get_header('Apca-api-secret-key'), SECRET)
        return Reply(request.full_url)

    def probe(self, **options):
        return probe_access(self.env, opener=self.opener, now=lambda: NOW,
                            sleep=lambda _: None, **options)

    def test_explicit_two_feed_probes_validate_and_return_only_summaries(self):
        before = set(self.root.rglob('*'))
        result = self.probe(feeds=('iex', 'sip'))
        self.assertEqual(result['requests_attempted'], 2)
        self.assertTrue(all(row['sample_complete'] for row in result['results']))
        self.assertEqual(result['results'][0]['symbol_counts'], {'AAPL': 1, 'MSFT': 1})
        self.assertFalse(result['trading_endpoints_used'])
        self.assertFalse(result['raw_response_persisted'])
        self.assertEqual(set(self.root.rglob('*')), before)
        text = json.dumps(result)
        for forbidden in (KEY, SECRET, '100.01', '2024-11-01T04:00:00Z', 'APCA-API-KEY-ID'):
            self.assertNotIn(forbidden, text)

    def test_dotenv_literals_alias_conflicts_and_invalid_values_are_redacted(self):
        credentials = _credentials(self.env)
        self.assertNotIn(KEY, repr(credentials))
        self.assertNotIn(SECRET, repr(credentials))
        for content in (f'ALPACA_API_KEY_ID={KEY}\nALPACA_API_KEY=DIFFERENT_TOKEN\nALPACA_SECRET_KEY={SECRET}',
                        f'ALPACA_API_KEY_ID={KEY}\nALPACA_SECRET_KEY={SECRET}\nALPACA_PAPER=false',
                        f'ALPACA_API_KEY_ID={KEY}\nALPACA_SECRET_KEY="{SECRET}',
                        f'ALPACA_API_KEY_ID={KEY}\nALPACA_SECRET_KEY=$(echo {SECRET})'):
            self.env.write_text(content)
            with self.assertRaises(MarketDataError) as error:
                self.probe()
            self.assertNotIn(KEY, str(error.exception))
            self.assertNotIn(SECRET, str(error.exception))
        self.assertEqual(self.requests, [])

    def test_no_automatic_feed_fallback_and_authentication_failure_stops(self):
        def denied(request, timeout):
            self.requests.append(request)
            error = HTTPError(request.full_url, 401, SECRET, Message(), BytesIO(SECRET.encode()))
            raise error
        result = probe_access(self.env, feeds=('iex', 'sip'), opener=denied, now=lambda: NOW, sleep=lambda _: None)
        self.assertEqual(result['requests_attempted'], 1)
        self.assertEqual(result['results'][0]['http_status'], 401)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_redirects_are_rejected_and_never_authorize_trading_requests(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://paper-api.alpaca.markets/v2/orders'))
        for status in (301, 302, 403):
            def fail(request, timeout):
                self.requests.append(request)
                raise HTTPError(request.full_url, status, SECRET, Message(), BytesIO(SECRET.encode()))
            result = probe_access(self.env, opener=fail, now=lambda: NOW, sleep=lambda _: None)
            self.assertEqual(result['requests_attempted'], 1)
            self.assertEqual(result['results'][0]['http_status'], status)
            self.assertNotIn(SECRET, json.dumps(result))
        result = probe_access(self.env, opener=lambda req, timeout: Reply('https://paper-api.alpaca.markets/v2/account'), now=lambda: NOW)
        self.assertEqual(result['results'][0]['result'], 'response_or_policy_failure')

    def test_partial_or_empty_symbols_are_not_complete_samples(self):
        for values in ({}, {'AAPL': json.loads(PAYLOAD)['bars']['AAPL']}):
            payload = json.dumps({'bars': values, 'next_page_token': None}).encode()
            result = probe_access(self.env, opener=lambda req, timeout: Reply(req.full_url, payload), now=lambda: NOW)
            self.assertFalse(result['results'][0]['sample_complete'])
        payload = json.loads(PAYLOAD)
        payload['next_page_token'] = 'synthetic-next-page'
        result = probe_access(self.env, opener=lambda req, timeout: Reply(req.full_url, json.dumps(payload).encode()), now=lambda: NOW)
        self.assertTrue(result['results'][0]['more_pages'])
        self.assertFalse(result['results'][0]['sample_complete'])
        self.assertEqual(result['requests_attempted'], 1)

    def test_inconsistent_financial_values_and_oversized_bodies_are_not_exposed(self):
        payload = json.loads(PAYLOAD)
        payload['bars']['AAPL'][0]['high'] = 1
        payload['bars']['AAPL'][0]['h'] = 1
        for body in (json.dumps(payload).encode(), b'x' * (1024 * 1024 + 1), SECRET.encode()):
            result = probe_access(self.env, opener=lambda req, timeout: Reply(req.full_url, body), now=lambda: NOW)
            self.assertEqual(result['results'][0]['result'], 'response_or_policy_failure')
            self.assertNotIn(SECRET, json.dumps(result))

    def test_retry_reset_policy_and_shared_budget_are_bounded(self):
        calls, delays = [], []
        def transient(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                headers = Message()
                headers['X-RateLimit-Remaining'] = '0'
                headers['X-RateLimit-Reset'] = '1735689603'
                raise HTTPError(request.full_url, 429, SECRET, headers, BytesIO(SECRET.encode()))
            return Reply(request.full_url)
        result = probe_access(self.env, opener=transient, now=lambda: NOW, sleep=delays.append)
        self.assertIn(3, delays)
        self.assertEqual(result['requests_attempted'], 2)
        self.assertTrue(result['results'][0]['sample_complete'])
        result = self.probe(feeds=('iex', 'sip'), max_requests=1)
        self.assertEqual(result['requests_attempted'], 1)
        self.assertEqual(result['results'][-1]['result'], 'response_or_policy_failure')

    def test_long_cooldown_or_network_failure_stops_without_sensitive_exception_text(self):
        def limited(request, timeout):
            headers = Message()
            headers['Retry-After'] = '61'
            raise HTTPError(request.full_url, 429, SECRET, headers, BytesIO(SECRET.encode()))
        for opener in (limited, lambda req, timeout: (_ for _ in ()).throw(URLError(SECRET))):
            result = probe_access(self.env, feeds=('iex', 'sip'), opener=opener, now=lambda: NOW, sleep=lambda _: None)
            self.assertEqual(result['requests_attempted'], 1)
            self.assertEqual(len(result['results']), 1)
            self.assertNotIn(SECRET, json.dumps(result))

    def test_probe_scope_and_historical_day_limits_are_checked_before_credentials(self):
        for options in ({'feeds': ('boats',)}, {'feeds': ('iex', 'iex')},
                        {'dates': ('2025-01-01',)}, {'dates': (DAY,) * 4},
                        {'max_requests': 13}, {'requests_per_second': 3}):
            with self.subTest(options=options), self.assertRaises(MarketDataError):
                self.probe(**options)
        self.assertEqual(self.requests, [])

    def test_cli_probe_cannot_touch_market_storage(self):
        output = StringIO()
        with patch('investor_ai.alpaca_probe.build_opener') as builder, \
                patch('investor_ai.market_cli.MarketStore', side_effect=AssertionError('No market store for probes')), \
                redirect_stdout(output):
            builder.return_value.open = self.opener
            self.assertEqual(main(['market', 'probe', '--env-file', str(self.env)]), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result['results'][0]['sample_complete'])
        self.assertNotIn(SECRET, output.getvalue())
        self.assertEqual(len(self.requests), 1)
