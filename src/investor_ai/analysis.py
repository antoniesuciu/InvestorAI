"""Pure, conservative analysis of reported SEC observations; no I/O or network.

The public interface is analyze(records, as_of=..., frequency=...). Percentages
use percentage points (15 means 15%); monetary values remain unscaled Decimals.
Calculations use a shared filing basis matching the latest known values. Cross-
filing cash-flow subtraction additionally requires exact, reviewed source evidence.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json

from .financials import ANALYSIS_METRICS, ANALYSIS_INSTANT_METRICS
from .analysis_evidence import validate_evidence, matching_evidence, fact_reference

ANALYSIS_VERSION = "2"
FLOW_METRICS = set(ANALYSIS_METRICS) - ANALYSIS_INSTANT_METRICS
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}


def utc_datetime(value: str | datetime) -> datetime:
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Use a timezone-aware analysis/availability timestamp")
    return value.astimezone(timezone.utc)


def cutoff(value: str | datetime | None = None) -> datetime:
    """A calendar date denotes the inclusive UTC end of that day."""
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str) and len(value) == 10:
        return datetime.combine(date.fromisoformat(value), datetime.max.time(), timezone.utc)
    return utc_datetime(value)


@dataclass
class Amount:
    value: Decimal | None = None
    status: str = "missing"
    sources: list[dict] = field(default_factory=list)
    formula: str = "reported"
    flags: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list, repr=False)
    selection_evidence: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)

    def output(self, unit: str) -> dict:
        return {"value": str(self.value) if self.value is not None else None,
                "unit": unit, "status": self.status, "formula": self.formula,
                "classification": "reported" if self.formula == "reported" else "derived",
                "flags": sorted(set(self.flags)), "inputs": self.sources,
                "selection_evidence": self.selection_evidence, "comparability_evidence": self.evidence}


def _sources(rows: list[dict]) -> list[dict]:
    unique = {}
    for row in rows:
        for source in row.get("_inputs", [row]):
            unique[source["record_id"]] = {k: v for k, v in source.items() if not k.startswith("_")}
    return [unique[key] for key in sorted(unique)]


def _eligible(row: dict, as_of: datetime) -> str | None:
    try:
        if row.get('metric') not in ANALYSIS_METRICS:
            return 'unsupported_metric'
        if row.get('taxonomy') != 'us-gaap':
            return 'unsupported_taxonomy'
        # Retrieval is a conservative known-availability bound, not a guessed release time.
        observed = utc_datetime(row["retrieved_at"])
        if row.get("analysis_available_at"):
            observed = max(observed, utc_datetime(row["analysis_available_at"]))
        if observed > as_of or date.fromisoformat(row["filed"]) > as_of.date():
            return "unavailable_at_cutoff"
        if row.get("acceptance_datetime") and utc_datetime(row["acceptance_datetime"]) > as_of:
            return "unavailable_at_cutoff"
        if date.fromisoformat(row["end"]) > as_of.date():
            return "future_period"
        if any(date.fromisoformat(row[key]).isoformat() != row[key] for key in ('filed', 'end')):
            return 'invalid_period'
        if any(date.fromisoformat(s["filed"]) > as_of.date() for s in row.get("fiscal_label_sources", [])):
            return "future_fiscal_evidence"
        if row.get("form") not in FORMS:
            return "unsupported_form"
        if row.get("classification", "reported") != "reported":
            return "unsupported_input_classification"
        if isinstance(row['value'], (bool, float)):
            return "invalid_value"
        if not Decimal(row["value"]).is_finite():
            return "invalid_value"
        start, end = row.get('start'), date.fromisoformat(row['end'])
        if row['period_type'] == 'instant':
            if start is not None:
                return 'invalid_period'
        else:
            days = (end - date.fromisoformat(start)).days + 1
            bounds = {'annual': (330, 400), 'quarterly': (70, 110), 'year_to_date': (150, 300)}
            if days <= 0 or (row['period_type'] in bounds and not bounds[row['period_type']][0] <= days <= bounds[row['period_type']][1]):
                return 'invalid_period'
        if set(row.get("quality_flags", [])) & {"ambiguous_fiscal_period", "filing_date_disagreement", "unexpected_duration_for_metric"}:
            return "unreliable_period_metadata"
        for key in ("record_id", "accession", "concept", "unit", "cik", "metric"):
            if not row.get(key):
                return "missing_provenance"
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return "invalid_availability_or_value"
    return None


def _derive_quarters(rows: list[dict], evidence: list[dict]) -> tuple[list[dict], list[dict]]:
    """Derive aligned intervals, requiring reviewed evidence across accessions."""
    groups = defaultdict(list)
    measurements = defaultdict(list)
    for row in rows:
        if row["metric"] in FLOW_METRICS and row.get("start"):
            # A known changed value remains a revision even if its fiscal label
            # is unresolved. It cannot be used to derive, or silently ignored.
            measurements[(row['metric'], row['concept'], row['unit'], row['start'], row['end'])].append(row)
        if row["metric"] in FLOW_METRICS and row.get("start") and row.get("fiscal_year") is not None:
            groups[(row["cik"], row["metric"], row["concept"], row["unit"],
                    row["fiscal_year"], row["start"])].append(row)
    latest_values = {}
    for key, values in measurements.items():
        latest, _ = _latest_rows(values)
        amounts = {Decimal(r['value']) for r in latest}
        latest_values[key] = amounts.pop() if len(amounts) == 1 else None
    derived = {}
    failures = defaultdict(lambda: {'reasons': set(), 'accessions': set(), 'records': {}})
    successes = set()
    for values in groups.values():
        for longer in values:
            q = {"Q2": 2, "Q3": 3, "FY": 4}.get(longer.get("fiscal_period"))
            if q is None or longer["period_type"] not in {"annual", "year_to_date"}:
                continue
            for shorter in values:
                if shorter.get("fiscal_period") != f"Q{q - 1}":
                    continue
                if shorter["period_type"] not in {"quarterly", "year_to_date"}:
                    continue
                start = date.fromisoformat(shorter["end"]) + timedelta(days=1)
                days = (date.fromisoformat(longer["end"]) - start).days + 1
                # Both cumulative lengths must agree with their fiscal end-quarter labels.
                long_days = (date.fromisoformat(longer["end"]) - date.fromisoformat(longer["start"])).days + 1
                short_days = (date.fromisoformat(shorter["end"]) - date.fromisoformat(shorter["start"])).days + 1
                if not (70 <= days <= 110 and abs(long_days - 91 * q) <= 20
                        and abs(short_days - 91 * (q - 1)) <= 20):
                    continue
                diagnostic_key = (start.isoformat(), longer['end'], longer['metric'])
                def refuse(reason):
                    failures[diagnostic_key]['reasons'].add(reason)
                    failures[diagnostic_key]['accessions'].update((longer['accession'], shorter['accession']))
                    failures[diagnostic_key]['records'].update({r['record_id']: r for r in (longer, shorter)})
                if any(latest_values[(r['metric'], r['concept'], r['unit'], r['start'], r['end'])] is None for r in (longer, shorter)):
                    refuse('conflicting_cumulative_values')
                    continue
                if any(latest_values[(r['metric'], r['concept'], r['unit'], r['start'], r['end'])] != Decimal(r['value'])
                       for r in (longer, shorter)):
                    refuse('cumulative_revision_not_reconciled')
                    continue
                reviewed = []
                cross_filing = longer['accession'] != shorter['accession']
                if cross_filing:
                    if longer['metric'] not in {'operating_cash_flow', 'capital_expenditure'}:
                        continue
                    earlier = shorter['filed'] < longer['filed']
                    if shorter['filed'] == longer['filed']:
                        short_time = shorter.get('accession_acceptance_at') or shorter.get('acceptance_datetime')
                        long_time = longer.get('accession_acceptance_at') or longer.get('acceptance_datetime')
                        earlier = bool(short_time and long_time and utc_datetime(short_time) <= utc_datetime(long_time))
                    if not earlier:
                        refuse('cumulative_revision_or_order_requires_reconciliation')
                        continue
                    matches = matching_evidence(longer, shorter, evidence, 'cashflow_comparability')
                    if not matches:
                        refuse('cross_filing_comparability_evidence_required')
                        continue
                    reviewed = [max(matches, key=lambda e: (e['verified_at'], e['id']))]
                row = deepcopy(longer)
                row.update(start=start.isoformat(), duration_days=days, period_type="quarterly",
                           fiscal_quarter=q, fiscal_period=f"Q{q}", is_year_to_date=False,
                           value=str(Decimal(longer["value"]) - Decimal(shorter["value"])),
                           _inputs=_sources([longer, shorter]), _formula="cumulative_current - cumulative_previous",
                           _vintage=(longer['accession'], shorter['accession']) if cross_filing else (longer['accession'],),
                           _evidence=reviewed)
                key = hashlib.sha256(json.dumps([longer["record_id"], shorter["record_id"], row["start"], [e['id'] for e in reviewed]]).encode()).hexdigest()
                row["record_id"] = "derived:" + key
                derived[row["record_id"]] = row
                successes.add(diagnostic_key)
    diagnostics = [{'start': start, 'end': end, 'metric': metric,
                    'reasons': sorted(value['reasons']), 'accessions': sorted(value['accessions']),
                    'inputs': _sources(list(value['records'].values()))}
                   for (start, end, metric), value in sorted(failures.items()) if (start, end, metric) not in successes]
    return list(derived.values()), diagnostics


def _latest_rows(rows: list[dict]) -> tuple[list[dict], str]:
    """Order dates/acceptances, never accession strings or missing-time midnights."""
    if not rows:
        return [], 'missing'
    day = max(row['filed'] for row in rows)
    latest = [row for row in rows if row['filed'] == day]
    accessions = {row['accession'] for row in latest}
    if len(accessions) == 1:
        return latest, 'ok'
    def accepted(row):
        return row.get('accession_acceptance_at') or row.get('acceptance_datetime')
    if any(not accepted(row) for row in latest):
        return latest, 'ambiguous_filing_order'
    last = max(utc_datetime(accepted(row)) for row in latest)
    return [row for row in latest if utc_datetime(accepted(row)) == last], 'ok'


def _pick(rows: list[dict], metric: str, concepts: dict[str, str], preferred: str | None = None,
          *, annual_balance=False, revision_accessions=frozenset(), corroborating_accessions=frozenset()) -> Amount:
    selected = [row for row in rows if row["metric"] == metric]
    if metric in concepts:
        selected = [r for r in selected if r["concept"] == concepts[metric]]
    if not selected:
        return Amount(status="missing_concept" if metric in concepts else "missing")
    if len({row["concept"] for row in selected}) > 1:
        return Amount(status="ambiguous_concept", sources=_sources(selected))
    authority = selected
    if annual_balance and any(r['form'] in {'10-K', '10-K/A'} for r in selected):
        authority = [r for r in selected if r['form'] in {'10-K', '10-K/A'} or r['accession'] in revision_accessions]
        authoritative, _ = _latest_rows(authority)
        latest_any, _ = _latest_rows(selected)
        if (any(r['accession'] not in {a['accession'] for a in authority} for r in latest_any)
                and {Decimal(r['value']) for r in latest_any} != {Decimal(r['value']) for r in authoritative}):
            return Amount(status='unreconciled_interim_revision', sources=_sources(authoritative + latest_any))
    latest, order = _latest_rows(authority)
    values = {Decimal(row['value']) for row in latest}
    if len(values) > 1:
        return Amount(status=order if order != 'ok' else 'conflicting_values', sources=_sources(latest))
    value = values.pop()
    # An unchanged later comparative corroborates a complete statement; it does
    # not erase it. Changed values restrict every downstream calculation instead.
    matching = [row for row in authority if Decimal(row['value']) == value]
    matching.extend(row for row in selected if row not in matching and row['accession'] in corroborating_accessions
                    and Decimal(row['value']) == value)
    preferred_rows = [row for row in matching if row['accession'] == preferred]
    # Prefer a reported interval when an independently derived interval agrees.
    row = min(preferred_rows or latest, key=lambda r: ("_formula" in r, r["record_id"]))
    flags = ["derived_from_cumulative"] if "_formula" in row else []
    if row.get('_evidence') and '_formula' in row:
        flags.append('reviewed_cross_filing_derivation')
    if len({Decimal(r['value']) for r in authority}) > 1:
        flags.append('revised_reported_value')
    if row['accession'] in revision_accessions:
        flags.append('documented_interim_revision')
    if annual_balance and not any(r['form'] in {'10-K', '10-K/A'} for r in authority):
        flags.append('unaudited_annual_balance')
    trace = [{key: r.get(key) for key in ('record_id', 'accession', 'filed', 'acceptance_datetime', 'retrieved_at', 'value', 'form')}
             for r in sorted(selected, key=lambda r: (r['filed'], r['record_id']))]
    return Amount(value, 'ok', _sources([row]), row.get('_formula', 'reported'), flags,
                  matching, trace, row.get('_evidence', []))


def _statement(rows: list[dict], *, audited=False) -> tuple[str | None, str]:
    """Prefer a complete focus statement; amendments remain metric-level revisions."""
    if not rows:
        return None, 'missing_period'
    groups = defaultdict(list)
    for row in rows:
        groups[row['accession']].append(row)
    def rank(group):
        focus = any(r.get('filing_report_date') == r['end'] or
                    (r.get('filing_fiscal_year') == r.get('fiscal_year') and r.get('fiscal_year') is not None)
                    for r in group)
        audited_form = any(r['form'] in {'10-K', '10-K/A'} for r in group)
        full_form = any(not r['form'].endswith('/A') for r in group)
        return (audited_form if audited else True, focus, full_form, len({r['metric'] for r in group}))
    best = max(rank(group) for group in groups.values())
    best_rows = [r for group in groups.values() if rank(group) == best for r in group]
    return _cohort(best_rows)


def _vintage(row: dict) -> tuple:
    return tuple(row.get('_vintage', (row['accession'],)))


def _align(inputs: list[Amount]) -> tuple[list[dict], list[dict]] | None:
    """Find a common filing basis that reproduces every latest selected value."""
    maps = []
    for amount in inputs:
        matches = defaultdict(list)
        for row in amount.candidates:
            matches[_vintage(row)].append(row)
        maps.append(matches)
    common = set(maps[0]).intersection(*(set(m) for m in maps[1:])) if maps else set()
    if not common:
        return None
    # Prefer the selected complete-statement basis when it supports all terms.
    preferred = [key for key in common if all(any(_vintage(row) == key and row['record_id'] in
                 {source['record_id'] for source in amount.sources} for row in amount.candidates)
                 for amount in inputs)]
    key = min(preferred or common)
    chosen = [min(mapping[key], key=lambda r: ('_formula' in r, r['record_id'])) for mapping in maps]
    evidence = {entry['id']: entry for row in chosen for entry in row.get('_evidence', [])}
    return chosen, [evidence[key] for key in sorted(evidence)]


def _cohort(rows: list[dict]) -> tuple[str | None, str]:
    candidates = {row["accession"] for row in rows}
    if not candidates:
        return None, "missing_period"
    latest_day = max(row["filed"] for row in rows)
    candidates = {row["accession"] for row in rows if row["filed"] == latest_day}
    if len(candidates) == 1:
        return candidates.pop(), "ok"
    times = {}
    for accession in candidates:
        observations = [r for r in rows if r["accession"] == accession]
        accepted = {utc_datetime(r.get('accession_acceptance_at') or r['acceptance_datetime']) for r in observations
                    if r.get('accession_acceptance_at') or r.get('acceptance_datetime')}
        if len(accepted) != 1 or any(not (r.get('accession_acceptance_at') or r.get('acceptance_datetime')) for r in observations):
            return None, "ambiguous_filing_order"
        times[accession] = accepted.pop()
    newest = [key for key, value in times.items() if value == max(times.values())]
    return (newest[0], "ok") if len(newest) == 1 else (None, "ambiguous_filing_order")


def _calculate(formula: str, inputs: list[Amount], operation, *, positive_base=False,
               nonnegative=False, percent=False) -> Amount:
    sources = _sources([source for amount in inputs for source in amount.sources])
    flags = [flag for amount in inputs for flag in amount.flags]
    if any(amount.value is None for amount in inputs):
        reasons = sorted({amount.status for amount in inputs if amount.value is None})
        return Amount(status="unavailable_inputs", sources=sources, formula=formula, flags=flags + reasons)
    values = [amount.value for amount in inputs]
    if positive_base and values[-1] <= 0:
        return Amount(status="nonpositive_denominator", sources=sources, formula=formula, flags=flags)
    if nonnegative and any(value < 0 for value in values):
        return Amount(status="negative_balance", sources=sources, formula=formula, flags=flags)
    aligned = _align(inputs)
    if aligned is None:
        return Amount(status='incompatible_revision_vintages', sources=sources, formula=formula, flags=flags)
    chosen, evidence = aligned
    sources = _sources(chosen)
    value = operation(*values)
    history = {item['record_id']: item for amount in inputs for item in amount.selection_evidence}
    return Amount(value * 100 if percent else value, "ok", sources, formula, flags,
                  selection_evidence=[history[key] for key in sorted(history)], evidence=evidence)


def _growth(current: Amount, row: dict, rows: list[dict], metric: str,
            frequency: str, concepts: dict[str, str]) -> tuple[Amount, Amount]:
    if row.get("fiscal_year") is None or (frequency == "quarterly" and row.get("fiscal_quarter") is None):
        missing = Amount(status="ambiguous_fiscal_period")
    else:
        previous = [r for r in rows if r["metric"] == metric
                    and r["period_type"] == frequency and r.get("fiscal_year") == row["fiscal_year"] - 1
                    and (frequency == "annual" or r.get("fiscal_quarter") == row["fiscal_quarter"])]
        intervals = {(r["start"], r["end"]) for r in previous}
        if len(intervals) > 1:
            missing = Amount(status="ambiguous_comparable_period", sources=_sources(previous))
        elif previous:
            previous_row = previous[0]
            gap = (date.fromisoformat(row["end"]) - date.fromisoformat(previous_row["end"])).days
            lengths = [(date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days for r in (row, previous_row)]
            if not 330 <= gap <= 400 or abs(lengths[0] - lengths[1]) > 14:
                missing = Amount(status="noncomparable_fiscal_calendar", sources=_sources(previous))
            else:
                previous_accession, _ = _statement(previous)
                missing = _pick(previous, metric, concepts, previous_accession)
                if lengths[0] != lengths[1]:
                    missing.flags.append('different_period_lengths')
                # Growth cannot silently bridge a taxonomy/definition change.
                if current.sources and missing.sources and {r["concept"] for r in current.sources} != {r["concept"] for r in missing.sources}:
                    missing = Amount(status="concept_change", sources=missing.sources)
        else:
            missing = Amount(status="missing_comparable_period")
    delta = _calculate("current - prior_year", [current, missing], lambda a, b: a - b)
    growth = _calculate("(current - prior_year) / prior_year * 100", [current, missing],
                        lambda a, b: (a - b) / b, positive_base=True, percent=True)
    if current.value is not None and current.value < 0:
        growth.flags.append("negative_current_value")
    return growth, delta


def analyze(records: list[dict], *, as_of: str | datetime | None = None,
            frequency: str = "annual", unit: str = "USD",
            concepts: dict[str, str] | None = None, evidence: list[dict] | None = None) -> dict:
    """Return JSON-ready periods and provenance-bearing metrics, without mutating inputs.

    Unchanged comparatives preserve complete statements; known revisions restrict
    compatible inputs. Cross-filing cash flows need reviewed evidence. No price, forecast,
    execution, network or filesystem dependency is present in this module.
    """
    if frequency not in {"annual", "quarterly"}:
        raise ValueError("Frequency must be annual or quarterly")
    as_of = cutoff(as_of)
    reviewed, evidence_diagnostics = validate_evidence(evidence or [], as_of)
    concepts = concepts or {}
    if set(concepts) - set(ANALYSIS_METRICS):
        raise ValueError("Unknown metric in concept selection")
    if any(value not in ANALYSIS_METRICS[key] for key, value in concepts.items()):
        raise ValueError("Concept selection must name a supported US-GAAP concept")
    ciks = {r.get("cik") for r in records}
    if len(ciks) > 1:
        raise ValueError("Analyze one company at a time")
    excluded = Counter()
    eligible = []
    acceptances = defaultdict(set)
    acceptance_sources = defaultdict(set)
    for row in records:
        if row.get('acceptance_datetime'):
            try:
                observed = max(utc_datetime(row['retrieved_at']), utc_datetime(row.get('analysis_available_at', row['retrieved_at'])))
                if observed <= as_of:
                    acceptances[row.get('accession')].add(utc_datetime(row['acceptance_datetime']))
                    acceptance_sources[row.get('accession')].add(row.get('record_id'))
            except (KeyError, TypeError, ValueError):
                pass
    for row in records:
        known = acceptances[row.get('accession')]
        if len(known) > 1:
            reason = 'inconsistent_filing_acceptance'
        elif known and next(iter(known)) > as_of:
            reason = 'unavailable_at_cutoff'
        else:
            reason = _eligible(row, as_of)
        if reason is None and row["unit"] != unit:
            reason = "different_unit"
        if reason:
            excluded[reason] += 1
        else:
            copy = deepcopy(row)
            if known:
                copy['accession_acceptance_at'] = next(iter(known)).isoformat()
                copy['accession_acceptance_sources'] = sorted(value for value in acceptance_sources[row.get('accession')] if value)[:1]
            eligible.append(copy)
    # Only exact, observed, source-reviewed corrections can displace annual
    # audited evidence with an interim comparative. Unrelated metrics stay guarded.
    revisions, revision_families = defaultdict(set), defaultdict(set)
    for entry in reviewed:
        if entry['kind'] != 'balance_revision':
            continue
        left = [r for r in eligible if fact_reference(r) == entry['facts'][0]]
        right = [r for r in eligible if fact_reference(r) == entry['facts'][1]]
        for first in left:
            for second in right:
                old, new = sorted((first, second), key=lambda r: r['filed'])
                if (old['form'] not in {'10-K', '10-K/A'} or old['start'] is not None or new['start'] is not None
                        or old['end'] != new['end'] or old['concept'] != new['concept'] or old['unit'] != new['unit']
                        or new['filed'] <= old['filed']):
                    continue
                revisions[(new['end'], new['metric'])].add(new['accession'])
                revision_families[new['end']].add(new['accession'])
                new.setdefault('_evidence', []).append(entry)
    with localcontext() as context:
        context.prec = 34
        derived, derivation_diagnostics = _derive_quarters(eligible, reviewed) if frequency == 'quarterly' else ([], [])
        rows = eligible + derived
        periods = defaultdict(list)
        for row in rows:
            if row["metric"] in FLOW_METRICS and row["period_type"] == frequency:
                periods[(row["start"], row["end"])].append(row)
        output = []
        for (start, end), candidates in sorted(periods.items()):
            accession, status = _statement(candidates)
            cohort = [r for r in candidates if r["accession"] == accession] if accession else []
            years = {r['fiscal_year'] for r in candidates if r.get('fiscal_year') is not None}
            quarters = {r['fiscal_quarter'] for r in candidates if r.get('fiscal_quarter') is not None}
            fy = years.pop() if len(years) == 1 else None
            fq = quarters.pop() if frequency == 'quarterly' and len(quarters) == 1 else None
            period = {"start": start, "end": end, "frequency": frequency, "fiscal_year": fy,
                      "fiscal_quarter": fq, "accession": accession, "selection_status": status}
            period['fiscal_status'] = 'known' if fy is not None and (frequency == 'annual' or fq is not None) else 'unresolved'
            # Balance-only ratios have their own coherent vintage, independent of
            # the filing that most recently repeats an income/cash-flow comparative.
            balance_candidates = [r for r in rows if r['end'] == end and r['period_type'] == 'instant']
            balance_accession, balance_status = _statement(balance_candidates, audited=frequency == 'annual')
            balances = balance_candidates
            period.update(balance_accession=balance_accession, balance_selection_status=balance_status)
            amounts = {metric: _pick(balances if metric in ANALYSIS_INSTANT_METRICS else candidates, metric, concepts,
                                    balance_accession if metric in ANALYSIS_INSTANT_METRICS else accession,
                                    annual_balance=frequency == 'annual' and metric in ANALYSIS_INSTANT_METRICS,
                                    revision_accessions=revisions[(end, metric)],
                                    corroborating_accessions=revision_families[end] if metric in ANALYSIS_INSTANT_METRICS else frozenset())
                       for metric in ANALYSIS_METRICS}
            period['statement_accession'] = accession
            if amounts['revenue'].sources:
                period['accession'] = amounts['revenue'].sources[0]['accession']
            if any(amount.status == 'ambiguous_filing_order' for amount in amounts.values()):
                period['selection_status'] = 'ambiguous_filing_order'
            # Explain why an available YTD amount is not used as a standalone quarter.
            if frequency == "quarterly":
                for metric in ("operating_cash_flow", "capital_expenditure"):
                    if amounts[metric].status == "missing":
                        cumulative = [r for r in eligible if r["metric"] == metric
                                      and r["end"] == end and r["period_type"] in {"annual", "year_to_date"}]
                        if cumulative:
                            diagnostic = [item for item in derivation_diagnostics if item['start'] == start and item['end'] == end and item['metric'] == metric]
                            reasons = [reason for item in diagnostic for reason in item['reasons']]
                            missing_status = ('conflicting_values' if 'conflicting_cumulative_values' in reasons else
                                              'cumulative_revision_not_reconciled' if any('revision' in reason for reason in reasons) else
                                              'cross_filing_comparability_evidence_required' if reasons else
                                              'insufficient_same_filing_cumulative_evidence')
                            proof_inputs = [source for item in diagnostic for source in item['inputs']]
                            amounts[metric] = Amount(status=missing_status, sources=_sources(proof_inputs or cumulative), flags=reasons)
            result = {key: amount.output(unit) for key, amount in amounts.items()}
            for metric, name in (("net_income", "net_margin_pct"), ("gross_profit", "gross_margin_pct"),
                                 ("operating_income", "operating_margin_pct")):
                value = _calculate(f"{metric} / revenue * 100", [amounts[metric], amounts["revenue"]],
                                   lambda a, b: a / b, positive_base=True, percent=True)
                result[name] = value.output("percent")
            if amounts["capital_expenditure"].value is not None and amounts["capital_expenditure"].value < 0:
                capex = Amount(status="negative_capex_payment", sources=amounts["capital_expenditure"].sources)
            else:
                capex = amounts["capital_expenditure"]
            result["free_cash_flow"] = _calculate("operating_cash_flow - capital_expenditure", [amounts["operating_cash_flow"], capex], lambda a, b: a - b).output(unit)
            for name, numerator, denominator in (("liabilities_to_assets", "liabilities", "assets"),
                                                  ("current_ratio", "current_assets", "current_liabilities"),
                                                  ("cash_to_current_liabilities", "cash", "current_liabilities"),
                                                  ("noncurrent_debt_to_equity", "noncurrent_debt", "equity")):
                result[name] = _calculate(f"{numerator} / {denominator}", [amounts[numerator], amounts[denominator]],
                                          lambda a, b: a / b, positive_base=True, nonnegative=True).output("ratio")
            reference = {**period, "accession": accession}
            for metric in ("revenue", "net_income"):
                growth, delta = _growth(amounts[metric], reference, rows, metric, frequency, concepts)
                result[metric + "_growth_yoy_pct"] = growth.output("percent")
                result[metric + "_change_yoy"] = delta.output(unit)
            output.append({"period": period, "metrics": result})
    return {"analysis_version": ANALYSIS_VERSION, "as_of": as_of.isoformat(), "frequency": frequency,
            "unit": unit, "cik": next(iter(ciks)) if ciks else None, "concepts": dict(concepts),
            "availability_policy": "observed_by_cutoff", "excluded_observations": dict(excluded),
            "evidence_diagnostics": evidence_diagnostics, "cashflow_derivation_diagnostics": derivation_diagnostics,
            "periods": output, "limitations": [
                "Historical dates before collection are unavailable; filing dates alone are not verified availability",
                "Calculations reconcile latest values to a shared filing basis; cross-filing cash flows require reviewed documentary evidence",
                "Free cash flow is operating cash flow less gross PP&E payments, not discretionary cash or a GAAP subtotal",
                "Noncurrent debt/equity excludes current debt, commercial paper and lease obligations",
                "Fiscal calendars and duration labels require validation; no forecasts or investment conclusions"]}
