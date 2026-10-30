"""Versioned market evidence; provider HTTP and credential policy stay in sources."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import fcntl
import json
import math
from pathlib import Path
import time
import uuid

from .analysis import cutoff, utc_datetime
from .market_data import MarketDataError, normalize_bundle, normalize_live_bundle, merge_action_coverage, coverage_covers, _seal_coverage
from .storage import utc_now, write_bytes, write_json

NORMALIZER_VERSION = '3'


def decode(body):
    from decimal import Decimal
    def invalid(value):
        raise MarketDataError('Nonfinite JSON input')
    try:
        result = json.loads(body, parse_float=Decimal, parse_constant=invalid)
    except (ValueError, UnicodeError) as exc:
        raise MarketDataError('Invalid market JSON input') from exc
    if not isinstance(result, dict):
        raise MarketDataError('Expected a market JSON object')
    return result


def _no_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower().replace('-', '_') in {'api_key', 'apikey', 'api_secret', 'secret', 'password', 'authorization', 'credentials'}:
                raise MarketDataError('Credential fields are not permitted in offline evidence')
            _no_secrets(item)
    elif isinstance(value, list):
        for item in value:
            _no_secrets(item)


def dataset_key(header):
    identity = {key: header[key] for key in ('instrument', 'provider', 'feed', 'adjustment', 'adjustment_as_of')}
    if header.get('synthetic') is False:
        identity['synthetic'] = False  # Preserve existing synthetic series keys.
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class JsonFileSource:
    """A working paginated source for authored synthetic bundles only.

    No boolean enables licensed market APIs. Real acquisition uses a separate
    provider adapter; a fixture label is not a data license.
    """
    def __init__(self, path):
        body = Path(path).read_bytes()
        if len(body) > 16 * 1024 * 1024:
            raise MarketDataError('Offline bundle exceeds 16 MiB')
        data = decode(body)
        _no_secrets(data)
        if data.get('synthetic') is not True:
            raise MarketDataError('Only authored synthetic bundles are enabled; live integration is blocked pending provider rights')
        self.header = {key: value for key, value in data.items() if key != 'pages'}
        self.pages = data.get('pages')
        if not isinstance(self.pages, list) or not self.pages:
            raise MarketDataError('Bundle needs nonempty pages')
        # Authored files must use plain string or integer monetary values. This
        # keeps source replay lossless and avoids serializing binary floats.
        try:
            self.pages = json.loads(json.dumps(self.pages, allow_nan=False))
        except (ValueError, TypeError) as exc:
            raise MarketDataError('Use decimal strings in offline source files') from exc
        self.fingerprint = hashlib.sha256(body).hexdigest()

    def fetch_page(self, *, start, end, token):
        index = int(token) if token is not None else 0
        if not 0 <= index < len(self.pages):
            raise MarketDataError('Invalid page token')
        page = deepcopy(self.pages[index])
        if not isinstance(page, dict) or any(not isinstance(page.get(key), list) for key in ('bars', 'actions')):
            raise MarketDataError('Each source page requires explicit bars and actions arrays')
        page['bars'] = [r for r in page['bars'] if start <= r['date'] <= end]
        page['actions'] = [r for r in page['actions'] if start <= r['ex_date'] <= end]
        page['next_page_token'] = str(index + 1) if index + 1 < len(self.pages) else None
        return json.dumps(page, sort_keys=True, allow_nan=False).encode()


class RetryableSourceError(Exception):
    """An adapter signals a transient failure, without exposing request secrets."""
    def __init__(self, retry_after=0):
        self.retry_after = retry_after


def _latest(rows, key):
    groups = {}
    for row in rows:
        identity = row[key]
        previous = groups.get(identity)
        if previous and row['observed_at'] == previous['observed_at'] and row['record_id'] != previous['record_id']:
            raise MarketDataError('Conflicting observations at the same availability timestamp')
        if previous is None or utc_datetime(row['observed_at']) > utc_datetime(previous['observed_at']):
            groups[identity] = row
    return [groups[k] for k in sorted(groups)]


class MarketStore:
    def __init__(self, root='data/market'):
        self.root = Path(root)

    @contextmanager
    def collection_lock(self):
        """Market-owned nonblocking POSIX lock; no SEC exception contract leaks."""
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / '.collect.lock').open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise MarketDataError('Another market collection is using this store; skip this invocation') from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self, reference):
        path = (self.root / reference['path']).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise MarketDataError('Market reference escapes store')
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != reference['sha256']:
            raise MarketDataError('Market evidence checksum mismatch')
        return body

    def _save(self, body, category='raw'):
        digest = hashlib.sha256(body).hexdigest()
        ref = {'path': f'{category}/{digest}.json', 'sha256': digest}
        if (self.root / ref['path']).exists():
            self._read(ref)
        else:
            write_bytes(self.root / ref['path'], body)
        return ref

    def _manifest(self, key, snapshot):
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key) or not snapshot.isalnum():
            raise MarketDataError('Invalid market snapshot identifier')
        return json.loads((self.root / 'datasets' / key / 'snapshots' / snapshot / 'manifest.json').read_text())

    def latest(self, key):
        path = self.root / 'datasets' / key / 'latest.json'
        return self._manifest(key, json.loads(path.read_text())['snapshot_id']) if path.exists() else None

    def load(self, manifest):
        for ref in manifest['sources']:
            self._read(ref)
        return json.loads(self._read(manifest['data']))

    def status(self, key, outcome):
        path = self.root / 'datasets' / key / 'status.json'
        old = json.loads(path.read_text()) if path.exists() else {}
        latest = self.latest(key)
        write_json(path, {**old, 'last_attempt_at': utc_now(), 'last_attempt_status': outcome,
                         'last_failed_at': utc_now() if outcome == 'failed' else old.get('last_failed_at'),
                         'last_successful_retrieval_at': latest['retrieved_at'] if latest else None})

    def publish(self, key, data, sources, *, retrieved_at, fingerprint):
        body = (json.dumps(data, sort_keys=True, allow_nan=False) + '\n').encode()
        reference = self._save(body, 'normalized')
        snapshot = uuid.uuid4().hex
        previous = self.latest(key)
        manifest = {'schema_version': 1, 'normalizer_version': NORMALIZER_VERSION,
                    'dataset_key': key, 'snapshot_id': snapshot, 'created_at': utc_now(),
                    'retrieved_at': retrieved_at, 'source_fingerprint': fingerprint,
                    'instrument': data['instrument'], 'provider': data['provider'], 'feed': data['feed'],
                    'adjustment': data['adjustment'], 'adjustment_as_of': data['adjustment_as_of'],
                    'synthetic': data['synthetic'], 'sources': sources, 'data': reference,
                    'previous_snapshot_id': previous['snapshot_id'] if previous else None}
        folder = self.root / 'datasets' / key
        write_json(folder / 'snapshots' / snapshot / 'manifest.json', manifest)
        write_json(folder / 'latest.json', {'snapshot_id': snapshot})
        return manifest

    def inspect(self, identifier, *, as_of=None, provider=None, feed=None, adjustment='raw', adjustment_as_of=None):
        at = cutoff(as_of)
        candidates = []
        folder = self.root / 'datasets'
        published = []
        for pointer in sorted(folder.glob('*/latest.json')):
            item = self.latest(pointer.parent.name)
            seen = set()
            while item:
                if item['snapshot_id'] in seen:
                    raise MarketDataError('Cyclic market snapshot chain')
                seen.add(item['snapshot_id'])
                published.append(item)
                parent = item.get('previous_snapshot_id')
                item = self._manifest(pointer.parent.name, parent) if parent else None
        for item in published:
            instrument = item['instrument']
            if identifier.upper() not in {instrument['id'].upper(), instrument['symbol'].upper()}:
                continue
            if (provider is not None and item['provider'] != provider) or (feed is not None and item['feed'] != feed):
                continue
            if item['adjustment'] != adjustment or (adjustment_as_of is not None and item['adjustment_as_of'] != adjustment_as_of):
                continue
            if max(utc_datetime(item['created_at']), utc_datetime(item['retrieved_at'])) <= at:
                candidates.append(item)
        if not candidates:
            raise MarketDataError('No saved market snapshot was observed by the requested cutoff')
        if len({m['dataset_key'] for m in candidates}) != 1:
            raise MarketDataError('Ambiguous market series; specify provider, feed and adjustment basis')
        newest = max(utc_datetime(m['created_at']) for m in candidates)
        selected = [m for m in candidates if utc_datetime(m['created_at']) == newest]
        if len(selected) != 1:
            raise MarketDataError('Ambiguous market snapshot completion order')
        manifest = selected[0]
        data = self.load(manifest)
        result = deepcopy(data)
        for name, identity in [('bars', 'date'), ('actions', 'id')]:
            eligible = [r for r in data[name] if utc_datetime(r['observed_at']) <= at and
                        (not r.get('published_at') or utc_datetime(r['published_at']) <= at)]
            result[name] = _latest(eligible, identity)
        result['quality']['missing_sessions'] = sorted({s['date'] for s in result['calendar']['sessions']
                                                     if result['coverage']['start'] <= s['date'] <= result['coverage']['end']}
                                                    - {r['date'] for r in result['bars']})
        result['quality']['actions_complete'] = coverage_covers(result['action_coverage'], start=result['coverage']['start'], end=result['coverage']['end'], as_of=at.isoformat())
        result['quality']['zero_volume_sessions'] = [r['date'] for r in result['bars'] if r['volume'] == '0']
        result['quality']['zero_volume_price_movement_sessions'] = [r['date'] for r in result['bars'] if r['volume'] == '0' and r['high'] != r['low']]
        result['quality']['unknown_cash_currency_events'] = sum(r['kind'] == 'dividend' and r.get('synthetic') is False and r.get('cash_currency') is None for r in result['actions'])
        return {**result, 'as_of': at.isoformat(), 'availability_policy': 'observed_by_cutoff',
                'snapshot': manifest, 'stored_bar_versions': len(data['bars']),
                'stored_action_versions': len(data['actions'])}


def collect_market(source, store, *, refresh=False, overlap_days=7, cache_hours=24,
                   requests_per_second=2, retries=2, max_requests=100, max_cooldown=60,
                   clock=time.monotonic, sleep=time.sleep):
    """Collect a bounded acquired or synthetic source into versioned local evidence.

    The source supplies header, fingerprint and fetch_page(start,end,token)->bytes.
    An optional prepare(start,end,request_budget,previous) fetches independently scoped evidence and
    return the final header, after cache lookup and under the collection lock.
    Retries and pagination share one per-invocation request budget and limiter.
    Source adapters own HTTP policy; this interface grants no contractual rights.
    """
    for value in (cache_hours, requests_per_second, max_cooldown):
        if not math.isfinite(value) or value < 0:
            raise MarketDataError('Invalid collection limits')
    if requests_per_second <= 0 or requests_per_second > 10 or not 0 <= retries <= 5:
        raise MarketDataError('Invalid request rate/retry limit')
    if not isinstance(max_requests, int) or not 1 <= max_requests <= 1000 or not isinstance(overlap_days, int) or overlap_days < 0:
        raise MarketDataError('Invalid request budget/overlap')
    header = deepcopy(source.header)
    _no_secrets(header)
    if type(header.get('synthetic')) is not bool or (header['synthetic'] is False and not hasattr(source, 'prepare')):
        raise MarketDataError('Acquired sources require explicit labels and preparation/provenance')
    key = dataset_key(header)
    with store.collection_lock():
        try:
            previous = store.latest(key)
            old = store.load(previous) if previous else None
            now = utc_now()
            if previous and previous['normalizer_version'] == NORMALIZER_VERSION and previous['source_fingerprint'] == source.fingerprint and not refresh:
                age = (utc_datetime(now) - utc_datetime(previous['retrieved_at'])).total_seconds()
                if 0 <= age < cache_hours * 3600:
                    store.status(key, 'cached')
                    return {'status': 'cached', 'snapshot_id': previous['snapshot_id'], 'dataset_key': key, 'synthetic': header['synthetic']}
            start, end = header['coverage']['start'], header['coverage']['end']
            if old and getattr(source, 'requires_full_refresh', False):
                start, end = min(start, old['coverage']['start']), max(end, old['coverage']['end'])
            if old and not refresh and not getattr(source, 'requires_full_refresh', False):
                start = max(start, (date.fromisoformat(old['coverage']['end']) - timedelta(days=overlap_days)).isoformat())
            if start > end:
                raise MarketDataError('Incremental range precedes current coverage; use refresh for historical reconciliation')
            if hasattr(source, 'prepare'):
                original_kind = header['synthetic']
                header = deepcopy(source.prepare(start=start, end=end, request_budget=max_requests, previous=None if refresh else old))
                _no_secrets(header)
                if (header.get('synthetic') is not original_kind or dataset_key(header) != key or
                        header.get('coverage') != {'start': start, 'end': end}):
                    raise MarketDataError('Prepared source changed its series identity or requested price window')
            else:
                header['coverage'] = {'start': start, 'end': end}
                actions = header['action_coverage']
                action_start, action_end = max(start, actions['start']), min(end, actions['end'])
                header['action_coverage'] = {'start': action_start if action_start <= action_end else start,
                                             'end': action_end if action_start <= action_end else end,
                                             'complete': actions['complete'] if action_start <= action_end else False}
            pages, sources, seen = [], [], set()
            token, calls, last = None, 0, None
            while True:
                for attempt in range(retries + 1):
                    if calls >= max_requests:
                        raise MarketDataError('Market request budget exhausted; snapshot not published')
                    if last is not None:
                        delay = 1 / requests_per_second - (clock() - last)
                        if delay > 0:
                            sleep(delay)
                    last = clock()
                    calls += 1
                    try:
                        body = source.fetch_page(start=start, end=end, token=token)
                        break
                    except RetryableSourceError as exc:
                        wait = exc.retry_after
                        if not isinstance(wait, (int, float)) or not math.isfinite(wait) or wait < 0 or wait > max_cooldown or attempt == retries:
                            raise MarketDataError('Source cooldown/retry limit exceeded') from None
                        sleep(max(wait, 2 ** attempt))
                    except Exception:
                        raise MarketDataError('Source request failed; sensitive request details suppressed') from None
                if len(body) > 16 * 1024 * 1024:
                    raise MarketDataError('Source page exceeds 16 MiB')
                page = decode(body)
                _no_secrets(page)
                ref = store._save(body)
                ref.update(retrieved_at=utc_now(), page_index=len(pages), request_start=start, request_end=end)
                sources.append(ref)
                pages.append(page)
                token = page.get('next_page_token')
                if token is None:
                    break
                if not isinstance(token, str) or not token or token in seen:
                    raise MarketDataError('Invalid or repeated pagination token')
                seen.add(token)
            observed = utc_now()
            evidence_times = [ref['retrieved_at'] for ref in sources]
            if header.get('evidence_observed_through') is not None:
                evidence_times.append(header['evidence_observed_through'])
            if any(utc_datetime(at) > utc_datetime(observed) for at in evidence_times):
                raise MarketDataError('Source evidence receipt is later than the collection observation time')
            normalize = normalize_bundle if header['synthetic'] else normalize_live_bundle
            data = normalize({**header, 'pages': pages}, observed_at=observed)
            if 'source_metadata' in header:
                data['source_metadata'] = deepcopy(header['source_metadata'])
            # Each observation points to its original raw page, even after merges.
            for name in ('bars', 'actions'):
                for row in data[name]:
                    row['source'] = sources[row['source_page']]
                    row['observation_id'] = hashlib.sha256(json.dumps([row['record_id'], row['observed_at'], row['source']['sha256']]).encode()).hexdigest()
            header_ref = store._save(json.dumps(header, sort_keys=True, allow_nan=False).encode())
            header_ref.update(retrieved_at=observed, kind='header')
            sources.append(header_ref)
            current_coverage = _seal_coverage({**data['action_coverage'], 'sources': deepcopy(sources)})
            invalidated_dates = []
            if old:
                old_sessions = {s['date']: s for s in old['calendar']['sessions']}
                current_sessions = {s['date']: s for s in data['calendar']['sessions']}
                if any(day not in current_sessions or current_sessions[day] != session
                       for day, session in old_sessions.items() if data['calendar']['start'] <= day <= data['calendar']['end']):
                    raise MarketDataError('Calendar revision requires explicit reconciliation; prior snapshot retained')
                # Retractions cannot safely be guessed from omitted records.
                old_actions = _latest(old['actions'], 'id')
                incoming = {r['id']: r for r in data['actions']}
                invalidated_dates = [r['ex_date'] for r in old_actions if r['id'] in incoming and incoming[r['id']]['record_id'] != r['record_id']]
                present_actions = {r['id'] for r in data['actions']}
                process = header.get('source_metadata', {}).get('action_process_coverage')
                if process:
                    data['quality']['unconfirmed_refresh_actions'] = sorted(r['id'] for r in old_actions
                        if r.get('process_date') and process['start'] <= r['process_date'] <= process['end'] and r['id'] not in present_actions)
                if header['action_coverage']['complete'] and any(
                        header['action_coverage']['start'] <= r['ex_date'] <= header['action_coverage']['end'] and r['id'] not in present_actions
                        for r in old_actions):
                    raise MarketDataError('Corporate-action disappearance requires explicit reconciliation')
                prior_dates = {r['date'] for r in old['bars']}
                if getattr(source, 'requires_full_refresh', False) and prior_dates - {r['date'] for r in data['bars']}:
                    raise MarketDataError('Adjusted refresh omitted existing observations; incompatible vintages cannot be merged')
                data['quality']['unconfirmed_refresh_sessions'] = sorted(prior_dates & set(data['quality']['missing_sessions']))
                for name in ('bars', 'actions'):
                    identity = 'date' if name == 'bars' else 'id'
                    latest_known = {r[identity]: r for r in _latest(old[name], identity)}
                    # Deduplicate only unchanged consecutive observations. A->B->A
                    # is a new occurrence of A, not a reason to resurrect B.
                    new = [r for r in data[name] if r[identity] not in latest_known or latest_known[r[identity]]['record_id'] != r['record_id']]
                    data[name] = sorted(old[name] + new, key=lambda r: (r['observed_at'], r['record_id']))
                sessions = {s['date']: s for s in old['calendar']['sessions']}
                sessions.update({s['date']: s for s in data['calendar']['sessions']})
                data['calendar']['sessions'] = [sessions[k] for k in sorted(sessions)]
                data['calendar']['start'] = min(old['calendar']['start'], data['calendar']['start'])
                data['calendar']['end'] = max(old['calendar']['end'], data['calendar']['end'])
                data['coverage'] = {'start': min(old['coverage']['start'], start), 'end': max(old['coverage']['end'], end)}
                # Never extend a completeness assertion just because bar history grew.
                sources = old['sources'] + sources if 'sources' in old else previous['sources'] + sources
            data['action_coverage'] = merge_action_coverage(old['action_coverage'] if old else None, current_coverage,
                                                         observed_at=observed, invalidated_dates=invalidated_dates)
            data['quality']['actions_complete'] = coverage_covers(data['action_coverage'], start=data['coverage']['start'], end=data['coverage']['end'], as_of=observed)
            manifest = store.publish(key, data, sources, retrieved_at=observed, fingerprint=source.fingerprint)
            store.status(key, 'success')
            return {'status': 'success', 'snapshot_id': manifest['snapshot_id'], 'dataset_key': key,
                    'request_start': start, 'request_end': end, 'requests': calls, 'synthetic': header['synthetic'],
                    'quality': data['quality']}
        except Exception:
            store.status(key, 'failed')
            raise
