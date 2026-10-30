"""Live-adapter interfaces tested with synthetic HTTP responses and no network."""
from io import BytesIO
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from investor_ai.alpaca import AlpacaClient
from investor_ai.alpaca_source import collect_alpaca, replay_alpaca_snapshot
from investor_ai.market_store import MarketStore
from investor_ai.market_data import adjusted_price_return, MarketDataError, normalize_bundle, normalize_live_bundle


class Reply(BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json'}
    def __init__(self, url, payload):
        super().__init__(json.dumps(payload).encode())
        self.url = url
    def geturl(self):
        return self.url


class AlpacaClientTests(unittest.TestCase):
    def test_credentials_cannot_enter_query_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('ALPACA_API_KEY_ID=SYNTHETIC_KEY_NOT_REAL\nALPACA_SECRET_KEY=SYNTHETIC_SECRET_NOT_REAL\n')
            client = AlpacaClient(path, opener=lambda *_args, **_kwargs: self.fail('Invalid query must not be sent'))
            with self.assertRaises(MarketDataError):
                client.get('calendar', {'start': '2024-01-01', 'end': '2024-01-02', 'api_key': 'SYNTHETIC_KEY_NOT_REAL'})
            self.assertEqual(client.requests, [])

    def test_read_only_client_preserves_exact_response_and_redacts_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.env'
            path.write_text('ALPACA_API_KEY_ID=SYNTHETIC_KEY_NOT_REAL\nALPACA_SECRET_KEY=SYNTHETIC_SECRET_NOT_REAL\nALPACA_PAPER=true\n')
            seen = []
            def open_request(request, timeout):
                seen.append(request)
                self.assertEqual(request.get_method(), 'GET')
                self.assertEqual(urlparse(request.full_url).hostname, 'data.alpaca.markets')
                return Reply(request.full_url, {'bars': {}, 'next_page_token': None})
            client = AlpacaClient(path, opener=open_request, sleep=lambda _: None)
            response = client.get('bars', {'symbols': 'AAPL', 'timeframe': '1Day', 'start': '2024-01-02', 'end': '2024-01-03', 'feed': 'sip', 'adjustment': 'raw', 'limit': 2})
            self.assertEqual(response.json()['bars'], {})
            self.assertEqual(len(seen), 1)
            self.assertNotIn('SYNTHETIC_SECRET', repr(response))
            self.assertNotIn('SYNTHETIC_SECRET', repr(client))


class FixtureClient:
    """Synthetic endpoint-shaped transport; never loads credentials or sockets."""
    max_requests = 100
    def __init__(self):
        self.requests = []
        self.now = lambda: '2025-01-01T00:00:00+00:00'
    def get(self, stream, params):
        from investor_ai.alpaca import AlpacaResponse
        self.requests.append({'stream': stream, 'params': params})
        if stream == 'asset':
            value = {'id': '00000000-0000-4000-8000-000000000001', 'symbol': 'SYNTH', 'class': 'us_equity', 'exchange': 'NASDAQ', 'status': 'active'}
        elif stream == 'calendar':
            value = [{'date': '2024-11-01', 'open': '09:30', 'close': '16:00'}, {'date': '2024-11-04', 'open': '09:30', 'close': '16:00'}]
            value = [r for r in value if params['start'] <= r['date'] <= params['end']]
        elif stream == 'bars':
            value = {'bars': {'SYNTH': [
                {'t': '2024-11-01T04:00:00Z', 'o': 100, 'h': 102, 'l': 99, 'c': 100, 'v': 1000},
                {'t': '2024-11-04T05:00:00Z', 'o': 101, 'h': 104, 'l': 100, 'c': 103, 'v': 1100}]}, 'next_page_token': None}
        else:
            value = {'corporate_actions': {'cash_dividends': [{'id': 'DIV-1', 'symbol': 'SYNTH', 'rate': '1.25', 'ex_date': '2024-11-04', 'process_date': '2024-11-01', 'payable_date': '2024-11-08'}]}, 'next_page_token': None}
        return AlpacaResponse(stream, params, json.dumps(value).encode(), self.now(), 'https://example.invalid/synthetic', {})


class AlpacaCollectionTests(unittest.TestCase):
    def test_bad_bundle_shapes_raise_controlled_validation_errors(self):
        for normalize in (normalize_bundle, normalize_live_bundle):
            for value in (None, [], 'bad'):
                with self.subTest(normalizer=normalize.__name__, value=value), self.assertRaises(MarketDataError):
                    normalize(value, observed_at='2025-01-01T00:00:00+00:00')

    def test_changed_current_ticker_identity_never_relabels_saved_history(self):
        class Changed(FixtureClient):
            changed = False
            def get(self, stream, params):
                response = super().get(stream, params)
                if self.changed and stream == 'asset':
                    value = response.json()
                    value['id'] = '00000000-0000-4000-8000-000000000002'
                    from investor_ai.alpaca import AlpacaResponse
                    return AlpacaResponse(stream, params, json.dumps(value).encode(), self.now(), response.endpoint, {})
                return response
        with tempfile.TemporaryDirectory() as folder, patch('investor_ai.market_store.utc_now', return_value='2025-01-01T00:00:00+00:00'):
            client, store = Changed(), MarketStore(folder)
            collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04')
            before = store.inspect('SYNTH')
            client.changed = True
            with self.assertRaises(MarketDataError):
                collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04', cache_hours=0)
            self.assertEqual(store.inspect('SYNTH')['snapshot'], before['snapshot'])
            self.assertEqual(store.inspect('SYNTH')['instrument']['id'], before['instrument']['id'])

    def test_midnight_change_and_malformed_action_shapes_never_publish(self):
        class Broken(FixtureClient):
            shape = 'midnight'
            def get(self, stream, params):
                if self.shape == 'midnight' and stream == 'bars':
                    self.now = lambda: '2025-01-02T05:01:00+00:00'
                response = super().get(stream, params)
                if stream == 'actions' and self.shape != 'midnight':
                    value = {'corporate_actions': [] if self.shape == 'categories' else {'cash_dividends': [None]}, 'next_page_token': None}
                    from investor_ai.alpaca import AlpacaResponse
                    return AlpacaResponse(stream, params, json.dumps(value).encode(), self.now(), response.endpoint, {})
                return response
        for shape in ('midnight', 'categories', 'row'):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as folder:
                client, store = Broken(), MarketStore(folder)
                client.shape = shape
                client.now = lambda: '2025-01-02T04:59:00+00:00'
                with patch('investor_ai.market_store.utc_now', side_effect=lambda: client.now()), self.assertRaises(MarketDataError):
                    collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04', adjustment='split')
                self.assertEqual(list((store.root / 'datasets').glob('*/latest.json')), [])

    def test_raw_incremental_correction_uses_independent_process_cursor_and_cache_is_offline(self):
        class Revisions(FixtureClient):
            at, close, cash = '2025-01-01T00:00:00+00:00', 103, '1.25'
            def __init__(self):
                super().__init__()
                self.now = lambda: self.at
            def get(self, stream, params):
                response = super().get(stream, params)
                value = response.json()
                if stream == 'bars':
                    rows = value['bars']['SYNTH']
                    rows[-1]['c'] = self.close
                    value['bars']['SYNTH'] = [r for r in rows if params['start'][:10] <= r['t'][:10] <= params['end'][:10]]
                if stream == 'actions' and self.cash != '1.25':
                    value['corporate_actions']['cash_dividends'][0].update(rate=self.cash, process_date='2025-02-01')
                from investor_ai.alpaca import AlpacaResponse
                return AlpacaResponse(stream, params, json.dumps(value).encode(), self.now(), response.endpoint, {})
        with tempfile.TemporaryDirectory() as folder:
            client, store = Revisions(), MarketStore(folder)
            with patch('investor_ai.market_store.utc_now', side_effect=client.now):
                collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04')
            before = store.inspect('SYNTH')
            client.at, client.close, client.cash = '2025-02-01T12:00:00+00:00', 104, '1.50'
            with patch('investor_ai.market_store.utc_now', side_effect=client.now):
                collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04', overlap_days=0, cache_hours=0)
            after = store.inspect('SYNTH')
            self.assertEqual(after['bars'][-1]['close'], '104')
            self.assertEqual(after['actions'][0]['amount'], '1.5')
            self.assertEqual(after['stored_action_versions'], 2)
            self.assertEqual(after['bars'][0]['observed_at'], before['bars'][0]['observed_at'])
            self.assertEqual(after['source_metadata']['action_process_coverage']['start'], '2024-12-24')
            self.assertEqual(next(r for r in after['source_metadata']['provider_requests'] if r['stream'] == 'bars')['params']['start'][:10], '2024-11-04')
            self.assertEqual(store.inspect('SYNTH', as_of='2025-01-01')['snapshot'], before['snapshot'])
            self.assertTrue(replay_alpaca_snapshot(store, after['snapshot'])['matches'])
            with patch.object(client, 'get', side_effect=AssertionError('Cache must not request any endpoint')), patch('investor_ai.market_store.utc_now', return_value='2025-02-01T13:00:00+00:00'):
                result = collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04')
            self.assertEqual(result[0]['status'], 'cached')
            self.assertEqual(result[0]['snapshot_id'], after['snapshot']['snapshot_id'])
            client.at = '2025-02-01T14:00:00+00:00'
            with patch('investor_ai.market_store.utc_now', side_effect=client.now):
                collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04', refresh=True)
            self.assertEqual(store.inspect('SYNTH')['source_metadata']['action_process_coverage']['start'], '2016-01-01')

    def test_pagination_and_unsupported_actions_are_preserved_without_false_completeness(self):
        class Pages(FixtureClient):
            loop = False
            def get(self, stream, params):
                response = super().get(stream, params)
                value = response.json()
                if stream == 'bars':
                    value['bars']['SYNTH'] = value['bars']['SYNTH'][:1] if not params.get('page_token') else value['bars']['SYNTH'][1:]
                    value['next_page_token'] = 'second' if self.loop or not params.get('page_token') else None
                if stream == 'actions':
                    value['corporate_actions']['stock_dividends'] = [{'id': 'UNSUPPORTED'}]
                from investor_ai.alpaca import AlpacaResponse
                return AlpacaResponse(stream, params, json.dumps(value).encode(), self.now(), response.endpoint, {})
        with tempfile.TemporaryDirectory() as folder, patch('investor_ai.market_store.utc_now', return_value='2025-01-01T00:00:00+00:00'):
            client, store = Pages(), MarketStore(folder)
            collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04', page_size=1)
            report = store.inspect('SYNTH')
            self.assertEqual(len(report['bars']), 2)
            self.assertFalse(report['quality']['actions_complete'])
            self.assertEqual(report['source_metadata']['action_diagnostics'][0]['reason'], 'unsupported_action')
            self.assertTrue(replay_alpaca_snapshot(store, report['snapshot'])['matches'])
            client.loop = True
            with self.assertRaisesRegex(MarketDataError, 'pagination'):
                collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04', page_size=1, refresh=True)
            self.assertEqual(store.inspect('SYNTH')['snapshot'], report['snapshot'])

    def test_real_labeled_collection_retains_unknown_currency_and_replays_offline(self):
        with tempfile.TemporaryDirectory() as folder, patch('investor_ai.market_store.utc_now', return_value='2025-01-01T00:00:00+00:00'):
            client, store = FixtureClient(), MarketStore(folder)
            result = collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04')
            self.assertEqual(result[0]['status'], 'success')
            report = store.inspect('SYNTH')
            self.assertFalse(report['synthetic'])
            self.assertEqual(len(report['bars']), 2)
            self.assertIsNone(report['actions'][0]['cash_currency'])
            self.assertEqual(report['quality']['unknown_cash_currency_events'], 1)
            self.assertFalse(report['quality']['actions_complete'])
            with patch.object(client, 'get', side_effect=AssertionError('Offline replay must not request data')):
                replay = replay_alpaca_snapshot(store, report['snapshot'])
            self.assertTrue(replay['matches'])

    def test_split_adjusted_return_has_an_independent_reference_and_rejects_dividend_mode(self):
        with tempfile.TemporaryDirectory() as folder, patch('investor_ai.market_store.utc_now', return_value='2025-01-01T00:00:00+00:00'):
            client, store = FixtureClient(), MarketStore(folder)
            collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04', adjustment='split')
            report = store.inspect('SYNTH', adjustment='split')
            result = adjusted_price_return(report['bars'], start='2024-11-01', end='2024-11-04', as_of=report['as_of'])
            self.assertEqual(result['value'], '0.03')  # $100 -> $103, excludes cash dividends.
            self.assertIn('not total return', ' '.join(result['limitations']))
            for row in report['bars']:
                row['adjustment'] = 'dividend'
            with self.assertRaises(MarketDataError):
                adjusted_price_return(report['bars'], start='2024-11-01', end='2024-11-04', as_of=report['as_of'])

    def test_adjusted_partial_refresh_covers_prior_history_and_never_carries_stale_rows(self):
        class Changing(FixtureClient):
            factor, missing = 1, False
            def get(self, stream, params):
                response = super().get(stream, params)
                if stream == 'bars':
                    value = response.json()
                    rows = value['bars']['SYNTH']
                    value['bars']['SYNTH'] = [r for r in rows if params['start'][:10] <= r['t'][:10] <= params['end'][:10]]
                    for row in value['bars']['SYNTH']:
                        for field in ('o', 'h', 'l', 'c'):
                            row[field] *= self.factor
                    if self.missing:
                        value['bars']['SYNTH'] = value['bars']['SYNTH'][1:]
                    from investor_ai.alpaca import AlpacaResponse
                    return AlpacaResponse(stream, params, json.dumps(value).encode(), self.now(), response.endpoint, {})
                return response
        with tempfile.TemporaryDirectory() as folder:
            client, store = Changing(), MarketStore(folder)
            with patch('investor_ai.market_store.utc_now', return_value='2025-01-01T00:00:00+00:00'):
                collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04', adjustment='split')
            original = store.inspect('SYNTH', adjustment='split')
            client.factor = 2
            client.now = lambda: '2025-01-01T01:00:00+00:00'
            with patch('investor_ai.market_store.utc_now', return_value=client.now()):
                collect_alpaca(client, store, ['SYNTH'], start='2024-11-04', end='2024-11-04', adjustment='split', cache_hours=0)
            latest = store.inspect('SYNTH', adjustment='split')
            self.assertEqual(latest['bars'][0]['close'], '200')
            self.assertEqual(adjusted_price_return(latest['bars'], start='2024-11-01', end='2024-11-04', as_of=latest['as_of'])['value'], '0.03')
            with self.assertRaises(MarketDataError):
                adjusted_price_return([original['bars'][0], latest['bars'][1]], start='2024-11-01', end='2024-11-04', as_of=latest['as_of'])
            before = latest['snapshot']
            client.missing = True
            client.now = lambda: '2025-01-01T02:00:00+00:00'
            with patch('investor_ai.market_store.utc_now', return_value=client.now()), self.assertRaises(MarketDataError):
                collect_alpaca(client, store, ['SYNTH'], start='2024-11-01', end='2024-11-04', adjustment='split', cache_hours=0)
            self.assertEqual(store.inspect('SYNTH', adjustment='split')['snapshot'], before)
