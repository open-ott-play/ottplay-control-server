"""Shared archive search for every player using the OTT-play control protocol.

Guide requests contain metadata only. Resolved media URLs live only in memory
between the authenticated controller and its local availability probe.
"""
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
from datetime import datetime
import hashlib
from http.client import HTTPException
import ipaddress
import json
import os
import queue
import secrets
import ssl
from pathlib import Path
import re
import tempfile
import threading
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, build_opener



def https_handler():
    """Retain platform verification; optionally require stronger OpenSSL keys."""
    context = ssl.create_default_context()
    context.minimum_version = max(context.minimum_version, ssl.TLSVersion.TLSv1_2)
    level = os.environ.get("OTT_TLS_SECURITY_LEVEL")
    if level is not None:
        if level not in ("2", "3", "4", "5"):
            raise ValueError("OTT_TLS_SECURITY_LEVEL must be 2, 3, 4 or 5")
        # Raise strength without widening a host-selected cipher allowlist.
        if int(level) > context.security_level:
            ciphers = ":".join(cipher["name"] for cipher in context.get_ciphers()
                               if cipher["protocol"] != "TLSv1.3")
            context.set_ciphers(ciphers + ":@SECLEVEL=" + level)
    return HTTPSHandler(context=context)


CACHE = Path(os.environ.get("OTT_CACHE", "~/.cache/ottplay-control")).expanduser()
GUIDE_TTL = 7200
ARCHIVE_TTL = 7 * 86400


class SearchError(Exception):
    pass


class EpgAvailabilityError(SearchError):
    """A guide endpoint failed before any candidate or playback was selected."""
    def __init__(self, message, *, status=None, transport=False):
        self.status, self.transport = status, transport
        super().__init__(message)


class GenerationChanged(SearchError):
    pass


class SearchTimeout(SearchError):
    pass


class ArchiveSourceError(SearchError):
    pass


class ArchiveTransientError(SearchError):
    pass


def _remaining(deadline=None, maximum=None, cancelled=None):
    if cancelled is not None and cancelled.is_set():
        raise SearchTimeout("Archive search timed out; no playback was requested")
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        raise SearchTimeout("Archive search timed out; no playback was requested")
    return maximum if remaining is None else remaining if maximum is None else min(maximum, remaining)


def _bounded_read(action, deadline):
    """Bound the caller even when a peer keeps an individual socket read alive."""
    if deadline is None:
        return action(None)
    cancelled, outcome = threading.Event(), queue.Queue(maxsize=1)
    def run():
        try:
            outcome.put((True, action(cancelled)))
        except Exception as exc:
            if cancelled.is_set() and isinstance(exc, HTTPError):
                exc.close()
            outcome.put((False, exc))
    _remaining(deadline)
    threading.Thread(target=run, name="ottplay-archive-http", daemon=True).start()
    try:
        ok, value = outcome.get(timeout=_remaining(deadline))
    except queue.Empty:
        cancelled.set()
        raise SearchTimeout("Archive search timed out; no playback was requested") from None
    if time.monotonic() >= deadline:
        cancelled.set()
        raise SearchTimeout("Archive search timed out; no playback was requested")
    if not ok:
        raise value
    return value


def _read_prefix(response, count, deadline=None, cancelled=None, initial=b""):
    data = bytearray(initial)
    read = getattr(response, "read1", response.read)
    while len(data) < count:
        _remaining(deadline, cancelled=cancelled)
        chunk = read(count - len(data))
        if not chunk:
            break
        data.extend(chunk)
    _remaining(deadline, cancelled=cancelled)
    return bytes(data)


def _progress(callback, stage, completed=None, total=None):
    if callback is not None:
        callback(stage, completed, total)


def text_key(value):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")))


def matches(title, query):
    terms = text_key(query).split()
    return bool(terms) and all(term in text_key(title) for term in terms)


def cache_path(kind, key, directory=None):
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    return (CACHE if directory is None else directory) / (kind + "-" + digest + ".json")


def cached(kind, key, ttl, refresh=False, directory=None):
    if refresh or ttl <= 0:
        return None
    try:
        value = json.loads(cache_path(kind, key, directory).read_text())
        if value["version"] == 1 and 0 <= time.time() - value["saved_at"] < ttl:
            return value["data"]
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return None


def remember(kind, key, data, directory=None):
    temporary = None
    directory = CACHE if directory is None else directory
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".cache-", dir=str(directory))
        with os.fdopen(fd, "w") as stream:
            json.dump({"version": 1, "saved_at": time.time(), "data": data}, stream)
        os.replace(temporary, cache_path(kind, key, directory))
    except OSError:
        pass
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def epg_request(base, path, payload=None, attempts=1, *, deadline=None):
    return _bounded_read(lambda cancelled: _epg_request(base, path, payload, attempts, deadline, cancelled), deadline)


def _epg_request(base, path, payload, attempts, deadline, cancelled):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = Request(base + path, data=body, headers={"Content-Type": "application/json", "User-Agent": "ottplay-cli/1.0"})
    handlers = [NoRedirect(), https_handler()]
    try:
        if ipaddress.ip_address(urlsplit(base).hostname).is_loopback:
            handlers.insert(0, ProxyHandler({}))
    except (TypeError, ValueError):
        pass
    try:
        for attempt in range(attempts):
            try:
                with build_opener(*handlers).open(
                        request, timeout=_remaining(deadline, 20, cancelled)) as response:
                    raw = _read_prefix(response, 16 * 1024 * 1024 + 1, deadline, cancelled)
                break
            except HTTPError as exc:
                exc.close()
                if attempt + 1 == attempts or exc.code not in (429, 500, 502, 503, 504):
                    raise
            except (OSError, HTTPException):
                if attempt + 1 == attempts:
                    raise
            delay = min(attempt + 1, _remaining(deadline, attempt + 1, cancelled))
            if cancelled is None:
                time.sleep(delay)
            elif cancelled.wait(delay):
                _remaining(deadline, cancelled=cancelled)
        _remaining(deadline, cancelled=cancelled)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError()
        value = json.loads(raw)
        if (not isinstance(value, dict) or value.get("version") != 1 or value.get("source") != "epg-one"
                or not isinstance(value.get("generation"), str) or not 1 <= len(value["generation"]) <= 256
                or value.get("stale") is not False):
            raise ValueError()
        return value
    except HTTPError as exc:
        exc.close()
        if exc.code == 409:
            raise GenerationChanged("EPG changed during the archive search") from None
        if exc.code in (429, 500, 502, 503, 504):
            raise EpgAvailabilityError(
                "EPG history request failed (HTTP %s); no playback was requested" % exc.code,
                status=exc.code) from None
        raise SearchError("EPG history request failed (HTTP %s); no playback was requested" % exc.code) from None
    except (OSError, HTTPException):
        raise EpgAvailabilityError(
            "Could not read fresh EPG history; no playback was requested", transport=True) from None
    except ValueError:
        raise SearchError("Could not read fresh EPG history; no playback was requested") from None


def clean_rows(rows):
    if not isinstance(rows, list) or len(rows) > 20000:
        raise SearchError("Invalid EPG history")
    clean = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("name"), str)
                or type(row.get("time")) is not int or type(row.get("time_to")) is not int
                or not 0 < row["time"] < row["time_to"]):
            raise SearchError("Invalid EPG history")
        name = " ".join("".join(c if not unicodedata.category(c).startswith("C") else " " for c in row["name"]).split())
        clean.add((row["time"], row["time_to"], name))
    return [{"time": start, "time_to": end, "name": name} for start, end, name in sorted(clean)]


def fresh(value, now, ttl=GUIDE_TTL):
    return (isinstance(value, dict) and type(value.get("until")) in (int, float)
            and now < value["until"] <= now + ttl)


def valid_match(value):
    return value is None or (isinstance(value, dict) and isinstance(value.get("channelId"), str)
            and 1 <= len(value["channelId"]) <= 512 and type(value.get("shift")) is int
            and -86400 <= value["shift"] <= 86400 and value["shift"] % 3600 == 0)


def valid_mapping(value, metadata, now, ttl=GUIDE_TTL):
    return (fresh(value, now, ttl) and isinstance(value.get("generation"), str)
            and 1 <= len(value["generation"]) <= 256 and isinstance(value.get("mappings"), dict)
            and all(row["id"] in value["mappings"] and valid_match(value["mappings"][row["id"]])
                    for row in metadata))


def valid_candidates(rows, channels, now):
    if not isinstance(rows, list) or not rows:
        return False
    by_id = {channel["id"]: channel for channel in channels}
    previous = (0, 0)
    for row in rows:
        if (not isinstance(row, dict) or row.get("mode") != "archive"
                or any(type(row.get(k)) is not int for k in ("number", "start", "end", "archive_hours", "consecutive"))
                or any(not isinstance(row.get(k), str) or not row[k].strip() for k in ("id", "channel", "title"))
                or row["id"] not in by_id):
            return False
        channel = by_id[row["id"]]
        order = (row["number"], row["start"])
        if (row["number"] != channel["number"] or row["channel"] != channel["name"]
                or row["archive_hours"] != channel["archiveHours"] or row["consecutive"] < 1
                or not 0 < row["archive_hours"] <= 144 or not previous < order
                or not now - row["archive_hours"] * 3600 <= row["start"] < row["end"] <= now):
            return False
        previous = order
    return True


def schedules(base, channels, refresh=False, cache=None, ttl=GUIDE_TTL, attempts=1, *, progress=None, deadline=None):
    read = cached if cache is None else cache.read
    write = remember if cache is None else cache.write
    lifetime = max(1, ttl)
    options = {}
    if attempts != 1:
        options["attempts"] = attempts
    if deadline is not None:
        options["deadline"] = deadline
    request = epg_request if not options else lambda *args: epg_request(*args, **options)
    metadata = [{k: c[k] for k in ("id", "name", "tvgId", "tvgName")} for c in channels]
    for attempt in range(2):
        try:
            _remaining(deadline)
            clock = time.time()
            key = [base, metadata]
            _progress(progress, "Reading cached channel mappings")
            mapping = read("mapping", key, ttl, refresh or bool(attempt))
            if not valid_mapping(mapping, metadata, clock, lifetime):
                generation, mappings = None, {}
                _progress(progress, "Matching archive channels", 0, len(metadata))
                for start in range(0, len(metadata), 100):
                    _remaining(deadline)
                    batch = metadata[start:start + 100]
                    response = request(base, "/match", {"version": 1, "source": "epg-one", "channels": batch})
                    if generation is not None and generation != response["generation"]:
                        raise GenerationChanged("EPG changed during channel matching")
                    generation = response["generation"]
                    if not isinstance(response.get("mappings"), dict):
                        raise SearchError("Invalid EPG channel mapping")
                    mappings.update({row["id"]: response["mappings"].get(row["id"]) for row in batch})
                    _progress(progress, "Matching archive channels", start + len(batch), len(metadata))
                mapping = {"generation": generation, "mappings": mappings, "until": clock + lifetime}
                if not valid_mapping(mapping, metadata, clock, lifetime):
                    raise SearchError("Invalid EPG channel mapping")
                write("mapping", key, mapping)
            else:
                _progress(progress, "Using cached channel mappings", len(metadata), len(metadata))
            ids = {}
            for channel in channels:
                match = mapping["mappings"].get(channel["id"])
                if match is None:
                    continue
                ids[channel["id"]] = (match["channelId"], match["shift"])

            def fetch(key):
                _remaining(deadline)
                cache_key = [base, mapping["generation"], key]
                saved = read("guide", cache_key, ttl, refresh)
                clock = time.time()
                rows = None
                if fresh(saved, clock, lifetime):
                    try:
                        rows = clean_rows(saved.get("rows"))
                    except SearchError:
                        pass
                if rows is None:
                    response = request(base, "/programmes?" + urlencode({
                        "channelId": key[0], "shift": key[1], "hours": 168, "generation": mapping["generation"]}))
                    if response["generation"] != mapping["generation"]:
                        raise GenerationChanged("EPG history changed")
                    rows = clean_rows(response.get("rows"))
                    saved = {"rows": rows, "until": clock + lifetime}
                    _remaining(deadline)
                    write("guide", cache_key, saved)
                return key, rows, saved["until"]

            guides, until = {}, mapping["until"]
            keys = set(ids.values())
            _progress(progress, "Loading programme history", 0, len(keys))
            pool, pending, remaining_keys = ThreadPoolExecutor(max_workers=4), set(), iter(keys)
            try:
                def submit_next():
                    key = next(remaining_keys, None)
                    if key is not None:
                        _remaining(deadline)
                        pending.add(pool.submit(fetch, key))
                for _ in range(4):
                    submit_next()
                while pending:
                    done, _ = wait(pending, timeout=_remaining(deadline), return_when=FIRST_COMPLETED)
                    _remaining(deadline)
                    for task in done:
                        pending.remove(task)
                        key, rows, expires = task.result()
                        guides[key] = rows
                        until = min(until, expires)
                        _progress(progress, "Loading programme history", len(guides), len(keys))
                        submit_next()
            finally:
                for task in pending:
                    task.cancel()
                # Deadline-aware reads release workers without waiting for unrelated
                # queued histories; no additional work is scheduled after expiry.
                pool.shutdown(wait=deadline is None, cancel_futures=True)
            return [(channel, [{**row, "time": row["time"] + channel["shift"],
                                "time_to": row["time_to"] + channel["shift"]} for row in guides[ids[channel["id"]]]])
                    for channel in channels if channel["id"] in ids], until
        except GenerationChanged:
            if attempt:
                raise
            _progress(progress, "Guide changed; refreshing archive schedules")
    raise SearchError("EPG changed repeatedly; retry the search")


def archive_chains(rows, query, now, hours):
    cutoff = now - min(144, max(0, hours)) * 3600
    result, chain, previous = [], [], None
    for row in rows:
        eligible = hours > 0 and cutoff <= row["time"] < row["time_to"] <= now and matches(row["name"], query)
        adjacent = previous is not None and previous["time_to"] == row["time"]
        if chain and (not eligible or not adjacent):
            result.append(chain)
            chain = []
        if eligible:
            chain.append(row)
        previous = row
    if chain:
        result.append(chain)
    return result


def _validate_guide(guide):
    if not isinstance(guide, list) or len(guide) > 10000:
        raise SearchError("Invalid archive candidate catalogue")
    result, ids, numbers = [], set(), set()
    for item in guide:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise SearchError("Invalid archive candidate catalogue")
        channel, rows = item
        if (not isinstance(channel, dict) or not isinstance(channel.get("id"), str)
                or not channel["id"] or channel["id"] in ids
                or not isinstance(channel.get("name"), str) or not channel["name"]
                or type(channel.get("number")) is not int or channel["number"] < 1
                or channel["number"] in numbers or type(channel.get("archiveHours")) is not int
                or not 0 <= channel["archiveHours"] <= 144):
            raise SearchError("Invalid archive candidate catalogue")
        ids.add(channel["id"])
        numbers.add(channel["number"])
        result.append((channel, clean_rows(rows)))
    return result


def _select_chains(current, chains, verify, *, fast=False, deadline=None):
    _remaining(deadline)
    selected = current(time.time())
    if not selected:
        candidates = list(chains)
        if fast:
            # Randomize complete chains, not episodes or network completion order.
            # Under stable availability, the first playable chain is uniform.
            secrets.SystemRandom().shuffle(candidates)
        for channel, chain in candidates:
            _remaining(deadline)
            for index, row in enumerate(chain):
                _remaining(deadline)
                if row["time"] < time.time() - min(144, channel["archiveHours"]) * 3600:
                    continue
                if verify(channel, row):
                    _remaining(deadline)
                    if row["time"] < time.time() - min(144, channel["archiveHours"]) * 3600:
                        continue
                    selected.append((channel, row, "archive", len(chain) - index))
                    break
            if fast and selected:
                break
    # Availability probes can cross a programme boundary or retention cutoff.
    _remaining(deadline)
    now = time.time()
    live = current(now)
    _remaining(deadline)
    now = time.time()
    return live or [item for item in selected if item[2] == "archive"
                   and item[1]["time"] >= now - min(144, item[0]["archiveHours"]) * 3600]


def _current_from_schedules(guide, query, now):
    return [(channel, row, "live", 1) for channel, rows in guide for row in rows
            if row["time"] <= now < row["time_to"] and matches(row["name"], query)]


def select_from_schedules(guide, query, verify, *, fast=False, deadline=None):
    """Select live broadcasts or each earliest playable contiguous archive chain.

    Sources supply only the private archive resolver/probe. Matching, retention,
    adjacency and the late live recheck are shared by local and remote players.
    """
    if fast:
        guide = _validate_guide(guide)
    def current(now):
        return _current_from_schedules(guide, query, now)

    now = time.time()
    chains = [(channel, chain) for channel, rows in guide
              for chain in archive_chains(rows, query, now, channel["archiveHours"])]
    selected = _select_chains(current, chains, verify, fast=fast, deadline=deadline)
    now = time.time()
    boundaries = [stamp for _, rows in guide for row in rows if matches(row["name"], query)
                  for stamp in (row["time"], row["time_to"]) if stamp > now]
    return selected, min(boundaries, default=float("inf"))


def _prepared_candidates(prepared, channels, query):
    """Validate the whole compact candidate universe before probing any media."""
    if (not isinstance(prepared, dict) or set(prepared) != {"current", "chains", "until"}
            or type(prepared["until"]) not in (int, float)
            or not time.time() < prepared["until"] <= time.time() + GUIDE_TTL
            or not isinstance(prepared["current"], list) or not isinstance(prepared["chains"], list)
            or len(prepared["current"]) + len(prepared["chains"]) > 10000):
        raise SearchError("Invalid compact archive candidates")
    by_id = {channel["id"]: channel for channel in channels}
    def channel_of(value):
        if (not isinstance(value, dict) or not isinstance(value.get("id"), str)
                or value != by_id.get(value["id"])):
            raise SearchError("Compact archive candidates do not match the player catalogue")
        return value
    def pair(item):
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise SearchError("Invalid compact archive candidates")
        return channel_of(item[0]), item[1]
    current, current_ids = [], set()
    for item in prepared["current"]:
        channel, row = pair(item)
        rows = clean_rows([row])
        if rows != [row] or not matches(row["name"], query):
            raise SearchError("Invalid compact current programmes")
        identity = (channel["id"], row["time"], row["time_to"], row["name"])
        if identity in current_ids:
            raise SearchError("Duplicate compact current programme")
        current_ids.add(identity)
        current.append((channel, row))
    chains, identities, total = [], set(), len(current)
    for item in prepared["chains"]:
        channel, rows = pair(item)
        clean = clean_rows(rows)
        if not clean or clean != rows or not channel["archiveHours"]:
            raise SearchError("Invalid compact archive chain")
        if (any(not matches(row["name"], query) for row in clean)
                or any(row["time_to"] > time.time() for row in clean)
                or any(left["time_to"] != right["time"] for left, right in zip(clean, clean[1:]))):
            raise SearchError("Invalid compact archive chain")
        identity = (channel["id"], tuple((row["time"], row["time_to"], row["name"]) for row in clean))
        if identity in identities:
            raise SearchError("Duplicate compact archive chain")
        identities.add(identity)
        total += len(clean)
        if total > 10000:
            raise SearchError("Compact archive candidates exceed the row limit")
        chains.append((channel, clean))
    return current, chains, prepared["until"]


def origin(url):
    try:
        if not isinstance(url, str):
            raise ValueError()
        parts = urlsplit(url)
        if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
                or parts.fragment or "\\" in url or any(ord(c) <= 32 or ord(c) == 127 for c in url)):
            raise ValueError()
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        if not 0 < port <= 65535:
            raise ValueError()
        return parts.scheme, parts.hostname.lower(), port
    except (ValueError, TypeError):
        raise SearchError("The player returned an invalid archive address") from None


def same_origin_opener(expected):
    class SameOriginRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            if origin(newurl) != expected:
                return None
            return super().redirect_request(request, fp, code, message, headers, newurl)
    return build_opener(SameOriginRedirect(), https_handler())


def archive_available(url, start, require_hls=False, allow_redirects=False, *, deadline=None, strict=False):
    """Check media reachability, rejecting a contradictory HLS wall-clock stamp.

    Unstamped HLS/TS does not prove an archive timeline: providers may return
    live media for an archive URL. Archive-mode dispatch remains the player's
    responsibility; requiring PDT/ENDLIST here would reject valid archives.
    """
    end = time.monotonic() + 15
    if deadline is not None:
        end = min(end, deadline)
    try:
        return _bounded_read(lambda cancelled: _archive_available(
            url, start, require_hls, allow_redirects, end, cancelled), end)
    except SearchTimeout:
        if strict:
            raise
    except HTTPError as exc:
        code = exc.code
        exc.close()
        if strict and code in (401, 403):
            raise ArchiveSourceError("Archive source denied access; no playback was requested") from None
        if strict and (code == 429 or code >= 500):
            raise ArchiveTransientError("Archive source is temporarily unavailable; no playback was requested") from None
    except (OSError, HTTPException):
        if strict:
            raise ArchiveTransientError("Archive media request failed; no playback was requested") from None
    except SearchError:
        if strict:
            raise ArchiveSourceError("The player returned an invalid archive address") from None
    except ValueError:
        pass
    return False


def _archive_available(url, start, require_hls, allow_redirects, deadline, cancelled):
    expected = origin(url)
    opener = same_origin_opener(expected) if allow_redirects else build_opener(NoRedirect(), https_handler())
    for depth in range(4):
        _remaining(deadline, cancelled=cancelled)
        if origin(url) != expected:
            return False
        with opener.open(Request(url, headers={"User-Agent": "ottplay-cli/1.0"}),
                         timeout=_remaining(deadline, 15, cancelled)) as response:
            raw = _read_prefix(response, 16, deadline, cancelled)
            response_url = response.geturl() if allow_redirects else url
            if not raw.startswith(b"#EXTM3U"):
                if depth > 0 and len(raw) >= 16 and raw[4:8] in (b"styp", b"moof"):
                    return True
                if require_hls and depth == 0:
                    return False
                raw = _read_prefix(response, 377, deadline, cancelled, raw)
                return len(raw) >= 377 and all(raw[i] == 0x47 for i in (0, 188, 376))
            raw = _read_prefix(response, 256 * 1024 + 1, deadline, cancelled, raw)
        if len(raw) > 256 * 1024:
            return False
        lines = [line.strip() for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
        stamps = [line.split(":", 1)[1] for line in lines if line.startswith("#EXT-X-PROGRAM-DATE-TIME:")]
        if stamps and abs(datetime.fromisoformat(stamps[0].replace("Z", "+00:00")).timestamp() - start) > 120:
            return False
        links = [line for line in lines if not line.startswith("#")]
        if not links or not any(line.startswith(("#EXTINF:", "#EXT-X-STREAM-INF:")) for line in lines):
            return False
        url = urljoin(response_url, links[0])
    return False

def search_archives(client, device, settings, snapshot, query, refresh=False, catalog_expired=None, *,
                    progress=None, fast=False, deadline=None, prepared=None, refresh_current=None):
    _remaining(deadline)
    capability = snapshot.get("archive")
    if (not isinstance(capability, dict) or capability.get("version") != 1
            or not isinstance(capability.get("revision"), str) or not 1 <= len(capability["revision"]) <= 128):
        raise SearchError("No current programme matches. Update this player to support archive search")
    channels = snapshot["channels"]
    for channel in channels:
        hours = channel.get("archiveHours")
        if type(hours) is not int or not 0 <= hours <= 144:
            raise SearchError("The player returned invalid archive retention")
    base = settings["url"].rstrip("/")
    identity = [client.config["server"], device, capability["revision"]]
    key = [base, identity, text_key(query)]
    until = time.time() + GUIDE_TTL
    _progress(progress, "Reading cached archive results")
    previous = cached("query", key, GUIDE_TTL, refresh) if not fast and prepared is None else None
    selected = None
    recheck_current = None
    candidate_count = None
    now = time.time()
    if fresh(previous, now):
        candidates = previous.get("programs")
        if valid_candidates(candidates, channels, now):
            selected = candidates
            _progress(progress, "Using cached archive results", len(selected), len(selected))
    receipt = snapshot["catalog"]
    checked_at = time.monotonic()

    def call(action, params):
        if deadline is None:
            return client.call(device, action, params)
        previous_timeout = client.timeout
        client.timeout = min(previous_timeout, _remaining(deadline))
        try:
            response = client.call(device, action, params)
            _remaining(deadline)
            return response
        finally:
            client.timeout = previous_timeout

    def fresh_receipt(force=False):
        nonlocal receipt, checked_at
        if force or time.monotonic() - checked_at >= 60:
            _progress(progress, "Refreshing player catalogue")
            current = call("epg_catalog", {})
            if (not isinstance(current, dict) or current.get("channels") != channels
                    or current.get("archive") != capability or not isinstance(current.get("catalog"), str)):
                raise SearchError("The player's catalogue changed during search; no playback was requested")
            receipt = current["catalog"]
            checked_at = time.monotonic()
        return receipt

    def result(channel, row, mode, count=1):
        return {"id": channel["id"], "channel": channel["name"], "number": channel["number"],
                "title": row["name"], "start": row["time"], "end": row["time_to"], "mode": mode,
                "consecutive": count, "archive_hours": channel["archiveHours"]}

    if selected is None:
        if prepared is None:
            options = {"progress": progress}
            if deadline is not None:
                options["deadline"] = deadline
            guide, until = schedules(base, channels, refresh, **options)
            if fast:
                guide = _validate_guide(guide)
                candidate_count = sum(len(archive_chains(rows, query, time.time(), channel["archiveHours"]))
                                      for channel, rows in guide)
        else:
            if not callable(refresh_current):
                raise SearchError("Compact archive search requires a current-programme recheck")
            current_rows, chains, until = _prepared_candidates(prepared, channels, query)
            candidate_count = len(chains)
        checked_candidates = 0

        def verify(channel, row):
            nonlocal checked_candidates
            _remaining(deadline)
            archive_key = [identity, channel["id"], row["time"]]
            verified = cached("archive", archive_key, ARCHIVE_TTL, refresh) is True
            if not verified:
                params = {"catalog": fresh_receipt(), "id": channel["id"], "start": row["time"],
                          "end": row["time_to"], "title": row["name"]}
                _progress(progress, "Resolving archive candidates", checked_candidates)
                try:
                    response = call("resolve_archive", params)
                except Exception as exc:
                    if catalog_expired is None or not catalog_expired(exc):
                        raise
                    # Retry only an acknowledged expired read, never playback.
                    params["catalog"] = fresh_receipt(force=True)
                    _progress(progress, "Resolving archive candidates", checked_candidates)
                    response = call("resolve_archive", params)
                if not isinstance(response, dict) or response.get("resolved") is not True:
                    raise SearchError("The player did not resolve the selected archive")
                _progress(progress, "Checking archive media", checked_candidates)
                options = {"strict": True} if fast or deadline is not None else {}
                if deadline is not None:
                    options["deadline"] = deadline
                verified = archive_available(response.get("url"), row["time"], **options)
                if verified:
                    _remaining(deadline)
                    remember("archive", archive_key, True)
            else:
                _progress(progress, "Using cached archive availability", checked_candidates)
            checked_candidates += 1
            _progress(progress, "Checking archive availability", checked_candidates)
            return verified

        _progress(progress, "Searching programme history")
        if prepared is None:
            options = {}
            if fast:
                options["fast"] = True
            if deadline is not None:
                options["deadline"] = deadline
            choices, boundary = select_from_schedules(guide, query, verify, **options)
            recheck_current = lambda now: _current_from_schedules(guide, query, now)
        else:
            checks = 0
            def current(now):
                nonlocal checks
                rows = current_rows
                if checks:
                    _progress(progress, "Rechecking current programmes")
                    rows = refresh_current()
                    _remaining(deadline)
                    rows, _, _ = _prepared_candidates(
                        {"current": rows, "chains": [], "until": time.time() + 1}, channels, query)
                    now = time.time()
                checks += 1
                return [(channel, row, "live", 1) for channel, row in rows
                        if row["time"] <= now < row["time_to"]]
            choices = _select_chains(current, chains, verify, fast=fast, deadline=deadline)
            recheck_current = current
            boundary = until
        selected = [result(*choice) for choice in choices]
        until = min(until, boundary)
        if not fast and selected and selected[0]["mode"] == "archive":
            remember("query", key, {"programs": selected, "until": until})
    receipt = fresh_receipt(force=True)
    # The final catalogue read can cross a programme boundary or the provider's
    # retention cutoff too. Never let its latency revive an ended live choice.
    had_selection = bool(selected)
    live = recheck_current(time.time()) if recheck_current is not None else []
    _remaining(deadline)
    now = time.time()
    live = [item for item in live if item[1]["time"] <= now < item[1]["time_to"]]
    if live:
        selected = [result(*item) for item in live]
    elif recheck_current is None and not fresh(previous, now):
        selected = []
    else:
        selected = [row for row in selected if row["mode"] == "archive"
                    and now - row["archive_hours"] * 3600 <= row["start"] < row["end"] <= now]
    if fast and had_selection and not selected:
        raise SearchError("Programme selection expired during validation; retry the query. No playback was requested")
    targets = {}
    for row in selected:
        target = {"catalog": receipt, "id": row["id"]}
        if row["mode"] == "archive":
            target.update({k: row[k] for k in ("start", "end", "title")})
            targets[(row["number"], row["start"], row["end"], row["title"])] = target
            targets[(row["number"], row["start"])] = target
        else:
            targets[row["number"]] = target
    _progress(progress, "Archive search complete", len(selected), len(selected))
    data = {"as_of": time.time(), "checked": len(channels), "total": len(channels),
            "partial": False, "programs": selected}
    if fast:
        data.update(selection="random_first_available", verified_all=False,
                    candidate_chains=len(selected) if candidate_count is None else candidate_count)
    return data, targets
