"""Offline integrity and semantic replay for bounded diagnostic reports."""
import argparse
import datetime
import hashlib
import importlib.util
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
DEBUG_POLICY = "workbench-v3"
ENGINE_POLICY = "workbench-v4"
PROVIDER_POLICY = "workbench-v5"
PERFORMANCE_POLICIES = ("performance-v1", "performance-v2", "performance-v3")
MAX_PERFORMANCE_BYTES = 1024 * 1024
MAX_PERFORMANCE_MANIFEST = 16 * 1024
PERFORMANCE_FILES = FILES | {"trial-plan.json"} | {"request-%03d.json" % index for index in range(96)}
LEGACY_POLICY = "legacy-web-v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")
ERROR_REASONS = frozenset((
    "invalid_native_binding", "not_bound", "deadline_exceeded", "invalid_response",
    "unsupported", "invalid_request", "runtime_mismatch", "unavailable",
    "unsupported_controller", "unauthorized", "rejected",
    "controller_unavailable", "controller_http_error", "request_pending_timeout",
    "receipt_missing", "rate_limited",
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


def read_bundle(directory, include_performance=False):
    """Read only fixed report names, with bounded reads and no symlink traversal."""
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
                    require(len(names) <= len(FILES) or include_performance and
                            len(names) <= len(PERFORMANCE_FILES) and names <= PERFORMANCE_FILES,
                            "unexpected_report_files")
            return names

        inventory = entries()
        require(FILES <= inventory and (inventory == FILES or include_performance and
                inventory <= PERFORMANCE_FILES), "unexpected_report_files")
        expanded = inventory != FILES
        values, fingerprints, descriptors = {}, {}, {}
        limits = [("manifest.json", MAX_PERFORMANCE_MANIFEST if expanded else MAX_MANIFEST_BYTES),
                  ("result.json", MAX_PERFORMANCE_BYTES if expanded else MAX_RESULT_BYTES)]
        limits += [(name, 16 * 1024) for name in sorted(inventory - FILES)]
        total = 0
        for name, limit in limits:
            if directory_fd is not None:
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
                fd = os.open(name, flags, dir_fd=directory_fd)
                stack.callback(os.close, fd)
            else:
                fd = _windows_open(path / name, False, stack)
            descriptors[name] = fd
            values[name], fingerprints[name] = _read_fd(fd, limit)
            total += len(values[name])
            require(total <= MAX_PERFORMANCE_BYTES, "report_size_invalid")
        require(entries() == inventory, "report_changed")
        for name, expected in fingerprints.items():
            _verify_unchanged(name, descriptors[name], expected, directory_fd)
        if include_performance:
            return values["manifest.json"], values["result.json"], {name: values[name] for name in inventory - FILES}
        return values["manifest.json"], values["result.json"]


def verify_manifest(raw, result, extra_files=None):
    manifest = _json(raw)
    if isinstance(manifest, dict) and "capture_complete" in manifest:
        require(set(manifest) == {"version", "complete", "files", "capture_complete", "physical_display_verified"}
                and type(manifest["version"]) is int and manifest["version"] == 1
                and manifest["complete"] is True and type(manifest["capture_complete"]) is bool
                and manifest["physical_display_verified"] is False, "invalid_manifest")
        values = {"result.json": result, **(extra_files or {})}
        require(set(values) <= PERFORMANCE_FILES - {"manifest.json"}, "invalid_manifest")
        files = manifest["files"]
        require(isinstance(files, list) and len(files) == len(values) <= 98, "invalid_manifest")
        seen = set()
        for entry in files:
            require(isinstance(entry, dict) and set(entry) == {"name", "bytes", "sha256"}, "invalid_manifest")
            name = entry["name"]
            require(isinstance(name, str) and name in values and name not in seen
                    and type(entry["bytes"]) is int and 0 < entry["bytes"] <= MAX_PERFORMANCE_BYTES
                    and isinstance(entry["sha256"], str) and HEX.fullmatch(entry["sha256"]), "invalid_manifest")
            seen.add(name)
            require(entry["bytes"] == len(values[name]), "byte_count_mismatch")
            require(entry["sha256"] == hashlib.sha256(values[name]).hexdigest(), "digest_mismatch")
        require(seen == set(values) and len(raw) + sum(map(len, values.values())) <= MAX_PERFORMANCE_BYTES,
                "invalid_manifest")
        return manifest
    require(not extra_files, "unexpected_report_files")
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
    return manifest


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


def _observations(rows, api, workbench, bundle, ids, section="snapshot", strongest_debug=False):
    require(isinstance(rows, list) and 1 <= len(rows) <= 2)
    require(all(isinstance(row, dict) for row in rows))
    require([row.get("lane") for row in rows] in (["web"], ["native"], ["web", "native"]))
    require(section != "debug" or [row["lane"] for row in rows] == ["web"])
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
                        and ("debug" in caps if section == "debug" else "snapshot" in caps.get("inspect", {}).get("sections", [])))
                require([item["action"] for item in requests] == ["capabilities", "inspect"]
                        and all(item["status"] == "ok" for item in requests))
                version = (3 if caps.get("debug_attempts") == {"version": 3} else 2) if (
                    strongest_debug and section == "debug" and caps.get("debug_versions") == [1, 2]) else 1
                envelope = {"version": version, "runtime": runtime, "section": section, "data": raw.get("data")}
                data = (workbench.debug_metadata(envelope, runtime, version) if section == "debug"
                        else workbench.snapshot_metadata(envelope, runtime, "snapshot"))
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


def _provider_observations(rows, api, workbench, ids):
    """Validate saved provider DTOs, never a raw status or private configuration."""
    require(isinstance(rows, list) and len(rows) == 1)
    raw = rows[0]
    common = {"lane", "status", "source", "observed_at", "requests"}
    require(isinstance(raw, dict) and common <= set(raw)
            and raw["lane"] == "web" and raw["source"] == "runtime_reported")
    status = raw["status"]
    require(status in ("observed", "unknown"))
    _timestamp(raw["observed_at"])
    receipts = raw["requests"]
    require(isinstance(receipts, list) and len(receipts) <= 2)
    for receipt in receipts:
        require(isinstance(receipt, dict) and set(receipt) == {"action", "request_id", "status"}
                and isinstance(receipt["request_id"], str) and workbench.REQUEST.fullmatch(receipt["request_id"])
                and receipt["status"] in ("ok", "unconfirmed"))
        require(receipt["request_id"] not in ids)
        ids.add(receipt["request_id"])
    require([row["action"] for row in receipts] in ([], ["capabilities"], ["capabilities", "status"]))
    require(all(row["status"] == "ok" for row in receipts[:-1]))
    caps = api.capabilities_metadata(raw["capabilities"]) if "capabilities" in raw else None
    if caps is not None:
        require(caps == raw["capabilities"] and receipts and receipts[0]["status"] == "ok")
    if status == "unknown":
        require(common | {"reason"} <= set(raw) <= common | {"reason", "capabilities"})
        require(raw["reason"] in ERROR_REASONS and raw["reason"] != "not_bound")
        return [{"lane": "web", "status": status, "reason": raw["reason"]}]
    require(set(raw) == common | {"capabilities", "runtime", "data"})
    require(caps is not None and caps.get("provider_diagnostics") == {"version": 1})
    runtime = workbench.token(raw["runtime"])
    require(caps["player"]["runtime"] == runtime
            and len(receipts) == 2 and all(row["status"] == "ok" for row in receipts))
    data = workbench.provider_metadata(raw["data"], runtime)
    return [{"lane": "web", "status": status, "runtime": runtime, "data": data}]


def evaluate_report(report, api, workbench):
    require(isinstance(report, dict) and type(report.get("version")) is int and report["version"] == 1)
    policy = report.get("evaluator", LEGACY_POLICY)
    require("evaluator" not in report or policy in (POLICY, DEBUG_POLICY, ENGINE_POLICY, PROVIDER_POLICY), "unsupported_evaluator")
    commands = ("bundle", "doctor", "debug") if policy == PROVIDER_POLICY else ("bundle", "test")
    require(report.get("command") in commands and report.get("read_only") is True
            and report.get("physical_display_verified") is False)
    require(isinstance(report.get("device_id"), str)
            and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", report["device_id"]))
    _timestamp(report.get("started_at"))
    _timestamp(report.get("completed_at"))
    tooling = report.get("tooling")
    require(isinstance(tooling, dict) and all(isinstance(tooling.get(key), str) and HEX.fullmatch(tooling[key])
                                             for key in ("workbench_sha256", "cli_sha256")))
    bundle = report["command"] == "bundle"
    if policy == PROVIDER_POLICY:
        require(set(report) == {"version", "evaluator", "command", "device_id", "started_at", "completed_at",
                                "read_only", "physical_display_verified", "tooling", "observations",
                                "debug_observations", "provider_observations", "verdict"})
        require(set(tooling) == {"workbench_sha256", "cli_sha256"})
    ids = set()
    before = _observations(report.get("observations"), api, workbench, bundle, ids)
    if policy in (DEBUG_POLICY, ENGINE_POLICY, PROVIDER_POLICY):
        require((bundle or policy == PROVIDER_POLICY) and "debug_observations" in report)
        debug = report["debug_observations"]
        if [row["lane"] for row in before] == ["native"]:
            require(debug == [])
        else:
            _observations(debug, api, workbench, True, ids, "debug",
                          strongest_debug=policy in (ENGINE_POLICY, PROVIDER_POLICY))
    else:
        require("debug_observations" not in report)
    correlation = None
    if policy == PROVIDER_POLICY:
        if [row["lane"] for row in before] == ["native"]:
            require(report["provider_observations"] == [])
        else:
            _provider_observations(report["provider_observations"], api, workbench, ids)
        correlation = workbench.provider_correlation(before, report["provider_observations"], debug)
        require(correlation in ("matched", "mixed", "unavailable"))
    else:
        require("provider_observations" not in report and "provider_correlation" not in report)
    if bundle or policy == PROVIDER_POLICY:
        require("scenario" not in report and "reason" not in report and "final_observations" not in report)
        kind = report["command"]
        verdict = "observed" if any(row["status"] == "observed" for row in before) else "unknown"
        if not bundle:
            verdict = workbench.provider_verdict(report["provider_observations"])
        reason = "bundle_integrity_verified" if bundle else "provider_report_integrity_verified"
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
    result = {"evaluation_policy": policy, "report_kind": kind, "verdict": verdict, "reason": reason}
    if correlation is not None:
        result["provider_correlation"] = correlation
    return result


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise InvalidReport("invalid_arguments")


def _performance_module():
    # Load the installed schema beside this verifier, never a path in a report.
    path = Path(__file__).resolve().with_name("performance.py")
    spec = importlib.util.spec_from_file_location("ott_report_performance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _performance_requests(report, extra_files, perf):
    rows = report.get("requests")
    require(isinstance(rows, list) and len(rows) <= 34)
    require(set(extra_files) == {"request-%03d.json" % index for index in range(len(rows))},
            "unexpected_report_files")
    failures = set(perf.ERRORS) | set(perf.REQUEST_FAILURE_REASONS) | {"transport_error", "unsupported_controller"}
    ids, previous_end, previous_cursor = set(), 0, -1
    for index, row in enumerate(rows):
        require(isinstance(row, dict) and set(row) >= {
            "action", "params", "started_monotonic", "finished_monotonic", "state"}
            and set(row) <= {"action", "params", "started_monotonic", "finished_monotonic", "state", "request_id", "reason"})
        perf.N(row["started_monotonic"])
        perf.N(row["finished_monotonic"])
        require(previous_end <= row["started_monotonic"] <= row["finished_monotonic"])
        previous_end = row["finished_monotonic"]
        require(row["state"] in ("confirmed", "confirmed_late", "unconfirmed", "rejected"))
        require(index == len(rows) - 1 or row["state"] == "confirmed")
        if row["state"] in ("confirmed", "confirmed_late"):
            perf.matching(r"[A-Za-z0-9_.:-]{1,80}")(row.get("request_id"))
            require(row["request_id"] not in ids and "reason" not in row)
            ids.add(row["request_id"])
        else:
            require("request_id" not in row)
            require("reason" not in row or row["reason"] in failures)
            require(row["state"] != "rejected" or "reason" in row)
        params = row["params"]
        if index == 0:
            require(row["action"] == "capabilities" and params == {})
        else:
            require(row["action"] == "perf_capture" and isinstance(params, dict))
            op = "status" if index == 1 else "read"
            perf.fields(version=perf.one(report["version"]), runtime=perf.matching(perf.RUNTIME),
                        op=perf.one(op), session_id=perf.matching(perf.SESSION_ID),
                        **({"after_seq": perf.I, "limit": perf.one(64)} if op == "read" else {}))(params)
            require(params["session_id"] == report.get("session_id"))
            if "capture" in report:
                require(params["runtime"] == report["capture"]["runtime"])
            if op == "read":
                require(params["after_seq"] > previous_cursor and (index != 2 or params["after_seq"] == 0))
                previous_cursor = params["after_seq"]
        expected = {key: row[key] for key in ("action", "params", "started_monotonic")}
        expected["state"] = "sent"
        require(perf.encoded(_json(extra_files["request-%03d.json" % index])) == perf.encoded(expected),
                "request_journal_mismatch")
    return rows


def evaluate_performance_report(report, manifest, extra_files, api):
    """Replay export structure and completeness, never commands or trial effects."""
    perf = _performance_module()
    try:
        require(report.get("operation") == "export", "unsupported_report")
        required = {"version", "evaluator", "device_id", "physical_display_verified", "started_at_unix_ms",
                    "completed_at_unix_ms", "operation", "read_only", "requests"}
        require(required <= set(report) <= required | {"session_id", "capture", "reason", "unknown_outcome",
                                                       "confirmed_late_result", "output"})
        perf.one(1, 2, 3)(report["version"])
        require(report["evaluator"] == "performance-v" + str(report["version"]))
        require(report["read_only"] is True and report["physical_display_verified"] is False)
        perf.matching(perf.RUNTIME)(report["device_id"])
        perf.I(report["started_at_unix_ms"])
        perf.I(report["completed_at_unix_ms"])
        # Wall-clock adjustment during collection is not proof of invalid media.
        if "output" in report:  # Earlier exporters included this local-only field.
            require(isinstance(report["output"], str) and len(report["output"]) <= 4096)
        if "session_id" in report:
            perf.matching(perf.SESSION_ID)(report["session_id"])
        if "unknown_outcome" in report:
            perf.B(report["unknown_outcome"])
        rows = _performance_requests(report, extra_files, perf)
        reasons_allowed = set(perf.ERRORS) | set(perf.REQUEST_FAILURE_REASONS) | {
            "discarded_prefix", "sequence_gap", "page_limit", "events_dropped", "missing_tail",
            "deadline_exceeded", "request_limit", "transport_error", "unauthorized", "unsupported_controller",
            "invalid_response", "session_changed", "session_clock_regressed", "nonadvancing_export",
            "nonmonotonic_export", "export_event_limit", "export_size_limit", "operation_failed", "interrupted"}
        if "capture" not in report:
            require(report.get("reason") in reasons_allowed and manifest.get("capture_complete") is False)
            if "confirmed_late_result" in report:
                require(rows and rows[-1]["state"] == "confirmed_late")
                if rows[-1]["action"] == "capabilities":
                    api.capabilities_metadata(report["confirmed_late_result"])
                else:
                    perf.metadata(report["confirmed_late_result"], rows[-1]["params"])
            return {"evaluation_policy": report["evaluator"], "report_kind": "perf.export", "verdict": "unknown",
                    "reason": "capture_unavailable", "capture_complete": False, "partial_reasons": [report["reason"]]}
        require("reason" not in report and "confirmed_late_result" not in report)
        capture = report["capture"]
        perf.fields(version=perf.one(report["version"]), runtime=perf.matching(perf.RUNTIME),
                    session_id=perf.one(report["session_id"]), scope=perf.one("frozen_highwater"),
                    highwater=perf.I, pages=perf.num(0, 32, True), first_session=lambda _: None,
                    last_session=lambda _: None, events=perf.array(lambda _: None, 1024),
                    complete=perf.B, partial_reasons=perf.array(perf.one(*reasons_allowed), len(reasons_allowed), True))(capture)
        first, last = capture["first_session"], capture["last_session"]
        for session in (first, last):
            perf.validate_session(session, report["version"])
            require(session["id"] == capture["session_id"])
        require(capture["highwater"] == first["next_seq"] - 1)
        require(len(rows) >= 2 and all(row["state"] == "confirmed" for row in rows[:2]))
        reads = rows[2:]
        require(capture["pages"] == sum(row["state"] == "confirmed" for row in reads))
        require(all(row["params"]["after_seq"] < capture["highwater"] for row in reads))
        require(len(capture["events"]) <= 64 * capture["pages"])
        reasons, mandatory = set(capture["partial_reasons"]), set()
        stable_keys = ("id", "started_at_unix_ms", "duration_seconds", "clock", "requested", "supported", "capture_version")
        changed = any(first.get(key) != last.get(key) for key in stable_keys)
        regressed = last["elapsed_ms"] < first["elapsed_ms"]
        if changed:
            mandatory.add("session_changed")
        if regressed:
            mandatory.add("session_clock_regressed")
        if first["dropped_events"] or last["dropped_events"]:
            mandatory.add("events_dropped")
        if reads and reads[-1]["state"] != "confirmed":
            require(bool(reasons), "semantic_mismatch")
        if first["first_seq"] > 1 and capture["events"]:
            mandatory.add("discarded_prefix")
        events = capture["events"]
        # A failed page may have replaced `last_session` before the collector
        # detected a changed or regressed boundary. Retained earlier events
        # remain useful, but cannot establish complete capture.
        event_session = dict(first, next_seq=capture["highwater"] + 1,
                             elapsed_ms=max(first["elapsed_ms"], last["elapsed_ms"]))
        if changed or regressed or "session_clock_regressed" in reasons:
            event_session["elapsed_ms"] = perf.SAFE
        previous, elapsed, event_bytes = 0, -1, 0
        for event in events:
            perf.validate_event(event, event_session)
            require(previous < event["seq"] <= capture["highwater"] and event["at_ms"] >= elapsed)
            if event["seq"] != previous + 1:
                mandatory.add("sequence_gap")
            previous, elapsed = event["seq"], event["at_ms"]
            event_bytes += len(perf.encoded(event)) - 1
        require(event_bytes <= 524288)
        if previous < capture["highwater"]:
            mandatory.add("missing_tail")
        require(mandatory <= reasons and capture["complete"] == (not reasons), "semantic_mismatch")
        require(manifest.get("capture_complete") is capture["complete"], "semantic_mismatch")
        require("page_limit" not in reasons or capture["pages"] == 32, "semantic_mismatch")
        if capture["complete"]:
            require(not changed and not regressed and last["next_seq"] >= first["next_seq"]
                    and all(row["state"] == "confirmed" for row in rows), "semantic_mismatch")
        return {"evaluation_policy": report["evaluator"], "report_kind": "perf.export",
                "verdict": "observed" if capture["complete"] else "unknown",
                "reason": "capture_completeness_verified", "capture_complete": capture["complete"],
                "event_count": len(events), "highwater": capture["highwater"], "partial_reasons": sorted(reasons)}
    except perf.Failure:
        raise InvalidReport("invalid_performance_report") from None


def main(argv, api, workbench, json_output=False):
    """No config or client access. A valid digest is not an authenticated report."""
    result = {"version": 1, "command": "report_verify", "valid": False, "integrity": "unverified",
              "authenticated": False, "physical_display_verified": False,
              "evaluation_policy": None, "report_kind": None, "verdict": "error"}
    json_output = json_output or any(arg in ("--json", "-j") for arg in argv)
    try:
        parser = Parser(prog="ott report", allow_abbrev=False,
                        description="Verify a local workbench or performance export without contacting a player. Integrity is not authenticity.")
        parser.add_argument("action", choices=("verify",))
        parser.add_argument("directory", metavar="DIR")
        parser.add_argument("-j", "--json", action="store_true", default=json_output)
        args = parser.parse_args(argv)
        json_output = args.json
        manifest, raw, extra_files = read_bundle(args.directory, include_performance=True)
        try:
            metadata = verify_manifest(manifest, raw, extra_files)
        except InvalidReport:
            result["integrity"] = "invalid"
            raise
        result["integrity"] = "verified"
        report = _json(raw)
        if isinstance(report, dict):
            marker = report.get("evaluator", LEGACY_POLICY)
            if isinstance(marker, str) and marker in (POLICY, DEBUG_POLICY, ENGINE_POLICY, PROVIDER_POLICY, LEGACY_POLICY, *PERFORMANCE_POLICIES):
                result["evaluation_policy"] = marker
        if isinstance(report, dict) and report.get("evaluator") in PERFORMANCE_POLICIES:
            require("capture_complete" in metadata, "invalid_manifest")
            result.update(evaluate_performance_report(report, metadata, extra_files, api), valid=True)
        else:
            require("capture_complete" not in metadata, "invalid_manifest")
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
        if "capture_complete" in result:
            print("Capture: " + ("complete" if result["capture_complete"] else "partial or unavailable"))
            if result.get("partial_reasons"):
                print("Partial reasons: " + ", ".join(result["partial_reasons"]))
        print("Reason: " + result["reason"])
    return {"observed": 0, "pass": 0, "fail": 2, "unknown": 3, "error": 1}[result["verdict"]]
