"""Small watchlist orchestration with a callable interface for future consumers."""

from datetime import datetime, timezone
import math
import re

from .financials import METRICS, PARSER_VERSION, extract_facts
from .sec import SecClient, SecError, decode_json, is_cik, normalize_cik, normalize_ticker
from .storage import Store
from . import __version__

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def is_fresh(timestamp: str, hours: float) -> bool:
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds()
    return 0 <= age < hours * 3600


class Collector:
    def __init__(self, client: SecClient, store: Store, *, cache_hours: float = 24,
                 cik_overrides: dict | None = None):
        if not math.isfinite(cache_hours) or cache_hours < 0:
            raise ValueError("Cache hours must be finite and nonnegative")
        self.client, self.store, self.cache_hours = client, store, cache_hours
        if cik_overrides is not None and not isinstance(cik_overrides, dict):
            raise ValueError("CIK overrides must be a ticker-to-CIK table")
        self.overrides = {}
        for key, value in (cik_overrides or {}).items():
            ticker, cik = normalize_ticker(key), normalize_cik(value)
            if ticker in self.overrides and self.overrides[ticker] != cik:
                raise ValueError(f"Conflicting CIK overrides for ticker aliases: {ticker}")
            self.overrides[ticker] = cik

    def identify(self, identifier: str, *, refresh: bool = False) -> str:
        identifier = identifier.strip().upper()
        if is_cik(identifier):
            return normalize_cik(identifier)
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}", identifier):
            raise ValueError(f"Invalid ticker: {identifier!r}")
        identifier = normalize_ticker(identifier)
        if identifier in self.overrides:
            return self.overrides[identifier]
        resource = self.store.resource("company_tickers")
        if refresh or not resource or not is_fresh(resource["retrieved_at"], self.cache_hours):
            self.store.save_resource("company_tickers", self.client.get(TICKERS_URL))
            resource = self.store.resource("company_tickers")
        index = decode_json(self.store.raw(resource))
        if not all(isinstance(row, dict) and isinstance(row.get("ticker"), str) for row in index.values()):
            raise SecError("Invalid SEC ticker directory")
        matches = {normalize_cik(row["cik_str"]) for row in index.values()
                   if normalize_ticker(row["ticker"]) == identifier}
        if len(matches) != 1:
            raise SecError(f"SEC ticker directory has {len(matches)} matches for {identifier}; supply a CIK")
        return matches.pop()

    def collect(self, identifiers: list[str], *, refresh: bool = False,
                filing_history: bool = False) -> list[dict]:
        if not identifiers or len(identifiers) > 20 or not all(isinstance(x, str) for x in identifiers):
            raise ValueError("Provide 1–20 string tickers/CIKs")
        results = []
        seen = set()
        with self.store.collection_lock():
            for identifier in identifiers:
                cik = None
                try:
                    identifier = identifier.strip().upper()
                    cik = self.identify(identifier, refresh=refresh)
                    if cik in seen:
                        known = self.store.latest(cik)
                        if known and not is_cik(identifier) and normalize_ticker(identifier) not in {normalize_ticker(t) for t in known['tickers']}:
                            raise SecError("Configured ticker/CIK association does not match collected SEC metadata")
                        continue
                    seen.add(cik)
                    previous = self.store.latest(cik)
                    if (not refresh and previous and previous["parser_version"] == PARSER_VERSION
                            and (not filing_history or previous["filing_history_complete"])
                            and is_fresh(previous["retrieved_at"], self.cache_hours)):
                        if not is_cik(identifier) and normalize_ticker(identifier) not in {normalize_ticker(t) for t in previous["tickers"]}:
                            raise SecError("Configured ticker/CIK association does not match cached SEC metadata")
                        self.store.records(previous)
                        sources = previous["sources"]
                        for reference in [sources["companyfacts"], sources["submissions"], *sources["submissions_history"]]:
                            self.store.raw(reference)
                        self.store.record_attempt(cik, "cached")
                        results.append({"identifier": identifier, "cik": cik, "status": "cached",
                                        "snapshot_id": previous["snapshot_id"],
                                        "record_count": previous["record_count"],
                                        "missing_metrics": previous["missing_metrics"]})
                        continue
                    manifest = self._retrieve(cik, identifier, filing_history)
                    status = "partial" if manifest["missing_metrics"] else "success"
                    self.store.record_attempt(cik, status)
                    results.append({"identifier": identifier, "cik": cik, "status": status,
                                    "snapshot_id": manifest["snapshot_id"],
                                    "record_count": manifest["record_count"],
                                    "missing_metrics": manifest["missing_metrics"]})
                except (SecError, ValueError, KeyError, TypeError, OSError) as exc:
                    if cik:
                        self.store.record_attempt(cik, "failed", error=str(exc))
                    results.append({"identifier": identifier, "cik": cik, "status": "failed", "error": str(exc)})
        return results

    def _retrieve(self, cik: str, identifier: str, filing_history: bool) -> dict:
        submissions_response = self.client.get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        submissions = submissions_response.json()
        if normalize_cik(submissions.get("cik", "")) != cik:
            raise SecError("Submissions returned a different CIK")
        tickers = submissions.get("tickers", [])
        if not isinstance(tickers, list) or not all(isinstance(ticker, str) for ticker in tickers):
            raise SecError("Invalid SEC submissions tickers")
        if not is_cik(identifier) and normalize_ticker(identifier) not in {normalize_ticker(t) for t in tickers}:
            raise SecError("Ticker/CIK association does not match official SEC submissions")
        references = {"submissions": self.store.save_response(submissions_response)}
        historical, history_refs = [], []
        metadata = submissions.get("filings")
        if not isinstance(metadata, dict):
            raise SecError("Invalid SEC submissions filings")
        files = metadata.get("files", [])
        if not isinstance(files, list) or not all(isinstance(entry, dict) for entry in files):
            raise SecError("Invalid historical submissions file list")
        if filing_history:
            if len(files) > 20:
                raise SecError("More than 20 historical submissions files; omit --filing-history to collect without older metadata")
            for entry in files:
                name = entry["name"]
                if not re.fullmatch(rf"CIK{cik}-submissions-[0-9]+\.json", name):
                    raise SecError("Unexpected historical submissions filename")
                response = self.client.get(f"https://data.sec.gov/submissions/{name}")
                historical.append(response.json())
                history_refs.append(self.store.save_response(response))
        references["submissions_history"] = history_refs
        facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        facts_response = self.client.get(facts_url)
        facts = facts_response.json()
        if normalize_cik(facts.get("cik", "")) != cik:
            raise SecError("CompanyFacts returned a different CIK")
        references["companyfacts"] = self.store.save_response(facts_response)
        records = extract_facts(facts, submissions, historical=historical,
                                retrieved_at=facts_response.retrieved_at, source_url=facts_url)
        if not records:
            raise SecError("No selected reported financial facts found")
        return self.store.publish({
            "cik": cik, "name": facts.get("entityName", submissions.get("name")), "tickers": tickers,
            "retrieved_at": facts_response.retrieved_at, "parser_version": PARSER_VERSION,
            "collector_version": __version__, "requested_identifier": identifier,
            "fiscal_year_end": submissions.get("fiscalYearEnd"),
            "sources": references, "filing_history_complete": filing_history or not files,
            "unloaded_submissions_files": [] if filing_history else files,
            "missing_metrics": sorted(set(METRICS) - {row["metric"] for row in records}),
            "limitations": ["Selected entity-wide US-GAAP concepts only; alternative concepts stay separate",
                            "Duration labels are inferred; filing fy/fp are not historical fiscal labels",
                            "Current SEC aggregates and acceptance times do not certify historical public availability",
                            "Quarterly cash-flow values are not derived from cumulative values"]}, records)
