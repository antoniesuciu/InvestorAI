"""Auditable analyst-reviewed links between exact reported financial facts.

Evidence is data, not authority to bypass period, value, revision or cutoff guards.
The pure engine checks its structure and scope; the storage adapter also verifies
the actual source-document hashes. No accounting-policy inference is automated.
"""

from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse
import re

FACT_FIELDS = ('cik', 'metric', 'taxonomy', 'concept', 'unit', 'start', 'end',
               'value', 'accession', 'fiscal_year', 'fiscal_period')
CHECKS = {'original_values_verified', 'relevant_revisions_reviewed', 'accounting_basis_consistent'}


def fact_reference(row: dict) -> dict:
    return {key: row.get(key) for key in FACT_FIELDS}


def _time(text: str) -> datetime:
    if not isinstance(text, str):
        raise ValueError('Evidence times must be ISO timestamp strings')
    result = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Evidence times must include a timezone')
    return result.astimezone(timezone.utc)


def validate_evidence(entries: list[dict], as_of: datetime) -> tuple[list[dict], list[dict]]:
    valid, diagnostics = [], []
    identities = Counter(e.get('id') for e in entries if isinstance(e, dict) and isinstance(e.get('id'), str))
    for entry in entries:
        identity = entry.get('id') if isinstance(entry, dict) else None
        try:
            if not isinstance(identity, str) or not identity or identities[identity] != 1:
                raise ValueError('missing or duplicate evidence ID')
            if entry.get('kind') not in {'cashflow_comparability', 'balance_revision'}:
                raise ValueError('unsupported evidence kind')
            if not isinstance(entry.get('basis'), str) or not entry['basis'].strip():
                raise ValueError('missing accounting-basis explanation')
            if not CHECKS <= set(entry.get('checks', [])):
                raise ValueError('incomplete source/revision review')
            facts = entry['facts']
            if not isinstance(facts, list) or len(facts) != 2 or any(set(fact) != set(FACT_FIELDS) for fact in facts):
                raise ValueError('evidence must reference two exact financial facts')
            if len({fact['cik'] for fact in facts}) != 1 or len({fact['metric'] for fact in facts}) != 1:
                raise ValueError('company/metric mismatch')
            if any(not isinstance(f['cik'], str) or not re.fullmatch(r'\d{10}', f['cik']) or
                   not isinstance(f['accession'], str) or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', f['accession']) for f in facts):
                raise ValueError('invalid CIK/accession')
            docs = entry['documents']
            if not isinstance(docs, list) or not docs:
                raise ValueError('missing original filing documents')
            if {doc['accession'] for doc in docs} != {fact['accession'] for fact in facts}:
                raise ValueError('documents do not cover both accessions')
            for doc in docs:
                if not all(isinstance(doc.get(key), str) for key in ('url', 'sha256', 'path')):
                    raise ValueError('invalid original-document reference')
                url = urlparse(doc['url'])
                prefix = f"/Archives/edgar/data/{int(facts[0]['cik'])}/{doc['accession'].replace('-', '')}/"
                if (url.scheme != 'https' or url.netloc != 'www.sec.gov' or not url.path.startswith(prefix)
                        or len(url.path) == len(prefix)):
                    raise ValueError('evidence must cite official original SEC filings')
                if not re.fullmatch('[0-9a-f]{64}', doc['sha256']) or not doc.get('path'):
                    raise ValueError('missing original-document hash/path')
            available = max(_time(entry['verified_at']), *(_time(doc['retrieved_at']) for doc in docs))
            if available > as_of:
                diagnostics.append({'id': identity, 'status': 'evidence_unavailable_at_cutoff'})
                continue
            valid.append(entry)
        except (KeyError, TypeError, ValueError) as exc:
            diagnostics.append({'id': identity, 'status': 'invalid_comparability_evidence', 'reason': str(exc)})
    return sorted(valid, key=lambda e: e['id']), sorted(diagnostics, key=lambda d: (str(d['id']), d['status'], d.get('reason', '')))


def matching_evidence(first: dict, second: dict, entries: list[dict], kind: str) -> list[dict]:
    wanted = [fact_reference(first), fact_reference(second)]
    return [entry for entry in entries if entry['kind'] == kind and
            (entry['facts'] == wanted or entry['facts'] == wanted[::-1])]
