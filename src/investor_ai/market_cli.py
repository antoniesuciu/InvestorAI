"""CLI adapter for the offline market-data foundation."""

import json
from pathlib import Path

from .market_data import MarketDataError
from .market_store import JsonFileSource, MarketStore, collect_market


def add_market_commands(commands):
    market = commands.add_parser('market', help='Historical daily market evidence (synthetic offline mode)')
    sub = market.add_subparsers(dest='market_command', required=True)
    collect = sub.add_parser('collect', help='Collect an authored synthetic JSON source; live APIs are not enabled')
    collect.add_argument('--provider', choices=['offline', 'alpaca', 'twelve-data'], default='offline')
    collect.add_argument('--source', type=Path, help='Authored synthetic bundle; not a provider API response')
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


def run_market(args):
    store = MarketStore(args.data_dir or 'data/market')
    if args.market_command == 'collect':
        if args.provider != 'offline':
            raise MarketDataError('Live market integration is incomplete: account/feed and retention rights must be resolved; see docs/MARKET_DATA_PROVIDER_DECISION.md')
        if args.source is None:
            raise MarketDataError('Offline collection requires --source with an authored synthetic bundle')
        result = collect_market(JsonFileSource(args.source), store, refresh=args.refresh,
                                overlap_days=args.overlap_days, cache_hours=args.cache_hours,
                                max_requests=args.max_requests)
        print(json.dumps(result, indent=2))
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
        print(f"SYNTHETIC OFFLINE DATA | {result['instrument']['id']} | {result['provider']}/{result['feed']} | {result['adjustment']}")
        print(f"Observed-by-cutoff: {result['as_of']} | retrieved {result['snapshot']['retrieved_at']}")
        print('date\topen\thigh\tlow\tclose\tvolume\tobserved_at')
        for row in result['bars']:
            print('\t'.join(row[key] for key in ('date', 'open', 'high', 'low', 'close', 'volume', 'observed_at')))
        print('Quality: ' + json.dumps(result['quality'], sort_keys=True))
        print(f"Actions: {len(result['actions'])}; whole-series complete coverage: {result['quality']['actions_complete']}. No total-return or historical-universe claim. --json includes provenance.")
    return 0
