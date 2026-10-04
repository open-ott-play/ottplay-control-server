#!/usr/bin/env python3
"""OTT-play remote CLI using the Python 3 standard library."""
import argparse
import base64
import getpass
import http.client
import ipaddress
import importlib.util
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings

HELP = """ott [--config FILE] [--json] PLAYER [COMMAND ...]
  ott diagnostics --help             scoped runtime diagnostics and session control
  ott presets                        list locally configured preset names
  ott devices                        list devices and their last connection
  ott alias NAME UUID                name an existing device
  ott add NAME UUID                  create a device access code and queue
  ott pair NAME                      show the player connection settings
  ott discover                       show controllers advertised in network DNS
  ott pending                        list pending player pairing requests
  ott approve NAME CODE              approve the code displayed by that player
  ott NAME                           show player status
  ott NAME load PRESET               apply a private Plex, M3U and optional Stalker preset
  ott NAME 12                        play channel 12 from the s listing
  ott NAME TITLE                     search channels, current EPG, then archives within 144 hours
  ott NAME play s                    play a channel whose name is reserved
  ott NAME s [TEXT]                  list channels, optionally matching TEXT
  ott NAME p                         list channel — current programme
  ott NAME p TEXT                    search current programmes, then available archives
  ott NAME p --list [TEXT]           list programmes without switching channels
  ott NAME vp TEXT                   loop VPortal videos whose titles match TEXT
  ott NAME vpr TEXT                  shuffle matching VPortal videos and loop the queue
  ott NAME vp --list TEXT            list matching VPortal videos without playing
  ott NAME v                         show volume
  ott NAME v 35                      set volume to 35%
  ott NAME v +5 / v -5               increase / decrease volume
  ott NAME providers                 list providers (zero-based indices)
  ott NAME provider m3u              select a provider by ID, index or name
  ott NAME provider-config FILE      update active provider settings from JSON
  ott NAME plex setup URL [TOKEN]    set Plex address and token (hidden prompt if omitted)
  ott NAME plex setup URL --token-file FILE  set Plex address and token from a file
  ott NAME plex server URL           update the saved Plex server address
  ott NAME plex token [TOKEN]        update the Plex token (hidden prompt if omitted)
  ott NAME plex token-file FILE      update the Plex token from a file
  ott NAME playlist URL              update the M3U playlist
  ott NAME profiles                  list the 15 M3U profiles without URLs
  ott NAME profile N                 select M3U profile 1–15
  ott NAME profile N url URL         change a profile's playlist URL
  ott NAME profile N history HOURS   set archive depth in hours (0–8760)
  ott NAME profile N vportal LINK    set a profile's VPortal link
  ott NAME profile N name NAME       rename a profile
  ott NAME profile-config N FILE     update profile settings atomically from JSON
  ott NAME restart [stream|player]   restart the stream (default) or reload the player
  ott NAME random [FROM TO]          play a random channel
  ott NAME msg TEXT                  show an on-screen message
  ott NAME exit                      close the player / enter standby

Configuration: ~/.config/ottplay-control/cli.json or OTT_CONFIG.
Player, channel, programme, VPortal and provider searches are case-insensitive.
"""


class Error(Exception):
    pass


class PlayerRejected(Error):
    """A completed request explicitly rejected by the player, with its metadata."""
    def __init__(self, message, data):
        super().__init__(message)
        self.data = data


class PlayerUnsupported(Error):
    pass


class TransportError(Error):
    pass


class HTTPError(Error):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Server returned HTTP {code}. " + {
            401: "Check admin_token in the private configuration.",
            403: "Insufficient permissions or a disallowed Origin.",
            404: "Check the address and update the server to support CLI requests.",
            429: "The queue or request limit has been reached; try again later.",
        }.get(code, "Check the request parameters."))


class EpgClient:
    """Public EPG transport, deliberately independent of controller credentials."""
    def __init__(self, settings, timeout):
        if (not isinstance(settings, dict) or set(settings) != {"url", "source"}
                or settings["source"] != "epg-one" or not isinstance(settings["url"], str)):
            raise Error('EPG configuration must contain url and source "epg-one"')
        url = settings["url"]
        try:
            parsed = urllib.parse.urlsplit(url)
            invalid = (parsed.scheme not in ("http", "https") or not parsed.hostname
                       or parsed.username is not None or parsed.password is not None
                       or "?" in url or "#" in url or "\\" in url
                       or any(ord(char) <= 32 or ord(char) == 127 for char in url)
                       or parsed.port == 0)
        except ValueError:
            invalid = True
        if invalid:
            raise Error("EPG url must be an HTTP(S) address without credentials, whitespace, query or fragment")
        self.url = url.rstrip("/") + "/current"
        self.timeout = min(10, timeout)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect())

    def current(self, channels, search):
        payload = {"version": 1, "source": "epg-one", "channels": channels, "search": search}
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > 512 * 1024:
            raise Error("EPG catalogue exceeds the request size limit; no playback was requested")
        deadline = time.monotonic() + self.timeout
        cancelled = threading.Event()
        outcome = queue.Queue(maxsize=1)
        def perform():
            try:
                outcome.put((True, self._current(body, deadline, cancelled)))
            except Exception as exc:
                outcome.put((False, exc))
        threading.Thread(target=perform, name="ottplay-epg-http", daemon=True).start()
        try:
            ok, value = outcome.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            cancelled.set()
            raise Error("EPG service timed out; no player EPG scan or playback was requested") from None
        if time.monotonic() >= deadline:
            cancelled.set()
            raise Error("EPG service timed out; no player EPG scan or playback was requested")
        if not ok:
            raise value
        return value

    def _current(self, body, deadline, cancelled):
        request = urllib.request.Request(self.url, data=body, headers={
            "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ottplay-cli/1.0"})
        try:
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise Error("EPG service timed out")
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise Error("EPG service did not return a successful response")
                body = bytearray()
                while True:
                    if cancelled.is_set() or time.monotonic() >= deadline:
                        raise Error("EPG service timed out")
                    chunk = response.read1(min(65536, 2 * 1024 * 1024 + 1 - len(body)))
                    if not chunk:
                        remaining = getattr(response, "length", None)
                        if isinstance(remaining, int) and remaining > 0:
                            raise Error("EPG service returned an incomplete response")
                        return json.loads(body)
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise Error("EPG service response exceeds the size limit")
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise Error(f"EPG service returned HTTP {code}; no player EPG scan or playback was requested") from None
        except (OSError, http.client.HTTPException):
            raise Error("EPG service unavailable; no player EPG scan or playback was requested") from None
        except ValueError:
            raise Error("EPG service returned invalid JSON") from None


def epg_text(value, maximum, encoding="utf-16-le"):
    if not isinstance(value, str):
        return False
    try:
        return len(value.encode(encoding)) <= maximum * (2 if encoding == "utf-16-le" else 1)
    except UnicodeEncodeError:
        return False


def epg_number(value):
    return type(value) in (int, float) and -9007199254740991 <= value <= 9007199254740991


def epg_batches(channels):
    """Keep every channel while respecting each public EPG request's limits."""
    batch, size = [], 2048  # Envelope and the bounded search text.
    for channel in channels:
        row = {key: value for key, value in channel.items() if key != "number"}
        row_size = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if batch and (len(batch) == 2048 or size + row_size > 480 * 1024):
            yield batch
            batch, size = [], 2048
        batch.append(row)
        size += row_size
    if batch:
        yield batch


def expired_epg_catalog(error):
    return (isinstance(error, PlayerRejected)
            and error.data.get("error") == "Channels or provider changed. Retry the EPG query before playing.")


def server_programs(client, device, settings, search, refresh=False):
    epg = EpgClient(settings, client.timeout)
    if not epg_text(search, 1024, "utf-8"):
        raise Error("Programme search must contain at most 1024 UTF-8 bytes")
    snapshot = client.call(device, "epg_catalog", {})
    invalid = "The player returned an invalid EPG catalogue; no playback was requested"
    if (not isinstance(snapshot, dict) or not epg_text(snapshot.get("catalog"), 128, "utf-8")
            or not snapshot["catalog"].strip() or not isinstance(snapshot.get("channels"), list)
            or len(snapshot["channels"]) > 10000):
        raise Error(invalid)
    channels, by_id, numbers = [], {}, set()
    for row in snapshot["channels"]:
        if (not isinstance(row, dict) or not all(epg_text(row.get(key), 512) for key in ("id", "name", "tvgId", "tvgName"))
                or not row["id"].strip() or row["id"] in by_id or type(row.get("number")) is not int
                or row["number"] < 1 or row["number"] in numbers or type(row.get("shift")) is not int
                or not -86400 <= row["shift"] <= 86400):
            raise Error(invalid)
        if numbers and row["number"] <= channels[-1]["number"]:
            raise Error(invalid)
        channels.append({key: row[key] for key in ("id", "number", "name", "tvgId", "tvgName", "shift")})
        by_id[row["id"]] = channels[-1]
        numbers.add(row["number"])
    if not channels:
        return {"as_of": time.time(), "checked": 0, "partial": False, "programs": [], "total": 0}, {}
    programs, targets, generation, as_of = [], {}, None, 0
    search_deadline = time.monotonic() + client.timeout
    for batch in epg_batches(channels):
        batch_by_id = {row["id"]: by_id[row["id"]] for row in batch}
        remaining = search_deadline - time.monotonic()
        if remaining <= 0:
            raise Error("EPG service timed out; no player EPG scan or playback was requested")
        epg.timeout = min(10, remaining)
        response = epg.current(batch, search)
        if time.monotonic() >= search_deadline:
            raise Error("EPG service timed out; no player EPG scan or playback was requested")
        invalid = "EPG service returned an invalid or incomplete current-programme result; no playback was requested"
        if (not isinstance(response, dict) or type(response.get("version")) is not int or response["version"] != 1
                or response.get("source") != "epg-one" or not epg_text(response.get("generation"), 256)
                or not response["generation"] or type(response.get("fetchedAt")) is not int
                or not 0 < response["fetchedAt"] <= 9007199254740991 or not epg_number(response.get("asOf"))
                or abs(response["asOf"] - time.time()) > 60
                or type(response.get("checked")) is not int or response["checked"] != len(batch)
                or type(response.get("total")) is not int or response["total"] != len(batch)
                or not isinstance(response.get("programs"), list) or len(response["programs"]) > len(batch)):
            raise Error(invalid)
        if response.get("stale") is not False:
            raise Error("EPG service has no fresh guide snapshot; no playback was requested")
        last_number = 0
        for row in response["programs"]:
            if (not isinstance(row, dict) or not isinstance(row.get("id"), str) or row["id"] not in batch_by_id
                    or not epg_text(row.get("title"), 16384) or not row["title"]
                    or any(not epg_number(row.get(key)) for key in ("start", "end"))
                    or not row["start"] <= response["asOf"] < row["end"]):
                raise Error(invalid)
            channel = by_id[row["id"]]
            if channel["number"] <= last_number:
                raise Error(invalid)
            last_number = channel["number"]
            programs.append({"channel": channel["name"], "number": channel["number"], "title": row["title"],
                             "start": row["start"], "end": row["end"]})
            targets[channel["number"]] = {"catalog": snapshot["catalog"], "id": row["id"]}
        if generation is not None and response["generation"] != generation:
            raise Error("EPG guide changed during search; retry the query. No playback was requested")
        generation = response["generation"]
        as_of = max(as_of, response["asOf"])
    programs = [row for row in programs if row["start"] <= as_of < row["end"]]
    if not programs and search.strip():
        spec = importlib.util.spec_from_file_location("ott_programme_search", Path(__file__).resolve().with_name("programme_search.py"))
        history = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(history)
        try:
            return history.search_archives(client, device, settings, snapshot, search, refresh,
                                           catalog_expired=expired_epg_catalog)
        except history.SearchError as exc:
            raise Error(str(exc)) from None
    return {"as_of": as_of, "checked": len(channels), "partial": False,
            "programs": programs, "total": len(channels)}, targets


def read_json(path):
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate configuration field")
            result[key] = value
        return result
    try:
        with Path(path).expanduser().open(encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=unique_fields)
    except (OSError, ValueError) as exc:
        raise Error(f"Could not read JSON file {path}") from exc


def write_private(path, data, exclusive=False):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".ott-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        if exclusive:
            # Reserve a provisioning journal without replacing another operation.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Client:
    def __init__(self, config, timeout=45):
        self.config = config
        self.timeout = timeout
        self.server = config["server"].rstrip("/")
        parsed = urllib.parse.urlsplit(self.server)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise Error("server must be an HTTP(S) address without credentials, query or fragment")
        self.credentials = read_json(config["server_config"])
        self.token = self.credentials["admin_token"]
        # Never forward an administrator credential through an HTTP redirect.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(NoRedirect())

    def api(self, path, payload=None, timeout=None):
        budget = min(10, self.timeout if timeout is None else timeout)
        deadline = time.monotonic() + budget
        cancelled = threading.Event()
        outcome = queue.Queue(maxsize=1)

        def perform():
            try:
                outcome.put((True, self._api(path, payload, budget, deadline, cancelled)))
            except Exception as exc:
                outcome.put((False, exc))

        # Socket timeouts alone are idle timeouts: trickling bytes (or DNS) can
        # exceed them. Bound the caller separately and never replay this POST.
        threading.Thread(target=perform, name="ottplay-http", daemon=True).start()
        try:
            ok, value = outcome.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            cancelled.set()
            raise TransportError("Timed out waiting for the server response") from None
        if time.monotonic() >= deadline:
            cancelled.set()
            raise TransportError("Timed out waiting for the server response")
        if not ok:
            raise value
        return value

    def _api(self, path, payload, budget, deadline, cancelled):
        headers = {"Authorization": "Bearer " + self.token, "User-Agent": "ottplay-cli/1.0"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.server + path, data=body, headers=headers)
        try:
            if cancelled.is_set() or time.monotonic() >= deadline:
                raise TransportError("Timed out waiting for the server response")
            with self.opener.open(request, timeout=budget) as response:
                body = bytearray()
                while True:
                    if cancelled.is_set() or time.monotonic() >= deadline:
                        raise TransportError("Timed out waiting for the server response")
                    # read1 returns available bytes instead of waiting for the
                    # entire body, letting a timed-out trickle worker retire.
                    chunk = response.read1(min(65536, 2 * 1024 * 1024 + 1 - len(body)))
                    if not chunk:
                        # Unlike read(), read1() can return EOF before the
                        # advertised Content-Length without IncompleteRead.
                        remaining = getattr(response, "length", None)
                        if isinstance(remaining, int) and remaining > 0:
                            raise TransportError("The server closed the connection before sending the complete response")
                        return response.status, json.loads(body)
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise Error("The server response exceeds the size limit")
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise HTTPError(code) from None
        except (OSError, http.client.HTTPException) as exc:
            raise TransportError("Server unavailable; check the address and connection") from exc
        except ValueError as exc:
            raise Error("The server returned invalid JSON") from exc

    def device(self, name):
        aliases = self.config.get("players", {})
        matches = [value for key, value in aliases.items() if key.casefold() == name.casefold()]
        if len(matches) == 1:
            return matches[0]
        ids = [row["id"] for row in self.credentials.get("devices", []) if row["id"].casefold() == name.casefold()]
        if len(ids) == 1:
            return ids[0]
        raise Error(f"Unknown player {name!r}. Run ott devices or ott add NAME UUID")

    def call(self, device, action, params):
        query = "?" + urllib.parse.urlencode({"device_id": device})
        deadline = time.monotonic() + self.timeout
        try:
            _, queued = self.api("/api/requests" + query, {"action": action, "params": params}, timeout=self.timeout)
        except Error as exc:
            # A lost POST response cannot prove whether the mutation was queued.
            # Replaying it would give a relative-volume command a second ID.
            if isinstance(exc, HTTPError) and exc.code < 500:
                raise
            raise Error(str(exc) + ". The request may have been accepted; do not repeat the change blindly.") from exc
        if not isinstance(queued, dict) or not isinstance(queued.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", queued["id"]):
            raise Error("The server did not return a request ID. The request may have been accepted; do not repeat the change blindly.")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.8, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                status, result = self.api("/api/requests" + query + "&id=" + queued["id"], timeout=remaining)
            except TransportError:
                continue
            except HTTPError as exc:
                if exc.code in (502, 503, 504):
                    continue
                if exc.code == 404:
                    raise Error("The request receipt has expired or is missing. The request may have been executed; do not repeat the change blindly.") from exc
                raise Error(f"The request receipt could not be read (HTTP {exc.code}). The request may have been executed; do not repeat the change blindly.") from exc
            except Error as exc:
                raise Error(str(exc) + ". The request may have been executed; do not repeat the change blindly.") from exc
            if time.monotonic() >= deadline:
                break
            if status == 202:
                if not isinstance(result, dict) or result.get("status") != "pending":
                    raise Error("The server returned an invalid pending status. The request may have been executed; do not repeat the change blindly.")
                continue
            if status != 200 or not isinstance(result, dict) or result.get("status") not in ("ok", "rejected", "unsupported") or not isinstance(result.get("data"), dict):
                raise Error("The server returned an invalid player response. The request may have been executed; do not repeat the change blindly.")
            if result.get("status") != "ok":
                data = result["data"]
                matches = data.get("matches", [])
                if not isinstance(matches, list) or not all(isinstance(row, dict) for row in matches):
                    raise Error("The player rejected the request but returned an invalid match list")
                details = "\n".join(f"{row.get('number', row.get('index', ''))}: {clean(row.get('name', ''))}" for row in matches)
                message = clean(data.get("error", "The player rejected the request")) + ("\n" + details if details else "")
                if result["status"] == "rejected":
                    raise PlayerRejected(message, data)
                raise PlayerUnsupported(message)
            return result["data"]
        raise Error("The player did not respond. Open it, check the address/access code and use a version that supports the CLI. The request may still execute before its TTL expires; do not repeat the change blindly.")


def clean(value):
    # Provider-controlled names must not send terminal control/escape sequences.
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))


def channel_identity(value):
    # Legacy channel IDs can be strings or JavaScript safe integers.
    if isinstance(value, str) and value and epg_text(value, 512):
        return value
    if type(value) is int and -9007199254740991 <= value <= 9007199254740991:
        return str(value)
    return None


def play_channel(client, device, params):
    text = params["query"].strip()
    if not text or re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", text):
        return client.call(device, "play", params), None
    response = client.call(device, "channels", {"search": text})
    matches = response.get("channels") if isinstance(response, dict) else None
    invalid = "The player returned an invalid channel list; no switch was requested"
    if not isinstance(matches, list):
        raise Error(invalid)
    previous, identities, rows = 0, set(), []
    for row in matches:
        if not isinstance(row, dict):
            raise Error(invalid)
        number, name = row.get("number"), row.get("name")
        identity = channel_identity(row.get("id"))
        if (type(number) is not int or not previous < number <= 9007199254740991
                or identity is None or identity in identities
                or not epg_text(name, 16384) or not name.strip()
                or text.casefold() not in name.casefold()):
            raise Error(invalid)
        previous = number
        identities.add(identity)
        rows.append({key: row[key] for key in ("id", "number", "name")})
    data = {"channels": rows}
    if not rows:
        error = Error("No channels match the search; no switch was requested")
        data["playback"] = {"error": str(error)}
        return data, error
    selected = secrets.choice(rows) if len(rows) > 1 else rows[0]
    try:
        # One mutation follows the complete read-only search. Never replay it.
        result = client.call(device, "play", {"query": str(selected["number"])})
        channel = result.get("channel") if isinstance(result, dict) else None
        if (not isinstance(channel, dict) or result.get("dispatched") is not True
                or type(channel.get("number")) is not int or channel["number"] != selected["number"]
                or channel_identity(channel.get("id")) != channel_identity(selected["id"])
                or channel.get("name") != selected["name"]):
            raise Error("The player did not confirm the requested channel switch. The request may have executed; do not repeat the change blindly.")
        data["playback"] = {"dispatched": True, "channel": {key: channel[key] for key in ("id", "number", "name")}}
        return data, None
    except Error as exc:
        data["playback"] = {"error": str(exc)}
        return data, exc


def select_programme(programs):
    # Validate every candidate before drawing: the random row may be any row.
    invalid = "The player returned an invalid programme list; no switch was requested"
    if not isinstance(programs, list):
        raise Error(invalid)
    previous = (0, 0)
    for row in programs:
        order = (row.get("number", 0), row.get("start", 0) if row.get("mode") == "archive" else 0) if isinstance(row, dict) else (0, 0)
        if (not isinstance(row, dict) or type(row.get("number")) is not int
                or not 0 < row["number"] <= 9007199254740991
                or type(order[1]) not in (int, float) or not previous < order
                or (row.get("mode") == "archive" and
                    (type(row.get("start")) is not int or type(row.get("end")) is not int
                     or not time.time() - 144 * 3600 <= row["start"] < row["end"] <= time.time()))
                or any(not epg_text(row.get(key), 16384) or not row[key].strip()
                       for key in ("channel", "title"))):
            raise Error(invalid)
        previous = order
    return (secrets.choice(programs) if len(programs) > 1 else programs[0]) if programs else None


def parse_command(words):
    if not words:
        return "status", {}
    verb, tail = words[0].casefold(), words[1:]
    text = " ".join(tail)
    if verb == "load":
        if len(tail) != 1 or not preset_name(tail[0]):
            raise Error("Use load PRESET with a short preset name")
        return "load", {"preset": tail[0]}
    if verb == "profiles":
        if tail:
            raise Error("Use profiles without arguments")
        return "profiles", {}
    if verb in ("profile", "profile-config"):
        if not tail or not re.fullmatch(r"[1-9]|1[0-5]", tail[0]):
            raise Error("Profile number must be an integer from 1 to 15")
        number = int(tail[0])
        if verb == "profile-config":
            if len(tail) != 2:
                raise Error("Use profile-config N FILE.json")
            settings = read_profile_settings(tail[1])
        elif len(tail) == 1:
            return "profile", {"number": number}
        else:
            field = {"url": "playlist", "history": "history_hours", "vportal": "vportal", "name": "name"}.get(tail[1].casefold())
            if field is None or len(tail) < 3 or (field != "name" and len(tail) != 3):
                raise Error("Use profile N [url URL | history HOURS | vportal LINK | name NAME]")
            value = " ".join(tail[2:])
            if field == "history_hours":
                if not re.fullmatch(r"0|[1-9][0-9]{0,3}", value):
                    raise Error("Profile history must be an integer from 0 to 8760 hours")
                value = int(value)
            settings = {field: value}
        validate_profile_settings(settings)
        params = {"number": number, "settings": settings}
        if len(json.dumps({"action": "profile_settings", "params": params}, ensure_ascii=False).encode("utf-8")) > 16 * 1024:
            raise Error("Profile settings exceed the 16 KiB request limit")
        return "profile_settings", params
    if verb == "restart":
        target = tail[0].casefold() if len(tail) == 1 else "stream"
        if len(tail) > 1 or target not in ("stream", "player"):
            raise Error("Use restart, restart stream or restart player")
        return "restart", {"target": target}
    if verb in ("vp", "vpr"):
        listing = tail[:1] == ["--list"]
        text = " ".join(tail[1:] if listing else tail).strip()
        try:
            length = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            raise Error("VPortal search must be valid UTF-8 text") from None
        if not 1 <= length <= 1024:
            raise Error("Use vp TEXT, vpr TEXT or vp --list TEXT; the search must contain 1–1024 UTF-8 bytes")
        action = "vportal_search" if listing else "vportal_random" if verb == "vpr" else "vportal"
        return action, {"query": text}
    if verb in ("s", "p"):
        if verb == "p":
            text = " ".join(tail[1:] if tail[:1] == ["--list"] else tail).strip()
        return ("channels" if verb == "s" else "programs"), {"search": text}
    if verb == "v":
        if not tail:
            return "status", {}
        if len(tail) != 1 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
            raise Error("Use v, v 35, v +5 or v -5")
        value = float(text)
        relative = text.startswith(("+", "-"))
        if not math.isfinite(value) or (not relative and not 0 <= value <= 100):
            raise Error("Absolute volume must be between 0 and 100")
        return "command", {"command": "set_volume", "volume_step" if relative else "volume": value}
    if verb in ("status", "providers") and not tail:
        return verb, {}
    if verb == "provider" and tail:
        return "provider", {"query": text}
    if verb == "provider-config":
        if len(tail) != 1:
            raise Error("Use provider-config FILE.json")
        data = read_settings_json(tail[0], "provider")
        if not isinstance(data, dict) or set(data) != {"provider", "settings"} or not isinstance(data["settings"], dict):
            raise Error('Expected {"provider":"xtream","settings":{...}}')
        if data["provider"] == "plex":
            data["settings"] = validate_plex_settings(data["settings"])
        return "provider_settings", data
    if verb == "plex":
        return "provider_settings", {"provider": "plex", "settings": parse_plex_settings(tail)}
    if verb == "playlist" and len(tail) == 1:
        return "provider_settings", {"provider": "m3u", "settings": {"playlist": text}}
    if verb == "msg" and tail:
        return "command", {"command": "popup_message", "message": text}
    if verb == "exit" and not tail:
        return "command", {"command": "exit_player"}
    if verb == "random":
        params = {"command": "random_channel"}
        if tail:
            if len(tail) != 2 or not all(re.fullmatch(r"[1-9]\d*", x) for x in tail) or int(tail[0]) > int(tail[1]):
                raise Error("Use random or random FROM TO (one-based channel numbers)")
            params["random_range"] = [int(x) for x in tail]
        return "command", params
    if verb == "play":
        if not tail:
            raise Error("play requires a channel number or name")
        return "play", {"query": text}
    return "play", {"query": " ".join(words)}


def read_settings_json(path, kind):
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate setting")
            result[key] = value
        return result
    try:
        with Path(path).expanduser().open(encoding="utf-8") as stream:
            raw = stream.read(16 * 1024 + 1)
        if len(raw.encode("utf-8")) > 16 * 1024:
            raise ValueError("Oversized settings")
        return json.loads(raw, object_pairs_hook=unique_fields)
    except (OSError, ValueError):
        raise Error(f"Could not read {kind} settings: use a valid JSON object without duplicate fields, at most 16 KiB") from None


def read_profile_settings(path):
    return read_settings_json(path, "profile")


def plex_token(path=None):
    if path is not None:
        try:
            with Path(path).expanduser().open(encoding="utf-8") as stream:
                token = stream.read(4097)
            if len(token.encode("utf-8")) > 4096:
                raise ValueError()
            return token
        except (OSError, ValueError):
            raise Error("Could not read Plex token file: use a UTF-8 file containing one token") from None
    try:
        # Refuse getpass's echoing fallback on terminals without hidden input.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass("Plex token: ")
    except (EOFError, OSError, getpass.GetPassWarning):
        raise Error("Hidden token input is unavailable; use a token file or provider-config JSON") from None


def parse_plex_settings(words):
    command = words[0].casefold() if words else ""
    values = words[1:]
    if command == "setup" and (len(values) == 1 or (len(values) == 2 and values[1] != "--token-file")
                               or (len(values) == 3 and values[1] == "--token-file")):
        # Validate the address before asking for a credential.
        settings = validate_plex_settings({"server": values[0]})
        settings["token"] = (plex_token(values[2]) if len(values) == 3 else
                             values[1] if len(values) == 2 else plex_token())
    elif command == "server" and len(values) == 1:
        settings = {"server": values[0]}
    elif command == "token" and len(values) <= 1:
        settings = {"token": values[0] if values else plex_token()}
    elif command == "token-file" and len(values) == 1:
        settings = {"token": plex_token(values[0])}
    else:
        raise Error("Use plex setup URL [TOKEN|--token-file FILE], plex server URL, plex token [TOKEN], or plex token-file FILE")
    return validate_plex_settings(settings)


def validate_plex_settings(settings):
    if not isinstance(settings, dict) or not settings or set(settings) - {"server", "token"}:
        raise Error("Plex settings must contain server and/or token")
    result = {}
    for key, value in settings.items():
        if not isinstance(value, str):
            raise Error("Plex settings must contain text values")
        value = value.strip()
        if not epg_text(value, 8192 if key == "server" else 1024):
            raise Error("Plex settings must be text: server up to 8192 characters, token up to 1024 characters")
        if key == "server":
            value = value.rstrip("/")
            try:
                parsed = urllib.parse.urlsplit(value)
                valid = (re.fullmatch(r"https?://(?:\[[0-9a-f:.]+\]|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::[0-9]{1,5})?(?:/[a-z0-9_./~-]*)?", value, re.I)
                         and not re.search(r"(?:^|/)\.\.(?:/|$)", value)
                         and parsed.hostname and (parsed.port is None or 1 <= parsed.port <= 65535))
            except ValueError:
                valid = False
            if not valid:
                raise Error("Use a Plex HTTP(S) server address without credentials, query, fragment or parent path segments")
        elif not value or re.search(r"[\s\x00-\x1f\x7f]", value):
            raise Error("Plex token must be nonempty and contain no whitespace or control characters")
        result[key] = value
    return result


def plex_settings_metadata(data, settings):
    if (not isinstance(data, dict) or data.get("provider") != "plex" or data.get("saved") is not True
            or not isinstance(data.get("fields"), list) or len(data["fields"]) != len(settings)
            or any(not isinstance(field, str) for field in data["fields"])
            or set(data["fields"]) != set(settings)):
        raise Error("The player did not confirm saving Plex settings. The request may have executed; do not repeat the change blindly.")
    return {"provider": "plex", "saved": True, "fields": data["fields"]}


def validate_profile_settings(settings):
    if (not isinstance(settings, dict) or not settings
            or set(settings) - {"name", "playlist", "history_hours", "vportal"}):
        raise Error("Profile settings must contain only name, playlist, history_hours or vportal, with at least one field")
    for key, value in settings.items():
        if key == "history_hours":
            if type(value) is not int or not 0 <= value <= 8760:
                raise Error("Profile history must be an integer from 0 to 8760 hours")
        elif (not epg_text(value, 256 if key == "name" else 8192, "utf-8")
              or any(ord(character) < 32 or ord(character) == 127 for character in value)):
            raise Error("Profile settings must be UTF-8 text without control characters: name up to 256 bytes, playlist and VPortal links up to 8192 bytes")


def profile_metadata(data, action, params):
    message = "The player returned invalid M3U profile metadata"
    if action != "profiles":
        message += ". The request may have executed; do not repeat the change blindly."
    def fail():
        raise Error(message)
    def profile(row):
        if (not isinstance(row, dict) or type(row.get("number")) is not int or not 1 <= row["number"] <= 15
                or not epg_text(row.get("name"), 256, "utf-8")
                or any(type(row.get(key)) is not bool for key in ("active", "playlist_configured", "vportal_configured"))
                or "history_hours" not in row or (row["history_hours"] is not None
                    and (type(row["history_hours"]) is not int or not 0 <= row["history_hours"] <= 8760))):
            fail()
        return {key: row[key] for key in ("number", "name", "active", "history_hours", "playlist_configured", "vportal_configured")}
    if not isinstance(data, dict) or data.get("provider") != "m3u":
        fail()
    if action == "profiles":
        if not isinstance(data.get("profiles"), list) or len(data["profiles"]) != 15:
            fail()
        rows = [profile(row) for row in data["profiles"]]
        if any(row["number"] != number for number, row in enumerate(rows, 1)) or sum(row["active"] for row in rows) != 1:
            fail()
        return {"provider": "m3u", "profiles": rows}
    row = profile(data.get("profile"))
    if row["number"] != params["number"]:
        fail()
    if action == "profile":
        if data.get("dispatched") is not True or row["active"] is not True:
            fail()
        return {"provider": "m3u", "profile": row, "dispatched": True}
    if data.get("saved") is not True:
        fail()
    for key, value in params["settings"].items():
        if key in ("name", "history_hours"):
            if row[key] != value:
                fail()
        elif row[key + "_configured"] != bool(value):
            fail()
    return {"provider": "m3u", "profile": row, "saved": True}


def preset_name(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}", value)


def preset_names(config):
    presets = config.get("presets", {}) if isinstance(config, dict) else None
    if not isinstance(presets, dict) or any(not preset_name(name) for name in presets):
        raise Error("Presets must be a mapping of short names using letters, digits, underscores or hyphens")
    if len({name.casefold() for name in presets}) != len(presets):
        raise Error("Preset names must be unique ignoring case")
    return sorted(presets, key=str.casefold)


def preset_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname or ""
        if ":" not in host:
            host = urllib.parse.unquote(host, errors="strict")
            valid_host = not any(ord(char) <= 32 or ord(char) == 127 or char in "#%/:<>?@[\\]^|" for char in host)
            valid_host = valid_host and not parsed.netloc.startswith("[")
            # Browsers interpret a numeric final label as IPv4, even in host.123.
            # Require the unambiguous four-decimal-octet form for preset URLs.
            numeric_host = host.rstrip(".")
            if re.fullmatch(r"(?:[0-9]+|0x[0-9a-f]*)", numeric_host.rsplit(".", 1)[-1], re.I):
                ipaddress.IPv4Address(numeric_host)
        else:
            # urlsplit validates IPv6 brackets; browser URLs do not allow zone IDs.
            valid_host = "%" not in host
        return (bool(re.match(r"https?://", value, re.I)) and parsed.scheme in ("http", "https")
                and bool(host) and valid_host and parsed.username is None and parsed.password is None
                and not re.search(r"[\\\s\x00-\x1f\x7f]", value)
                and (parsed.port is None or 1 <= parsed.port <= 65535))
    except (TypeError, ValueError):
        return False


def preset_vportal(value):
    # Match the player's cabinet-link grammar without decoding the key or URL.
    match = re.fullmatch(r'portal::(?:\[|%5b)key:([^\[\]\s<>"\\]{1,1024}?)(?:\]|%5d)(https?://[^\s<>"\\]+)',
                         value.strip(), re.I)
    return bool(match and epg_text(match[1], 1024) and re.fullmatch(
        r'https?://(?:\[[0-9a-f:.]+\]|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::[0-9]{1,5})?(?:[/?][^#]*)?',
        match[2], re.I) and preset_url(match[2]))


def validate_preset(config, requested):
    names = preset_names(config)
    if not preset_name(requested):
        raise Error("Invalid preset name")
    matches = [name for name in names if name.casefold() == requested.casefold()]
    if len(matches) != 1:
        raise Error("Unknown preset; use ott presets to list local names")
    name = matches[0]
    value = config["presets"][name]
    if (not isinstance(value, dict) or not {"m3u", "plex", "active_profile"} <= set(value)
            or set(value) - {"m3u", "plex", "active_profile", "stalker"}):
        raise Error("A preset must contain m3u, plex and active_profile, with optional stalker profiles")
    if not isinstance(value["m3u"], list) or not 1 <= len(value["m3u"]) <= 15:
        raise Error("A preset must contain 1–15 complete M3U profiles")
    profiles = []
    numbers = set()
    for row in value["m3u"]:
        if (not isinstance(row, dict) or set(row) != {"number", "name", "playlist", "history_hours", "vportal"}
                or type(row["number"]) is not int or not 1 <= row["number"] <= 15 or row["number"] in numbers):
            raise Error("Preset M3U profiles require complete fields and unique numbers from 1 to 15")
        settings = {key: row[key] for key in ("name", "playlist", "history_hours", "vportal")}
        validate_profile_settings(settings)
        if not preset_url(settings["playlist"]) or (settings["vportal"] and not preset_vportal(settings["vportal"])):
            raise Error("Preset profiles require an HTTP(S) playlist and a complete VPortal cabinet link")
        profiles.append({"number": row["number"], "settings": settings})
        numbers.add(row["number"])
    active = value["active_profile"]
    if type(active) is not int or active not in numbers:
        raise Error("Preset active_profile must name one of its M3U profiles")
    if not isinstance(value["plex"], dict) or set(value["plex"]) != {"server", "token"}:
        raise Error("A preset requires both Plex server and token")
    plex = validate_plex_settings(value["plex"])
    if not preset_url(plex["server"]):
        raise Error("Preset Plex server must be a valid HTTP(S) address")
    stalker = []
    if "stalker" in value:
        if not isinstance(value["stalker"], list) or not 1 <= len(value["stalker"]) <= 15:
            raise Error("A Stalker preset requires 1–15 complete profiles")
        seen = set()
        for row in value["stalker"]:
            if (not isinstance(row, dict) or set(row) != {"number", "name", "server", "mac"}
                    or type(row["number"]) is not int or not 1 <= row["number"] <= 15 or row["number"] in seen
                    or not epg_text(row["name"], 256, "utf-8") or re.search(r"[\x00-\x1f\x7f]", row["name"])
                    or not epg_text(row["server"], 8192, "utf-8")
                    or not preset_url(row["server"]) or not isinstance(row["mac"], str)
                    or not re.fullmatch(r"(?:[a-f0-9]{2}:){5}[a-f0-9]{2}", row["mac"], re.I)):
                raise Error("Stalker profiles require unique numbers 1–15, a name, HTTP(S) server and MAC")
            seen.add(row["number"])
            stalker.append({"profile": row["number"], **{key: row[key] for key in ("name", "server", "mac")}})
        stalker.sort(key=lambda row: row["profile"])
    requests = [("provider_settings", {"provider": "plex", "settings": plex})]
    requests.extend(("profile_settings", row) for row in profiles)
    requests.extend(("provider_settings", {"provider": "stalker", "settings": row}) for row in stalker)
    for action, params in requests:
        if len(json.dumps({"action": action, "params": params}, ensure_ascii=False).encode("utf-8")) > 16 * 1024:
            raise Error("A preset settings request exceeds the 16 KiB limit")
    result = {"m3u": sorted(profiles, key=lambda row: row["number"]), "plex": plex, "active_profile": active}
    if stalker:
        result["stalker"] = stalker
    return name, result


def load_preset(client, device, name, preset):
    """Apply acknowledged steps; repeat only proven pre-write mount rejections."""
    completed = []
    stage = "start"
    budget = client.timeout

    def step(label, action, params, validate, retry_errors=()):
        nonlocal stage
        stage = label
        deadline = time.monotonic() + budget
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Error("Preset phase timed out")
            pending = False
            try:
                client.timeout = remaining
                data = client.call(device, action, params)
            except PlayerRejected as exc:
                if not isinstance(exc.data, dict) or exc.data.get("error") not in retry_errors:
                    raise
                pending = True
            finally:
                client.timeout = budget
            if time.monotonic() >= deadline:
                raise Error("Preset phase timed out")
            if not pending:
                result = validate(data)
                if result is not None:
                    completed.append(label)
                    return result
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))

    def provider(expected):
        def validate(data):
            if not isinstance(data, dict) or data.get("dispatched") is not True or data.get("provider") != expected:
                raise Error("Provider switch was not confirmed")
            return True
        return validate

    def plex_status(data):
        if not isinstance(data, dict) or not isinstance(data.get("provider"), str):
            raise Error("Invalid provider status")
        # This may report the selected ID before the driver mounts. Never wait for
        # ready: a Plex credential editor or empty catalogue is still configurable.
        return True if data["provider"] == "plex" else None

    def profiles(data):
        return profile_metadata(data, "profiles", {})["profiles"]

    def save_profile(row, active):
        def validate(data):
            result = profile_metadata(data, "profile_settings", row)
            if result["profile"]["active"] != (row["number"] == active):
                raise Error("The active profile changed during loading")
            return True
        step("save_profile_" + str(row["number"]), "profile_settings", row, validate)

    try:
        if preset.get("stalker"):
            step("select_stalker", "provider", {"query": "stalker"}, provider("stalker"))
            for row in preset["stalker"]:
                def confirmed(data):
                    if (not isinstance(data, dict) or data.get("provider") != "stalker" or data.get("saved") is not True
                            or type(data.get("profile")) is not int or data["profile"] != row["profile"]
                            or not isinstance(data.get("fields"), list) or len(data["fields"]) != 4
                            or set(data["fields"]) != {"profile", "name", "server", "mac"}):
                        raise Error("Stalker profile save was not confirmed")
                    return True
                step("save_stalker_" + str(row["profile"]), "provider_settings",
                     {"provider": "stalker", "settings": row}, confirmed,
                     ("Select this provider before changing its settings.",))
        step("select_plex", "provider", {"query": "plex"}, provider("plex"))
        step("wait_plex", "status", {}, plex_status)
        settings = {"provider": "plex", "settings": preset["plex"]}
        step("save_plex", "provider_settings", settings,
             lambda data: plex_settings_metadata(data, preset["plex"]),
             ("Select this provider before changing its settings.", "Plex settings are unavailable on this player."))
        step("select_m3u", "provider", {"query": "m3u"}, provider("m3u"))
        rows = step("wait_m3u", "profiles", {}, profiles,
                    ("Select the M3U provider before managing profiles.",))
        current = next(row["number"] for row in rows if row["active"])
        desired = preset["active_profile"]
        for row in preset["m3u"]:
            if row["number"] != current:
                save_profile(row, current)
        if current == desired:
            save_profile(next(row for row in preset["m3u"] if row["number"] == current), current)
        params = {"number": desired}
        def selected(data):
            result = profile_metadata(data, "profile", params)
            expected = next(row for row in preset["m3u"] if row["number"] == desired)
            profile_metadata({"provider": "m3u", "saved": True, "profile": result["profile"]},
                             "profile_settings", expected)
            return True
        step("select_profile_" + str(desired), "profile", params, selected)
        if current != desired:
            for row in preset["m3u"]:
                if row["number"] == current:
                    save_profile(row, desired)
        def verified(data):
            final = profiles(data)
            if next(row["number"] for row in final if row["active"]) != desired:
                raise Error("Final active profile differs from the preset")
            for row in preset["m3u"]:
                profile_metadata({"provider": "m3u", "saved": True, "profile": final[row["number"] - 1]},
                                 "profile_settings", row)
            return True
        step("verify_profiles", "profiles", {}, verified)
    except KeyboardInterrupt:
        return {"preset": name, "status": "interrupted", "stage": stage, "completed": completed,
                "error": "Loading interrupted. A request may have executed; inspect the player before retrying."}
    except (Error, KeyError, TypeError, ValueError, OSError):
        return {"preset": name, "status": "failed", "stage": stage, "completed": completed,
                "error": "Loading stopped. A request may have executed; inspect the player before retrying."}
    return {"preset": name, "status": "loaded", "provider": "m3u", "active_profile": desired,
            "completed": completed}


def preset_command(config, words, timeout, json_output):
    receipt = {"status": "failed", "stage": "validate", "completed": [], "error": "Invalid preset configuration or command."}
    try:
        _, params = parse_command(words[1:])
        name, preset = validate_preset(config, params["preset"])
        receipt["preset"] = name
        receipt["stage"] = "connect"
        receipt["error"] = "Could not prepare the player connection."
        client = Client(config, timeout)
        device = client.device(words[0])
        receipt = load_preset(client, device, name, preset)
    except Error as exc:
        if receipt["stage"] == "validate":
            # Local validators use fixed messages, never credential values.
            receipt["error"] = str(exc)
    except (KeyError, TypeError, ValueError, OSError):
        pass
    except KeyboardInterrupt:
        receipt.update(status="interrupted", error="Preset loading interrupted before connecting.")
    if json_output:
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    elif receipt["status"] == "loaded":
        print(f"Preset loaded: {receipt['preset']} (M3U profile {receipt['active_profile']}).")
    else:
        done = ", ".join(receipt["completed"]) or "none"
        print(f"Preset loading stopped at {receipt['stage']}. Completed: {done}. {receipt['error']}", file=sys.stderr)
    return 0 if receipt["status"] == "loaded" else 130 if receipt["status"] == "interrupted" else 1


def restart_metadata(data, target):
    if (not isinstance(data, dict) or data.get("accepted") is not True or data.get("target") != target
            or (target == "stream" and data.get("dispatched") is not True)
            or (target == "player" and (data.get("dispatched") is not False or data.get("effect") != "reload-after-ack"))):
        raise Error("The player did not confirm the restart request. It may have executed; do not repeat the change blindly.")
    result = {"accepted": True, "target": target, "dispatched": target == "stream"}
    if target == "player":
        result["effect"] = "reload-after-ack"
    return result


def vportal_metadata(data, playing, shuffled=False):
    """Validate queue acknowledgement and expose only public video metadata."""
    message = "The player returned invalid VPortal metadata"
    if playing:
        message += ". The request may have executed; do not repeat the change blindly."
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise Error(message)
    items = data["items"]
    if type(data.get("total")) is not int or data["total"] != len(items):
        raise Error(message)
    if playing and (not items or data.get("loop") is not True or data.get("dispatched") is not True):
        raise Error(message)
    if shuffled and data.get("shuffled") is not True:
        raise Error("The player did not confirm shuffling the VPortal queue. Update the player; the request may have executed, so do not repeat it blindly.")
    public = []
    for number, row in enumerate(items, 1):
        if (not isinstance(row, dict) or type(row.get("number")) is not int or row["number"] != number
                or not isinstance(row.get("title"), str)):
            raise Error(message)
        public.append({"number": number, "title": row["title"]})
    result = {"items": public, "total": len(public)}
    if playing:
        result.update(loop=True, dispatched=True)
    if shuffled:
        result["shuffled"] = True
    return result


def provision_kubernetes(client, config_path, name, device):
    kube = client.config["kubernetes"]
    journal_path = Path(config_path).expanduser().with_name(Path(config_path).name + ".pending-add.json")
    identity = {"name": name.casefold(), "device": device, "kubernetes": kube, "server": client.server,
                "server_config": str(Path(client.config["server_config"]).expanduser().resolve())}
    resuming = journal_path.exists()
    journal = read_json(journal_path) if resuming else None
    if resuming:
        if (not isinstance(journal, dict) or set(journal) != {"version", "identity", "before", "after"}
                or type(journal["version"]) is not int or journal["version"] != 1 or not isinstance(journal["identity"], dict)
                or not all(isinstance(journal[field], dict) and isinstance(journal[field].get("devices"), list)
                           for field in ("before", "after"))):
            raise Error("Invalid pending-add.json; reconcile it with the configuration before recovery")
        if journal.get("identity") != identity or client.credentials not in (journal.get("before"), journal.get("after")):
            raise Error("An unfinished ott add belongs to another configuration; complete it or reconcile pending-add.json first")
    else:
        updated = json.loads(json.dumps(client.credentials))
        updated["devices"].append({"id": device, "token": secrets.token_urlsafe(32)})
        journal = {"version": 1, "identity": identity, "before": client.credentials, "after": updated}
    base = ["kubectl", "--context", kube["context"], "-n", kube["namespace"], "--request-timeout=15s"]

    def current_config(require_alias=False):
        # kubectl/rollout may take seconds: preserve edits made meanwhile, but
        # never bind this operation's token to another server or target alias.
        current = read_json(config_path)
        try:
            same_target = (isinstance(current, dict) and current.get("server", "").rstrip("/") == identity["server"]
                           and current.get("kubernetes") == identity["kubernetes"]
                           and str(Path(current["server_config"]).expanduser().resolve()) == identity["server_config"])
        except (AttributeError, KeyError, TypeError, ValueError):
            same_target = False
        if not same_target:
            raise Error("The server address or Kubernetes/server_config settings in cli.json changed; reconcile pending-add.json before continuing")
        aliases = current.get("players", {})
        if not isinstance(aliases, dict):
            raise Error("The players mapping in cli.json changed; reconcile pending-add.json before continuing")
        matching = [key for key in aliases if key.casefold() == name.casefold()]
        if (len(matching) > 1 or any(aliases[key] != device for key in matching)
                or (require_alias and not matching)):
            raise Error("The target alias in cli.json changed; reconcile pending-add.json before continuing")
        current.setdefault("players", {})[matching[0] if matching else name] = device
        return current

    try:
        resource = json.loads(subprocess.check_output(base + ["get", "secret", kube["secret"], "-o", "json"], timeout=20, stderr=subprocess.PIPE))
        live = json.loads(base64.b64decode(resource["data"]["config.json"]))
        if live not in (journal["before"], journal["after"]):
            raise Error("The cluster configuration changed; reconcile server_config and pending-add.json before retrying")
        if not resuming:
            # Save the generated credential before the first external mutation.
            # A lost kubectl response can then be reconciled against the live Secret.
            write_private(journal_path, journal, exclusive=True)
        if live != journal["after"]:
            resource["data"]["config.json"] = base64.b64encode(json.dumps(journal["after"]).encode()).decode()
            subprocess.run(base + ["replace", "-f", "-"], input=json.dumps(resource), text=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
        write_private(client.config["server_config"], journal["after"])
        client.credentials = journal["after"]
        client.config = current_config()
        write_private(config_path, client.config)
        subprocess.run(base + ["rollout", "restart", "deployment/" + kube["deployment"]], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)
        subprocess.run(base + ["rollout", "status", "deployment/" + kube["deployment"], "--timeout=90s"], check=True, stdout=sys.stderr, stderr=subprocess.PIPE, timeout=100)
        client.config = current_config(require_alias=True)
        journal_path.unlink()
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise Error(f"Device addition is incomplete. Retry ott add {name} {device}; the saved access code will be reused. Do not delete pending-add.json before reconciliation.") from exc


def management(client, config_path, words, json_output=False):
    verb = words[0].casefold()
    if verb == "discover" and len(words) == 1:
        _, data = client.api("/api/discovery")
        if json_output:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            for row in data["servers"]:
                print(f"{clean(row['id'])}  {clean(row['address'])}  domain={clean(row['domain'])}")
            if not data["servers"]:
                print("No OTT-play controllers are advertised in network DNS.")
        return True
    if verb in ("pending", "approve"):
        if (verb == "pending" and len(words) != 1) or (verb == "approve" and len(words) != 3):
            raise Error("Use ott pending or ott approve NAME CODE")
        device = client.device(words[1]) if verb == "approve" else None
        code = words[2].upper() if verb == "approve" else None
        if code is not None and not re.fullmatch(r"[A-Z0-9]{8}", code):
            raise Error("Enter the eight-character pairing code displayed by the player")
        _, data = client.api("/api/pairings")
        rows = data.get("pairings")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise Error("The server returned an invalid pairing list")
        if verb == "pending":
            if json_output:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    aliases = [key for key, value in client.config.get("players", {}).items() if value == row["device_id"]]
                    print(f"{clean(','.join(aliases) or row['device_id'])}  code={clean(row['code'])}  {clean(row['address'])}  expires_in={row['expires_in']}s")
                if not rows:
                    print("No players are waiting for pairing approval.")
            return True
        matches = [row for row in rows if row.get("device_id") == device and row.get("code") == code]
        if len(matches) != 1:
            raise Error("No unique pending request matches this player and code. Check the code on the player, then run ott pending")
        row = matches[0]
        if not isinstance(row.get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", row["id"]):
            raise Error("The server returned an invalid pairing ID")
        try:
            status, result = client.api("/api/pairings/approve", {"id": row["id"], "code": code})
        except Error as exc:
            if isinstance(exc, HTTPError) and exc.code < 500:
                raise
            raise Error("Approval may have succeeded. Check the player's connection and ott pending before trying again") from exc
        if status != 200 or not isinstance(result, dict) or result.get("status") != "approved":
            raise Error("The server did not confirm approval. Check the player's connection before trying again")
        if json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"Pairing approved for {clean(words[1])}. The player will save its own access code and connect automatically.")
        return True
    if verb == "devices" and len(words) == 1:
        _, data = client.api("/api/devices")
        for row in data["devices"]:
            aliases = [key for key, value in client.config.get("players", {}).items() if value == row["id"]]
            print(f"{','.join(aliases) or '—'}  {row['id']}  last_seen={row.get('last_seen') or 'never'}  pending={row['pending']}")
        return True
    if verb == "pair" and len(words) == 2:
        device = client.device(words[1])
        row = next((x for x in client.credentials["devices"] if x["id"] == device), None)
        if not row:
            raise Error("No local access code is available for this device")
        print("Settings → Remote control → Command server")
        print("Address:", client.config.get("player_server", client.server))
        print("Access code:", row["token"])
        print("Device UUID / queue:", device)
        print("Select Connect. Do not use this access code in another player.")
        return True
    if verb in ("alias", "add") and len(words) == 3:
        name, device = words[1:]
        if not re.fullmatch(r"[a-zA-Z0-9\u0430-\u044f\u0410-\u042f\u0451\u0401_-]{1,32}", name) or name.casefold() in {"devices", "alias", "add", "pair", "help", "discover", "pending", "approve"}:
            raise Error("Choose a name of up to 32 letters/digits, underscores or hyphens, without spaces")
        if not re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", device):
            raise Error("Invalid device UUID")
        aliases = client.config.setdefault("players", {})
        existing = next((key for key in aliases if key.casefold() == name.casefold()), name)
        if existing in aliases and aliases[existing] != device:
            raise Error("The name is already bound to another UUID; edit cli.json to reassign it")
        registered = any(x["id"] == device for x in client.credentials["devices"])
        journal_path = Path(config_path).expanduser().with_name(Path(config_path).name + ".pending-add.json")
        if verb == "add" and client.config.get("kubernetes") and (not registered or journal_path.exists()):
            if not registered and len(client.credentials["devices"]) >= 64:
                raise Error("The limit of 64 devices has been reached")
            provision_kubernetes(client, config_path, existing, device)
            print(f"{existing} → {device}. Connection settings: ott pair {existing}")
            return True
        elif not registered:
            if verb == "alias":
                raise Error("The UUID is not registered yet. Use ott add NAME UUID")
            if len(client.credentials["devices"]) >= 64:
                raise Error("The limit of 64 devices has been reached")
            updated = json.loads(json.dumps(client.credentials))
            updated["devices"].append({"id": device, "token": secrets.token_urlsafe(32)})
            write_private(client.config["server_config"], updated)
            print("Server configuration updated. Restart the command server.", file=sys.stderr)
        aliases[existing] = device
        write_private(config_path, client.config)
        print(f"{existing} → {device}. Connection settings: ott pair {existing}")
        return True
    return False


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "diagnostics":
        # Keep scoped diagnostic credentials separate from the legacy admin config.
        module_path = Path(__file__).resolve().with_name("diagnostics.py")
        if not module_path.is_file():
            print("Error: Install diagnostics.py beside ott.py", file=sys.stderr)
            return 1
        spec = importlib.util.spec_from_file_location("ott_diagnostics", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.main(argv[1:])
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default=os.environ.get("OTT_CONFIG", str(Path.home() / ".config/ottplay-control/cli.json")))
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="Refresh cached EPG history and archive checks")
    parser.add_argument("--help", "-h", action="store_true")
    parser.add_argument("words", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.help or not args.words or args.words == ["help"]:
        print(HELP)
        return 0
    try:
        if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 300:
            raise Error("--timeout must be between 1 and 300 seconds")
        config = read_json(args.config)
        if args.words[0].casefold() == "presets":
            if len(args.words) != 1:
                raise Error("Use presets without arguments")
            names = preset_names(config)
            print(json.dumps({"presets": names}, indent=2) if args.json else "\n".join(names))
            return 0
        if (len(args.words) > 1 and args.words[1].casefold() == "load"
                and args.words[0].casefold() not in ("alias", "add", "approve", "pair", "devices", "discover", "pending")):
            return preset_command(config, args.words, args.timeout, args.json)
        client = Client(config, args.timeout)
        if management(client, args.config, args.words, json_output=args.json):
            return 0
        device = client.device(args.words[0])
        words = args.words[1:]
        action, params = parse_command(words)
        catalog_play = None
        playback_error = None
        plex_settings = action == "provider_settings" and params.get("provider") == "plex"
        if action == "programs" and "epg" in config:
            data, catalog_play = server_programs(client, device, config["epg"], params["search"], refresh=args.refresh)
        elif action == "play":
            data, playback_error = play_channel(client, device, params)
            if data.get("channels") == [] and "epg" in config:
                print("No channel matches. Searching current EPG and available archives...", file=sys.stderr)
                query = params["query"]
                data, catalog_play = server_programs(client, device, config["epg"], query, refresh=args.refresh)
                action, params, words = "programs", {"search": query}, ["p", query]
                playback_error = None
        else:
            try:
                data = client.call(device, action, params)
            except (PlayerRejected, PlayerUnsupported) as exc:
                if action not in ("profiles", "profile", "profile_settings", "restart") and not plex_settings:
                    raise
                # Settings can contain credentials: do not echo player-supplied errors.
                reason = "unsupported by this player" if isinstance(exc, PlayerUnsupported) else "rejected by the player"
                if plex_settings:
                    raise Error(f"The Plex settings request was {reason}; select Plex, unlock its settings and use a player with remote Plex support") from None
                raise Error(f"The {action} request was {reason}; check its settings on the player") from None
        if action in ("vportal", "vportal_random", "vportal_search"):
            data = vportal_metadata(data, action != "vportal_search", action == "vportal_random")
        elif action in ("profiles", "profile", "profile_settings"):
            data = profile_metadata(data, action, params)
        elif action == "restart":
            data = restart_metadata(data, params["target"])
        elif plex_settings:
            data = plex_settings_metadata(data, params["settings"])
        if action == "programs" and params["search"] and words[1:2] != ["--list"]:
            selected = select_programme(data["programs"])
            if selected:
                try:
                    number = selected["number"]
                    archive = selected.get("mode") == "archive"
                    key = (number, selected["start"]) if archive else number
                    target = catalog_play[key] if catalog_play is not None else None
                    # Use the returned catalogue number, even for duplicate or numeric names.
                    playback = (client.call(device, "play_archive_catalog" if archive else "play_catalog", target) if target is not None
                                else client.call(device, "play", {"query": str(number)}))
                    channel = playback.get("channel") if isinstance(playback, dict) else None
                    if (not isinstance(channel, dict) or playback.get("dispatched") is not True
                            or type(channel.get("number")) is not int or channel["number"] != number
                            or channel.get("name") != selected["channel"]
                            or (target is not None and channel.get("id") != target["id"])
                            or (archive and (playback.get("start") != selected["start"] or playback.get("end") != selected["end"]))):
                        raise Error("The player did not confirm the requested channel switch. The request may have executed; do not repeat the change blindly.")
                    if catalog_play is not None:
                        playback = {"dispatched": True, "channel": {key: channel[key] for key in ("id", "number", "name")}}
                        if archive:
                            playback.update({"mode": "archive", "start": selected["start"], "end": selected["end"]})
                    data["playback"] = playback
                except Error as exc:
                    playback_error = exc
                    data["playback"] = {"error": str(exc)}
            elif not data.get("partial"):
                print("No current programmes or available archives match the search." if "epg" in config else
                      "No current programmes match the search.", file=sys.stderr)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif action == "channels" or (action == "play" and "channels" in data):
            for row in data["channels"]:
                print(f"{row['number']}: {clean(row['name'])}")
        elif action == "programs":
            for row in data["programs"]:
                detail = ""
                if row.get("mode") == "archive":
                    detail = " [archive from %s; %s consecutive programmes]" % (
                        time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(row["start"])), row["consecutive"])
                print(f"{clean(row['channel'])} — {clean(row['title'])}{detail}")
        elif action in ("vportal", "vportal_random", "vportal_search"):
            for row in data["items"]:
                print(f"{row['number']}: {clean(row['title'])}")
        elif action == "status" and words and words[0].casefold() == "v":
            if data.get("volume") is None:
                raise Error("The platform does not report volume")
            print(f"{data['volume']:g}%")
        elif action == "providers":
            for row in data["providers"]:
                print(f"{'*' if row['active'] else ' '} {row['index']}: {clean(row['id'])} — {clean(row['name'])}")
        elif action == "profiles":
            for row in data["profiles"]:
                history = "unknown" if row["history_hours"] is None else str(row["history_hours"])
                print(f"{'*' if row['active'] else ' '} {row['number']}: {clean(row['name'])} | history: {history} h | "
                      f"playlist: {'set' if row['playlist_configured'] else 'empty'} | VPortal: {'set' if row['vportal_configured'] else 'empty'}")
        elif action == "profile":
            print(f"Profile switch requested: {data['profile']['number']}: {clean(data['profile']['name'])}")
        elif action == "profile_settings":
            print(f"Profile settings saved: {data['profile']['number']}: {clean(data['profile']['name'])}")
        elif action == "restart":
            print("Stream restart requested." if data["target"] == "stream" else
                  "Player reload accepted; waiting for the acknowledgement to reach the player.")
        elif plex_settings:
            print("Plex settings saved: " + ", ".join(data["fields"]) + ".")
        elif action == "play":
            print(f"Channel switch requested: {data['channel']['number']}: {clean(data['channel']['name'])}")
        elif action == "command" and params.get("command") == "set_volume":
            if data.get("volume") is None:
                raise Error("The command was sent, but the platform does not report volume")
            print(f"{data['volume']:g}%")
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        if action in ("programs", "play") and data.get("playback", {}).get("dispatched"):
            channel = data["playback"]["channel"]
            sys.stdout.flush()
            if len(data.get("programs", data.get("channels", []))) > 1:
                print(f"Randomly selected channel: {channel['number']}: {clean(channel['name'])}", file=sys.stderr)
            print(f"Channel switch requested: {channel['number']}: {clean(channel['name'])}", file=sys.stderr)
        if action in ("vportal", "vportal_random"):
            shuffled = "shuffled; " if action == "vportal_random" else ""
            print(f"VPortal playback requested: {data['total']} videos; {shuffled}repeat enabled.", file=sys.stderr)
        elif action == "vportal_search" and not data["items"]:
            print("No VPortal videos match the search.", file=sys.stderr)
        if action == "programs" and data.get("partial"):
            print(f"Programme search is incomplete: checked {data['checked']} of {data['total']} channels on the player. "
                  "Unchecked channels may contain matches. This is search coverage, not EPG download progress.", file=sys.stderr)
        if playback_error:
            raise playback_error
        return 3 if action == "programs" and data.get("partial") else 0
    except (Error, KeyError, TypeError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print("Error: " + (str(exc) if isinstance(exc, Error) else "Check the configuration; the operation did not complete"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
