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


def cache_path(kind, key):
    digest = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    return CACHE / (kind + "-" + digest + ".json")


def cached(kind, key, ttl, refresh=False):
    if refresh:
        return None
    try:
        value = json.loads(cache_path(kind, key).read_text())
        if value["version"] == 1 and 0 <= time.time() - value["saved_at"] < ttl:
            return value["data"]
    except (OSError, KeyError, TypeError, ValueError):
        pass
    return None


def remember(kind, key, data):
    temporary = None
    try:
        CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".cache-", dir=str(CACHE))
        with os.fdopen(fd, "w") as stream:
            json.dump({"version": 1, "saved_at": time.time(), "data": data}, stream)
        os.replace(temporary, cache_path(kind, key))
    except OSError:
        pass
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def epg_request(base, path, payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    request = Request(base + path, data=body, headers={"Content-Type": "application/json", "User-Agent": "ottplay-cli/1.0"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
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


def schedules(base, channels, refresh=False):
    metadata = [{k: c[k] for k in ("id", "name", "tvgId", "tvgName")} for c in channels]
    for attempt in range(2):
        try:
            clock = time.time()
            key = [base, metadata]
            mapping = cached("mapping", key, GUIDE_TTL, refresh or bool(attempt))
            if not isinstance(mapping, dict) or clock >= mapping.get("until", 0):
                generation, mappings = None, {}
                for start in range(0, len(metadata), 100):
                    batch = metadata[start:start + 100]
                    response = epg_request(base, "/match", {"version": 1, "source": "epg-one", "channels": batch})
                    if generation is not None and generation != response["generation"]:
                        raise GenerationChanged("EPG changed during channel matching")
                    generation = response["generation"]
                    if not isinstance(response.get("mappings"), dict):
                        raise SearchError("Invalid EPG channel mapping")
                    mappings.update({row["id"]: response["mappings"].get(row["id"]) for row in batch})
                mapping = {"generation": generation, "mappings": mappings, "until": clock + GUIDE_TTL}
                remember("mapping", key, mapping)
            ids = {}
            for channel in channels:
                match = mapping["mappings"].get(channel["id"])
                if match is None:
                    continue
                if (not isinstance(match, dict) or not isinstance(match.get("channelId"), str)
                        or not 1 <= len(match["channelId"]) <= 512 or type(match.get("shift")) is not int
                        or not -86400 <= match["shift"] <= 86400 or match["shift"] % 3600):
                    raise SearchError("Invalid EPG channel mapping")
                ids[channel["id"]] = (match["channelId"], match["shift"])

            def fetch(key):
                cache_key = [base, mapping["generation"], key]
                saved = cached("guide", cache_key, GUIDE_TTL, refresh)
                clock = time.time()
                if not isinstance(saved, dict) or clock >= saved.get("until", 0):
                    response = epg_request(base, "/programmes?" + urlencode({
                        "channelId": key[0], "shift": key[1], "hours": 168, "generation": mapping["generation"]}))
                    if response["generation"] != mapping["generation"]:
                        raise GenerationChanged("EPG history changed")
                    saved = {"rows": clean_rows(response.get("rows")), "until": clock + GUIDE_TTL}
                    remember("guide", cache_key, saved)
                return key, clean_rows(saved["rows"]), saved["until"]

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


def origin(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None
                or parts.fragment or "\\" in url or any(ord(c) <= 32 for c in url)):
            raise ValueError()
        return parts.scheme, parts.hostname.lower(), parts.port or (443 if parts.scheme == "https" else 80)
    except (ValueError, TypeError):
        raise SearchError("The player returned an invalid archive address") from None


def archive_available(url, start):
    try:
        expected = origin(url)
        opener = build_opener(NoRedirect())
        for depth in range(4):
            if origin(url) != expected:
                return False
            with opener.open(Request(url, headers={"User-Agent": "ottplay-cli/1.0"}), timeout=15) as response:
                raw = response.read(256 * 1024 + 1)
            if not raw.startswith(b"#EXTM3U"):
                return (len(raw) >= 377 and all(raw[i] == 0x47 for i in (0, 188, 376))) or (
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
            url = urljoin(url, links[0])
    except HTTPError as exc:
        exc.close()
    except (OSError, HTTPException, ValueError, SearchError):
        pass
    return False


def search_archives(client, device, settings, snapshot, query, refresh=False):
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
    if isinstance(previous, dict) and now < previous.get("until", 0):
        candidates = previous.get("programs")
        if (isinstance(candidates, list) and candidates and all(
                row.get("mode") == "archive" and now - row["archive_hours"] * 3600 <= row["start"] < row["end"] <= now
                for row in candidates)):
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
        selected = []
        now = time.time()
        current = [result(channel, row, "live") for channel, rows in guide for row in rows
                   if row["time"] <= now < row["time_to"] and matches(row["name"], query)]
        for channel, rows in ([] if current else guide):
            for chain in archive_chains(rows, query, now, channel["archiveHours"]):
                for index, row in enumerate(chain):
                    if row["time"] < time.time() - channel["archiveHours"] * 3600:
                        continue
                    archive_key = [identity, channel["id"], row["time"]]
                    verified = cached("archive", archive_key, ARCHIVE_TTL, refresh) is True
                    if not verified:
                        params = {"catalog": fresh_receipt(), "id": channel["id"], "start": row["time"],
                                  "end": row["time_to"], "title": row["name"]}
                        response = client.call(device, "resolve_archive", params)
                        if not isinstance(response, dict) or response.get("resolved") is not True:
                            raise SearchError("The player did not resolve the selected archive")
                        verified = archive_available(response.get("url"), row["time"])
                        if verified:
                            remember("archive", archive_key, True)
                    if verified:
                        selected.append(result(channel, row, "archive", len(chain) - index))
                        break
        # A programme may have started during slow archive probes. Prefer it.
        now = time.time()
        live = [result(channel, row, "live") for channel, rows in guide for row in rows
                if row["time"] <= now < row["time_to"] and matches(row["name"], query)]
        boundaries = [stamp for _, rows in guide for row in rows if matches(row["name"], query)
                      for stamp in (row["time"], row["time_to"]) if stamp > now]
        selected = live or [row for row in selected if row["start"] >= now - row["archive_hours"] * 3600]
        until = min([until] + boundaries)
        if selected and not live:
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
