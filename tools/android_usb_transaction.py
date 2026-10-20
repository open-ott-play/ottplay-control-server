#!/usr/bin/env python3
"""Atomic maintenance transactions for an already provisioned USB manager."""
import argparse
import base64
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid


PORT_TOKEN = "__OTT_CDP_PORT__"
MAX_PAYLOAD = 128 * 1024 * 1024
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")


class TransactionError(RuntimeError):
    pass


class Transactions:
    """Keep device binding, temporary resources and use in one manager lock."""

    def __init__(self, manager, runner=subprocess.run):
        self.manager = manager
        self.runner = runner

    def _container(self):
        self.manager._require_lock()
        details = self.manager._inspect()
        if not details or not details.get("State", {}).get("Running"):
            raise TransactionError("Shared ADB container is unavailable")
        identifier = details.get("Id", "")
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise TransactionError("Shared container identity is unavailable")
        return identifier

    def _aux(self, container, arguments, data=None):
        self.manager._require_lock()
        # Use the observed container ID, never a name that another process could
        # replace between staging and use.
        command = [
            self.manager.c["docker_path"], "exec",
            *(["-i"] if data is not None else []), container, *arguments,
        ]
        try:
            result = self.runner(
                command, input=data, capture_output=True,
                timeout=self.manager.c.get("command_timeout_seconds", 60),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise TransactionError("USB transaction operation timed out or failed") from None
        if result.returncode:
            raise TransactionError("USB transaction operation failed")
        return result.stdout

    def _temporary(self):
        return "/tmp/ott-transaction-" + uuid.uuid4().hex

    def _reserve_temporary(self, container):
        temporary = self._temporary()
        source = (
            "import os,sys\n"
            "fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n"
            "os.close(fd)\n"
        )
        # Ownership begins only after exclusive creation succeeds. A collision
        # must not reach an operation's cleanup and remove somebody else's file.
        self._aux(container, ["python3", "-c", source, temporary])
        return temporary

    def _processes(self, alias):
        """Prefer all processes; old toolbox ps may reject -A or return only a header."""
        def probe(all_processes):
            # Capture ps status inside the device shell: old ADB shell protocols
            # do not return it, while transport errors must still propagate.
            command = ("ps -A" if all_processes else "ps") + (
                " 2>&1; ott_ps_status=$?; echo; "
                "echo __OTT_PS_STATUS__=$ott_ps_status"
            )
            raw = self.manager._device_adb(alias, ("shell", command))
            output = raw.decode(errors="replace").replace("\r\n", "\n").strip()
            match = re.fullmatch(r"(.*?)\s*__OTT_PS_STATUS__=([0-9]{1,3})", output, re.S)
            if match is None:
                raise TransactionError("Process discovery returned an invalid status")
            body, status = match.group(1).strip(), int(match.group(2))
            unsupported = status and re.search(
                r"(?i:\b(?:unknown|invalid|unrecognized|unsupported|bad)\s+"
                r"(?:option|argument)\b)[^\n]*\bA\b|(?i:\bbad\s+pid)\s+['\"]?-A(?:['\"]|\s|$)",
                body.splitlines()[0] if body else "",
            )
            if unsupported and all_processes:
                return None
            if status:
                raise TransactionError("Process discovery failed")
            lines = body.splitlines()
            if not lines:
                return []
            header = lines[0].split()
            if "PID" not in header or header[-1] not in ("NAME", "CMD", "COMMAND", "ARGS"):
                raise TransactionError("Process discovery returned an invalid listing")
            pid_column = header.index("PID")
            rows = [line.split() for line in lines[1:] if line.strip()]
            if any(len(row) <= pid_column + 1 or not row[pid_column].isdigit() for row in rows):
                raise TransactionError("Process discovery returned an invalid listing")
            return [(row[pid_column], row[-1]) for row in rows]

        rows = probe(True)
        return probe(False) if not rows else rows

    def pull(self, alias, device_path):
        if not isinstance(device_path, str) or not device_path.startswith("/") or "\0" in device_path:
            raise TransactionError("An absolute device file path is required")

        def action(device):
            container = self._container()
            probe = (
                "if [ -f " + shlex.quote(device_path) + " ]; then "
                "echo __OTT_REGULAR_FILE__; else echo __OTT_NOT_REGULAR__; fi"
            )
            kind = self.manager._device_adb(alias, ("shell", probe), container_id=container)
            if kind.strip() != b"__OTT_REGULAR_FILE__":
                raise TransactionError("Pull requires one readable regular device file")
            # An existing non-directory destination makes ADB reject a source
            # changed into a directory after the probe instead of recursing.
            temporary = self._reserve_temporary(container)
            try:
                self.manager._device_adb(
                    alias, ("pull", device_path, temporary),
                    file_size_limit=MAX_PAYLOAD, container_id=container,
                )
                return self._aux(container, ["cat", temporary])
            finally:
                self._aux(container, ["rm", "-f", temporary])

        return self.manager._operate(alias, action)

    def staged_adb(self, alias, payload, arguments):
        if not isinstance(payload, bytes) or len(payload) > MAX_PAYLOAD:
            raise TransactionError("Invalid or oversized staged data")
        if not isinstance(arguments, (list, tuple)) or any(
            not isinstance(value, str) or "\0" in value for value in arguments
        ):
            raise TransactionError("Invalid staged arguments")
        arguments = list(arguments)
        install = (
            len(arguments) >= 2 and arguments[0] == "install"
            and arguments[-1] == "@staged"
            and all(flag in ("-r", "-t", "-d", "-g") for flag in arguments[1:-1])
        )
        push = (
            len(arguments) == 3 and arguments[:2] == ["push", "@staged"]
            and isinstance(arguments[2], str) and arguments[2].startswith("/")
            and "\0" not in arguments[2]
        )
        if not install and not push:
            raise TransactionError("Only an APK install or device push may consume staged data")

        def action(device):
            container = self._container()
            temporary = self._reserve_temporary(container)
            source = (
                "import os,sys,shutil,stat\n"
                "fd=os.open(sys.argv[1],os.O_WRONLY|os.O_NOFOLLOW)\n"
                "with os.fdopen(fd,'wb') as f:\n"
                " s=os.fstat(f.fileno())\n"
                " if not stat.S_ISREG(s.st_mode) or s.st_size:raise ValueError('Invalid staging file')\n"
                " shutil.copyfileobj(sys.stdin.buffer,f,1024*1024)\n"
            )
            try:
                self._aux(container, ["python3", "-c", source, temporary], payload)
                command = [temporary if value == "@staged" else value for value in arguments]
                return self.manager._device_adb(alias, command, container_id=container)
            finally:
                self._aux(container, ["rm", "-f", temporary])

        return self.manager._operate(alias, action)

    def cdp(self, alias, package, source, request, expected_model=None):
        if not isinstance(package, str) or not PACKAGE.fullmatch(package):
            raise TransactionError("Invalid application package")
        if (
            not isinstance(source, str) or PORT_TOKEN not in source
            or len(source) > 256 * 1024 or not isinstance(request, dict)
        ):
            raise TransactionError("Invalid CDP request")
        payload = json.dumps(request).encode()
        if len(payload) > 4 * 1024 * 1024:
            raise TransactionError("CDP request is too large")

        def text(raw):
            return raw.decode(errors="replace").replace("\r\n", "\n").strip()

        def action(device):
            container = self._container()
            if expected_model is not None:
                model = text(self.manager._device_adb(alias, ("shell", "getprop", "ro.product.model")))
                if model != expected_model:
                    raise TransactionError("Unexpected device model")
            pids = [pid for pid, name in self._processes(alias) if name == package]
            if len(pids) != 1:
                raise TransactionError("Application process is not unique")
            socket = "webview_devtools_remote_" + pids[0]
            sockets = text(self.manager._device_adb(alias, ("shell", "cat", "/proc/net/unix")))
            names = {line.split()[-1].lstrip("@") for line in sockets.splitlines() if line.split()}
            if socket not in names:
                raise TransactionError("Application debugging socket is unavailable")
            remote = "localabstract:" + socket
            raw_port = self.manager._forward(alias, ("forward", "tcp:0", remote))
            port = text(raw_port)
            if not port.isdecimal() or not 1 <= int(port) <= 65535:
                raise TransactionError("ADB did not allocate a valid forwarding port")
            local = "tcp:" + port
            try:
                # Setup, use and removal all occur before _operate releases its
                # lock. A reconcile cannot reassign this port between calls.
                return self._aux(
                    container, ["python3", "-c", source.replace(PORT_TOKEN, port)], payload,
                )
            finally:
                self.manager._forward(alias, ("forward", "--remove", local))

        return self.manager._operate(alias, action)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    # Imported lazily so routing/validation tests do not require Linux fcntl.
    from android_usb_manager import Manager

    raw = sys.stdin.buffer.read((MAX_PAYLOAD * 4 // 3) + 8 * 1024 * 1024 + 1)
    if len(raw) > (MAX_PAYLOAD * 4 // 3) + 8 * 1024 * 1024:
        raise TransactionError("Transaction request is too large")
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise TransactionError("Invalid transaction")
    transaction = Transactions(Manager(json.loads(Path(args.config).read_text())))
    alias = request.get("alias")
    operation = request.get("operation")
    if operation == "pull":
        result = transaction.pull(alias, request.get("device_path"))
    elif operation == "staged_adb":
        try:
            payload = base64.b64decode(request.get("data", ""), validate=True)
        except (ValueError, TypeError):
            raise TransactionError("Invalid staged encoding") from None
        result = transaction.staged_adb(alias, payload, request.get("arguments", []))
    elif operation == "cdp":
        result = transaction.cdp(
            alias, request.get("package"), request.get("source"),
            request.get("request"), request.get("expected_model"),
        )
    else:
        raise TransactionError("Unknown USB transaction")
    sys.stdout.buffer.write(result)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        raise SystemExit(str(exc))
