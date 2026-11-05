#!/usr/bin/env python3
"""Run one pinned CLI case against an already owned Mac acceptance session.

No server, player, pairing, build, or retry is performed here. Default mode only
validates inputs. The public result contains a small projection; actual response
data is returned by run_case() and retained only in the private output directory.
An owner receipt is hash-bound as opaque evidence, not interpreted as proof of
native qualification. The runtime owner remains responsible for that proof.
One CLI child may issue several legitimate RPCs (discovery, paging, settings
get/set, or a Favorites transaction); this is not a one-RPC guarantee.
Filesystem/process entry points require POSIX host APIs. Their availability
does not establish Native compatibility or qualification on any platform.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

MAX_JSON = 2 * 1024 * 1024
MAX_SOURCE = 4 * 1024 * 1024
MAX_CLOSURE = 32 * 1024 * 1024
CLEANUP_SECONDS = 5
HEX = re.compile(r"[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,256}\Z")
REQUEST = re.compile(r"[0-9a-f]{32}\Z")
NONSECRET_SETTINGS = frozenset(("interfaceMode", "bufSize", "uiFontScale"))
# case: (CLI words, response action, parameter names, mutation)
CASES = {
    "caps": (("caps",), "capabilities", (), False),
    "status": (("status",), "status", (), False),
    "profiles": (("profiles",), "profiles", (), False),
    "settings_schema": (("settings", "schema"), "player_settings", (), False),
    "setting_read": (("setting",), "player_settings", ("setting",), False),
    "settings_write": (("setting",), "player_settings", ("setting", "value"), True),
    "profile_select": (("profile",), "profile", ("number",), True),
    "plex_status": (("plex", "status"), "plex_queue", (), False),
    "plex_libraries": (("plex", "libraries"), "plex_library", (), False),
    "plex_library_preview": (("plex", "library", "--preview"), "plex_library", ("library_id",), False),
    "plex_preview": (("plex", "preview"), "plex_queue", ("ids",), False),
    "plex_play": (("plex", "play"), "plex_queue", ("ids",), True),
    "plex_next": (("plex", "next"), "plex_queue", (), True),
    "plex_previous": (("plex", "prev"), "plex_queue", (), True),
    "plex_stop": (("plex", "stop"), "plex_queue", (), True),
    "plex_repeat": (("plex", "repeat"), "plex_queue", ("mode",), True),
    "pause": (("pause",), "playback", (), True),
    "resume": (("resume",), "playback", (), True),
    "relative_seek": (("seek",), "playback", ("seconds",), True),
    "categories": (("categories",), "categories", (), False),
    "channels_page": (("s",), "channels", ("limit", "offset"), False),
    "tracks_list": (("tracks",), "media_tracks", (), False),
    "tracks_audio": (("tracks", "audio"), "media_tracks", ("id",), True),
    "tracks_subtitle": (("tracks", "subtitle"), "media_tracks", ("id",), True),
    "tracks_subtitle_off": (("tracks", "subtitle", "off"), "media_tracks", (), True),
    "favorites_list": (("fav", "list"), "favorites", (), False),
    "favorites_global_current": (("fav", "global", "current"), "favorites_global_items", (), False),
    "favorites_add": (("fav", "add"), "favorites", (), True),
    "favorites_play": (("fav", "play"), "favorites", ("id",), True),
    "favorites_remove": (("fav", "remove"), "favorites", ("id",), True),
    "plex_servers_list": (("plex", "servers"), "plex_servers", (), False),
    "plex_servers_select": (("plex", "servers", "select"), "plex_servers", ("server_id", "context"), True),
    "local_roots": (("local", "roots"), "local_browser", (), False),
    "local_sources": (("local", "sources"), "local_media", (), False),
    "local_browse": (("local", "browse"), "local_browser", ("entry_id", "generation"), False),
    "local_select": (("local", "select"), "local_browser", ("select_id", "generation"), True),
    "local_handle_play": (("local", "play"), "local_media", ("handle",), True),
    "local_handle_shuffle": (("local", "shuffle"), "local_media", ("handle",), True),
    "local_connect_handle": (("local", "connect"), "local_media", ("handle",), True),
}
FAVORITE_ACTIONS = frozenset(("favorites", "favorites_global", "favorites_global_items", "favorites_global_recovery"))
MULTI_OPERATION_CASES = frozenset(("settings_write", "tracks_audio", "tracks_subtitle", "tracks_subtitle_off",
                                  "favorites_add", "favorites_play", "favorites_remove"))


class CaseError(Exception):
    """A fixed safe reason code; never include input or exception text."""


def require(condition, reason):
    if not condition:
        raise CaseError(reason)


def host_supported():
    return (os.name == "posix" and
            all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "getuid", "killpg", "set_blocking")) and
            all(hasattr(signal, name) for name in ("SIGTERM", "SIGKILL")))


def require_host():
    require(host_supported(), "unsupported_host")


def exact(value, fields, reason):
    require(isinstance(value, dict) and set(value) == set(fields), reason)


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def invalid(_):
        raise CaseError("invalid_json_number")

    def finite(value):
        number = float(value)
        require(math.isfinite(number), "invalid_json_number")
        return number

    try:
        require(len(raw) <= MAX_JSON, "json_limit")
        return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite)
    except (ValueError, UnicodeError, RecursionError):
        raise CaseError("invalid_json") from None


def regular(path, limit, private=False):
    """Read one regular, nonsymlink file once, with a strict byte bound."""
    require_host()
    require(isinstance(path, str) and Path(path).is_absolute(), "invalid_path")
    target = Path(path)
    require(str(target.resolve()) == path, "noncanonical_path")
    # Inspect special files without waiting for a FIFO writer before fstat.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, "invalid_file")
        if private:
            require(info.st_uid == os.getuid() and info.st_mode & 0o077 == 0, "file_not_private")
        data = stream.read(limit + 1)
        require(len(data) <= limit, "file_limit")
        return data


def pinned(pin, private=False):
    exact(pin, ("path", "sha256"), "invalid_pin")
    require(isinstance(pin["sha256"], str) and HEX.fullmatch(pin["sha256"]), "invalid_digest")
    data = regular(pin["path"], MAX_JSON, private)
    require(hashlib.sha256(data).hexdigest() == pin["sha256"], "pin_mismatch")
    return data


def command(case, params, allow_mutation):
    require(case in CASES, "unknown_case")
    words, action, fields, mutation = CASES[case]
    if case == "plex_play" and isinstance(params, dict) and "shuffle" in params:
        fields = (*fields, "shuffle")
        require(type(params["shuffle"]) is bool, "invalid_shuffle")
    exact(params, fields, "invalid_case_parameters")
    require(not mutation or allow_mutation, "mutation_not_enabled")
    non_tokens = {"ids", "shuffle", "number", "value", "seconds", "limit", "offset"}
    require(all(isinstance(value, str) and TOKEN.fullmatch(value)
                for key, value in params.items() if key not in non_tokens), "invalid_case_token")
    words = list(words)
    if case in ("setting_read", "settings_write"):
        require(params["setting"] in NONSECRET_SETTINGS, "setting_not_allowlisted")
        words.append(params["setting"])
        if case == "settings_write":
            # uiFontScale limits come from the CLI's fresh descriptor, not this helper.
            maximum = {"interfaceMode": 1, "bufSize": 3600, "uiFontScale": 2147483647}[params["setting"]]
            require(type(params["value"]) is int and 0 <= params["value"] <= maximum, "invalid_setting_value")
            words.append(str(params["value"]))
    elif case == "profile_select":
        require(type(params["number"]) is int and 1 <= params["number"] <= 15, "invalid_profile")
        words.append(str(params["number"]))
    elif case in ("plex_preview", "plex_play"):
        ids = params["ids"]
        require(isinstance(ids, list) and 1 <= len(ids) <= 500 and all(
            isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value) for value in ids), "invalid_plex_ids")
        if params.get("shuffle") is True:
            words.append("--shuffle")
        words += ids
    elif case == "plex_library_preview":
        require(re.fullmatch(r"[1-9][0-9]{0,19}", params["library_id"]), "invalid_library_id")
        words.append("id:" + params["library_id"])
    elif case == "plex_repeat":
        require(params["mode"] in ("none", "all"), "invalid_repeat_mode")
        words.append(params["mode"])
    elif case == "relative_seek":
        require(type(params["seconds"]) is int and 0 < abs(params["seconds"]) <= 3600, "invalid_seek")
        words.append(format(params["seconds"], "+d") + "s")
    elif case == "channels_page":
        require(type(params["limit"]) is int and 1 <= params["limit"] <= 200 and
                type(params["offset"]) is int and 0 <= params["offset"] <= 1000000, "invalid_page")
        words += ["--limit", str(params["limit"]), "--offset", str(params["offset"])]
    elif case in ("tracks_audio", "tracks_subtitle"):
        require(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", params["id"]), "invalid_track_id")
        words.append("id:" + params["id"])
    elif case in ("favorites_play", "favorites_remove"):
        require(re.fullmatch(r"(?:fav-[a-z0-9-]{1,60}|[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12})",
                             params["id"]), "invalid_favorite_id")
        words.append("id:" + params["id"])
    elif case == "plex_servers_select":
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", params["server_id"]), "invalid_server_id")
        words += [params["server_id"], "--context", params["context"]]
    elif case in ("local_browse", "local_select"):
        words += [params["entry_id" if case == "local_browse" else "select_id"],
                  "--generation", params["generation"]]
    elif case in ("local_handle_play", "local_handle_shuffle", "local_connect_handle"):
        words += ["--handle", params["handle"]]
    return words, action, mutation


@dataclass
class Prepared:
    handoff: dict
    case: str
    params: dict
    words: list
    action: str
    mutation: bool
    files: dict
    credentials: dict
    ca: bytes


def prepare(handoff, case, params, allow_mutation=False, clock=time.monotonic):
    require_host()
    words, action, mutation = command(case, params, allow_mutation)
    exact(handoff, ("version", "cli_root", "cli_commit", "cli_files", "config", "credentials", "ca",
                    "owner_receipt", "origin", "alias", "device_id", "deadline_monotonic"), "invalid_handoff")
    require(type(handoff["version"]) is int and handoff["version"] == 1, "invalid_handoff_version")
    require(isinstance(handoff["cli_commit"], str) and re.fullmatch(r"[0-9a-f]{40}", handoff["cli_commit"]),
            "invalid_commit")
    deadline = handoff["deadline_monotonic"]
    require(type(deadline) in (int, float) and math.isfinite(deadline) and
            CLEANUP_SECONDS < deadline - clock() <= 600, "invalid_deadline")
    alias, device = handoff["alias"], handoff["device_id"]
    require(isinstance(alias, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", alias), "invalid_alias")
    require(isinstance(device, str) and TOKEN.fullmatch(device), "invalid_device")
    origin = handoff["origin"]
    require(isinstance(origin, str), "invalid_origin")
    try:
        url = urlsplit(origin)
        valid = (url.scheme == "https" and url.hostname == "127.0.0.1" and url.port is not None and
                 origin == "https://127.0.0.1:" + str(url.port) and 1 <= url.port <= 65535)
    except ValueError:
        valid = False
    require(valid, "invalid_origin")
    config = decode(pinned(handoff["config"], True))
    credentials = decode(pinned(handoff["credentials"], True))
    exact(config, ("server", "server_config", "players"), "invalid_private_config")
    require(config["server"] == origin and config["server_config"] == handoff["credentials"]["path"] and
            config["players"] == {alias: device}, "config_target_mismatch")
    require(isinstance(credentials, dict), "invalid_credentials")
    token_key = "operator_token" if "operator_token" in credentials else "admin_token"
    require(isinstance(credentials.get(token_key), str) and bool(credentials[token_key]), "invalid_credentials")
    ca = pinned(handoff["ca"])
    pinned(handoff["owner_receipt"])  # Opaque owner evidence; no invented qualification schema.
    root = handoff["cli_root"]
    require(isinstance(root, str) and Path(root).is_absolute() and str(Path(root).resolve()) == root,
            "invalid_source_root")
    manifest = handoff["cli_files"]
    require(isinstance(manifest, dict) and 1 <= len(manifest) <= 512 and "cli/ott.py" in manifest,
            "invalid_cli_manifest")
    actual = {str(path.relative_to(root)) for path in (Path(root) / "cli").rglob("*.py")}
    require(actual == set(manifest), "incomplete_cli_closure")
    files, total = {}, 0
    for name, digest in manifest.items():
        require(isinstance(name, str) and name.startswith("cli/") and name.endswith(".py") and
                all(part not in ("", ".", "..") for part in name.split("/")), "invalid_source_member")
        require(isinstance(digest, str) and HEX.fullmatch(digest), "invalid_digest")
        data = regular(str(Path(root) / name), MAX_SOURCE)
        total += len(data)
        require(total <= MAX_CLOSURE and hashlib.sha256(data).hexdigest() == digest, "source_pin_mismatch")
        files[name] = data
    return Prepared(decode(encoded(handoff)), case, decode(encoded(params)), words, action, mutation, files,
                    {token_key: credentials[token_key]}, ca)


def write_private(path, data):
    require_host()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def encoded(value):
    return (json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode()


def receipts(stderr, action, extra_actions=()):
    result, seen = [], set()
    for line in stderr.splitlines():
        if not line.lstrip().startswith(b"{"):
            continue  # Arbitrary human errors/progress never enter the public report.
        row = decode(line)
        exact(row, ("action", "request_id", "status"), "invalid_receipt")
        require(row["action"] in (action, "capabilities", *extra_actions) and
                row["status"] in ("ok", "unknown", "rejected", "unsupported"), "invalid_receipt")
        identifier = row["request_id"]
        require(identifier is None or isinstance(identifier, str) and REQUEST.fullmatch(identifier), "invalid_receipt")
        require(identifier is None or identifier not in seen, "duplicate_receipt")
        if identifier is not None:
            seen.add(identifier)
        result.append(row)
        require(len(result) <= 128, "receipt_limit")
    return result


def project(prepared, data):
    """Small additional shape checks, not a replacement for CLI/native contracts."""
    require(isinstance(data, dict) and "error" not in data, "invalid_response")
    case, summary = prepared.case, {}
    expected_ops = {"settings_schema": "schema", "setting_read": "get", "plex_servers_list": "list",
                    "plex_servers_select": "select", "local_roots": "roots", "local_sources": "sources",
                    "local_browse": "list", "local_select": "select", "local_handle_play": "play",
                    "local_handle_shuffle": "shuffle", "local_connect_handle": "connect", "settings_write": "set",
                    "plex_libraries": "list", "plex_library_preview": "preview", "tracks_list": "list",
                    "tracks_audio": "select", "tracks_subtitle": "select", "tracks_subtitle_off": "select"}
    if case in expected_ops:
        require(type(data.get("version")) is int and data["version"] == 1 and
                data.get("op") == expected_ops[case] and isinstance(data.get("runtime"), str) and
                bool(data["runtime"]), "invalid_response")
    if case == "caps":
        require(type(data.get("version")) is int and data["version"] == 1 and isinstance(data.get("player"), dict) and
                isinstance(data["player"].get("runtime"), str), "invalid_response")
        summary["capabilities"] = [key for key in ("plex_queue", "plex_queue_state_v2", "plex_queue_repeat", "plex_servers",
            "local_media", "local_browser", "player_settings", "favorites") if key in data]
    elif case == "status":
        require(bool(data), "invalid_response")
        summary["response_received"] = True
    elif case in ("profiles", "settings_schema", "plex_servers_list", "local_sources", "local_roots", "local_browse",
                  "plex_libraries", "categories", "channels_page", "tracks_list", "favorites_list"):
        key = {"profiles": "profiles", "settings_schema": "settings", "plex_servers_list": "servers",
               "local_sources": "sources", "local_roots": "entries", "local_browse": "entries",
               "plex_libraries": "libraries", "categories": "categories", "channels_page": "channels",
               "tracks_list": "tracks", "favorites_list": "entries"}[case]
        require(isinstance(data.get(key), list) and len(data[key]) <= 2048, "invalid_response")
        summary["item_count"] = len(data[key])
    elif case == "setting_read":
        descriptor, setting = data.get("descriptor"), data.get("setting")
        require(isinstance(descriptor, dict) and descriptor.get("sensitive") is False and
                descriptor.get("id") == prepared.params["setting"] and isinstance(setting, dict) and
                setting.get("id") == prepared.params["setting"], "invalid_response")
        for name in ("value", "persisted_value", "effective_value"):
            if name in setting:
                require(type(setting[name]) is int, "invalid_response")
                summary[name] = setting[name]
    elif case == "settings_write":
        setting, application = data.get("setting"), data.get("application")
        require(isinstance(setting, dict) and setting.get("id") == prepared.params["setting"] and
                setting.get("persisted_value") == prepared.params["value"] and data.get("persisted") is True and
                isinstance(application, dict) and application.get("state") in ("completed", "deferred", "failed"),
                "invalid_response")
        summary.update(persisted=True, application_state=application["state"])
    elif case == "profile_select":
        require(data.get("number") == prepared.params["number"] or
                isinstance(data.get("profile"), dict) and data["profile"].get("number") == prepared.params["number"],
                "invalid_response")
        summary["profile_number"] = prepared.params["number"]
    elif case in ("plex_status", "plex_play", "plex_next", "plex_previous", "plex_stop", "plex_repeat"):
        require(isinstance(data.get("ids"), list) and len(data["ids"]) <= 1000 and "index" in data and
                type(data.get("active")) is bool and
                data.get("state") in ("idle", "preparing", "playing", "paused", "ended", "error"), "invalid_response")
        ids, index, state, active = data["ids"], data.get("index"), data["state"], data["active"]
        require((bool(ids) and type(index) is int and 0 <= index < len(ids)) or
                (not ids and index is None), "invalid_response")
        require((state == "idle" and not active and not ids) or
                (state in ("preparing", "playing", "paused") and active and bool(ids)) or
                (state == "ended" and active and bool(ids) and index == len(ids) - 1) or
                (state == "error" and active == bool(ids)), "invalid_response")
        summary.update(item_count=len(data["ids"]), index=data["index"], active=data["active"], state=data["state"])
        if case == "plex_repeat":
            require(active and bool(ids) and data.get("repeat") == prepared.params["mode"], "invalid_response")
            summary["repeat"] = data["repeat"]
    elif case in ("plex_preview", "plex_library_preview"):
        queue = data if case == "plex_preview" else data.get("queue")
        require(isinstance(queue, dict) and queue.get("state") == "ready" and isinstance(queue.get("ids"), list) and
                len(queue["ids"]) <= 500, "invalid_response")
        summary["item_count"] = len(queue["ids"])
    elif case in ("pause", "resume", "relative_seek"):
        operation = "seek_relative" if case == "relative_seek" else case
        require(data.get("operation") == operation and data.get("dispatched") is True, "invalid_response")
        summary["dispatched"] = True
        if case == "relative_seek":
            require(data.get("offset") == prepared.params["seconds"], "invalid_response")
            summary["offset_seconds"] = prepared.params["seconds"]
    elif case in ("tracks_audio", "tracks_subtitle", "tracks_subtitle_off"):
        require(data.get("accepted") is True and data.get("outcome") in ("requested", "confirmed"), "invalid_response")
        summary.update(accepted=True, outcome=data["outcome"])
    elif case == "favorites_global_current":
        require(data.get("collection") == "global" and data.get("op") == "current" and
                data.get("status") in ("ready", "unavailable") and "entry" in data, "invalid_response")
        summary["current_available"] = data["entry"] is not None
    elif case in ("favorites_add", "favorites_play", "favorites_remove"):
        if data.get("collection") == "global":
            if case == "favorites_play":
                require(data.get("dispatched") is True and data.get("playback_confirmed") is False, "invalid_response")
                summary.update(dispatched=True, playback_confirmed=False)
            else:
                require(data.get("status") == "verified", "unconfirmed_favorite_change")
                summary["change_verified"] = True
        else:
            field = {"favorites_add": "created", "favorites_play": "dispatched", "favorites_remove": "removed"}[case]
            require(type(data.get(field)) is bool and (case == "favorites_add" or data[field]), "invalid_response")
            summary[field] = data[field]
    elif case == "plex_servers_select":
        require(data.get("server_id") == prepared.params["server_id"] and
                data.get("context") == prepared.params["context"] and data.get("activated") is True and
                data.get("persisted") is False and data.get("playback_confirmed") is False, "invalid_response")
        summary.update(activated=True, persisted=False, playback_confirmed=False)
    elif case == "local_select":
        require(data.get("generation") == prepared.params["generation"] and isinstance(data.get("source"), dict) and
                isinstance(data["source"].get("handle"), str) and data.get("persisted") is False and
                data.get("activated") is False, "invalid_response")
        summary.update(activated=False, persisted=False)
    else:
        require(type(data.get("count")) is int and data["count"] >= (0 if case == "local_connect_handle" else 1) and data.get("accepted") is True and
                data.get("playback_confirmed") is False, "invalid_response")
        summary.update(item_count=data["count"], accepted=True, playback_confirmed=False)
    return summary


def capture(argv, env, directory, end, cleanup_end, limit, clock=time.monotonic):
    """One process group; bounded pipe reads, no stdin and no retries."""
    require_host()
    process, streams, selector = None, [bytearray(), bytearray()], selectors.DefaultSelector()
    reason, cleanup_ok, code = None, True, None
    try:
        require(clock() < end, "deadline_before_spawn")
        process = subprocess.Popen(argv, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        for index, stream in enumerate((process.stdout, process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, index)
        while selector.get_map() or process.poll() is None:
            if clock() >= end:
                reason = "deadline"
                break
            for key, _ in selector.select(min(0.05, max(0, end - clock()))):
                chunk = os.read(key.fd, min(65536, limit + 1 - len(streams[key.data])))
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    streams[key.data].extend(chunk)
                    if len(streams[key.data]) > limit:
                        reason = "output_limit"
                        break
            if reason:
                break
        code = process.poll()
    except KeyboardInterrupt:
        reason = "interrupted"
    except (OSError, ValueError, CaseError):
        reason = "process_failure"
    finally:
        selector.close()
        if process is not None:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, sig)
                except ProcessLookupError:
                    pass
                except OSError:
                    cleanup_ok = False
                try:
                    process.wait(timeout=max(0.01, min(1, cleanup_end - clock())))
                except subprocess.TimeoutExpired:
                    cleanup_ok = False if sig == signal.SIGKILL else cleanup_ok
            for stream in (process.stdout, process.stderr):
                stream.close()
            code = process.returncode
    return code, bytes(streams[0][:limit]), bytes(streams[1][:limit]), reason, cleanup_ok, process is not None


def run_case(prepared, *, execute=False, output=None, seconds=45, byte_limit=MAX_JSON, clock=time.monotonic):
    require_host()
    require(type(seconds) in (int, float) and math.isfinite(seconds) and 0 < seconds <= 90, "invalid_case_timeout")
    require(type(byte_limit) is int and 1024 <= byte_limit <= MAX_JSON, "invalid_output_limit")
    report = {"version": 1, "case": prepared.case, "result": "not_executed", "scope": "cli_response_only",
              "native_qualification": "not_evaluated", "cli_invocations": 0, "automatic_retries": 0,
              "mutation": prepared.mutation, "receipts": []}
    require(clock() < prepared.handoff["deadline_monotonic"] - CLEANUP_SECONDS, "deadline_before_spawn")
    if not execute:
        return report, None
    require(isinstance(output, str) and Path(output).is_absolute() and str(Path(output).resolve()) == output,
            "invalid_output_path")
    root = Path(output)
    root.mkdir(mode=0o700)  # Existing case directories are never overwritten/replayed.
    try:
        write_private(root / "binding.private.json", encoded({
            "version": 1, "cli_commit": prepared.handoff["cli_commit"],
            "cli_files": prepared.handoff["cli_files"],
            "owner_receipt_sha256": prepared.handoff["owner_receipt"]["sha256"],
            "config_sha256": prepared.handoff["config"]["sha256"],
            "credentials_sha256": prepared.handoff["credentials"]["sha256"],
            "ca_sha256": prepared.handoff["ca"]["sha256"],
            "origin": prepared.handoff["origin"], "alias": prepared.handoff["alias"],
            "device_id": prepared.handoff["device_id"],
            "deadline_monotonic": prepared.handoff["deadline_monotonic"],
            "case": prepared.case, "params": prepared.params,
        }))
        source = root / "source"
        for name, data in prepared.files.items():
            target = source / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            write_private(target, data)
        for name in ("home", "tmp"):
            (root / name).mkdir(mode=0o700)
        write_private(root / "credentials.json", encoded(prepared.credentials))
        write_private(root / "ca.pem", prepared.ca)
        config = {"server": prepared.handoff["origin"], "server_config": str(root / "credentials.json"),
                  "players": {prepared.handoff["alias"]: prepared.handoff["device_id"]}}
        write_private(root / "cli.json", encoded(config))
        end = min(prepared.handoff["deadline_monotonic"] - CLEANUP_SECONDS, clock() + seconds)
        require(end > clock(), "deadline_before_spawn")
        argv = [sys.executable, "-B", str(source / "cli/ott.py"), "-c", str(root / "cli.json"),
                "-t", str(max(0.001, end - clock())), "--json", "--receipt", prepared.handoff["alias"], *prepared.words]
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
               "LANG": "en_US.UTF-8", "SSL_CERT_FILE": str(root / "ca.pem"), "NO_PROXY": "127.0.0.1,localhost",
               "no_proxy": "127.0.0.1,localhost", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    except (OSError, CaseError):
        # We own this newly created directory and have not launched a child.
        # Post-spawn uncertainty is handled separately and keeps its evidence.
        try:
            shutil.rmtree(root)
        except OSError:
            pass  # Cleanup failure must not replace the original staging error.
        raise
    code, stdout, stderr, reason, cleanup_ok, spawned = capture(
        argv, env, str(root), end, prepared.handoff["deadline_monotonic"], byte_limit, clock)
    report.update(cli_invocations=int(spawned), exit_code=code, cleanup_ok=cleanup_ok,
                  staging_cleanup_ok=False,
                  output_bytes={"stdout": len(stdout), "stderr": len(stderr)})
    data = None
    try:
        write_private(root / "stdout.private", stdout)
        write_private(root / "stderr.private", stderr)
        require(not reason and cleanup_ok, reason or "cleanup_failed")
        extras = FAVORITE_ACTIONS if prepared.case.startswith("favorites_") else (("categories",) if prepared.case == "channels_page" else ())
        rows = receipts(stderr, prepared.action, extras)
        report["receipts"] = rows
        target = [row for row in rows if row["action"] in
                  (FAVORITE_ACTIONS if prepared.case.startswith("favorites_") else (prepared.action,))]
        require(bool(target), "missing_operation_receipt")
        if prepared.mutation and prepared.case not in MULTI_OPERATION_CASES:
            require(len(target) == 1, "multiple_mutation_receipts")
        # A later refusal cannot settle an earlier unconfirmed mutation or
        # correlate a receipt whose request identity was never established.
        require(all(row["status"] != "unknown" and row["request_id"] is not None for row in rows),
                "unconfirmed_operation")
        if target[-1]["status"] in ("rejected", "unsupported"):
            require(type(code) is int and code > 0, "unconfirmed_operation")
            report["result"] = target[-1]["status"]
        else:
            require(type(code) is int and code == 0 and
                    all(row["status"] == "ok" for row in rows),
                    "unconfirmed_operation")
            data = decode(stdout)
            report["summary"] = project(prepared, data)
            report["result"] = "response_received"
            write_private(root / "response.private.json", encoded(data))
    except CaseError as error:
        report.update(result="unknown" if spawned else "not_executed", reason=str(error))
        data = None
    except (OSError, ValueError):
        report.update(result="unknown" if spawned else "not_executed", reason="evidence_write_failed")
        data = None
    # Keep uncertain mutation journals/configs for the owner to reconcile. In
    # particular, a lost Favorites commit receipt must not erase its journal.
    if cleanup_ok and (report["result"] == "response_received" or not spawned):
        try:
            for name in ("source", "home", "tmp"):
                shutil.rmtree(root / name)
            for name in ("credentials.json", "ca.pem", "cli.json"):
                (root / name).unlink()
            report["staging_cleanup_ok"] = True
        except OSError:
            report.update(result="unknown" if spawned else "not_executed", reason="staging_cleanup_failed")
            data = None
    if clock() >= prepared.handoff["deadline_monotonic"]:
        report.update(result="unknown" if spawned else "not_executed", reason="deadline")
        data = None
    try:
        write_private(root / "result.json", encoded(report))
    except OSError:
        report.update(result="unknown" if spawned else "not_executed", reason="evidence_write_failed")
        data = None
    return report, data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--case", required=True, choices=sorted(CASES))
    parser.add_argument("--params-file")
    parser.add_argument("--allow-mutation", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--seconds", type=float, default=45)
    options = parser.parse_args(argv)
    try:
        handoff = decode(regular(options.handoff, MAX_JSON, True))
        params = decode(regular(options.params_file, MAX_JSON, True)) if options.params_file else {}
        prepared = prepare(handoff, options.case, params, options.allow_mutation)
        report, _ = run_case(prepared, execute=options.execute, output=options.output, seconds=options.seconds)
    except (CaseError, OSError, ValueError, TypeError) as error:
        report = {"version": 1, "case": options.case, "result": "not_executed",
                  "reason": str(error) if isinstance(error, CaseError) else "input_or_output_failure"}
    print(json.dumps(report, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
    return 0 if report["result"] in ("not_executed", "response_received") and "reason" not in report else 2 if report["result"] == "unknown" else 1


if __name__ == "__main__":
    raise SystemExit(main())
