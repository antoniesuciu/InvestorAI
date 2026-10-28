"""Offline adapter from permitted SEC snapshots to the pure analysis interface."""

from datetime import datetime
import json
from pathlib import Path

from .analysis import analyze, cutoff, utc_datetime
from .financials import ANALYSIS_METRICS, PARSER_VERSION, extract_facts
from .sec import SecError, decode_json
from .storage import Store
from .analysis_evidence import validate_evidence


def analyze_company(store: Store, identifier: str, *, as_of: str | datetime | None = None,
                    frequency: str = "annual", unit: str = "USD", concepts: dict | None = None,
                    snapshot_id: str | None = None, evidence_path: str | Path | None = None) -> dict:
    """Choose a snapshot completed by the cutoff and reparse its existing raw data.

    Additional balance/income tags are already in CompanyFacts. Neither collection
    nor stored Milestone 1 snapshots are changed. No network request is possible.
    """
    as_of = cutoff(as_of)
    cik = store.resolve_local(identifier)
    if snapshot_id:
        manifests = [store.snapshot(cik, snapshot_id)]
    else:
        folder = store.root / "companies" / cik / "snapshots"
        manifests = [store.snapshot(cik, path.name) for path in folder.iterdir()
                     if path.is_dir() and (path / "manifest.json").is_file()] if folder.exists() else []
    candidates = []
    for manifest in manifests:
        sources = manifest["sources"]
        references = [sources["companyfacts"], sources["submissions"], *sources["submissions_history"]]
        available = max(utc_datetime(manifest["created_at"]), *(utc_datetime(ref["retrieved_at"]) for ref in references))
        if available <= as_of:
            candidates.append((available, manifest))
    if not candidates:
        raise SecError("No saved snapshot was available by the requested analysis date; filing dates alone cannot certify historical availability")
    newest = max(value for value, _ in candidates)
    latest = [item for value, item in candidates if value == newest]
    if len(latest) != 1:
        raise SecError('Ambiguous snapshot completion order; select an explicit snapshot ID')
    manifest = latest[0]
    # Verify persisted results as well as every raw object before using the snapshot.
    store.records(manifest)
    sources = manifest["sources"]
    facts = decode_json(store.raw(sources["companyfacts"]))
    submissions = decode_json(store.raw(sources["submissions"]))
    history = [decode_json(store.raw(ref)) for ref in sources["submissions_history"]]
    rows = extract_facts(facts, submissions, historical=history,
                         retrieved_at=sources["companyfacts"]["retrieved_at"],
                         source_url=sources["companyfacts"]["url"], metrics=ANALYSIS_METRICS)
    available = newest
    for row in rows:
        row["analysis_available_at"] = available.isoformat()
    path = Path(evidence_path) if evidence_path is not None else store.root / 'analysis_evidence.json'
    entries = []
    if path.exists():
        document = json.loads(path.read_text())
        if not isinstance(document, dict) or document.get('schema_version') != 1 or not isinstance(document.get('entries'), list):
            raise SecError('Comparability evidence file must have schema_version=1 and an entries list')
        entries = document['entries']
        usable, _ = validate_evidence(entries, as_of)
        for item in usable:
            for reference in item['documents']:
                store.raw(reference)  # Validates path containment and original-document content hash.
    elif evidence_path is not None:
        raise SecError('The specified comparability evidence file does not exist')
    report = analyze(rows, as_of=as_of, frequency=frequency, unit=unit, concepts=concepts, evidence=entries)
    report['evidence_file'] = str(path) if entries else None
    report["snapshot"] = {key: manifest[key] for key in ("snapshot_id", "cik", "name", "retrieved_at", "created_at")}
    report["snapshot"]["sources"] = sources
    report["snapshot"]["extractor_version"] = PARSER_VERSION
    return report
