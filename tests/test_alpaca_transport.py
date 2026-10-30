"""Read-only Alpaca HTTP contracts exercised without network or real credentials."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.message import Message
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from investor_ai.alpaca import AlpacaClient
from investor_ai.market_data import MarketDataError


KEY = 'SYNTHETIC_TRANSPORT_KEY'
SECRET = 'SYNTHETIC_TRANSPORT_SECRET'
PAYLOAD = b'{"bars":{},"next_page_token":"synthetic-page-2","price":100.010000001}'
BAR_QUERY = {'symbols': 'AAPL,MSFT', 'timeframe': '1Day', 'start': '2024-01-02',
             'end': '2024-01-03', 'feed': 'sip', 'adjustment': 'raw', 'limit': 2}


class Clock:
    def __init__(self):
        self.elapsed = 0.0
        self.delays = []

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.elapsed += seconds

    def now(self):
        return (datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=self.elapsed)).isoformat()


class Reply(BytesIO):
    status = 200

    def __init__(self, url, body=PAYLOAD, headers=None):
        super().__init__(body)
        self.url = url
        self.headers = headers or {'Content-Type': 'application/json'}

    def geturl(self):
        return self.url


class UnreadErrorBody(BytesIO):
    def read(self, *args):
        raise AssertionError('An authentication/error body must not be read')


class AlpacaTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = Path(self.temp.name) / '.env'
        self.env.write_text(f'ALPACA_API_KEY_ID={KEY}\nALPACA_SECRET_KEY={SECRET}\nALPACA_PAPER=true\n')
        self.clock = Clock()
        for target in ('socket.getaddrinfo', 'socket.socket.connect', 'socket.socket.connect_ex'):
            guard = patch(target, side_effect=AssertionError('Transport tests cannot use network'))
            guard.start()
            self.addCleanup(guard.stop)

    def client(self, opener, **options):
        return AlpacaClient(self.env, opener=opener, clock=lambda: self.clock.elapsed,
                            sleep=self.clock.sleep, now=self.clock.now, **options)

    def test_supported_routes_are_get_only_with_header_auth_and_safe_provenance(self):
        seen = []
        def opener(request, timeout):
            seen.append(request)
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)
            self.assertEqual(request.get_header('Apca-api-key-id'), KEY)
            self.assertEqual(request.get_header('Apca-api-secret-key'), SECRET)
            self.assertNotIn(KEY, request.full_url)
            self.assertNotIn(SECRET, request.full_url)
            return Reply(request.full_url, headers={'Content-Type': 'application/json',
                         'X-Request-ID': 'synthetic-request', 'Set-Cookie': 'do-not-retain',
                         'Authorization': 'do-not-retain'})
        client = self.client(opener)
        specs = [('bars', BAR_QUERY), ('actions', {'symbols': 'AAPL', 'start': '2024-01-01', 'end': '2024-01-03'}),
                 ('calendar', {'start': '2024-01-01', 'end': '2024-01-03', 'date_type': 'TRADING'}),
                 ('asset', {'symbol': 'AAPL'})]
        expected = [('data.alpaca.markets', '/v2/stocks/bars'), ('data.alpaca.markets', '/v1/corporate-actions'),
                    ('paper-api.alpaca.markets', '/v2/calendar'), ('paper-api.alpaca.markets', '/v2/assets/AAPL')]
        for (stream, query), route in zip(specs, expected):
            response = client.get(stream, query)
            parsed = urlparse(seen[-1].full_url)
            self.assertEqual((parsed.hostname, parsed.path), route)
            evidence = response.evidence()
            self.assertEqual(evidence['body'].encode(), PAYLOAD)
            self.assertEqual(evidence['sha256'], hashlib.sha256(PAYLOAD).hexdigest())
            self.assertEqual(response.json()['price'], Decimal('100.010000001'))
            self.assertNotIn('set-cookie', evidence['headers'])
            self.assertNotIn('authorization', evidence['headers'])
            self.assertNotIn(KEY, json.dumps(evidence))
            self.assertNotIn(SECRET, repr(response))
        self.assertEqual(parse_qs(urlparse(seen[0].full_url).query)['feed'], ['sip'])

    def test_order_routes_paid_feeds_and_recent_data_fail_before_any_request(self):
        def forbidden(*args, **kwargs):
            self.fail('Rejected operations must not reach the opener')
        client = self.client(forbidden)
        cases = [('orders', {}), ('account', {}), ('/v2/orders', {}),
                 ('asset', {'symbol': 'AAPL/../orders'}),
                 ('calendar', {'start': '2024-01-01', 'api_key': KEY})]
        for feed in ('otc', 'boats', 'overnight'):
            cases.append(('bars', {**BAR_QUERY, 'feed': feed}))
        cases.extend([('bars', {**BAR_QUERY, 'end': '2025-01-01'}),
                      ('bars', {**BAR_QUERY, 'adjustment': 'all'}),
                      ('bars', {**BAR_QUERY, 'page_token': SECRET})])
        for stream, query in cases:
            with self.subTest(stream=stream, query_keys=tuple(query)), self.assertRaises(MarketDataError):
                client.get(stream, query)
        self.assertEqual(client.requests, [])

    def test_terminal_authorization_and_redirect_errors_never_read_body_or_fallback(self):
        for status in (301, 302, 400, 401, 403):
            seen = []
            error_body = UnreadErrorBody(SECRET.encode())
            def fail(request, timeout):
                seen.append(request)
                raise HTTPError(request.full_url, status, SECRET, Message(), error_body)
            client = self.client(fail)
            with self.subTest(status=status), self.assertRaises(MarketDataError) as error:
                client.get('bars', BAR_QUERY)
            self.assertEqual(len(seen), 1)
            self.assertEqual(parse_qs(urlparse(seen[0].full_url).query)['feed'], ['sip'])
            self.assertTrue(error_body.closed)
            self.assertNotIn(SECRET, str(error.exception))

    def test_success_at_an_unexpected_redirect_url_is_rejected(self):
        client = self.client(lambda request, timeout: Reply('https://paper-api.alpaca.markets/v2/orders'))
        with self.assertRaisesRegex(MarketDataError, 'redirect'):
            client.get('bars', BAR_QUERY)
        self.assertEqual(len(client.requests), 1)

    def test_transient_retries_share_budget_and_honor_rate_reset_before_retry_after(self):
        requests = []
        def opener(request, timeout):
            requests.append(self.clock.elapsed)
            if len(requests) == 1:
                headers = Message()
                headers['X-RateLimit-Remaining'] = '0'
                headers['X-RateLimit-Reset'] = '1735689603'
                headers['Retry-After'] = '1'
                raise HTTPError(request.full_url, 429, SECRET, headers, UnreadErrorBody())
            return Reply(request.full_url)
        client = self.client(opener, max_requests=2)
        response = client.get('bars', BAR_QUERY)
        self.assertEqual(requests, [0, 3])
        self.assertEqual(response.json()['next_page_token'], 'synthetic-page-2')
        # The transport preserves pagination state; it does not issue hidden pages.
        self.assertEqual(len(client.requests), 2)
        with self.assertRaisesRegex(MarketDataError, 'budget'):
            client.get('bars', {**BAR_QUERY, 'page_token': 'synthetic-page-2'})
        self.assertEqual(len(requests), 2)

    def test_invalid_and_excessive_cooldowns_cannot_retry(self):
        headers_cases = [{'Retry-After': 'NaN'}, {'Retry-After': '-1'}, {'Retry-After': '61'},
                         {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': 'NaN'},
                         {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1735689661'}]
        for mapping in headers_cases:
            calls = []
            def opener(request, timeout):
                calls.append(request)
                headers = Message()
                for key, value in mapping.items():
                    headers[key] = value
                raise HTTPError(request.full_url, 429, SECRET, headers, UnreadErrorBody())
            client = self.client(opener)
            with self.subTest(header_names=tuple(mapping)), self.assertRaises(MarketDataError):
                client.get('bars', BAR_QUERY)
            self.assertEqual(len(calls), 1)

    def test_exhausted_success_quota_delays_the_next_endpoint(self):
        seen = []
        def opener(request, timeout):
            seen.append(self.clock.elapsed)
            headers = {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1735689603'} if len(seen) == 1 else {}
            return Reply(request.full_url, body=b'[]', headers=headers)
        client = self.client(opener)
        client.get('calendar', {'start': '2024-01-01', 'end': '2024-01-03'})
        client.get('asset', {'symbol': 'AAPL'})
        self.assertEqual(seen, [0, 3])

    def test_network_errors_retry_boundedly_without_exposing_exception_details(self):
        def unreachable(request, timeout):
            raise URLError(SECRET)
        client = self.client(unreachable, retries=1)
        with self.assertRaises(MarketDataError) as error:
            client.get('bars', BAR_QUERY)
        self.assertEqual(len(client.requests), 2)
        self.assertNotIn(SECRET, str(error.exception))

    def test_response_size_json_and_nonfinite_values_are_checked_before_evidence(self):
        for body in (b'x' * (16 * 1024 * 1024 + 1), b'<html>not JSON</html>', b'{"value": NaN}'):
            client = self.client(lambda request, timeout: Reply(request.full_url, body))
            with self.subTest(size=len(body)), self.assertRaises(MarketDataError):
                client.get('bars', BAR_QUERY)
            self.assertEqual(len(client.requests), 1)

    def test_response_evidence_is_detached_from_request_history(self):
        client = self.client(lambda request, timeout: Reply(request.full_url))
        response = client.get('bars', BAR_QUERY)
        evidence = response.evidence()
        evidence['params']['feed'] = 'iex'
        evidence['headers']['x-request-id'] = 'changed-by-caller'
        self.assertEqual(response.params['feed'], 'sip')
        self.assertEqual(client.requests[0]['params']['feed'], 'sip')
        self.assertNotIn('x-request-id', response.headers)
        response.params['feed'] = 'changed-response-metadata'
        self.assertEqual(client.requests[0]['params']['feed'], 'sip')
