"""Shared archive search for every player using the OTT-play control protocol.

Guide requests contain metadata only. Resolved media URLs live only in memory
between the authenticated controller and its local availability probe.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

CACHE = Path(os.environ.get("OTT_CACHE", "~/.cache/ottplay-control")).expanduser()
GUIDE_TTL = 7200
ARCHIVE_TTL = 7 * 86400


class SearchError(Exception):
    pass


class GenerationChanged(SearchError):
    pass


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


def epg_request(base, path, payload=None, attempts=1):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = Request(base + path, data=body, headers={"Content-Type": "application/json", "User-Agent": "ottplay-cli/1.0"})
    try:
        for attempt in range(attempts):
            try:
                with build_opener(NoRedirect()).open(request, timeout=20) as response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                break
            except HTTPError as exc:
                exc.close()
                if attempt + 1 == attempts or exc.code not in (429, 500, 502, 503, 504):
                    raise
            except (OSError, HTTPException):
                if attempt + 1 == attempts:
                    raise
            time.sleep(attempt + 1)
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
        raise SearchError("EPG history request failed (HTTP %s); no playback was requested" % exc.code) from None
    except (OSError, HTTPException, ValueError):
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


def schedules(base, channels, refresh=False, cache=None, ttl=GUIDE_TTL, attempts=1):
    read = cached if cache is None else cache.read
    write = remember if cache is None else cache.write
    lifetime = max(1, ttl)
    request = epg_request if attempts == 1 else lambda *args: epg_request(*args, attempts=attempts)
    metadata = [{k: c[k] for k in ("id", "name", "tvgId", "tvgName")} for c in channels]
    for attempt in range(2):
        try:
            clock = time.time()
            key = [base, metadata]
            mapping = read("mapping", key, ttl, refresh or bool(attempt))
            if not valid_mapping(mapping, metadata, clock, lifetime):
                generation, mappings = None, {}
                for start in range(0, len(metadata), 100):
                    batch = metadata[start:start + 100]
                    response = request(base, "/match", {"version": 1, "source": "epg-one", "channels": batch})
                    if generation is not None and generation != response["generation"]:
                        raise GenerationChanged("EPG changed during channel matching")
                    generation = response["generation"]
                    if not isinstance(response.get("mappings"), dict):
                        raise SearchError("Invalid EPG channel mapping")
                    mappings.update({row["id"]: response["mappings"].get(row["id"]) for row in batch})
                mapping = {"generation": generation, "mappings": mappings, "until": clock + lifetime}
                if not valid_mapping(mapping, metadata, clock, lifetime):
                    raise SearchError("Invalid EPG channel mapping")
                write("mapping", key, mapping)
            ids = {}
            for channel in channels:
                match = mapping["mappings"].get(channel["id"])
                if match is None:
                    continue
                ids[channel["id"]] = (match["channelId"], match["shift"])

            def fetch(key):
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
                    write("guide", cache_key, saved)
                return key, rows, saved["until"]

            guides, until = {}, mapping["until"]
            with ThreadPoolExecutor(max_workers=4) as pool:
                for key, rows, expires in pool.map(fetch, set(ids.values())):
                    guides[key] = rows
                    until = min(until, expires)
            return [(channel, [{**row, "time": row["time"] + channel["shift"],
                                "time_to": row["time_to"] + channel["shift"]} for row in guides[ids[channel["id"]]]])
                    for channel in channels if channel["id"] in ids], until
        except GenerationChanged:
            if attempt:
                raise
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


def select_from_schedules(guide, query, verify):
    """Select live broadcasts or each earliest playable contiguous archive chain.

    Sources supply only the private archive resolver/probe. Matching, retention,
    adjacency and the late live recheck are shared by local and remote players.
    """
    def current(now):
        return [(channel, row, "live", 1) for channel, rows in guide for row in rows
                if row["time"] <= now < row["time_to"] and matches(row["name"], query)]

    now = time.time()
    selected = current(now)
    if not selected:
        for channel, rows in guide:
            for chain in archive_chains(rows, query, now, channel["archiveHours"]):
                for index, row in enumerate(chain):
                    if row["time"] < time.time() - min(144, channel["archiveHours"]) * 3600:
                        continue
                    if verify(channel, row):
                        selected.append((channel, row, "archive", len(chain) - index))
                        break
    # Availability probes can cross a programme boundary or retention cutoff.
    now = time.time()
    live = current(now)
    selected = live or [item for item in selected if item[2] == "archive"
                       and item[1]["time"] >= now - min(144, item[0]["archiveHours"]) * 3600]
    boundaries = [stamp for _, rows in guide for row in rows if matches(row["name"], query)
                  for stamp in (row["time"], row["time_to"]) if stamp > now]
    return selected, min(boundaries, default=float("inf"))


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
    return build_opener(SameOriginRedirect())


def archive_available(url, start, require_hls=False, allow_redirects=False):
    try:
        expected = origin(url)
        opener = same_origin_opener(expected) if allow_redirects else build_opener(NoRedirect())
        for depth in range(4):
            if origin(url) != expected:
                return False
            with opener.open(Request(url, headers={"User-Agent": "ottplay-cli/1.0"}), timeout=15) as response:
                raw = response.read(256 * 1024 + 1)
                response_url = response.geturl() if allow_redirects else url
            if not raw.startswith(b"#EXTM3U"):
                return ((not require_hls or depth > 0) and len(raw) >= 377 and all(raw[i] == 0x47 for i in (0, 188, 376))) or (
                    depth > 0 and len(raw) >= 16 and raw[4:8] in (b"styp", b"moof"))
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
    except HTTPError as exc:
        exc.close()
    except (OSError, HTTPException, ValueError, SearchError):
        pass
    return False


def search_archives(client, device, settings, snapshot, query, refresh=False, catalog_expired=None):
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
    previous = cached("query", key, GUIDE_TTL, refresh)
    selected = None
    now = time.time()
    if fresh(previous, now):
        candidates = previous.get("programs")
        if valid_candidates(candidates, channels, now):
            selected = candidates
    receipt = snapshot["catalog"]
    checked_at = time.monotonic()

    def fresh_receipt(force=False):
        nonlocal receipt, checked_at
        if force or time.monotonic() - checked_at >= 60:
            current = client.call(device, "epg_catalog", {})
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
        guide, until = schedules(base, channels, refresh)

        def verify(channel, row):
            archive_key = [identity, channel["id"], row["time"]]
            verified = cached("archive", archive_key, ARCHIVE_TTL, refresh) is True
            if not verified:
                params = {"catalog": fresh_receipt(), "id": channel["id"], "start": row["time"],
                          "end": row["time_to"], "title": row["name"]}
                try:
                    response = client.call(device, "resolve_archive", params)
                except Exception as exc:
                    if catalog_expired is None or not catalog_expired(exc):
                        raise
                    # Retry only an acknowledged expired read, never playback.
                    params["catalog"] = fresh_receipt(force=True)
                    response = client.call(device, "resolve_archive", params)
                if not isinstance(response, dict) or response.get("resolved") is not True:
                    raise SearchError("The player did not resolve the selected archive")
                verified = archive_available(response.get("url"), row["time"])
                if verified:
                    remember("archive", archive_key, True)
            return verified

        choices, boundary = select_from_schedules(guide, query, verify)
        selected = [result(*choice) for choice in choices]
        until = min(until, boundary)
        if selected and selected[0]["mode"] == "archive":
            remember("query", key, {"programs": selected, "until": until})
    receipt = fresh_receipt(force=True)
    targets = {}
    for row in selected:
        target = {"catalog": receipt, "id": row["id"]}
        if row["mode"] == "archive":
            target.update({k: row[k] for k in ("start", "end", "title")})
            targets[(row["number"], row["start"])] = target
        else:
            targets[row["number"]] = target
    return {"as_of": time.time(), "checked": len(channels), "total": len(channels),
            "partial": False, "programs": selected}, targets
