"""Bounded official SEC HTTP access. No API keys or alternate providers."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import json
import math
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class SecError(Exception):
    """A request or SEC data contract could not be satisfied."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Automatic follow-ups would escape our per-request rate limiter.
        raise SecError("SEC redirected the request; inspect the endpoint before retrying")


def normalize_cik(identifier: str | int) -> str:
    text = str(identifier).strip().upper().removeprefix("CIK")
    if not re.fullmatch(r"[0-9]{1,10}", text) or int(text) == 0:
        raise ValueError(f"Invalid CIK: {identifier!r}")
    return text.zfill(10)


def is_cik(identifier: str) -> bool:
    return bool(re.fullmatch(r"(?:CIK)?[0-9]+", identifier.strip().upper()))


def normalize_ticker(identifier: str) -> str:
    """Recognize common dot/dash share-class aliases; still verify SEC associations."""
    return identifier.strip().upper().replace('.', '-')


def decode_json(body: bytes) -> dict:
    def reject_constant(value):
        raise ValueError(f"Non-finite JSON number: {value}")

    try:
        result = json.loads(body, parse_float=Decimal, parse_constant=reject_constant)
    except (ValueError, UnicodeError) as exc:
        raise SecError("SEC response was not valid finite JSON") from exc
    if not isinstance(result, dict):
        raise SecError("Expected a SEC JSON object")
    return result


@dataclass(frozen=True)
class Response:
    url: str
    body: bytes
    retrieved_at: str
    headers: dict[str, str]

    def json(self) -> dict:
        return decode_json(self.body)


class SecClient:
    """One shared limiter per client; the collector serializes store-wide jobs."""

    def __init__(self, user_agent: str, *, requests_per_second: float = 2,
                 retries: int = 3, timeout: float = 30, opener=None,
                 clock=time.monotonic, sleep=time.sleep):
        contact = re.search(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", user_agent or "")
        if (not contact or not user_agent[:contact.start()].strip()
                or "\n" in user_agent or "\r" in user_agent):
            raise ValueError("SEC User-Agent must identify your application followed by a real contact email; "
                             "a contact URL alone is insufficient")
        if not math.isfinite(requests_per_second) or not 0 < requests_per_second <= 10:
            raise ValueError("SEC request rate must be greater than 0 and at most 10/second")
        if not 0 <= retries <= 5 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Use 0–5 retries and a positive finite timeout")
        self.user_agent = user_agent
        self.interval = 1 / requests_per_second
        self.retries, self.timeout = retries, timeout
        self.opener, self.clock, self.sleep = opener or build_opener(NoRedirect()).open, clock, sleep
        self._next_request = 0.0
        self._lock = threading.Lock()

    def _wait(self):
        with self._lock:
            delay = max(0.0, self._next_request - self.clock())
            if delay:
                self.sleep(delay)
            self._next_request = self.clock() + self.interval

    def get(self, url: str) -> Response:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.netloc not in {"www.sec.gov", "data.sec.gov"}
                or parsed.username or parsed.password):
            raise ValueError("Only official HTTPS SEC endpoints are allowed")
        for attempt in range(self.retries + 1):
            self._wait()
            request = Request(url, headers={"User-Agent": self.user_agent,
                                           "Accept": "application/json",
                                           "Accept-Encoding": "identity"})
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    final = urlparse(response.geturl())
                    if final.scheme != "https" or final.netloc not in {"www.sec.gov", "data.sec.gov"}:
                        raise SecError("Unexpected redirect outside SEC")
                    if response.status != 200:
                        raise SecError(f"Unexpected SEC HTTP status {response.status}")
                    body = response.read(64 * 1024 * 1024 + 1)
                    if len(body) > 64 * 1024 * 1024:
                        raise SecError("SEC response exceeded 64 MiB limit")
                    decode_json(body)  # Detect HTML error bodies, including HTTP 200 responses.
                    headers = {name: response.headers[name] for name in
                               ("ETag", "Last-Modified", "Content-Type") if name in response.headers}
                    return Response(url, body, datetime.now(timezone.utc).isoformat(), headers)
            except HTTPError as exc:
                status, retry_after = exc.code, exc.headers.get("Retry-After")
                exc.close()
                if status not in {429, 500, 502, 503, 504} or attempt == self.retries:
                    raise SecError(f"SEC HTTP {status} for {url}; no alternate provider used") from exc
                delay = 2 ** attempt
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            then = parsedate_to_datetime(retry_after)
                            delay = max(delay, (then - datetime.now(timezone.utc)).total_seconds())
                        except (TypeError, ValueError, OverflowError):
                            pass
                if not math.isfinite(delay) or delay > 60:
                    raise SecError("SEC requested a long cooldown; stop and retry later") from exc
                self.sleep(delay)
            except (URLError, TimeoutError, OSError, HTTPException) as exc:
                if attempt == self.retries:
                    raise SecError(f"SEC network request failed for {url}: {exc}") from exc
                self.sleep(2 ** attempt)
        raise AssertionError("unreachable")
