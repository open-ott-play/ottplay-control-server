"""Resolve a private HLS playlist without an OttPlay player or controller.

The explicit machine command exchanges private media URLs through local pipes.
Only channel metadata reaches EPG; playback and random selection belong to the
caller. EPG selection, archive validation and cache storage are shared with ott's
remote-player search.
"""
from datetime import datetime
from http.client import HTTPException
import importlib.util
import math
from pathlib import Path
import re
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request

_spec = importlib.util.spec_from_file_location(
    "ott_playlist_programme_search", Path(__file__).resolve().with_name("programme_search.py"))
history = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(history)
SearchError = history.SearchError


def display_text(value):
    return " ".join("".join(c if not unicodedata.category(c).startswith("C") else " "
                            for c in value).split())


def number(value, minimum, maximum):
    try:
        parsed = float(value)
        return max(minimum, min(maximum, parsed)) if math.isfinite(parsed) else 0
    except (ValueError, TypeError):
        return 0


def validate_request(request):
    fields = {"version", "query", "playlist_url", "epg_url", "refresh", "cache_seconds",
              "playlist_cache_seconds", "archive_cache_seconds", "cache_dir"}
    if (not isinstance(request, dict) or set(request) - fields
            or type(request.get("version")) is not int or request["version"] != 1
            or not isinstance(request.get("query"), str)):
        raise SearchError("Invalid playlist resolver request")
    try:
        if not history.text_key(request["query"]) or len(request["query"].encode("utf-8")) > 1024:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise SearchError("Enter title keywords containing at most 1024 UTF-8 bytes") from None
    if "refresh" in request and type(request["refresh"]) is not bool:
        raise SearchError("Invalid playlist resolver refresh option")
    for field in ("playlist_url", "epg_url"):
        value = request.get(field, "")
        if field == "epg_url" and value == "":
            continue
        if not isinstance(value, str) or len(value) > 8192:
            raise SearchError("Invalid playlist or EPG address")
        history.origin(value)
        if field == "epg_url" and "?" in value:
            raise SearchError("EPG address must not contain a query")
    for field in ("cache_seconds", "playlist_cache_seconds", "archive_cache_seconds"):
        if field in request and (type(request[field]) not in (int, float)
                                 or (type(request[field]) is float and not math.isfinite(request[field]))
                                 or request[field] < 0):
            raise SearchError("Invalid playlist resolver cache duration")
    if "cache_dir" in request and (not isinstance(request["cache_dir"], str)
                                  or not request["cache_dir"] or "\0" in request["cache_dir"]
                                  or len(request["cache_dir"]) > 4096):
        raise SearchError("Invalid playlist resolver cache directory")


class Cache:
    def __init__(self, request):
        self.directory = Path(request.get("cache_dir", history.CACHE)).expanduser()
        self.refresh = request.get("refresh", False)
        self.guide_ttl = min(7200, int(request.get("cache_seconds", 7200)))
        self.playlist_ttl = min(604800, int(request.get("playlist_cache_seconds", 604800))) if self.guide_ttl else 0
        self.archive_ttl = min(604800, int(request.get("archive_cache_seconds", 604800))) if self.guide_ttl else 0

    def ttl(self, kind):
        return {"playlist": self.playlist_ttl, "playlist-archive": self.archive_ttl}.get(kind, self.guide_ttl)

    def read(self, kind, key, ttl=None, refresh=False):
        return history.cached(kind, key, self.ttl(kind) if ttl is None else ttl,
                              self.refresh or refresh, directory=self.directory)

    def write(self, kind, key, value):
        if self.ttl(kind):
            history.remember(kind, key, value, directory=self.directory)


def parse_playlist(text, playlist_url):
    if not text.lstrip("\ufeff \t\r\n").startswith("#EXTM3U"):
        raise SearchError("The proxy did not return an M3U playlist")
    expected = history.origin(playlist_url)
    entries, pending = [], None
    for line in text.splitlines():
        line = line.strip().lstrip("\ufeff")
        if line.startswith("#EXTINF:"):
            quoted, pending = False, None
            for index, char in enumerate(line):
                if char == '"':
                    quoted = not quoted
                elif char == "," and not quoted:
                    attrs = dict(re.findall(r'([\w-]+)="([^\"]*)"', line[:index]))
                    name = display_text(line[index + 1:]) or display_text(attrs.get("tvg-name", ""))
                    if name:
                        pending = {"name": name, "group": display_text(attrs.get("group-title", "")),
                                   "search": history.text_key(name + " " + attrs.get("tvg-name", "")),
                                   "tvg_id": attrs.get("tvg-id", ""), "tvg_name": attrs.get("tvg-name", ""),
                                   "archive_hours": number(attrs.get("tvg-rec", "0"), 0, 6) * 24,
                                   "epg_shift": int(number(attrs.get("tvg-shift", "0"), -24, 24) * 3600)}
                    break
        elif line.startswith("#EXTGRP:") and pending and not pending["group"]:
            pending["group"] = display_text(line[len("#EXTGRP:"):])
        elif line and not line.startswith("#"):
            entry, pending = pending, None
            if entry:
                try:
                    url = urljoin(playlist_url, line)
                    allowed = history.origin(url) == expected
                except (SearchError, ValueError):
                    continue
                if allowed:
                    entries.append({**entry, "url": url})
    if not entries:
        raise SearchError("The playlist contains no named HTTP streams served by the configured proxy")
    return entries


def load_playlist(request, cache):
    url = request["playlist_url"]
    saved = cache.read("playlist", url)
    if isinstance(saved, str):
        try:
            return parse_playlist(saved, url)
        except SearchError:
            pass
    limit = 8 * 1024 * 1024
    try:
        opener = history.same_origin_opener(history.origin(url))
        with opener.open(Request(url, headers={"User-Agent": "ottplay-cli/1.0"}), timeout=20) as response:
            raw = response.read(limit + 1)
        if len(raw) > limit:
            raise SearchError("The playlist exceeds the 8 MiB limit")
        text = raw.decode("utf-8-sig")
    except HTTPError as exc:
        exc.close()
        raise SearchError("Could not load the proxy playlist") from None
    except (OSError, HTTPException, UnicodeError):
        raise SearchError("Could not load a valid UTF-8 proxy playlist") from None
    entries = parse_playlist(text, url)
    cache.write("playlist", url, text)
    return entries


def channel_key(entry):
    parts = urlsplit(entry["url"])
    proxy_channel = re.fullmatch(r"/channel/[^/]+/index\.m3u8", parts.path) is not None
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key not in ("utc", "lutc")
             and not (proxy_channel and key == "q" and re.fullmatch(r"\d{13}", value))]
    return history.origin(entry["url"]), parts.path, tuple(sorted(query))


def archive_url(entry, start, now):
    parts = urlsplit(entry["url"])
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key not in ("utc", "lutc")]
    query += [("utc", str(int(start))), ("lutc", str(int(now)))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def programme_entry(entry, row, mode, count):
    stamp = datetime.fromtimestamp(row["time"]).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    result = {**entry, "name": "%s — %s" % (row["name"], entry["name"]), "mode": mode,
              "programme_start": row["time"], "programme_end": row["time_to"],
              "details": "%s from %s%s" % (mode, stamp, "; %s consecutive programmes" % count if count > 1 else "")}
    if mode == "archive":
        result["url"] = archive_url(entry, row["time"], time.time())
    return result


def cached_choices(value, entries, now, ttl):
    if not history.fresh(value, now, ttl) or not isinstance(value.get("choices"), list) or not value["choices"]:
        return None
    choices = []
    for row in value["choices"]:
        if (not isinstance(row, dict) or row.get("mode") not in ("live", "archive")
                or any(type(row.get(key)) is not int for key in ("index", "start", "end", "count"))
                or not 0 <= row["index"] < len(entries) or row["count"] < 1
                or not isinstance(row.get("title"), str) or not row["title"]
                or not 0 < row["start"] < row["end"]):
            return None
        entry = entries[row["index"]]
        if row["mode"] == "live":
            if not row["start"] <= now < row["end"]:
                return None
        elif not now - min(144, entry["archive_hours"]) * 3600 <= row["start"] < row["end"] <= now:
            return None
        choices.append(programme_entry(entry, {"name": row["title"], "time": row["start"],
                                               "time_to": row["end"]}, row["mode"], row["count"]))
    if len({row["mode"] for row in choices}) != 1:
        return None
    return choices


def resolve(request):
    validate_request(request)
    cache = Cache(request)
    entries = load_playlist(request, cache)
    query = request["query"]
    matches = [entry for entry in entries if history.matches(entry["search"], query)]
    if matches:
        return {"version": 1, "kind": "playlist", "matches": matches}
    base = request.get("epg_url", "").rstrip("/")
    if not base:
        raise SearchError("Configure an EPG address for programme search")
    key = [base, history.text_key(query), entries]
    matches = cached_choices(cache.read("playlist-query", key), entries, time.time(), cache.guide_ttl)
    if matches:
        return {"version": 1, "kind": matches[0]["mode"], "matches": matches}
    channels = [{"id": str(index), "name": entry["name"][:256], "tvgId": entry["tvg_id"][:256],
                 "tvgName": entry["tvg_name"][:256], "shift": entry["epg_shift"],
                 "archiveHours": entry["archive_hours"], "index": index}
                for index, entry in enumerate(entries)]
    guide, until = history.schedules(base, channels, request.get("refresh", False),
                                     cache=cache, ttl=cache.guide_ttl, attempts=3)

    def verify(channel, row):
        entry = entries[channel["index"]]
        archive_key = [channel_key(entry), row["time"]]
        if cache.read("playlist-archive", archive_key) is True:
            return True
        url = archive_url(entry, row["time"], time.time())
        if history.archive_available(url, row["time"], require_hls=True, allow_redirects=True):
            cache.write("playlist-archive", archive_key, True)
            return True
        return False

    selected, boundary = history.select_from_schedules(guide, query, verify)
    matches = [programme_entry(entries[channel["index"]], row, mode, count)
               for channel, row, mode, count in selected]
    if selected:
        choices = [{"index": channel["index"], "title": row["name"], "start": row["time"],
                    "end": row["time_to"], "mode": mode, "count": count}
                   for channel, row, mode, count in selected]
        cache.write("playlist-query", key, {"choices": choices,
                    "until": min(until, boundary, time.time() + cache.guide_ttl)})
    return {"version": 1, "kind": matches[0]["mode"] if matches else "none", "matches": matches}
