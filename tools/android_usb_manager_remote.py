#!/usr/bin/env python3
"""Run an alias-scoped USB manager command on an existing SSH host."""
import argparse
from pathlib import PurePosixPath
import re
import shlex
import subprocess


def build_command(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Existing SSH host or config alias")
    parser.add_argument(
        "--remote-config", default="/usr/local/etc/ott-android-manager/config.json"
    )
    parser.add_argument(
        "--remote-script",
        default="/usr/local/etc/ott-android-manager/android_usb_manager.py",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status").add_argument("alias", nargs="?")
    for command in ("run", "tick"):
        commands.add_parser(command)
    for command in ("recover", "wake", "reboot"):
        commands.add_parser(command).add_argument("alias")
    swipe = commands.add_parser("swipe")
    swipe.add_argument("alias")
    swipe.add_argument("coordinates", type=int, nargs=4)
    swipe.add_argument("--duration", type=int, default=500)
    screenshot = commands.add_parser("screenshot")
    screenshot.add_argument("alias")
    screenshot.add_argument("path", help="New absolute PNG path on the USB host")
    adb = commands.add_parser("adb")
    adb.add_argument("alias")
    adb.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if (
        not args.host
        or args.host.startswith("-")
        or any(character.isspace() or ord(character) < 32 for character in args.host)
    ):
        parser.error("--host must be one SSH host or configured alias")
    for value in (args.remote_config, args.remote_script):
        if not PurePosixPath(value).is_absolute() or any(
            ord(character) < 32 for character in value
        ):
            parser.error("Remote paths must be absolute and contain no control characters")
    alias = getattr(args, "alias", None)
    if alias is not None and (
        not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,59}", alias)
        or alias in (".", "..")
    ):
        parser.error("Invalid device alias")
    remote = [
        "sudo", "-n", "/usr/bin/python3", "-B", args.remote_script,
        "--config", args.remote_config, args.command,
    ]
    if alias is not None:
        remote.append(alias)
    if args.command == "adb":
        arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
        if not arguments or any("\0" in argument for argument in arguments):
            parser.error("adb requires a command after the device alias")
        remote.extend(["--", *arguments])
    elif args.command == "swipe":
        if any(value < 0 for value in args.coordinates) or not 50 <= args.duration <= 3000:
            parser.error("swipe needs non-negative coordinates and duration 50..3000 ms")
        remote.extend([*map(str, args.coordinates), "--duration", str(args.duration)])
    elif args.command == "screenshot":
        if not PurePosixPath(args.path).is_absolute() or any(
            ord(character) < 32 for character in args.path
        ):
            parser.error("screenshot needs a new absolute path on the USB host")
        remote.append(args.path)
    return [
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "--", args.host, shlex.join(remote),
    ]


def main(argv=None):
    # No PTY, text conversion, JSON wrapping, capture, or stdout diagnostics:
    # exec-out screenshots and other binary output pass through unchanged.
    return subprocess.run(build_command(argv)).returncode


if __name__ == "__main__":
    raise SystemExit(main())
