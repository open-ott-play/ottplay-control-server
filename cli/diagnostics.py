#!/usr/bin/env python3
"""Bounded HTTPS client for OTT-play diagnostic protocol 2 (Python stdlib only)."""

import argparse
import json
import math
import os
import queue
import re
import stat
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

BASE = "/api/v2/diagnostics"
MAX_REPLY = 65536
SAFE_INTEGER = 9007199254740991
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,80}\Z")
DEVICE = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")
MESSAGES = {
    "invalid_arguments": "Invalid arguments; consult --help.",
    "invalid_server": "Use an HTTPS controller base without credentials, query or fragment.",
    "invalid_token": "A valid scoped operator credential is required.",
    "unsafe_token_file": "Token file must be an owned private regular file, not a symlink.",
    "token_file_unsupported": "Token files require POSIX ownership checks; use --token-env on this platform.",
    "token_unavailable": "Cannot read the configured operator credential.",
    "invalid_input": "Invalid identifier, epoch, action, time budget or pagination value.",
    "invalid_response": "The controller returned an invalid diagnostic response.",
    "response_too_large": "The controller response exceeded the bounded read limit.",
    "redirect_refused": "Controller redirects are prohibited.",
    "transport_error": "The controller request failed.",
    "timeout": "The controller request exceeded its time budget.",
    "request_in_progress": "An earlier request is still in progress; do not repeat a mutation.",
    "http_error": "The controller rejected the request.",
    "epoch_changed": "The controller epoch changed; inspect current state before another mutation.",
}


class DiagnosticError(Exception):
    def __init__(self, code, *, unknown=False, status=None, server_code=None):
        self.code = code if code in MESSAGES else "invalid_response"
        self.unknown = unknown
        self.status = status
        self.server_code = server_code
        super().__init__(MESSAGES[self.code])

    def public(self):
        error = {"code": self.code, "message": str(self), "unknown_outcome": self.unknown}
        if self.status is not None:
            error["http_status"] = self.status
        if self.server_code in SERVER_ERROR_CODES:
            error["server_code"] = self.server_code
        return {"error": error}


def identifier(value, device=False):
    if not isinstance(value, str) or not (DEVICE if device else IDENTIFIER).fullmatch(value):
        raise DiagnosticError("invalid_input")
    return value


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise DiagnosticError("invalid_input")
    return value


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("nonfinite")

    try:
        # JSON over both HTTPS and MCP stdio is UTF-8. json.loads(bytes)
        # otherwise silently accepts UTF-16/32 encodings and BOMs.
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise DiagnosticError("invalid_response") from None


def server_base(value):
    try:
        if not isinstance(value, str) or re.search(r"[\s\\%]", value):
            raise ValueError()
        url = urllib.parse.urlsplit(value)
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment
                or any(part in (".", "..") for part in url.path.split("/"))
                or "//" in url.path or url.port == 0):
            raise ValueError()
        # Evaluate port parsing even when not otherwise needed.
        _ = url.port
        return urllib.parse.urlunsplit((url.scheme, url.netloc, url.path.rstrip("/"), "", ""))
    except (ValueError, TypeError):
        raise DiagnosticError("invalid_server") from None


def load_token(*, env=None, path=None):
    if bool(env) == bool(path):
        raise DiagnosticError("invalid_token")
    if env:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", env):
            raise DiagnosticError("invalid_token")
        token = os.environ.get(env, "")
    else:
        # POSIX ownership/mode checks cannot establish a Windows ACL; use env there.
        if os.name != "posix":
            raise DiagnosticError("token_file_unsupported")
        fd = None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            metadata = os.fstat(fd)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                    or metadata.st_mode & 0o077 or metadata.st_size > 258):
                raise DiagnosticError("unsafe_token_file")
            token = os.read(fd, 259).decode("ascii").rstrip("\r\n")
        except DiagnosticError:
            raise
        except (OSError, UnicodeError, TypeError):
            raise DiagnosticError("token_unavailable") from None
        finally:
            if fd is not None:
                os.close(fd)
    if not TOKEN.fullmatch(token):
        raise DiagnosticError("invalid_token")
    return token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DiagnosticError("redirect_refused")


# Public output deliberately omits unknown server fields and all raw messages.
PUBLIC_KEYS = frozenset("""diagnostics_protocol server_epoch runtimes runtime_id device_id
instance_id boot_id reported_uuid last_seen_age_ms consent granted epoch capabilities
active_session_id session_id request_id state control_revision device_stop_confirmed
age_ms remaining_lease_ms lease_remaining_ms lease_ms profile status result error_code
events seq first_seq last_seq next_seq truncated_before_seq dropped_total elapsed_ms
kind code metrics target created_age_ms updated_age_ms retained_events accepted_through_seq
start_request_id stop_request_id start_revision stop_revision last_result session event
idempotency_retention_ms repair_id action
""".split())
BOOLEAN_METRICS = frozenset(("paused", "ended", "available", "enabled"))
METRICS = frozenset("""errors dropped recoveries stalls waiting bufferAhead currentTime
droppedFrames errorCode networkState readyState height width duration httpStatus latencyMs
loadedBytes inputEvents inputListeners epgEntries epgPending epgErrors paused ended available enabled""".split())


def public_output(value, token, depth=0, metrics=False):
    if depth > 10:
        raise DiagnosticError("invalid_response")
    if isinstance(value, dict):
        allowed = METRICS if metrics else PUBLIC_KEYS
        result = {}
        for key, item in value.items():
            if key not in allowed:
                continue
            if metrics:
                if key in BOOLEAN_METRICS:
                    valid = type(item) is bool
                else:
                    valid = (type(item) in (int, float) and 0 <= item <= SAFE_INTEGER
                             and math.isfinite(item))
                if not valid:
                    raise DiagnosticError("invalid_response")
            result[key] = public_output(item, token, depth + 1, key == "metrics")
        return result
    if isinstance(value, list):
        if len(value) > 256:
            raise DiagnosticError("invalid_response")
        return [public_output(item, token, depth + 1, metrics) for item in value]
    if isinstance(value, str):
        if token in value:
            return "[redacted]"
        if len(value) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:-]*", value):
            raise DiagnosticError("invalid_response")
        return value
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(value) and abs(value) <= SAFE_INTEGER:
        return value
    raise DiagnosticError("invalid_response")


SESSION_STATES = frozenset(("start_pending", "active", "stop_pending", "stopped",
                            "rejected", "expired", "revoked"))
EVENT_KINDS = frozenset(("lifecycle", "playback", "network", "input", "epg"))
EVENT_CODES = frozenset(("sample", "start", "stop", "waiting", "playing", "stalled",
                         "ended", "error", "ready"))
REPAIR_ACTIONS = ("restart_stream", "reload_player")
REPAIR_STATES = frozenset(("pending", "applied", "accepted", "rejected", "unsupported",
                           "expired", "revoked"))


def validate_reply(value, kind, context):
    """Fail visibly if an accepted reply cannot establish its documented result."""
    def check(condition):
        if not condition:
            raise DiagnosticError("invalid_response")

    def number(item, maximum=SAFE_INTEGER, minimum=0, integral=True):
        check(type(item) in ((int,) if integral else (int, float)))
        check(minimum <= item <= maximum and math.isfinite(item))

    def label(item, device=False):
        check(isinstance(item, str) and (DEVICE if device else IDENTIFIER).fullmatch(item))

    check("error" not in value)
    if kind == "runtimes":
        rows = value.get("runtimes")
        check(isinstance(rows, list) and len(rows) <= 256)
        seen = set()
        for row in rows:
            check(isinstance(row, dict))
            for key in ("runtime_id", "instance_id", "boot_id"):
                label(row.get(key))
            check(row["runtime_id"] not in seen)
            seen.add(row["runtime_id"])
            number(row.get("last_seen_age_ms"))
            consent = row.get("consent")
            check(isinstance(consent, dict) and type(consent.get("granted")) is bool)
            if consent["granted"]:
                label(consent.get("epoch"))
            else:
                check("epoch" not in consent)
            caps = row.get("capabilities")
            check(isinstance(caps, list) and len(caps) <= 5)
            check(all(isinstance(cap, str) and cap in (EVENT_KINDS - {"lifecycle"}) | {"repairs"} for cap in caps))
            check(len(set(caps)) == len(caps))
            if row.get("active_session_id") not in (None, ""):
                label(row["active_session_id"])
    elif kind in ("start", "stop"):
        label(value.get("session_id"))
        label(value.get("request_id"))
        check(value.get("state") == kind + "_pending")
        number(value.get("control_revision"), minimum=1)
        number(value.get("idempotency_retention_ms"), minimum=1)
        if kind == "stop":
            check(value["session_id"] == context)
    elif kind == "status":
        check(value.get("session_id") == context)
        label(value.get("device_id"), True)
        label(value.get("runtime_id"))
        check(isinstance(value.get("state"), str) and value["state"] in SESSION_STATES)
        check(type(value.get("device_stop_confirmed")) is bool)
        number(value.get("lease_remaining_ms"), 600000)
        number(value.get("control_revision"), minimum=1)
        number(value.get("dropped_total"))
    elif kind == "events":
        after_seq, limit = context
        rows = value.get("events")
        check(isinstance(rows, list) and len(rows) <= limit)
        previous = after_seq
        for row in rows:
            check(isinstance(row, dict))
            number(row.get("seq"), minimum=1)
            check(row["seq"] > previous)
            previous = row["seq"]
            event = row.get("event")
            check(isinstance(event, dict))
            number(event.get("elapsed_ms"), 600000, integral=False)
            check(isinstance(event.get("kind"), str) and event["kind"] in EVENT_KINDS)
            check(isinstance(event.get("code"), str) and event["code"] in EVENT_CODES)
            metrics = event.get("metrics", {})
            check(isinstance(metrics, dict) and len(metrics) <= 24)
        number(value.get("next_seq"))
        check(value["next_seq"] == previous)
        number(value.get("truncated_before_seq"))
        number(value.get("dropped_total"))
    elif kind == "revoke":
        check(value.get("status") == "revoked" and value.get("device_stop_confirmed") is False)
    elif kind == "repair":
        check(set(value) == {"diagnostics_protocol", "server_epoch", "repair_id", "state",
                             "idempotency_retention_ms"})
        label(value.get("repair_id"))
        check(value.get("state") == "pending")
        number(value.get("idempotency_retention_ms"), minimum=1)
    elif kind == "repair_status":
        check(set(value) == {"diagnostics_protocol", "server_epoch", "repair_id", "device_id",
                             "runtime_id", "action", "state", "lease_remaining_ms"})
        check(value.get("repair_id") == context)
        label(value.get("device_id"), True)
        label(value.get("runtime_id"))
        check(isinstance(value.get("action"), str) and value["action"] in REPAIR_ACTIONS)
        check(isinstance(value.get("state"), str) and value["state"] in REPAIR_STATES)
        check(value["state"] != "applied" or value["action"] == "restart_stream")
        check(value["state"] != "accepted" or value["action"] == "reload_player")
        number(value.get("lease_remaining_ms"), 30000)


SERVER_ERROR_CODES = frozenset("""body_limit consent_required credential_role_denied
diagnostics_disabled entropy_unavailable future_revision idempotency_conflict
identity_collision identity_limit invalid_capabilities invalid_content_type
invalid_credentials invalid_error_code invalid_framing invalid_json invalid_poll
invalid_query invalid_registration invalid_result invalid_start invalid_stop
method_denied not_found origin_denied preflight_denied rate_limited result_conflict
runtime_busy runtime_expired runtime_limit runtime_mismatch server_epoch_mismatch
session_terminal stale_control stale_poll state_limit stop_already_requested
telemetry_rate_limited unexpected_body response_limit invalid_events invalid_event
session_inactive sequence_conflict event_storage_limit invalid_repair capability_required
repair_busy invalid_repair_poll invalid_repair_result repair_terminal""".split())


def http_error(response, mutation, epoch):
    """Read only a bounded typed error envelope, never relay a remote message."""
    error = DiagnosticError("http_error", status=response.code,
                            unknown=mutation and response.code >= 500)
    try:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return error
        raw = response.read(8193)
        if len(raw) > 8192:
            return error
        value = strict_json(raw)
        if (not isinstance(value, dict) or type(value.get("diagnostics_protocol")) is not int
                or value["diagnostics_protocol"] != 2 or not isinstance(value.get("server_epoch"), str)
                or not IDENTIFIER.fullmatch(value["server_epoch"])):
            return error
        if epoch is not None and value["server_epoch"] != epoch:
            error = DiagnosticError("epoch_changed", status=response.code, unknown=mutation)
        code = value.get("error", {}).get("code")
        if isinstance(code, str) and code in SERVER_ERROR_CODES:
            error.server_code = code
    except Exception:
        pass
    finally:
        response.close()
    return error


class DiagnosticsClient:
    def __init__(self, server, token, timeout=10, *, opener=None):
        self.server = server_base(server)
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise DiagnosticError("invalid_token")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise DiagnosticError("invalid_input")
        self._token = token
        self.timeout = timeout
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self._busy = threading.Lock()

    def _request(self, method, path, payload=None, *, epoch=None, kind, context=None):
        mutation = method != "GET"
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        if epoch is not None:
            headers["X-OTT-Diagnostics-Epoch"] = identifier(epoch)
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.server + BASE + path, data=body, headers=headers, method=method)
        if not self._busy.acquire(blocking=False):
            raise DiagnosticError("request_in_progress", unknown=mutation)
        outcome = queue.Queue(maxsize=1)

        def run():
            completed = (False, DiagnosticError("transport_error", unknown=mutation))
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    status = response.status
                    if status != (202 if kind in ("start", "stop", "repair") else 200):
                        raise DiagnosticError("http_error", status=status, unknown=mutation)
                    if response.headers.get_content_type() != "application/json":
                        raise DiagnosticError("invalid_response", unknown=mutation)
                    raw = response.read(MAX_REPLY + 1)
                    if len(raw) > MAX_REPLY:
                        raise DiagnosticError("response_too_large", unknown=mutation)
                    value = strict_json(raw)
                    if (not isinstance(value, dict)
                            or type(value.get("diagnostics_protocol")) is not int
                            or value["diagnostics_protocol"] != 2):
                        raise DiagnosticError("invalid_response", unknown=mutation)
                    if not isinstance(value.get("server_epoch"), str) or not IDENTIFIER.fullmatch(value["server_epoch"]):
                        raise DiagnosticError("invalid_response", unknown=mutation)
                    if epoch is not None and value["server_epoch"] != epoch:
                        raise DiagnosticError("epoch_changed", unknown=mutation)
                    validate_reply(value, kind, context)
                    completed = (True, public_output(value, self._token))
            except urllib.error.HTTPError as exc:
                completed = (False, http_error(exc, mutation, epoch))
            except DiagnosticError as exc:
                exc.unknown = exc.unknown or mutation
                completed = (False, exc)
            except Exception:
                completed = (False, DiagnosticError("transport_error", unknown=mutation))
            finally:
                self._busy.release()
            # Publish only after response cleanup and releasing the worker slot.
            outcome.put(completed)

        threading.Thread(target=run, daemon=True).start()
        try:
            ok, result = outcome.get(timeout=self.timeout)
        except queue.Empty:
            raise DiagnosticError("timeout", unknown=mutation) from None
        if not ok:
            raise result
        return result

    def runtimes(self, device_id):
        return self._request("GET", "/runtimes?" + urllib.parse.urlencode({"device_id": identifier(device_id, True)}), kind="runtimes")

    def start(self, device_id, runtime_id, consent_epoch, lease_ms, idempotency_key, server_epoch):
        body = {"device_id": identifier(device_id, True), "runtime_id": identifier(runtime_id),
                "consent_epoch": identifier(consent_epoch), "lease_ms": integer(lease_ms, 1000, 600000),
                "idempotency_key": identifier(idempotency_key), "server_epoch": identifier(server_epoch),
                "profile": "standard"}
        return self._request("POST", "/sessions", body, epoch=server_epoch, kind="start")

    def status(self, session_id):
        return self._request("GET", "/sessions/" + identifier(session_id), kind="status", context=session_id)

    def events(self, session_id, after_seq=0, limit=32):
        query = urllib.parse.urlencode({"after_seq": integer(after_seq, 0, SAFE_INTEGER), "limit": integer(limit, 1, 32)})
        return self._request("GET", "/sessions/" + identifier(session_id) + "/events?" + query, kind="events", context=(after_seq, limit))

    def stop(self, session_id, idempotency_key, server_epoch):
        body = {"idempotency_key": identifier(idempotency_key), "reason": "operator", "server_epoch": identifier(server_epoch)}
        return self._request("POST", "/sessions/" + identifier(session_id) + "/stop", body, epoch=server_epoch, kind="stop", context=session_id)

    def revoke(self, runtime_id, server_epoch):
        return self._request("DELETE", "/runtimes/" + identifier(runtime_id), epoch=server_epoch, kind="revoke")

    def repair(self, device_id, runtime_id, consent_epoch, action, deadline_ms,
               idempotency_key, server_epoch):
        if not isinstance(action, str) or action not in REPAIR_ACTIONS:
            raise DiagnosticError("invalid_input")
        body = {"device_id": identifier(device_id, True), "runtime_id": identifier(runtime_id),
                "consent_epoch": identifier(consent_epoch), "action": action,
                "deadline_ms": integer(deadline_ms, 1000, 30000),
                "idempotency_key": identifier(idempotency_key), "server_epoch": identifier(server_epoch)}
        return self._request("POST", "/repairs", body, epoch=server_epoch, kind="repair")

    def repair_status(self, repair_id):
        return self._request("GET", "/repairs/" + identifier(repair_id),
                             kind="repair_status", context=repair_id)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise DiagnosticError("invalid_arguments")


def connection_arguments(parser):
    parser.add_argument("--server", required=True, help="Exact HTTPS controller base, including deployment path")
    credentials = parser.add_mutually_exclusive_group(required=True)
    credentials.add_argument("--token-env", help="Environment variable name containing the scoped token")
    credentials.add_argument("--token-file", help="POSIX owned mode-0600 file; use --token-env on Windows")
    parser.add_argument("--timeout", type=float, default=10, help="Total request seconds, at most 30")


def client_from_arguments(args):
    return DiagnosticsClient(args.server, load_token(env=args.token_env, path=args.token_file), args.timeout)


def main(argv=None):
    try:
        parser = Parser(description=__doc__)
        connection_arguments(parser)
        commands = parser.add_subparsers(dest="command", required=True)
        runtimes = commands.add_parser("runtimes", help="List exact device runtimes and discover server_epoch")
        runtimes.add_argument("--device", required=True)
        start = commands.add_parser("start", help="Request capture; acceptance does not confirm execution")
        start.add_argument("--device", required=True)
        start.add_argument("--runtime", required=True)
        start.add_argument("--consent-epoch", required=True)
        start.add_argument("--lease-ms", type=int, default=600000)
        start.add_argument("--idempotency-key", required=True)
        start.add_argument("--server-epoch", required=True)
        for name in ("status", "events", "stop"):
            child = commands.add_parser(name)
            child.add_argument("--session", required=True)
            if name == "events":
                child.add_argument("--after-seq", type=int, default=0)
                child.add_argument("--limit", type=int, default=32)
            if name == "stop":
                child.add_argument("--idempotency-key", required=True)
                child.add_argument("--server-epoch", required=True)
        revoke = commands.add_parser("revoke", help="Revoke one runtime; device stop remains unconfirmed")
        revoke.add_argument("--runtime", required=True)
        revoke.add_argument("--server-epoch", required=True)
        repair = commands.add_parser("repair", help="Request an exact-runtime restart; acceptance does not prove recovery")
        repair.add_argument("--device", required=True)
        repair.add_argument("--runtime", required=True)
        repair.add_argument("--consent-epoch", required=True)
        repair.add_argument("--action", choices=REPAIR_ACTIONS, required=True)
        repair.add_argument("--deadline-ms", type=int, default=10000)
        repair.add_argument("--idempotency-key", required=True)
        repair.add_argument("--server-epoch", required=True)
        repair_status = commands.add_parser("repair-status", help="Read the exact repair receipt; accepted reload intent is not an observed reload")
        repair_status.add_argument("--repair", required=True)
        args = parser.parse_args(argv)
        client = client_from_arguments(args)
        if args.command == "runtimes":
            result = client.runtimes(args.device)
        elif args.command == "start":
            result = client.start(args.device, args.runtime, args.consent_epoch, args.lease_ms, args.idempotency_key, args.server_epoch)
        elif args.command == "status":
            result = client.status(args.session)
        elif args.command == "events":
            result = client.events(args.session, args.after_seq, args.limit)
        elif args.command == "stop":
            result = client.stop(args.session, args.idempotency_key, args.server_epoch)
        elif args.command == "repair":
            result = client.repair(args.device, args.runtime, args.consent_epoch, args.action,
                                   args.deadline_ms, args.idempotency_key, args.server_epoch)
        elif args.command == "repair-status":
            result = client.repair_status(args.repair)
        else:
            result = client.revoke(args.runtime, args.server_epoch)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except DiagnosticError as exc:
        print(json.dumps(exc.public()), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps(DiagnosticError("transport_error", unknown=True).public()), file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
