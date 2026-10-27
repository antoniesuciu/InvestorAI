"""CLI boundary; underlying Collector, Store and extract_facts are reusable APIs."""

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
import tomllib

from .collector import Collector
from .financials import METRICS
from .sec import SecClient, SecError
from .storage import Store


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(prog="investor-ai", description="Official SEC financial data collector")
    app.add_argument("--config", type=Path, default=Path("watchlist.toml"))
    app.add_argument("--data-dir", type=Path, help="Local output store; use a Git-ignored path")
    commands = app.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help="Retrieve a small watchlist; suitable for a local schedule")
    collect.add_argument("identifiers", nargs="*", help="Tickers, numeric CIKs or CIK-prefixed identifiers")
    collect.add_argument("--user-agent", default=os.environ.get("SEC_USER_AGENT"))
    collect.add_argument("--refresh", action="store_true", help="Ignore the cache age")
    collect.add_argument("--cache-hours", type=float)
    collect.add_argument("--filing-history", action="store_true", help="Also retrieve older submissions metadata")
    collect.add_argument("--requests-per-second", type=float, default=2)
    commands.add_parser("companies", help="List locally collected companies and refresh state")
    inspect = commands.add_parser("inspect", help="Inspect saved reported observations without network access")
    inspect.add_argument("identifier")
    inspect.add_argument("--snapshot", help="Inspect a saved snapshot ID instead of the latest collection")
    inspect.add_argument("--metric", choices=sorted(METRICS))
    inspect.add_argument("--period", choices=["annual", "quarterly", "year_to_date", "instant", "other", "all"], default="all")
    inspect.add_argument("--filed-through", type=date.fromisoformat,
                         help="Filing-date filter only; NOT a certified point-in-time reconstruction")
    inspect.add_argument("--limit", type=int, default=20, help="0 prints all rows")
    inspect.add_argument("--json", action="store_true", help="Include full provenance and manifest")
    return app


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = tomllib.loads(args.config.read_text()) if args.config.exists() else {}
        store = Store(args.data_dir or config.get("data_dir", "data/sec"))
        if args.command == "collect":
            client = SecClient(args.user_agent, requests_per_second=args.requests_per_second)
            identifiers = args.identifiers or config.get("watchlist", ["AAPL", "MSFT"])
            if not isinstance(identifiers, list) or not identifiers or not all(isinstance(x, str) for x in identifiers):
                raise ValueError("Configure a nonempty watchlist of string tickers/CIKs")
            if len(identifiers) > 20:
                raise ValueError("This collector is bounded to 20 watchlist identifiers per run")
            collector = Collector(client, store,
                                  cache_hours=args.cache_hours if args.cache_hours is not None else config.get("cache_hours", 24),
                                  cik_overrides=config.get("cik_overrides"))
            results = collector.collect(identifiers, refresh=args.refresh, filing_history=args.filing_history)
            print(json.dumps(results, indent=2))
            return 1 if any(item["status"] == "failed" for item in results) else 0
        if args.command == "companies":
            results = []
            for manifest in store.companies():
                status_file = store.root / "companies" / manifest["cik"] / "status.json"
                status = json.loads(status_file.read_text()) if status_file.exists() else {}
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(manifest["retrieved_at"])).total_seconds()
                results.append({"cik": manifest["cik"], "tickers": manifest["tickers"], "name": manifest["name"],
                                "last_successful_retrieval_at": manifest["retrieved_at"], "age_hours": round(age / 3600, 2),
                                "record_count": manifest["record_count"], **status})
            print(json.dumps(results, indent=2))
            return 0
        if args.limit < 0:
            raise ValueError("Limit must be nonnegative")
        cik = store.resolve_local(args.identifier)
        manifest = store.snapshot(cik, args.snapshot) if args.snapshot else store.latest(cik)
        if not manifest:
            raise ValueError(f"No local snapshot for CIK {cik}; collect it first")
        rows = [row for row in store.records(manifest)
                if (not args.metric or row["metric"] == args.metric)
                and (args.period == "all" or row["period_type"] == args.period)
                and (not args.filed_through or row["filed"] <= args.filed_through.isoformat())]
        rows.sort(key=lambda row: (row["end"], row["filed"], row["metric"], row["concept"]), reverse=True)
        total = len(rows)
        if args.limit:
            rows = rows[:args.limit]
        if args.json:
            print(json.dumps({"manifest": manifest, "matching_count": total, "observations": rows}, indent=2))
        else:
            print(f"{manifest['name']} | CIK {cik} | retrieved {manifest['retrieved_at']}")
            print(f"{len(rows)} of {total} observations; all filing versions retained; no quarterly derivation")
            print("metric\tperiod\tstart\tend\tvalue\tunit\tfiled\taccession\tconcept")
            for row in rows:
                print("\t".join(str(row.get(key) or "") for key in
                                ("metric", "period_type", "start", "end", "value", "unit", "filed", "accession", "concept")))
            print("Full provenance: --json. Duration categories are inferred; filing dates are not exact availability.")
        return 0
    except (SecError, ValueError, TypeError, KeyError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
