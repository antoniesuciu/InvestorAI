"""Pure daily market-data validation over explicitly labeled source evidence.

No provider/network/storage operations occur here. Calendar sessions and action
coverage are evidence supplied by the source, never inferred from weekdays.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class MarketDataError(ValueError):
    """Invalid, unavailable or financially incompatible market evidence."""


def _date(value):
    if not isinstance(value, str):
        raise MarketDataError('Expected an ISO calendar date')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise MarketDataError('Invalid ISO calendar date') from exc
    if parsed.isoformat() != value:
        raise MarketDataError('Dates must use YYYY-MM-DD')
    return parsed


def _time(value):
    if not isinstance(value, str):
        raise MarketDataError('Expected a timezone-aware timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise MarketDataError('Invalid timestamp') from exc
    if parsed.tzinfo is None:
        raise MarketDataError('Timestamp requires a timezone')
    return parsed.astimezone(timezone.utc)


def _number(value, *, positive=False, integer=False):
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise MarketDataError('Financial values must be decimal strings or integers')
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise MarketDataError('Invalid financial number') from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise MarketDataError('Expected a finite nonnegative financial number')
    if integer and number != number.to_integral_value():
        raise MarketDataError('Volume must be an integer')
    text = format(number, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _range(value):
    if not isinstance(value, dict):
        raise MarketDataError('Missing coverage interval')
    start, end = _date(value['start']), _date(value['end'])
    if start > end:
        raise MarketDataError('Coverage interval is reversed')
    return {'start': start.isoformat(), 'end': end.isoformat()}


def _identity(value, pattern, label):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise MarketDataError('Invalid ' + label)
    return value


def _stamp(row, context, observed, page):
    row.update(context)
    row['record_id'] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    row.update(observed_at=observed, source_page=page)
    return row


_COVERAGE_CONTEXT = ('instrument_id', 'provider', 'feed', 'currency', 'adjustment', 'adjustment_as_of', 'synthetic')


def _seal_coverage(proof):
    proof = deepcopy(proof)
    proof.pop('evidence_id', None)
    proof['evidence_id'] = hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()
    return proof


def coverage_windows(proof, as_of):
    """Verify scoped completeness evidence; return its independently observed windows.

    Fingerprints verify integrity, not source truth or an external completeness
    certification. Trimmed windows retain their original source claim unchanged.
    """
    try:
        if _seal_coverage(proof)['evidence_id'] != proof.get('evidence_id'):
            raise MarketDataError('Missing or changed action completeness evidence')
        if _time(proof['observed_at']) > _time(as_of):
            raise MarketDataError('Action completeness evidence was not available by the cutoff')
        _range(proof)
        if not isinstance(proof.get('event_ids'), list):
            raise MarketDataError('Missing action ledger evidence')
        if 'windows' not in proof:
            return [{'start': proof['start'], 'end': proof['end'], 'evidence': deepcopy(proof)}] if proof['complete'] is True else []
        if not isinstance(proof['windows'], list):
            raise MarketDataError('Invalid complete-window evidence')
        result = []
        for window in proof['windows']:
            leaf = window['evidence']
            _range(window)
            if ('windows' in leaf or leaf['complete'] is not True or
                    any(leaf[key] != proof[key] for key in _COVERAGE_CONTEXT) or
                    window['start'] < leaf['start'] or window['end'] > leaf['end'] or
                    window['start'] < proof['start'] or window['end'] > proof['end'] or
                    not set(leaf['event_ids']) <= set(proof['event_ids'])):
                raise MarketDataError('Incompatible action-window evidence')
            coverage_windows(leaf, proof['observed_at'])
            result.append(deepcopy(window))
        return result
    except (KeyError, TypeError) as exc:
        raise MarketDataError('Malformed action-window evidence') from exc


def _windows_cover(windows, start, end):
    boundary = _date(start)
    finish = _date(end)
    for window in sorted(windows, key=lambda w: (w['start'], w['end'])):
        left, right = _date(window['start']), _date(window['end'])
        if left > boundary:
            return False
        if right >= finish:
            return True
        if right >= boundary:
            boundary = right + timedelta(days=1)
    return False


def coverage_covers(proof, *, start, end, as_of):
    return _windows_cover(coverage_windows(proof, as_of), start, end)


def merge_action_coverage(previous, current, *, observed_at, invalidated_dates=()):
    """Union independently evidenced windows without bridging unverified gaps.

    An incomplete new request invalidates its overlap in the current view. Earlier
    snapshots remain immutable. Known incomplete revisions also invalidate their
    previous effective dates, even if moved outside the new request interval.
    """
    current_windows = coverage_windows(current, observed_at)
    prior_windows = coverage_windows(previous, observed_at) if previous else []
    if previous and any(previous[k] != current[k] for k in _COVERAGE_CONTEXT):
        raise MarketDataError('Cannot combine different action-evidence series')
    if not current['complete']:
        exclusions = [(current['start'], current['end']), *((day, day) for day in invalidated_dates)]
        for left, right in exclusions:
            retained = []
            for window in prior_windows:
                if window['end'] < left or window['start'] > right:
                    retained.append(window)
                    continue
                if window['start'] < left:
                    retained.append({**window, 'end': (_date(left) - timedelta(days=1)).isoformat()})
                if window['end'] > right:
                    retained.append({**window, 'start': (_date(right) + timedelta(days=1)).isoformat()})
            prior_windows = retained
    windows = prior_windows + current_windows
    proof = {k: deepcopy(v) for k, v in current.items() if k not in {'evidence_id', 'windows'}}
    proof['observed_at'] = _time(observed_at).isoformat()
    proof['windows'] = sorted(windows, key=lambda w: (w['start'], w['end'], w['evidence']['evidence_id']))
    proof['start'] = min((w['start'] for w in windows), default=current['start'])
    proof['end'] = max((w['end'] for w in windows), default=current['end'])
    proof['complete'] = _windows_cover(windows, proof['start'], proof['end'])
    proof['event_ids'] = sorted(set(current['event_ids']).union(*(set(w['evidence']['event_ids']) for w in windows)))
    return _seal_coverage(proof)


def normalize_bundle(bundle: dict, *, observed_at: str) -> dict:
    """Validate one fully fetched synthetic bundle, returning canonical JSON data.

    Volume is an integer-valued decimal string. Missing sessions are diagnostics;
    contradictory duplicates and bars outside the evidenced calendar are errors.
    Original source bytes and their integrity are the storage adapter's concern.
    """
    try:
        if not isinstance(bundle, dict) or bundle.get('synthetic') is not True:
            raise MarketDataError('Only clearly labeled synthetic offline input is enabled here')
        return _normalize(bundle, observed_at)
    except (KeyError, TypeError, ZoneInfoNotFoundError) as exc:
        raise MarketDataError('Missing or malformed market-data metadata') from exc


def normalize_live_bundle(bundle: dict, *, observed_at: str) -> dict:
    """Validate acquired real evidence; this is not a contractual permission grant."""
    try:
        if not isinstance(bundle, dict) or bundle.get('synthetic') is not False:
            raise MarketDataError('Live normalization requires an explicit real-data label')
        return _normalize(bundle, observed_at)
    except (KeyError, TypeError, ZoneInfoNotFoundError) as exc:
        raise MarketDataError('Missing or malformed live market metadata') from exc


def _normalize(bundle, observed_at):
    if not isinstance(bundle, dict) or type(bundle.get('schema_version')) is not int or bundle['schema_version'] != 1:
        raise MarketDataError('Expected market bundle schema_version=1')
    if type(bundle.get('synthetic')) is not bool:
        raise MarketDataError('A synthetic/real label is required')
    observed = _time(observed_at)
    observed_text = observed.isoformat()
    provider = _identity(bundle['provider'], r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', 'provider')
    feed = _identity(bundle['feed'], r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', 'feed')
    instrument = deepcopy(bundle['instrument'])
    for key, pattern in [('id', r'[A-Z0-9][A-Z0-9_.-]{0,63}'), ('symbol', r'[A-Z0-9][A-Z0-9.-]{0,19}'),
                         ('mic', r'[A-Z]{4}'), ('currency', r'[A-Z]{3}')]:
        _identity(instrument[key], pattern, 'instrument ' + key)
    if instrument.get('cik') is not None:
        _identity(instrument['cik'], r'[0-9]{10}', 'CIK')
        if int(instrument['cik']) == 0:
            raise MarketDataError('CIK cannot be zero')
    zone = ZoneInfo(instrument['timezone'])
    adjustment = bundle['adjustment']
    if adjustment not in {'raw', 'split', 'dividend', 'all'}:
        raise MarketDataError('Unknown price adjustment')
    basis = bundle.get('adjustment_as_of')
    if adjustment == 'raw':
        if basis is not None:
            raise MarketDataError('Raw prices cannot have an adjustment anchor')
    elif basis is None or _date(basis) > observed.astimezone(zone).date():
        raise MarketDataError('Adjusted prices require an observed adjustment anchor')
    if not bundle['synthetic'] and adjustment != 'raw' and bundle.get('source_metadata', {}).get('adjustment_anchor_kind') == 'retrieval_day_provider_latest' and basis != observed.astimezone(zone).date().isoformat():
        raise MarketDataError('Adjustment observation crossed its acquisition date')
    coverage = _range(bundle['coverage'])
    action_coverage = _range(bundle['action_coverage'])
    if type(bundle['action_coverage'].get('complete')) is not bool:
        raise MarketDataError('Action completeness must be explicit')
    action_coverage['complete'] = bundle['action_coverage']['complete']
    action_coverage['observed_at'] = observed_text
    calendar = _range(bundle['calendar'])
    if calendar['start'] > coverage['start'] or calendar['end'] < coverage['end']:
        raise MarketDataError('Calendar does not cover requested price interval')
    sessions = {}
    for session in bundle['calendar']['sessions']:
        day = _date(session['date']).isoformat()
        opening, closing = _time(session['open_at']), _time(session['close_at'])
        if (day in sessions or not calendar['start'] <= day <= calendar['end'] or opening >= closing
                or opening.astimezone(zone).date().isoformat() != day
                or closing.astimezone(zone).date().isoformat() != day):
            raise MarketDataError('Conflicting or invalid calendar session')
        sessions[day] = {'date': day, 'open_at': opening.isoformat(), 'close_at': closing.isoformat()}
    calendar.update(sessions=[sessions[day] for day in sorted(sessions)], observed_at=observed_text)
    context = {'provider': provider, 'feed': feed, 'instrument_id': instrument['id'],
               'currency': instrument['currency'], 'adjustment': adjustment, 'adjustment_as_of': basis,
               'synthetic': bundle['synthetic']}
    if not bundle['synthetic'] and adjustment != 'raw' and bundle.get('source_metadata', {}).get('adjustment_vintage') is not None:
        context['adjustment_vintage'] = _identity(bundle.get('source_metadata', {}).get('adjustment_vintage'), r'[a-f0-9]{64}', 'adjustment acquisition vintage')
    # Completeness is scoped evidence, not a transferable boolean. Bind it to
    # the exact source/instrument/representation and its observation time.
    action_coverage.update(context)
    bars, actions = {}, {}
    duplicate_bars = duplicate_actions = 0
    pages = bundle['pages']
    if not isinstance(pages, list) or not pages:
        raise MarketDataError('A complete paginated bundle is required')
    for page_index, page in enumerate(pages):
        for value in page['bars']:
            day = _date(value['date']).isoformat()
            if not coverage['start'] <= day <= coverage['end'] or day not in sessions:
                raise MarketDataError('Bar outside coverage or evidenced trading sessions')
            if _time(sessions[day]['close_at']) > observed:
                raise MarketDataError('Daily bar session has not closed by observation time')
            if basis is not None and day > basis:
                raise MarketDataError('Bar is later than its adjustment anchor')
            row = {'date': day, **{key: _number(value[key], positive=True) for key in ('open', 'high', 'low', 'close')},
                   'volume': _number(value['volume'], integer=True)}
            if not Decimal(row['low']) <= min(Decimal(row['open']), Decimal(row['close'])) <= max(Decimal(row['open']), Decimal(row['close'])) <= Decimal(row['high']):
                raise MarketDataError('OHLC values are inconsistent')
            if value.get('published_at') is not None:
                published = _time(value['published_at'])
                if published > observed or published < _time(sessions[day]['close_at']):
                    raise MarketDataError('Daily bar publication conflicts with observation/session time')
                row['published_at'] = published.isoformat()
            row = _stamp(row, context, observed_text, page_index)
            if day in bars:
                if bars[day]['record_id'] != row['record_id']:
                    raise MarketDataError('Conflicting duplicate daily bars')
                duplicate_bars += 1
            else:
                bars[day] = row
        for value in page['actions']:
            action_id = _identity(value['id'], r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}', 'action identifier')
            ex_date = _date(value['ex_date']).isoformat()
            if not action_coverage['start'] <= ex_date <= action_coverage['end']:
                raise MarketDataError('Action lies outside its coverage')
            row = {'id': action_id, 'kind': value['kind'], 'ex_date': ex_date}
            if row['kind'] == 'split':
                row['ratio'] = _number(value['ratio'], positive=True)
            elif row['kind'] == 'dividend':
                row['amount'] = _number(value['amount'])
                if bundle['synthetic'] and value.get('currency') != instrument['currency']:
                    raise MarketDataError('Dividend currency does not match the instrument')
                if not bundle['synthetic']:
                    currency = value.get('currency') or None
                    if currency is not None:
                        _identity(currency, r'[A-Z]{3}', 'dividend cash currency')
                    row.update(cash_currency=currency, currency_status='reported' if currency else 'unknown')
            else:
                raise MarketDataError('Unsupported corporate action; no inference is permitted')
            for key in ('declared_at', 'published_at'):
                if value.get(key) is not None:
                    timestamp = _time(value[key])
                    if timestamp > observed:
                        raise MarketDataError('Action evidence is unavailable at observation time')
                    row[key] = timestamp.isoformat()
            if value.get('payment_date') is not None:
                row['payment_date'] = _date(value['payment_date']).isoformat()
            if not bundle['synthetic']:
                for key in ('process_date', 'record_date'):
                    if value.get(key) is not None:
                        row[key] = _date(value[key]).isoformat()
                for key in ('new_rate', 'old_rate'):
                    if value.get(key) is not None:
                        row[key] = _number(value[key], positive=True)
                if value.get('provider_type'):
                    row['provider_type'] = value['provider_type']
            row = _stamp(row, context, observed_text, page_index)
            if action_id in actions:
                if actions[action_id]['record_id'] != row['record_id']:
                    raise MarketDataError('Conflicting duplicate corporate actions')
                duplicate_actions += 1
            else:
                actions[action_id] = row
    expected = {day for day in sessions if coverage['start'] <= day <= coverage['end']}
    action_coverage['event_ids'] = sorted(actions)
    action_coverage['evidence_id'] = hashlib.sha256(json.dumps(action_coverage, sort_keys=True).encode()).hexdigest()
    return {'schema_version': 1, 'synthetic': bundle['synthetic'], 'instrument': instrument, 'provider': provider, 'feed': feed,
            'adjustment': adjustment, 'adjustment_as_of': basis, 'observed_at': observed_text,
            'coverage': coverage, 'action_coverage': action_coverage, 'calendar': calendar,
            'bars': [bars[day] for day in sorted(bars)], 'actions': [actions[key] for key in sorted(actions)],
            'quality': {'missing_sessions': sorted(expected - bars.keys()), 'duplicate_bars': duplicate_bars,
                        'duplicate_actions': duplicate_actions, 'zero_volume_sessions': sorted(day for day, row in bars.items() if Decimal(row['volume']) == 0),
                        'zero_volume_price_movement_sessions': sorted(day for day, row in bars.items() if Decimal(row['volume']) == 0 and row['high'] != row['low']),
                        'actions_complete': action_coverage['complete']}}


def split_price_return(bars: list[dict], actions: list[dict], *, start: str, end: str,
                       as_of: str, action_coverage: dict) -> dict:
    """Split-corrected price return, without dividends or a total-return claim.

    Only raw prices are accepted. The source's complete action-coverage assertion
    and every selected record must already have been observed by the cutoff.
    A split ratio is new shares per old share (including ratios below one).
    """
    first, last, cutoff = _date(start), _date(end), _time(as_of)
    if first >= last:
        raise MarketDataError('Return endpoints must be strictly ordered')
    if not coverage_covers(action_coverage, start=start, end=end, as_of=as_of):
        raise MarketDataError('Complete action evidence was not available by the cutoff')
    eligible = [row for row in bars if row['date'] in {start, end} and _time(row['observed_at']) <= cutoff]
    endpoints = []
    for day in (start, end):
        selected = [row for row in eligible if row['date'] == day]
        latest = max((_time(r['observed_at']) for r in selected), default=None)
        selected = [row for row in selected if _time(row['observed_at']) == latest]
        if not selected or len({r['record_id'] for r in selected}) != 1:
            raise MarketDataError('Missing or conflicting return endpoint evidence')
        endpoints.append(selected[0])
    identity = ('instrument_id', 'provider', 'feed', 'currency', 'adjustment', 'adjustment_as_of', 'synthetic')
    if endpoints[0]['adjustment'] != 'raw' or any(endpoints[0][key] != endpoints[1][key] for key in identity):
        raise MarketDataError('Return endpoints require compatible raw price evidence')
    if any(action_coverage.get(key) != endpoints[0][key] for key in identity):
        raise MarketDataError('Action completeness evidence does not match the price source/instrument')
    proof = {key: value for key, value in action_coverage.items() if key != 'evidence_id'}
    if hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest() != action_coverage.get('evidence_id'):
        raise MarketDataError('Missing or changed action completeness evidence')
    chosen = {}
    for row in actions:
        if _time(row['observed_at']) > cutoff:
            continue  # Future-observed revisions cannot alter the earlier view.
        if any(row[key] != endpoints[0][key] for key in identity):
            raise MarketDataError('Action and price evidence are incompatible')
        current = chosen.get(row['id'])
        if current is None or _time(row['observed_at']) > _time(current['observed_at']):
            chosen[row['id']] = row
        elif _time(row['observed_at']) == _time(current['observed_at']) and row['record_id'] != current['record_id']:
            raise MarketDataError('Conflicting action revisions')
    # The latest eligible event can move its ex-date outside the interval. Do
    # not let the superseded event remain in the calculation after that move.
    if not isinstance(action_coverage.get('event_ids'), list) or not set(action_coverage['event_ids']) <= chosen.keys():
        raise MarketDataError('The action ledger is missing events referenced by complete evidence')
    chosen = {key: row for key, row in chosen.items() if start < row['ex_date'] <= end}
    with localcontext() as context:
        context.prec = 34
        factor = Decimal(1)
        for row in chosen.values():
            if row['kind'] == 'split':
                factor *= Decimal(_number(row['ratio'], positive=True))
        result = Decimal(endpoints[1]['close']) * factor / Decimal(endpoints[0]['close']) - 1
    return {'value': str(result), 'unit': 'fraction', 'classification': 'derived',
            'formula': 'end_raw_close * product(new_shares / old_shares) / start_raw_close - 1',
            'start': start, 'end': end, 'as_of': cutoff.isoformat(), 'split_factor': str(factor),
            'inputs': deepcopy(endpoints + sorted(chosen.values(), key=lambda r: r['id'])),
            'action_coverage': deepcopy(action_coverage),
            'limitations': ['Price return excludes dividends; it is not total return',
                            ('Synthetic offline evidence; no survivorship-free universe is established' if endpoints[0]['synthetic']
                             else 'No survivorship-free universe is established')]}


def adjusted_price_return(bars, *, start, end, as_of):
    """Price return on one provider split-adjustment vintage; not total return.

    No claim of a complete local action ledger follows from a provider's supplied
    split-adjusted prices. Dividend/all representations require separate methods.
    """
    if _date(start) >= _date(end):
        raise MarketDataError('Return endpoints must be ordered')
    cutoff = _time(as_of)
    rows = []
    for day in (start, end):
        candidates = [r for r in bars if r['date'] == day and _time(r['observed_at']) <= cutoff and
                      (not r.get('published_at') or _time(r['published_at']) <= cutoff)]
        latest = max((_time(r['observed_at']) for r in candidates), default=None)
        selected = [r for r in candidates if _time(r['observed_at']) == latest]
        if not selected or len({r['record_id'] for r in selected}) != 1:
            raise MarketDataError('Missing or conflicting return endpoints')
        rows.append(selected[0])
    identity = ('instrument_id', 'provider', 'feed', 'currency', 'adjustment', 'adjustment_as_of', 'synthetic')
    if rows[0]['adjustment'] != 'split' or any(rows[0][k] != rows[1][k] for k in identity):
        raise MarketDataError('Compatible split-adjusted prices in one vintage required')
    if rows[0]['synthetic'] is False and (not rows[0].get('adjustment_vintage') or rows[0].get('adjustment_vintage') != rows[1].get('adjustment_vintage')):
        raise MarketDataError('Return endpoints require the same full adjustment acquisition vintage')
    if not rows[0]['adjustment_as_of']:
        raise MarketDataError('Adjustment vintage evidence is missing')
    with localcontext() as context:
        context.prec = 34
        value = Decimal(_number(rows[1]['close'], positive=True)) / Decimal(_number(rows[0]['close'], positive=True)) - 1
    return {'value': str(value), 'unit': 'fraction', 'classification': 'derived', 'formula': 'end_split_close / start_split_close - 1',
            'start': start, 'end': end, 'as_of': cutoff.isoformat(), 'inputs': deepcopy(rows),
            'limitations': ['Price return excludes dividends; it is not total return',
                            'Conditional on provider split adjustments; no complete action ledger or pre-collection availability is certified']}
