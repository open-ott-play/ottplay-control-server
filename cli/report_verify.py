"""Offline integrity and semantic replay for bounded workbench report bundles."""
import argparse
import datetime
import hashlib
import json
import math
import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path


MAX_RESULT_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 4 * 1024
MAX_JSON_DEPTH = 32
FILES = frozenset(("manifest.json", "result.json"))
POLICY = "workbench-v2"
LEGACY_POLICY = "legacy-web-v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")
ERROR_REASONS = frozenset((
    "invalid_native_binding", "not_bound", "deadline_exceeded", "invalid_response",
    "unsupported", "invalid_request", "runtime_mismatch", "unavailable",
    "unsupported_controller", "unauthorized", "rejected",
))


class InvalidReport(Exception):
    def __init__(self, reason):
        self.reason = reason


def require(condition, reason="invalid_report"):
    if not condition:
        raise InvalidReport(reason)


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "invalid_json")
            result[key] = value
        return result

    def constant(_value):
        raise InvalidReport("invalid_json")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            require(depth <= MAX_JSON_DEPTH, "invalid_json")
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
            elif isinstance(item, float):
                require(math.isfinite(item), "invalid_json")
        return value
    except (UnicodeError, ValueError, RecursionError, OverflowError):
        raise InvalidReport("invalid_json") from None


def _read_fd(fd, limit):
    before = os.fstat(fd)
    require(stat.S_ISREG(before.st_mode), "unsafe_report_path")
    require(0 < before.st_size <= limit, "report_size_invalid")
    chunks, total = [], 0
    while total <= limit:
        chunk = os.read(fd, min(65536, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    require(total <= limit and total == before.st_size, "report_size_invalid")
    require(_fingerprint(before) == _fingerprint(os.fstat(fd)), "report_changed")
    return b"".join(chunks), _fingerprint(before)


def _verify_unchanged(name, fd, expected, directory_fd):
    if directory_fd is not None:
        # POSIX permits replacement while open: verify the directory entry too.
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    else:
        # Windows handles exclude file writes and file/ancestor rename/delete
        # until this check finishes. Compare the retained handle with itself:
        # CPython 3.12 path stat reports birthtime as ctime, whereas fstat can
        # report ChangeTime. Cross-API fingerprints reject unchanged files.
        info = os.fstat(fd)
    require(_fingerprint(info) == expected, "report_changed")


def _windows_open(path, directory, stack):
    """Hold every ancestor without delete sharing; never follow reparse points."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class FileInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                    ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInfo)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # OPEN_EXISTING, OPEN_REPARSE_POINT, BACKUP_SEMANTICS. Files also exclude
    # writers while read; directories permit writes but prohibit rename/delete.
    handle = kernel.CreateFileW(str(path), 0 if directory else 0x80000000,
                                3 if directory else 1, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError("Unable to open report entry")
    try:
        info = FileInfo()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise OSError("Unable to inspect report entry")
        require(not info.attributes & 0x400 and bool(info.attributes & 0x10) == directory,
                "unsafe_report_path")
        if directory:
            stack.callback(kernel.CloseHandle, handle)
            handle = None
            return None
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
        handle = None
        stack.callback(os.close, fd)
        return fd
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


def read_bundle(directory):
    """Read only the two fixed names, with bounded reads and no symlink traversal."""
    require(isinstance(directory, str) and "\0" not in directory, "unsafe_report_path")
    path = Path(directory).expanduser().absolute()
    require(".." not in path.parts, "unsafe_report_path")
    with ExitStack() as stack:
        directory_fd = None
        if os.name == "posix":
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            directory_fd = os.open(path.anchor, flags)
            stack.callback(os.close, directory_fd)
            for part in path.parts[1:]:
                directory_fd = os.open(part, flags, dir_fd=directory_fd)
                stack.callback(os.close, directory_fd)
        elif os.name == "nt":
            # UNC/device namespaces can access a network or pipe, not an offline
            # local report. Keep drive ancestors locked until both files finish.
            require(re.fullmatch(r"[A-Za-z]:", path.drive) is not None
                    and all(":" not in part for part in path.parts[1:]), "unsafe_report_path")
            current = Path(path.anchor)
            _windows_open(current, True, stack)
            for part in path.parts[1:]:
                current = current / part
                _windows_open(current, True, stack)
        else:
            raise InvalidReport("unsupported_filesystem")

        def entries():
            names = set()
            with os.scandir(directory_fd if directory_fd is not None else path) as iterator:
                for entry in iterator:
                    names.add(entry.name)
                    require(len(names) <= len(FILES), "unexpected_report_files")
            return names

        require(entries() == FILES, "unexpected_report_files")
        values, fingerprints, descriptors = {}, {}, {}
        for name, limit in (("manifest.json", MAX_MANIFEST_BYTES), ("result.json", MAX_RESULT_BYTES)):
            if directory_fd is not None:
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
                fd = os.open(name, flags, dir_fd=directory_fd)
                stack.callback(os.close, fd)
            else:
                fd = _windows_open(path / name, False, stack)
            descriptors[name] = fd
            values[name], fingerprints[name] = _read_fd(fd, limit)
        require(entries() == FILES, "report_changed")
        for name, expected in fingerprints.items():
            _verify_unchanged(name, descriptors[name], expected, directory_fd)
        return values["manifest.json"], values["result.json"]


def verify_manifest(raw, result):
    manifest = _json(raw)
    require(isinstance(manifest, dict) and set(manifest) == {"version", "complete", "files", "physical_display_verified"},
            "invalid_manifest")
    require(type(manifest["version"]) is int and manifest["version"] == 1
            and manifest["complete"] is True and manifest["physical_display_verified"] is False, "invalid_manifest")
    files = manifest["files"]
    require(isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict), "invalid_manifest")
    entry = files[0]
    require(set(entry) == {"name", "bytes", "sha256"} and entry["name"] == "result.json"
            and type(entry["bytes"]) is int and 0 < entry["bytes"] <= MAX_RESULT_BYTES
            and isinstance(entry["sha256"], str) and HEX.fullmatch(entry["sha256"]), "invalid_manifest")
    require(entry["bytes"] == len(result), "byte_count_mismatch")
    require(entry["sha256"] == hashlib.sha256(result).hexdigest(), "digest_mismatch")


def _timestamp(value):
    require(isinstance(value, str) and 1 <= len(value) <= 40)
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value))
    require(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None)


def _logs(value, api, workbench, runtime):
    require(isinstance(value, dict) and type(value.get("version")) is int and value["version"] == 1)
    require(value.get("runtime_correlation") in ("matched", "mismatch", "unavailable"))
    workbench.number(integral=True)(value.get("unknown_events_omitted"))
    events = value.get("events")
    require(isinstance(events, list) and len(events) <= 100)
    for row in events:
        require(isinstance(row, dict) and row.get("event") in workbench.NATIVE_EVENTS)
        workbench.number()(row.get("time"))
        if "runtime" in row:
            require(isinstance(row["runtime"], str) and bool(row["runtime"]))
        if "boot_id" in row:
            require(isinstance(row["boot_id"], str))
    if value["runtime_correlation"] == "matched":
        require(all(row.get("runtime") == runtime for row in events))
    api.android_metadata({"version": 1, "events": events}, "maintenance", {"operation": "logs"})


def _observations(rows, api, workbench, bundle, ids):
    require(isinstance(rows, list) and 1 <= len(rows) <= 2)
    require(all(isinstance(row, dict) for row in rows))
    require([row.get("lane") for row in rows] in (["web"], ["native"], ["web", "native"]))
    result = []
    for raw in rows:
        lane, status = raw["lane"], raw.get("status")
        require(status in ("observed", "unknown") and raw.get("source") == "runtime_reported")
        _timestamp(raw.get("observed_at"))
        requests = raw.get("requests")
        require(isinstance(requests, list) and len(requests) <= 2)
        for receipt in requests:
            require(isinstance(receipt, dict) and isinstance(receipt.get("request_id"), str)
                    and workbench.REQUEST.fullmatch(receipt["request_id"])
                    and receipt.get("status") in ("ok", "unconfirmed"))
            require(receipt["request_id"] not in ids)
            ids.add(receipt["request_id"])
            require(receipt.get("action") in (("capabilities", "inspect") if lane == "web" else ("maintenance",)))
        row = {"lane": lane, "status": status}
        caps = api.capabilities_metadata(raw["capabilities"]) if "capabilities" in raw else None
        if lane == "native":
            require(caps is None)
        if status == "unknown":
            reason = raw.get("reason")
            require(reason in ERROR_REASONS and "data" not in raw and "runtime" not in raw)
            require(reason != "not_bound" or lane == "native" and not requests)
            require("logs" not in raw and "logs_error" not in raw)
            row["reason"] = reason
        else:
            require("reason" not in raw and isinstance(raw.get("runtime"), str))
            runtime = workbench.token(raw["runtime"])
            if lane == "web":
                require(caps is not None and caps["player"]["runtime"] == runtime
                        and "snapshot" in caps.get("inspect", {}).get("sections", []))
                require([item["action"] for item in requests] == ["capabilities", "inspect"]
                        and all(item["status"] == "ok" for item in requests))
                data = workbench.snapshot_metadata({"version": 1, "runtime": runtime, "section": "snapshot", "data": raw.get("data")},
                                                   runtime, "snapshot")
                require("logs" not in raw and "logs_error" not in raw)
            else:
                require(requests and requests[0]["status"] == "ok" and (bundle or len(requests) == 1))
                data = workbench.native_metadata(api, raw.get("data"), "health")
                require(data["runtime"] == runtime)
                original = raw["data"]
                require(type(original.get("media_identity_available")) is bool
                        and original["media_identity_available"] == data["media_identity_available"])
                if "physical_display_verified" in original:
                    require(original["physical_display_verified"] is False)
                if "logs" in raw:
                    require(bundle and "logs_error" not in raw and len(requests) == 2 and requests[-1]["status"] == "ok")
                    _logs(raw["logs"], api, workbench, runtime)
                if "logs_error" in raw:
                    require(bundle and raw["logs_error"] in ERROR_REASONS)
            row.update(data=data, runtime=runtime)
        result.append(row)
    return result


def evaluate_report(report, api, workbench):
    require(isinstance(report, dict) and type(report.get("version")) is int and report["version"] == 1)
    policy = report.get("evaluator", LEGACY_POLICY)
    require("evaluator" not in report or policy == POLICY, "unsupported_evaluator")
    require(report.get("command") in ("bundle", "test") and report.get("read_only") is True
            and report.get("physical_display_verified") is False)
    require(isinstance(report.get("device_id"), str)
            and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", report["device_id"]))
    _timestamp(report.get("started_at"))
    _timestamp(report.get("completed_at"))
    tooling = report.get("tooling")
    require(isinstance(tooling, dict) and all(isinstance(tooling.get(key), str) and HEX.fullmatch(tooling[key])
                                             for key in ("workbench_sha256", "cli_sha256")))
    bundle = report["command"] == "bundle"
    ids = set()
    before = _observations(report.get("observations"), api, workbench, bundle, ids)
    if bundle:
        require("scenario" not in report and "reason" not in report and "final_observations" not in report)
        kind = "bundle"
        verdict = "observed" if any(row["status"] == "observed" for row in before) else "unknown"
        reason = "bundle_integrity_verified"
    elif report.get("scenario") == "health":
        require("reason" not in report and "final_observations" not in report)
        kind, verdict, reason = "test.health", workbench.health_verdict(before), "health_recomputed"
    elif report.get("scenario") == "media-progress":
        kind = "test.media-progress"
        workbench.number(1, 30)(report.get("duration_seconds"))
        require(report.get("evidence_level") == "decoder_progress_only")
        interval = report.get("sample_interval_seconds")
        require(isinstance(interval, dict) and "min" in interval and "max" in interval)
        low, high = workbench.number()(interval["min"]), workbench.number()(interval["max"])
        require(low <= high)
        after = _observations(report.get("final_observations"), api, workbench, False, ids)
        require([row["lane"] for row in before] == [row["lane"] for row in after])
        if policy == LEGACY_POLICY and before[0]["lane"] == "native":
            verdict, reason = "unknown", "media_identity_unavailable"
        else:
            verdict, reason = workbench.progress_verdict(before, after, (low, high))
        require(report.get("reason") == reason, "semantic_mismatch")
    else:
        raise InvalidReport("unsupported_report")
    require(report.get("verdict") == verdict, "semantic_mismatch")
    return {"evaluation_policy": policy, "report_kind": kind, "verdict": verdict, "reason": reason}


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InvalidReport("invalid_arguments")


def main(argv, api, workbench, json_output=False):
    """No config or client access. A valid digest is not an authenticated report."""
    result = {"version": 1, "command": "report_verify", "valid": False, "integrity": "unverified",
              "authenticated": False, "physical_display_verified": False,
              "evaluation_policy": None, "report_kind": None, "verdict": "error"}
    json_output = json_output or any(arg in ("--json", "-j") for arg in argv)
    try:
        parser = Parser(prog="ott report", allow_abbrev=False,
                        description="Verify a local workbench report without contacting a player. Integrity is not authenticity.")
        parser.add_argument("action", choices=("verify",))
        parser.add_argument("directory", metavar="DIR")
        parser.add_argument("-j", "--json", action="store_true", default=json_output)
        args = parser.parse_args(argv)
        json_output = args.json
        manifest, raw = read_bundle(args.directory)
        try:
            verify_manifest(manifest, raw)
        except InvalidReport:
            result["integrity"] = "invalid"
            raise
        result["integrity"] = "verified"
        report = _json(raw)
        if isinstance(report, dict):
            marker = report.get("evaluator", LEGACY_POLICY)
            if marker in (POLICY, LEGACY_POLICY) and isinstance(marker, str):
                result["evaluation_policy"] = marker
        result.update(evaluate_report(report, api, workbench), valid=True)
    except SystemExit as exc:
        return int(exc.code)
    except InvalidReport as exc:
        result["reason"] = exc.reason
    except OSError:
        result["reason"] = "report_unreadable"
    except (api.Error, workbench.InvalidData, ValueError, TypeError, KeyError,
            AttributeError, OverflowError, RecursionError):
        # Neither library validation errors nor malicious metadata may print
        # filenames, contents, credentials or a traceback at this boundary.
        result["reason"] = "invalid_report"
    if json_output:
        print(json.dumps(result, ensure_ascii=True, allow_nan=False, sort_keys=True))
    else:
        print("Report verification: " + (result["verdict"] if result["valid"] else "invalid"))
        print("Integrity: " + result["integrity"] + "; authenticated: false; physical display verified: false")
        print("Reason: " + result["reason"])
    return {"observed": 0, "pass": 0, "fail": 2, "unknown": 3, "error": 1}[result["verdict"]]
