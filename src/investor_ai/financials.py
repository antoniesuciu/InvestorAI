"""Lossless selected CompanyFacts observations; no cross-filing arithmetic."""

from datetime import date
from decimal import Decimal
from collections import defaultdict
import hashlib
import json
import re

from .sec import SecError, normalize_cik

PARSER_VERSION = "3"

# Alternative concepts are retained separately, not silently coalesced or added.
METRICS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capital_expenditure": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
}
INSTANT_METRICS = {"assets", "liabilities", "cash"}

# Additional concepts are parsed only when requested by the offline analysis adapter.
# The collector's original seven-metric contract remains unchanged.
ANALYSIS_METRICS = {
    **METRICS,
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "equity": ("StockholdersEquity",),
    "noncurrent_debt": ("LongTermDebtNoncurrent",),
}
ANALYSIS_INSTANT_METRICS = INSTANT_METRICS | {"current_assets", "current_liabilities", "equity", "noncurrent_debt"}


def filing_index(submissions: dict, historical: list[dict] | None = None) -> dict:
    """Join only by accession; recent and optional older SEC columnar arrays."""
    result = {}
    metadata = submissions.get("filings")
    if not isinstance(metadata, dict):
        raise SecError("Expected a submissions filings object")
    tables = [metadata.get("recent", {})] + (historical or [])
    for table in tables:
        if not isinstance(table, dict):
            raise SecError("Expected a submissions metadata object")
        accessions = table.get("accessionNumber", [])
        if not isinstance(accessions, list):
            raise SecError("Invalid submissions accession array")
        for key, values in table.items():
            if not isinstance(values, list) or len(values) != len(accessions):
                raise SecError(f"Mismatched SEC submissions column: {key}")
        for i, accession in enumerate(accessions):
            item = {key: values[i] for key, values in table.items()}
            previous = result.get(accession, {})
            if any(previous[key] != item[key] for key in previous.keys() & item.keys()):
                raise SecError(f"Conflicting submissions metadata for {accession}")
            result[accession] = {**previous, **item}
    return result


def period_type(start: str | None, end: str) -> tuple[str, int | None]:
    last = date.fromisoformat(end)
    if start is None:
        return "instant", None
    days = (last - date.fromisoformat(start)).days + 1
    if days <= 0:
        raise ValueError("Fact period ends before it starts")
    if 330 <= days <= 400:
        return "annual", days
    if 70 <= days <= 110:
        return "quarterly", days
    if 150 <= days <= 300:
        return "year_to_date", days
    return "other", days


def label_fiscal_periods(records: list[dict]) -> list[dict]:
    """Use exact reported intervals as fiscal evidence, never calendar-year guesses.

    Labels may be enriched from another filing; their evidence dates are retained.
    They do not change the value's filing date or establish public availability.
    """
    anchors = defaultdict(list)
    for row in records:
        if row['fiscal_year'] is not None and row['fiscal_period'] in {'FY', 'Q1', 'Q2', 'Q3', 'Q4'}:
            anchors[(row['start'], row['end'], row['period_type'])].append(
                (row['fiscal_year'], row['fiscal_period'], row['accession'], row['filed']))
    result = {}
    for row in records:
        candidates = [anchor for anchor in anchors[(row['start'], row['end'], row['period_type'])]
                      if anchor[3] <= row['filed']]
        labels = {(fy, fp) for fy, fp, _, _ in candidates}
        direct = row['fiscal_year'] is not None
        row['fiscal_label_sources'] = [dict(accession=accession, filed=filed)
                                      for accession, filed in sorted({(accession, filed) for _, _, accession, filed in candidates})]
        if len(labels) == 1:
            row['fiscal_year'], row['fiscal_period'] = labels.pop()
            row['fiscal_label_basis'] = 'filing_report_period' if direct else 'matching_reported_period'
        else:
            row['fiscal_year'] = row['fiscal_period'] = row['fiscal_label_basis'] = None
            if labels:
                row['quality_flags'].append('ambiguous_fiscal_period')
        # Quarter labels describe standalone quarters or interim balance instants.
        # A reporting focus of FY is not automatically a fourth-quarter label.
        row['fiscal_quarter'] = ({'Q1': 1, 'Q2': 2, 'Q3': 3, 'Q4': 4}.get(row['fiscal_period'])
                                 if row['period_type'] in {'quarterly', 'instant'} else None)
        row['is_year_to_date'] = row['year_to_date_basis'] = None
        if row['period_type'] in {'annual', 'year_to_date'}:
            row['is_year_to_date'], row['year_to_date_basis'] = True, 'duration_inference'
        elif row['period_type'] == 'quarterly' and row['fiscal_quarter'] is not None:
            row['is_year_to_date'] = row['fiscal_quarter'] == 1
            row['year_to_date_basis'] = 'fiscal_quarter_label'
        identity = {k: v for k, v in row.items() if k != 'retrieved_at'}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        row['record_id'] = key
        result[key] = row
    return list(result.values())


def extract_facts(companyfacts: dict, submissions: dict, *, retrieved_at: str,
                  source_url: str, historical: list[dict] | None = None,
                  metrics: dict | None = None) -> list[dict]:
    cik = normalize_cik(companyfacts.get("cik", ""))
    if cik != normalize_cik(submissions.get("cik", "")):
        raise SecError("CompanyFacts and submissions CIKs differ")
    filings = filing_index(submissions, historical)
    facts = companyfacts.get("facts")
    taxonomy = facts.get("us-gaap") if isinstance(facts, dict) else None
    if not isinstance(taxonomy, dict):
        raise SecError("CompanyFacts lacks a US-GAAP facts object")
    records = []
    for metric, concepts in (METRICS if metrics is None else metrics).items():
        for concept in concepts:
            definition = taxonomy.get(concept, {})
            if not isinstance(definition, dict) or not isinstance(definition.get("units", {}), dict):
                raise SecError(f"Invalid {concept} units object")
            for unit, observations in definition.get("units", {}).items():
                if not isinstance(observations, list) or not all(isinstance(row, dict) for row in observations):
                    raise SecError(f"Invalid {concept} observations array")
                for observation in observations:
                    try:
                        end = observation["end"]
                        filed = observation["filed"]
                        date.fromisoformat(filed)
                        accession = observation["accn"]
                        if not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
                            raise ValueError("Invalid accession")
                        value = observation["val"]
                        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
                            raise ValueError("Expected a finite numeric reported value")
                        if isinstance(value, Decimal) and not value.is_finite():
                            raise ValueError("Non-finite value")
                        kind, days = period_type(observation.get("start"), end)
                    except (KeyError, TypeError, ValueError) as exc:
                        raise SecError(f"Invalid {concept} observation: {exc}") from exc
                    flags = []
                    if (metric in ANALYSIS_INSTANT_METRICS) != (kind == "instant"):
                        flags.append("unexpected_duration_for_metric")
                    if unit != "USD":
                        flags.append("non_usd_unit")
                    filing = filings.get(accession, {})
                    if not filing:
                        flags.append("filing_metadata_not_in_loaded_submissions")
                    if filing and filing.get("filingDate") != filed:
                        flags.append("filing_date_disagreement")
                    # fy/fp are the filing's fiscal focus, not the observation's year/quarter.
                    focus_matches = (filing.get("reportDate") == end
                                     and filing.get("form", "").removesuffix("/A") in {"10-K", "10-Q"})
                    fiscal_year = observation.get("fy") if focus_matches else None
                    fp = observation.get("fp") if focus_matches else None
                    raw_observation = {k: str(v) if isinstance(v, Decimal) else v
                                       for k, v in observation.items()}
                    record = {
                        "cik": cik, "metric": metric, "taxonomy": "us-gaap", "concept": concept,
                        "label": definition.get("label"), "value": str(value), "unit": unit,
                        "start": observation.get("start"), "end": end,
                        "duration_days": days, "period_type": kind,
                        "period_basis": "instant" if kind == "instant" else "duration_inference",
                        "fiscal_year": fiscal_year, "fiscal_period": fp,
                        "filing_fiscal_year": observation.get("fy"),
                        "filing_fiscal_period": observation.get("fp"),
                        "frame": observation.get("frame"), "accession": accession,
                        "form": observation.get("form"), "filed": filed,
                        "acceptance_datetime": filing.get("acceptanceDateTime") or None,
                        "filing_report_date": filing.get("reportDate") or None,
                        "public_available_at": None,
                        "availability_note": "Filing date/acceptance do not certify first public availability",
                        "retrieved_at": retrieved_at, "classification": "reported",
                        "source_url": source_url,
                        "filing_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}-index.html",
                        "source_observation": raw_observation, "quality_flags": flags,
                    }
                    records.append(record)
    return sorted(label_fiscal_periods(records), key=lambda row: (row["metric"], row["end"],
                  row["start"] or "", row["concept"], row["unit"], row["filed"], row["record_id"]))
