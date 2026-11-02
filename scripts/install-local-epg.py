#!/usr/bin/env python3
"""Install a separate loopback-only native EPG service for the macOS CLI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


LABEL = "com.ottplay.epg-local"
OWNER = "ottplay-control-server/local-epg"
DEFAULT_PORT = 18792


class InstallError(Exception):
    pass


class Paths:
    def __init__(self, home=None):
        home = Path.home() if home is None else Path(home)
        self.root = home / "Library/Application Support/OTTPlay/epg-local"
        self.manifest = self.root / "installation.json"
        self.agent = home / "Library/LaunchAgents" / (LABEL + ".plist")
        self.cache = home / ".cache/ottplay-control/epg-native"
        self.logs = home / "Library/Logs/OTTPlay/epg-local"

    def binary(self, source):
        return self.root / "revisions" / source / "ottplay-server"


def _plain(path, directory=False):
    if any(parent.is_symlink() for parent in path.parents):
        raise InstallError("Refusing a symlink in the local EPG installation path")
    if path.is_symlink() or (path.exists() and (not path.is_dir() if directory else not path.is_file())):
        raise InstallError("Refusing an unexpected file type in the local EPG installation")


def _directory(path):
    # Do not redirect installation writes through a user-created symlink.
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise InstallError("Refusing a symlink in the local EPG installation path")
    _plain(path, directory=True)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)


def _atomic(path, data, mode=0o600):
    _plain(path)
    _directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".epg-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _metadata(source, digest, port):
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise InstallError("source-sha must be a full lowercase Git commit ID")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise InstallError("sha256 must be a full lowercase binary digest")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise InstallError("port must be between 1024 and 65535")
    return {"version": 1, "owner": OWNER, "source_sha": source, "sha256": digest, "port": port}


def plist_bytes(paths, metadata):
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(paths.binary(metadata["source_sha"])), "--host", "127.0.0.1",
                             "--port", str(metadata["port"])],
        "EnvironmentVariables": {"EPG_ONLY": "true", "EPG_CACHE_DIR": str(paths.cache)},
        "WorkingDirectory": str(paths.root),
        "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 15,
        "ProcessType": "Background",
        "StandardOutPath": str(paths.logs / "stdout.log"),
        "StandardErrorPath": str(paths.logs / "stderr.log"),
    }, sort_keys=True)


def owned(paths, *, required=False):
    for path in (paths.root, paths.cache, paths.logs):
        _plain(path, directory=True)
    for path in (paths.manifest, paths.agent):
        _plain(path)
    if not paths.manifest.exists():
        if paths.agent.exists() or (paths.root.exists() and any(paths.root.iterdir())):
            raise InstallError("Existing local EPG files have no ownership manifest; refusing to replace them")
        if required:
            raise InstallError("Local EPG is not installed")
        return None
    try:
        if paths.manifest.stat().st_size > 4096:
            raise ValueError()
        value = json.loads(paths.manifest.read_text())
        expected = _metadata(value["source_sha"], value["sha256"], value["port"])
        if type(value.get("version")) is not int or value != expected:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise InstallError("Invalid local EPG ownership manifest") from None
    if paths.agent.exists() and paths.agent.read_bytes() != plist_bytes(paths, value):
        raise InstallError("The local EPG LaunchAgent was modified; refusing to replace it")
    return value


def _launchctl(*arguments, check=True):
    result = subprocess.run(["launchctl", *arguments], capture_output=True, text=True, timeout=20)
    if check and result.returncode:
        raise InstallError("launchctl could not complete the local EPG operation")
    return result.returncode == 0


def _target():
    return "gui/" + str(os.getuid())


def running():
    return _launchctl("print", _target() + "/" + LABEL, check=False)


def _port_free(port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", port))
    except OSError:
        raise InstallError("The requested local EPG port is already in use") from None


def _verified_binary(path, digest):
    _plain(path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 512 * 1024 * 1024:
            raise InstallError("Expected a regular native server binary of at most 512 MiB")
        data = path.read_bytes()
    except OSError:
        raise InstallError("Could not read the native EPG binary") from None
    if hashlib.sha256(data).hexdigest() != digest:
        raise InstallError("Native EPG binary SHA-256 does not match")
    return data


def install(paths, binary, source, digest, port=DEFAULT_PORT, *, start=True):
    metadata = _metadata(source, digest, port)
    previous = owned(paths)
    was_running = running()
    if was_running and previous is None:
        raise InstallError("The LaunchAgent label is already owned by another installation")
    data = _verified_binary(Path(binary), digest)
    destination = paths.binary(source)
    if destination.exists():
        _verified_binary(destination, digest)
    if not was_running or previous["port"] != port:
        _port_free(port)
    old_agent = paths.agent.read_bytes() if paths.agent.exists() else None
    old_manifest = paths.manifest.read_bytes() if paths.manifest.exists() else None
    for directory in (paths.root, destination.parent, paths.cache, paths.logs, paths.agent.parent):
        _directory(directory)
    for log in (paths.logs / "stdout.log", paths.logs / "stderr.log"):
        _plain(log)
        if not log.exists():
            _atomic(log, b"")
    if not destination.exists():
        _atomic(destination, data, mode=0o700)
    _plain(paths.manifest)
    _plain(paths.agent)
    current_manifest = paths.manifest.read_bytes() if paths.manifest.exists() else None
    current_agent = paths.agent.read_bytes() if paths.agent.exists() else None
    if current_manifest != old_manifest or current_agent != old_agent:
        raise InstallError("Local EPG ownership changed while preparing the installation")
    if was_running:
        _launchctl("bootout", _target() + "/" + LABEL)
    try:
        _atomic(paths.agent, plist_bytes(paths, metadata))
        _atomic(paths.manifest, (json.dumps(metadata, indent=2) + "\n").encode())
        if start:
            _launchctl("bootstrap", _target(), str(paths.agent))
    except Exception:
        if previous is None and paths.manifest.exists() and paths.agent.exists():
            # A first bootstrap failure leaves an owned, stopped installation
            # that can be inspected and started without losing its cache.
            raise
        if old_agent is None:
            paths.agent.unlink(missing_ok=True)
        else:
            _atomic(paths.agent, old_agent)
        if old_manifest is None:
            paths.manifest.unlink(missing_ok=True)
        else:
            _atomic(paths.manifest, old_manifest)
        if was_running and old_agent is not None:
            _launchctl("bootstrap", _target(), str(paths.agent), check=False)
        raise
    return metadata


def start_service(paths):
    metadata = owned(paths, required=True)
    if not paths.agent.exists():
        raise InstallError("The managed LaunchAgent is missing; reinstall it")
    _verified_binary(paths.binary(metadata["source_sha"]), metadata["sha256"])
    if not running():
        _port_free(metadata["port"])
        _launchctl("bootstrap", _target(), str(paths.agent))
    return metadata


def stop_service(paths):
    metadata = owned(paths, required=True)
    if running():
        _launchctl("bootout", _target() + "/" + LABEL)
    return metadata


def uninstall(paths):
    stop_service(paths)
    paths.agent.unlink(missing_ok=True)
    # Keep the manifest with the immutable binary: ownership remains provable if
    # reinstalling, while cached public guide data and logs remain recoverable.
    return {"registered": False, "cache_preserved": True, "runtime_preserved": True}


def status(paths):
    metadata = owned(paths)
    if metadata is None:
        return {"installed": False, "registered": False, "running": False, "ready": False}
    result = {"installed": True, "registered": paths.agent.exists(), "running": running(),
              "ready": False, "source_sha": metadata["source_sha"],
              "url": "http://127.0.0.1:" + str(metadata["port"]) + "/epg/v1"}
    if result["running"]:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(result["url"] + "/health", timeout=3) as response:
                body = response.read(4097)
            if len(body) <= 4096:
                value = json.loads(body)
                result["ready"] = (isinstance(value, dict) and type(value.get("version")) is int
                                   and value.get("version") == 1 and value.get("source") == "epg-one"
                                   and value.get("stale") is False)
        except (OSError, ValueError, urllib.error.URLError):
            pass
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install")
    setup.add_argument("--binary", required=True, type=Path)
    setup.add_argument("--source-sha", required=True)
    setup.add_argument("--sha256", required=True)
    setup.add_argument("--port", type=int, default=DEFAULT_PORT)
    setup.add_argument("--no-start", action="store_true")
    for command in ("status", "start", "stop", "uninstall"):
        commands.add_parser(command)
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("This installer manages a per-user macOS LaunchAgent; see docs/local-epg.md for other systems")
    if os.getuid() == 0:
        parser.error("Run this installer as the signed-in user, without sudo")
    paths = Paths()
    try:
        if args.command == "install":
            metadata = install(paths, args.binary, args.source_sha, args.sha256, args.port, start=not args.no_start)
            result = {"installed": True, "source_sha": metadata["source_sha"],
                      "fallback_url": "http://127.0.0.1:" + str(metadata["port"]) + "/epg/v1"}
        else:
            result = {"status": status, "start": start_service, "stop": stop_service, "uninstall": uninstall}[args.command](paths)
        print(json.dumps(result, indent=2))
        return 0
    except (InstallError, OSError, subprocess.SubprocessError) as exc:
        print("Error: " + (str(exc) if isinstance(exc, InstallError) else "Local EPG installation operation failed"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
