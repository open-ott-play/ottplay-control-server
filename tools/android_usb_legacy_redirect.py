#!/usr/bin/env python3
"""Redirect retired per-tablet USB commands to the shared manager."""
import argparse
import json
import os
from pathlib import Path
import re


OLD_CONFIG = "/usr/local/etc/ott-k8a-recovery/config.json"
NEW_CONFIG = "/usr/local/etc/ott-android-manager/config.json"
NEW_SCRIPT = "/usr/local/etc/ott-android-manager/android_usb_manager.py"


class RedirectError(RuntimeError):
    pass


def read_config(path):
    source = Path(path)
    if source.is_symlink():
        raise RedirectError("Configuration must not be a symlink")
    try:
        value = json.loads(source.read_text())
    except (OSError, ValueError):
        raise RedirectError("Cannot read valid private manager configuration") from None
    if not isinstance(value, dict):
        raise RedirectError("Invalid manager configuration")
    return value


def identity(target):
    if not isinstance(target, dict):
        raise RedirectError("Missing legacy device identity")
    for field in ("serial", "model", "android_id"):
        if (
            not isinstance(target.get(field), str)
            or not target[field]
            or any(ord(character) < 32 for character in target[field])
        ):
            raise RedirectError("Incomplete legacy device identity")
    if type(target.get("sdk")) is not int:
        raise RedirectError("Invalid legacy Android SDK")
    if not re.fullmatch(r"[0-9a-f]{16}", target["android_id"]):
        raise RedirectError("Invalid legacy Android identity")
    return tuple(target[field] for field in ("serial", "model", "sdk", "android_id"))


def build_command(
    argv=None, *, old_config=OLD_CONFIG, new_config=NEW_CONFIG,
    new_script=NEW_SCRIPT, reader=read_config,
):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "wake", "recover", "reboot", "run"):
        commands.add_parser(command)
    swipe = commands.add_parser("swipe")
    swipe.add_argument("coordinates", type=int, nargs=4)
    swipe.add_argument("--duration", type=int, default=500)
    commands.add_parser("screenshot").add_argument("name")
    args = parser.parse_args(argv)
    if args.command == "run":
        raise RedirectError(
            "Legacy service retired; use ott-android-manager.service"
        )
    if args.config != str(old_config):
        raise RedirectError("Only the known legacy configuration is accepted")
    if args.command == "screenshot" and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}\.png", args.name
    ):
        raise RedirectError("Screenshot requires a bare PNG filename using letters, digits, _ or -")
    previous, current = reader(old_config), reader(new_config)
    if (
        type(previous.get("version")) is not int or previous["version"] != 1
        or type(current.get("version")) is not int or current["version"] != 2
    ):
        raise RedirectError("Unsupported configuration version")
    expected = identity(previous.get("target"))
    devices = current.get("devices")
    if not isinstance(devices, dict):
        raise RedirectError("Missing shared device registry")
    matches = []
    for alias, device in devices.items():
        if not isinstance(device, dict) or device.get("enabled", True) is not True:
            continue
        try:
            candidate = identity(device.get("target"))
        except RedirectError:
            continue
        if candidate == expected:
            if (
                not isinstance(alias, str)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,59}", alias)
            ):
                raise RedirectError("Invalid shared device alias")
            matches.append(alias)
    if len(matches) != 1:
        raise RedirectError("Legacy target has no unique enabled shared identity")
    command = [
        "/usr/bin/python3", "-B", str(new_script), "--config", str(new_config),
        args.command, matches[0],
    ]
    if args.command == "swipe":
        command.extend([*map(str, args.coordinates), "--duration", str(args.duration)])
    elif args.command == "screenshot":
        command.append(args.name)
    return command


def main(argv=None):
    command = build_command(argv)
    os.execv(command[0], command)


if __name__ == "__main__":
    try:
        main()
    except (RedirectError, OSError) as exc:
        raise SystemExit(str(exc))
