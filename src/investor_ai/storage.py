"""Local content-addressed JSON snapshots, separate from acquisition/parsing."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import uuid

from .sec import Response, SecError, is_cik, normalize_cik, normalize_ticker


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_bytes(path: Path, body: bytes):
    """Publish complete bytes atomically; interrupted writes never occupy the final name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open('xb') as handle:
            handle.write(body)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value):
    write_bytes(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode('utf-8'))


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    @contextmanager
    def collection_lock(self):
        """Coordinate CLI/scheduled invocations using this store (POSIX)."""
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".collect.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SecError("Another collection is using this store; skip this invocation") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def save_response(self, response: Response) -> dict:
        digest = hashlib.sha256(response.body).hexdigest()
        relative = Path("raw") / f"{digest}.json"
        path = self.root / relative
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise SecError(f"Raw object checksum failed: {relative}")
        else:
            write_bytes(path, response.body)
        return {"url": response.url, "retrieved_at": response.retrieved_at,
                "sha256": digest, "path": str(relative), "headers": response.headers}

    def resource(self, name: str) -> dict | None:
        path = self.root / "resources" / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def save_resource(self, name: str, response: Response):
        write_json(self.root / "resources" / f"{name}.json", self.save_response(response))

    def raw(self, reference: dict) -> bytes:
        path = (self.root / reference["path"]).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise SecError("Raw reference escapes data directory")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != reference["sha256"]:
            raise SecError("Raw response checksum mismatch")
        return body

    def _company_dir(self, cik: str) -> Path:
        return self.root / "companies" / normalize_cik(cik)

    def latest(self, cik: str) -> dict | None:
        path = self._company_dir(cik) / "latest.json"
        if not path.exists():
            return None
        run_id = json.loads(path.read_text())["snapshot_id"]
        return self.snapshot(cik, run_id)

    def snapshot(self, cik: str, run_id: str) -> dict:
        if not run_id.isalnum():
            raise SecError("Invalid local snapshot identifier")
        return json.loads((self._company_dir(cik) / "snapshots" / run_id / "manifest.json").read_text())

    def records(self, manifest: dict) -> list[dict]:
        if not manifest["snapshot_id"].isalnum():
            raise SecError("Invalid local snapshot identifier")
        path = self._company_dir(manifest["cik"]) / "snapshots" / manifest["snapshot_id"] / "facts.json"
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != manifest["facts_sha256"]:
            raise SecError("Normalized facts checksum mismatch")
        return json.loads(body)

    def publish(self, metadata: dict, records: list[dict]) -> dict:
        run_id = uuid.uuid4().hex
        folder = self._company_dir(metadata["cik"]) / "snapshots" / run_id
        folder.mkdir(parents=True)
        write_json(folder / "facts.json", records)
        manifest = {**metadata, "schema_version": 1, "snapshot_id": run_id,
                    "facts_sha256": hashlib.sha256((folder / "facts.json").read_bytes()).hexdigest(),
                    "record_count": len(records), "created_at": utc_now()}
        write_json(folder / "manifest.json", manifest)
        # Publish only after every object is complete; failed refreshes retain the prior pointer.
        write_json(self._company_dir(metadata["cik"]) / "latest.json", {"snapshot_id": run_id})
        return manifest

    def record_attempt(self, cik: str, status: str, *, error: str | None = None):
        latest = self.latest(cik)
        path = self._company_dir(cik) / "status.json"
        prior = json.loads(path.read_text()) if path.exists() else {}
        now = utc_now()
        write_json(path, {
            "last_attempt_at": now, "last_attempt_status": status, "error": error,
            "last_failed_attempt_at": now if status == "failed" else prior.get("last_failed_attempt_at"),
            "last_failure": error if status == "failed" else prior.get("last_failure"),
            "last_successful_retrieval_at": latest["retrieved_at"] if latest else None})

    def companies(self) -> list[dict]:
        folder = self.root / "companies"
        return [self.latest(path.name) for path in sorted(folder.iterdir()) if path.name.isdecimal()
                and (path / "latest.json").exists()] if folder.exists() else []

    def resolve_local(self, identifier: str) -> str:
        if is_cik(identifier):
            return normalize_cik(identifier)
        matches = {manifest['cik'] for manifest in self.companies()
                   if normalize_ticker(identifier) in {normalize_ticker(ticker) for ticker in manifest['tickers']}}
        if len(matches) > 1:
            raise ValueError(f"Ambiguous local ticker {identifier}; use an explicit CIK")
        if matches:
            return matches.pop()
        raise ValueError(f"No collected company for {identifier}; collect it first or use its CIK")
