"""CLI adapter for daily market collection and offline analysis."""

import json
from pathlib import Path

from .market_data import MarketDataError
from .market_store import JsonFileSource, MarketStore, collect_market


def add_market_commands(commands):
    market = commands.add_parser('market', help='Historical daily market data and price returns')
    sub = market.add_subparsers(dest='market_command', required=True)
    probe = sub.add_parser('probe', help='Small authenticated read-only checks; no raw response persistence')
    probe.add_argument('--env-file', type=Path, default=Path('.env'), help='Ignored local credential configuration')
    probe.add_argument('--feed', choices=['iex', 'sip', 'both'], default='iex', help='Explicit diagnostic feed; success is not license clearance')
    probe.add_argument('--date', action='append', dest='dates', help='Historical YYYY-MM-DD; up to three dates, default 2024-11-01')
    collect = sub.add_parser('collect', help='Collect explicit historical Alpaca data or an authored synthetic source')
    collect.add_argument('--provider', choices=['offline', 'alpaca-replay', 'alpaca', 'twelve-data'], default='offline')
    collect.add_argument('--source', type=Path, help='Authored synthetic bundle; not a provider API response')
    collect.add_argument('identifiers', nargs='*', help='Alpaca US equity symbols, e.g. AAPL MSFT')
    collect.add_argument('--env-file', type=Path, default=Path('.env'))
    collect.add_argument('--start', help='Inclusive historical market date')
    collect.add_argument('--end', help='Inclusive completed historical market date')
    collect.add_argument('--feed', choices=['sip', 'iex'], default='sip')
    collect.add_argument('--adjustment', choices=['raw', 'split', 'dividend'], default='raw')
    collect.add_argument('--page-size', type=int, default=1000)
    collect.add_argument('--action-page-size', type=int, default=1000)
    collect.add_argument('--process-start', default='2016-01-01', help='Initial independent action process-date lower bound')
    collect.add_argument('--action-overlap-days', type=int, default=7)
    collect.add_argument('--refresh', action='store_true', help='Reconcile the full source interval')
    collect.add_argument('--overlap-days', type=int, default=7)
    collect.add_argument('--cache-hours', type=float, default=24)
    collect.add_argument('--max-requests', type=int, default=100)
    inspect = sub.add_parser('inspect', help='Inspect saved market evidence without requests')
    inspect.add_argument('identifier', help='Explicit instrument ID or unambiguous symbol; CIK is not a security ID')
    inspect.add_argument('--as-of', help='Inclusive UTC date or timezone-aware timestamp; observed evidence only')
    inspect.add_argument('--provider')
    inspect.add_argument('--feed')
    inspect.add_argument('--adjustment', choices=['raw', 'split', 'dividend', 'all'], default='raw')
    inspect.add_argument('--adjustment-as-of', help='Exact adjustment anchor; separate from historical knowledge cutoff')
    inspect.add_argument('--limit', type=int, default=10, help='0 includes all records')
    inspect.add_argument('--json', action='store_true')
    returns = sub.add_parser('returns', help='Offline price returns; never a total-return claim')
    returns.add_argument('identifier')
    returns.add_argument('--start', required=True)
    returns.add_argument('--end', required=True)
    returns.add_argument('--as-of')
    returns.add_argument('--provider', default='alpaca')
    returns.add_argument('--feed', default='sip')
    returns.add_argument('--adjustment', choices=['raw', 'split'], default='split')
    returns.add_argument('--adjustment-as-of')
    replay = sub.add_parser('replay', help='Reparse saved original Alpaca responses without network')
    replay.add_argument('identifier')
    replay.add_argument('--provider', default='alpaca')
    replay.add_argument('--feed', default='sip')
    replay.add_argument('--adjustment', choices=['raw', 'split', 'dividend'], default='raw')
    replay.add_argument('--adjustment-as-of')
    replay.add_argument('--as-of')


def run_market(args):
    if args.market_command == 'probe':
        from .alpaca_probe import probe_access
        feeds = ('iex', 'sip') if args.feed == 'both' else (args.feed,)
        result = probe_access(args.env_file, feeds=feeds, dates=args.dates or ('2024-11-01',))
        print(json.dumps(result, indent=2))
        expected = len(feeds) * len(args.dates or [None])
        return 0 if len(result['results']) == expected and all(row.get('sample_complete') for row in result['results']) else 1
    store = MarketStore(args.data_dir or 'data/market')
    if args.market_command == 'collect':
        if args.provider == 'alpaca':
            if not args.identifiers or not args.start or not args.end:
                raise MarketDataError('Alpaca integration is incomplete without explicit symbols, --start and --end')
            if args.source is not None:
                raise MarketDataError('A fixture is not a live source; use --provider alpaca-replay')
            from .alpaca import AlpacaClient
            from .alpaca_source import collect_alpaca
            client = AlpacaClient(args.env_file, max_requests=args.max_requests)
            result = collect_alpaca(client, store, args.identifiers, start=args.start, end=args.end,
                                    feed=args.feed, adjustment=args.adjustment, page_size=args.page_size,
                                    action_page_size=args.action_page_size, process_start=args.process_start,
                                    action_overlap_days=args.action_overlap_days, refresh=args.refresh,
                                    overlap_days=args.overlap_days, cache_hours=args.cache_hours, max_requests=args.max_requests)
            print(json.dumps(result, indent=2))
            return 0
        if args.provider not in {'offline', 'alpaca-replay'}:
            raise MarketDataError('Live market integration is incomplete: account/feed and retention rights must be resolved; see docs/MARKET_DATA_PROVIDER_DECISION.md')
        if args.source is None:
            raise MarketDataError('Offline collection requires --source with an authored synthetic bundle')
        if args.provider == 'alpaca-replay':
            from .alpaca_replay import AlpacaReplaySource
            source = AlpacaReplaySource(args.source, max_requests=args.max_requests)
        else:
            source = JsonFileSource(args.source)
        result = collect_market(source, store, refresh=args.refresh,
                                overlap_days=args.overlap_days, cache_hours=args.cache_hours,
                                max_requests=args.max_requests)
        if args.provider == 'alpaca-replay':
            result['replay_provider_requests'] = len(source.requests)
            result['live_validated'] = False
        print(json.dumps(result, indent=2))
        return 0
    if args.market_command in {'returns', 'replay'}:
        result = store.inspect(args.identifier, as_of=args.as_of, provider=args.provider, feed=args.feed,
                               adjustment=args.adjustment, adjustment_as_of=args.adjustment_as_of)
        if args.market_command == 'replay':
            from .alpaca_source import replay_alpaca_snapshot
            output = replay_alpaca_snapshot(store, result['snapshot'])
        elif args.adjustment == 'split':
            from .market_data import adjusted_price_return
            output = adjusted_price_return(result['bars'], start=args.start, end=args.end, as_of=result['as_of'])
        else:
            from .market_data import split_price_return
            output = split_price_return(result['bars'], result['actions'], start=args.start, end=args.end,
                                        as_of=result['as_of'], action_coverage=result['action_coverage'])
        print(json.dumps(output, indent=2))
        return 0
    if args.limit < 0:
        raise MarketDataError('Limit must be nonnegative')
    result = store.inspect(args.identifier, as_of=args.as_of, provider=args.provider, feed=args.feed,
                           adjustment=args.adjustment, adjustment_as_of=args.adjustment_as_of)
    result['total_bars'] = len(result['bars'])
    if args.limit:
        result['bars'] = result['bars'][-args.limit:]
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        label = 'SYNTHETIC OFFLINE DATA' if result['synthetic'] else 'REPORTED MARKET DATA'
        print(f"{label} | {result['instrument']['id']} | {result['provider']}/{result['feed']} | {result['adjustment']}")
        print(f"Observed-by-cutoff: {result['as_of']} | retrieved {result['snapshot']['retrieved_at']}")
        print('date\topen\thigh\tlow\tclose\tvolume\tobserved_at')
        for row in result['bars']:
            print('\t'.join(row[key] for key in ('date', 'open', 'high', 'low', 'close', 'volume', 'observed_at')))
        print('Quality: ' + json.dumps(result['quality'], sort_keys=True))
        print(f"Actions: {len(result['actions'])}; whole-series complete coverage: {result['quality']['actions_complete']}. No total-return or historical-universe claim. --json includes provenance.")
    return 0
