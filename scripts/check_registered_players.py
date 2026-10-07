#!/usr/bin/env python3
"""Read-only, inventory-driven acceptance checks for registered player searches."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import time


def load_module(name, filename):
    path = Path(__file__).resolve().parents[1] / "cli" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ott = load_module("registered_player_ott", "ott.py")
history = load_module("registered_player_history", "programme_search.py")
READ_ACTIONS = frozenset(("channels", "epg_catalog", "resolve_archive", "capabilities"))
DEVICE_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
GAPS = frozenset(("unsupported", "rejected", "unresponsive", "empty_catalog", "empty_channels",
                  "not_checked", "epg_not_configured", "archive_unsupported", "no_archive_channels",
                  "no_matches", "live_match"))


class ReadOnlyViolation(Exception):
    pass


class InvalidResponse(Exception):
    pass


class ReadOnlyClient:
    """A fail-closed RPC boundary, including for helpers called by this script."""
    def __init__(self, client, device=None, timeout=None):
        self.client = copy.copy(client) if timeout is not None else client
        if timeout is not None:
            self.client.timeout = timeout
        self.device = device
        self.config = self.client.config
        self.timeout = self.client.timeout
        self.calls = Counter()

    def api(self, path, payload=None, timeout=None):
        if path != "/api/devices" or payload is not None:
            raise ReadOnlyViolation()
        return self.client.api(path, timeout=timeout)

    def call(self, device, action, params):
        if action not in READ_ACTIONS or (self.device is not None and device != self.device):
            raise ReadOnlyViolation()
        self.calls[action] += 1
        return self.client.call(device, action, params)


def inventory(client, config):
    _, data = client.api("/api/devices")
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list) or len(data["devices"]) > 128:
        raise InvalidResponse()
    registered = {}
    for row in data["devices"]:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not DEVICE_ID.fullmatch(row["id"]) or type(row.get("pending")) is not int
                or row["pending"] < 0):
            raise InvalidResponse()
        seen = row.get("last_seen")
        if seen is not None:
            if not isinstance(seen, str) or len(seen) > 40:
                raise InvalidResponse()
            try:
                stamp = datetime.fromisoformat(seen.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError()
                seen = stamp.isoformat()
            except ValueError:
                raise InvalidResponse() from None
        registered[row["id"]] = {"id": row["id"], "aliases": [], "last_seen": seen,
                                 "pending": row["pending"]}
    aliases = config.get("players", {})
    if not isinstance(aliases, dict):
        raise InvalidResponse()
    stale = []
    for alias, device in aliases.items():
        if (not ott.epg_text(alias, 512, "utf-8") or not alias.strip()
                or not isinstance(device, str) or not DEVICE_ID.fullmatch(device)):
            raise InvalidResponse()
        alias = ott.clean(alias).strip()
        if re.search(r"[A-Za-z][A-Za-z0-9+.-]*://", alias):
            alias = "[redacted alias]"
        if device in registered:
            registered[device]["aliases"].append(alias)
        else:
            stale.append({"alias": alias, "id": device})
    for row in registered.values():
        row["aliases"].sort(key=str.casefold)
    return [registered[key] for key in sorted(registered)], sorted(stale, key=lambda row: row["alias"].casefold())


def catalog_summary(data):
    if (not isinstance(data, dict) or not ott.epg_text(data.get("catalog"), 128, "utf-8")
            or not data["catalog"].strip() or not isinstance(data.get("channels"), list)
            or len(data["channels"]) > 10000):
        raise InvalidResponse()
    ids, previous, archive_count = set(), 0, 0
    for channel in data["channels"]:
        if (not isinstance(channel, dict)
                or not all(ott.epg_text(channel.get(key), 512) for key in ("id", "name", "tvgId", "tvgName"))
                or not channel["id"].strip() or channel["id"] in ids
                or type(channel.get("number")) is not int or channel["number"] <= previous
                or type(channel.get("shift")) is not int or not -86400 <= channel["shift"] <= 86400):
            raise InvalidResponse()
        ids.add(channel["id"])
        previous = channel["number"]
        hours = channel.get("archiveHours", 0)
        if type(hours) is not int or not 0 <= hours <= 144:
            raise InvalidResponse()
        archive_count += hours > 0
    capability = data.get("archive")
    archive_supported = (isinstance(capability, dict) and type(capability.get("version")) is int
                         and capability["version"] == 1 and isinstance(capability.get("revision"), str)
                         and 1 <= len(capability["revision"]) <= 128)
    return {"status": "ok" if ids else "empty_catalog", "channels": len(ids),
            "archive_channels": archive_count, "archive_supported": archive_supported}


def channel_summary(data):
    if not isinstance(data, dict) or not isinstance(data.get("channels"), list):
        raise InvalidResponse()
    ids, previous = set(), 0
    for channel in data["channels"]:
        if not isinstance(channel, dict):
            raise InvalidResponse()
        identity = ott.channel_identity(channel.get("id"))
        if (identity is None or identity in ids or type(channel.get("number")) is not int
                or not previous < channel["number"] <= 9007199254740991
                or not ott.epg_text(channel.get("name"), 16384) or not channel["name"].strip()):
            raise InvalidResponse()
        ids.add(identity)
        previous = channel["number"]
    return {"status": "ok" if ids else "empty_channels", "channels": len(ids)}


def failure_status(error):
    if isinstance(error, ott.PlayerUnsupported):
        return "unsupported"
    if isinstance(error, ott.PlayerRejected):
        return "rejected"
    if isinstance(error, ReadOnlyViolation):
        return "read_only_violation"
    if isinstance(error, InvalidResponse):
        return "invalid_response"
    if isinstance(error, ott.TransportError):
        return "transport_error"
    if isinstance(error, ott.HTTPError):
        return "http_error"
    if isinstance(error, ott.Error) and str(error).startswith("The player did not respond."):
        return "unresponsive"
    return "search_failed"


def measure(operation):
    started = time.monotonic()
    try:
        result = operation()
    except Exception as error:
        result = {"status": failure_status(error)}
    return {**result, "elapsed_seconds": round(time.monotonic() - started, 3)}


def probe_catalogues(client, row, timeout):
    readonly = ReadOnlyClient(client, row["id"], timeout)
    catalogue = measure(lambda: catalog_summary(readonly.call(row["id"], "epg_catalog", {})))
    channels = ({"status": "not_checked"} if catalogue["status"] == "unresponsive" else
                measure(lambda: channel_summary(readonly.call(row["id"], "channels", {"search": ""}))))
    return {**row, "catalogue": catalogue, "channels": channels}


def current_check(client, device, settings):
    data, _ = ott.server_programs(client, device, settings, "")
    if (not isinstance(data, dict) or data.get("partial") is not False
            or type(data.get("checked")) is not int or data["checked"] != data.get("total")
            or not isinstance(data.get("programs"), list)):
        raise InvalidResponse()
    return {"status": "ok" if data["total"] else "empty_catalog", "checked": data["checked"],
            "total": data["total"], "matches": len(data["programs"])}


def archive_check(client, device, settings, query, refresh=False):
    snapshot = client.call(device, "epg_catalog", {})
    summary = catalog_summary(snapshot)
    if summary["status"] == "empty_catalog":
        return {"status": "empty_catalog"}
    if not summary["archive_supported"]:
        return {"status": "archive_unsupported"}
    if not summary["archive_channels"]:
        return {"status": "no_archive_channels"}
    before = client.calls["resolve_archive"]
    data, _ = history.search_archives(client, device, settings, snapshot, query, refresh,
                                      catalog_expired=ott.expired_epg_catalog)
    programs = data["programs"]
    archive_matches = sum(row.get("mode") == "archive" for row in programs)
    live_matches = sum(row.get("mode") == "live" for row in programs)
    if len(programs) != archive_matches + live_matches or (archive_matches and live_matches):
        raise InvalidResponse()
    resolutions = client.calls["resolve_archive"] - before
    return {"status": "ok" if archive_matches else "live_match" if live_matches else "no_matches",
            "archive_matches": archive_matches, "live_matches": live_matches,
            "archive_resolution_requests": resolutions,
            "cached_availability_only": bool(archive_matches and not resolutions)}


def run_checks(client, config, archive_query=None, refresh=False, workers=4, probe_timeout=15):
    rows, stale = inventory(ReadOnlyClient(client), config)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda row: probe_catalogues(client, row, probe_timeout), rows))
    # Provider archive connection limits apply across devices: keep all media
    # resolution/probing sequential. Catalogue presence never depends on status.
    for row in results:
        readonly = ReadOnlyClient(client, row["id"])
        row["current"] = {"status": "not_checked"}
        row["archive"] = {"status": "not_requested" if archive_query is None else "not_checked"}
        if row["catalogue"]["status"] != "ok":
            continue
        if "epg" not in config:
            row["current"] = {"status": "epg_not_configured"}
            if archive_query is not None:
                row["archive"] = {"status": "epg_not_configured"}
            continue
        row["current"] = measure(lambda: current_check(readonly, row["id"], config["epg"]))
        if archive_query is not None:
            row["archive"] = measure(lambda: archive_check(readonly, row["id"], config["epg"], archive_query, refresh))
    counts = Counter()
    for row in results:
        statuses = [row[key]["status"] for key in ("catalogue", "channels", "current", "archive")]
        row["status"] = ("failed" if any(status not in GAPS | {"ok", "not_requested"} for status in statuses)
                         else "partial" if any(status in GAPS for status in statuses) else "passed")
        counts[row["status"]] += 1
    status = "failed" if counts["failed"] else "partial" if counts["partial"] or stale or not results else "passed"
    report = {"version": 1, "status": status, "registered": len(results),
              "aliases": sum(len(row["aliases"]) for row in results),
              "unaliased": sum(not row["aliases"] for row in results),
              "archive_requested": archive_query is not None,
              "summary": {key: counts[key] for key in ("passed", "partial", "failed")},
              "stale_aliases": stale, "players": results}
    return report, {"passed": 0, "partial": 3, "failed": 1}[status]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("OTT_CONFIG", str(Path.home() / ".config/ottplay-control/cli.json")))
    parser.add_argument("--probe-timeout", type=float, default=15,
                        help="Catalogue/channel presence RPC deadline in seconds (1–300)")
    parser.add_argument("--timeout", type=float, default=45,
                        help="Search RPC deadline and aggregate current EPG budget in seconds (1–300)")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent catalogue/channel probes (1–4)")
    parser.add_argument("--archive-query", help="Also exercise shared archive search for these title keywords")
    parser.add_argument("--refresh", action="store_true", help="Bypass archive-search caches")
    parser.add_argument("--output", help="Also save the report atomically with private file permissions")
    args = parser.parse_args(argv)
    try:
        if (not math.isfinite(args.timeout) or not 1 <= args.timeout <= 300
                or not math.isfinite(args.probe_timeout) or not 1 <= args.probe_timeout <= 300
                or not 1 <= args.workers <= 4
                or (args.archive_query is not None and (not history.text_key(args.archive_query)
                    or not ott.epg_text(args.archive_query, 1024, "utf-8")))):
            raise ValueError()
        config = ott.read_json(args.config)
        client = ott.Client(config, args.timeout)
        report, status = run_checks(client, config, args.archive_query, args.refresh, args.workers, args.probe_timeout)
    except KeyboardInterrupt:
        report, status = {"version": 1, "status": "interrupted"}, 130
    except Exception as error:
        report, status = {"version": 1, "status": "failed", "error": failure_status(error)}, 1
    if args.output:
        try:
            ott.write_private(args.output, report)
        except Exception:
            report, status = {"version": 1, "status": "failed", "error": "report_write_failed"}, 1
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
