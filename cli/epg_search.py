"""Validate compact native guide candidates before private archive probing."""
from collections import deque
import importlib.util
import json
import math
from pathlib import Path
import time


TEXT_PROFILE = "archive-nfkc-casefold-words-u17-v1"
MAX_ROWS = 10000
MAX_BYTES = 16 * 1024 * 1024
INVALID = "EPG service returned invalid or incomplete search candidates; no playback was requested"


def _history_module():
    spec = importlib.util.spec_from_file_location('ott_compact_programme_search', Path(__file__).resolve().with_name('programme_search.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _text(value, limit, *, utf8=False):
    if not isinstance(value, str):
        return False
    try:
        return len(value.encode('utf-8' if utf8 else 'utf-16-le')) <= limit * (1 if utf8 else 2)
    except UnicodeError:
        return False


def _integer(value, minimum=0, maximum=9007199254740991):
    return type(value) is int and minimum <= value <= maximum


def _remaining(host, deadline):
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise host.Error("EPG search timed out; no playback was requested")
    return remaining


def _catalogue(host, snapshot):
    if (not isinstance(snapshot, dict) or not _text(snapshot.get('catalog'), 128, utf8=True)
            or not snapshot['catalog'].strip() or not isinstance(snapshot.get('channels'), list)
            or len(snapshot['channels']) > MAX_ROWS):
        raise host.Error("The player returned an invalid EPG catalogue; no playback was requested")
    by_id, wire, number = {}, [], 0
    archive = snapshot.get('archive') is not None
    for channel in snapshot['channels']:
        if (not isinstance(channel, dict) or any(not _text(channel.get(key), 512)
                for key in ('id', 'tvgId', 'tvgName', 'name'))
                or not channel['id'].strip() or channel['id'] in by_id
                or not _integer(channel.get('number'), 1) or channel['number'] <= number
                or not _integer(channel.get('shift'), -86400, 86400)
                or not _integer(channel.get('archiveHours', None if archive else 0), 0, 144)):
            raise host.Error("The player returned an invalid EPG catalogue; no playback was requested")
        number = channel['number']
        by_id[channel['id']] = channel
        row = {key: channel[key] for key in ('id', 'tvgId', 'tvgName', 'name', 'shift')}
        row['archiveHours'] = channel.get('archiveHours', 0)
        wire.append(row)
    return by_id, wire


def _batches(wire):
    batch, size = [], 2048
    for row in wire:
        length = len(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode()) + 1
        if batch and (len(batch) == 256 or size + length > 480 * 1024):
            yield batch
            batch, size = [], 2048
        batch.append(row)
        size += length
    if batch:
        yield batch


def _literal_key(value):
    # Match hosted current search, including its intentional dotless-i exception.
    return ''.join(char if char == '\u0131' else char.lower().upper() for char in value)


def _validate(host, history, response, batch, by_id, query, generation):
    now = time.time()
    if (not isinstance(response, dict) or type(response.get('version')) is not int
            or response['version'] != 1 or response.get('source') != 'epg-one'
            or not _text(response.get('generation'), 80, utf8=True) or not response['generation']
            or (generation is not None and response['generation'] != generation)
            or not _integer(response.get('fetchedAt'), 1)
            or not -60 <= now - response['fetchedAt'] / 1000 < 7200
            or response.get('stale') is not False or response.get('complete') is not True
            or not _integer(response.get('asOf'), 1) or abs(response['asOf'] - now) > 60
            or not _integer(response.get('validUntil'), 1)
            or not response['asOf'] < response['validUntil'] <= response['asOf'] + 7200
            or response['validUntil'] > response['fetchedAt'] / 1000 + 7200
            or type(response.get('checked')) is not int or response['checked'] != len(batch)
            or type(response.get('total')) is not int or response['total'] != len(batch)
            or response.get('textProfile') != TEXT_PROFILE
            or any(not isinstance(response.get(key), list) for key in ('current', 'archiveCurrent', 'archives'))):
        raise host.Error(INVALID)
    try:
        size = len(json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode())
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise host.Error(INVALID) from None
    if size > 2 * 1024 * 1024:
        raise host.Error(INVALID)
    allowed = {row['id'] for row in batch}
    count, as_of = 0, response['asOf']

    def row_value(row, channel, *, live, literal=False):
        nonlocal count
        count += 1
        if (count > MAX_ROWS or not isinstance(row, dict)
                or not _integer(row.get('start'), 1) or not _integer(row.get('end'), 1)
                or row['start'] >= row['end'] or not _text(row.get('title'), 16384) or not row['title']):
            raise host.Error(INVALID)
        legacy = {'time': row['start'], 'time_to': row['end'], 'name': row['title']}
        if live:
            if not row['start'] <= as_of < row['end']:
                raise host.Error(INVALID)
        elif (channel.get('archiveHours', 0) <= 0 or row['end'] > as_of
              or row['start'] < as_of - channel['archiveHours'] * 3600):
            raise host.Error(INVALID)
        if literal:
            if _literal_key(query) not in _literal_key(row['title']):
                raise host.Error(INVALID)
        else:
            try:
                clean = history.clean_rows([legacy])
            except history.SearchError:
                raise host.Error(INVALID) from None
            if clean != [legacy] or not history.matches(row['title'], query):
                raise host.Error(INVALID)
        return legacy

    def channel_value(row):
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] not in allowed:
            raise host.Error(INVALID)
        return by_id[row['id']]

    current, archive_current, chains = [], [], []
    last = 0
    for row in response['current']:
        channel = channel_value(row)
        if channel['number'] <= last:
            raise host.Error(INVALID)
        last = channel['number']
        current.append((channel, row_value(row, channel, live=True, literal=True)))
    previous = None
    for row in response['archiveCurrent']:
        channel = channel_value(row)
        legacy = row_value(row, channel, live=True)
        order = (channel['number'], legacy['time'], legacy['time_to'], legacy['name'])
        if previous is not None and order <= previous:
            raise host.Error(INVALID)
        previous = order
        archive_current.append((channel, legacy))
    previous = None
    seen = set()
    for value in response['archives']:
        channel = channel_value(value)
        if not isinstance(value.get('rows'), list) or not value['rows']:
            raise host.Error(INVALID)
        rows = [row_value(row, channel, live=False) for row in value['rows']]
        if any(left['time_to'] != right['time'] for left, right in zip(rows, rows[1:])):
            raise host.Error(INVALID)
        order = (channel['number'], rows[0]['time'], rows[0]['time_to'], rows[0]['name'])
        identity = (channel['id'], tuple((row['time'], row['time_to'], row['name']) for row in rows))
        if (previous is not None and order < previous) or identity in seen:
            raise host.Error(INVALID)
        seen.add(identity)
        previous = order
        chains.append((channel, rows))
    return {'current': current, 'archive_current': archive_current, 'chains': chains,
            'as_of': as_of, 'until': response['validUntil'], 'generation': response['generation'],
            'rows': count, 'bytes': size}


def run(host, client, device, settings, snapshot, query, deadline, progress=None,
        fast=False, generation=None, refresh=False):
    """Search one whole catalogue; only validated candidates reach media probes."""
    if not _text(query, 1024, utf8=True) or not query.strip():
        raise host.Error("Programme search must contain 1 to 1024 UTF-8 bytes")
    if generation is not None and (not _text(generation, 80, utf8=True) or not generation):
        raise host.Error(INVALID)
    by_id, wire = _catalogue(host, snapshot)
    if not wire:
        return {'as_of': time.time(), 'checked': 0, 'total': 0, 'partial': False, 'programs': []}, {}
    history = _history_module()
    epg = host.EpgClient(settings, _remaining(host, deadline), operation='search')
    split_count = 0

    def collect(expected):
        nonlocal split_count
        pending, checked = deque(_batches(wire)), 0
        result = {'current': [], 'archive_current': [], 'chains': [], 'rows': 0, 'bytes': 0,
                  'generation': expected, 'as_of': 0, 'until': float('inf')}
        while pending:
            batch = pending.popleft()
            epg.timeout = min(10, _remaining(host, deadline))
            if progress:
                progress('Searching indexed EPG channels', checked, len(wire))
            try:
                response = epg.search(batch, query, generation=result['generation'])
            except host.EpgHTTPError as error:
                if error.status != 504 or error.epg_code != 'EPG_TIMEOUT' or len(batch) < 2 or split_count >= 4:
                    raise
                middle = len(batch) // 2
                pending.appendleft(batch[middle:])
                pending.appendleft(batch[:middle])
                split_count += 1
                if progress:
                    progress('Splitting timed-out EPG batch', split_count, 4)
                continue
            _remaining(host, deadline)
            value = _validate(host, history, response, batch, by_id, query, result['generation'])
            result['generation'] = value['generation']
            result['as_of'] = max(result['as_of'], value['as_of'])
            result['until'] = min(result['until'], value['until'])
            result['rows'] += value['rows']
            result['bytes'] += value['bytes']
            if result['rows'] > MAX_ROWS or result['bytes'] > MAX_BYTES:
                raise host.Error("EPG search candidate limit exceeded; no playback was requested")
            for key in ('current', 'archive_current', 'chains'):
                result[key].extend(value[key])
            checked += len(batch)
            if progress:
                progress('Searching indexed EPG channels', checked, len(wire))
        return result

    def fresh_collect(expected):
        value = collect(expected)
        if value['until'] <= max(time.time(), value['as_of']):
            if progress:
                progress('Programme window changed; refreshing search')
            value = collect(value['generation'])
        if value['until'] <= max(time.time(), value['as_of']):
            raise host.Error("EPG programme window changed during search; no playback was requested")
        return value

    value = fresh_collect(generation)
    literal = [(channel, row) for channel, row in value['current'] if row['time'] <= value['as_of'] < row['time_to']]
    if literal:
        return {'as_of': value['as_of'], 'checked': len(wire), 'total': len(wire), 'partial': False,
                'programs': [{'channel': channel['name'], 'number': channel['number'], 'title': row['name'],
                              'start': row['time'], 'end': row['time_to']} for channel, row in literal]}, {
                    channel['number']: {'catalog': snapshot['catalog'], 'id': channel['id']} for channel, _ in literal}
    prepared = {'current': value['archive_current'], 'chains': value['chains'], 'until': value['until']}

    def refresh_current():
        nonlocal value
        _remaining(host, deadline)
        if time.time() >= value['until']:
            value = fresh_collect(value['generation'])
        now = time.time()
        return [(channel, row) for channel, row in value['archive_current'] if row['time'] <= now < row['time_to']]

    if progress:
        progress('Checking compact archive candidates', 0, len(prepared['chains']))
    try:
        return history.search_archives(client, device, settings, snapshot, query, refresh,
                                       catalog_expired=host.expired_epg_catalog, progress=progress,
                                       fast=fast, deadline=deadline, prepared=prepared,
                                       refresh_current=refresh_current)
    except history.SearchError as error:
        raise host.Error(str(error)) from None
